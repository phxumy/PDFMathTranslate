from types import SimpleNamespace

import numpy as np

from pdf2zh.converter import (
    _mark_prose_words,
    _recover_preserved_region_prose,
    _is_prose_italic_font,
    _MATH_FONT_RE,
    _should_extend_formula_run,
)


def run(text, x=20, y=100, font="CMR8", size=8):
    chars = []
    for value in text:
        if value == " ":
            x += size * 0.35
            continue
        ch = SimpleNamespace(
            x0=x,
            x1=x + size * 0.45,
            y0=y,
            y1=y + size,
            size=size,
            fontname=font,
            matrix=(1, 0, 0, 1, x, y),
            get_text=lambda v=value: v,
        )
        chars.append(ch)
        x += size * 0.45
    return chars


def detection(name, bounds, value=2):
    return SimpleNamespace(class_name=name, bounds=bounds, value=value)


def recover(chars, detections):
    _mark_prose_words(chars)
    kinds = {d.value: d.class_name for d in detections}
    mapping = _recover_preserved_region_prose(
        chars, np.zeros((800, 600)), detections, kinds, set()
    )
    return mapping, kinds


def test_computer_modern_text_italic_is_distinct_from_math_italic():
    assert _is_prose_italic_font("ABCDEF+CMTI10")
    assert _is_prose_italic_font("ABCDEF+CMSL8")
    assert not _is_prose_italic_font("ABCDEF+CMMI10")
    assert _MATH_FONT_RE.search("CMMI10")
    assert not _MATH_FONT_RE.search("CMITT10")
    chars = run("Let a convex function", font="CMTI10")
    _mark_prose_words(chars)
    assert all(getattr(ch, "_pdf2zh_cm_prose", False) for ch in chars)
    variable = run("x", font="CMTI10")
    _mark_prose_words(variable)
    assert not getattr(variable[0], "_pdf2zh_cm_prose", False)


def test_roman_prose_after_symbol_does_not_join_formula():
    symbol = run("μ", font="CMMI10")
    word = run("horizontal", x=symbol[-1].x1 + 0.4)
    _mark_prose_words(symbol + word)
    assert not _should_extend_formula_run(symbol, word[0])
    operator = run("sin", x=symbol[-1].x1 + 0.4)
    _mark_prose_words(operator)
    assert not getattr(operator[0], "_pdf2zh_prose_word_start", False)


def test_numeric_scientific_footnote_recovers_short_continuation_and_preserves_furniture():
    first = run("1An event happens with high probability", y=100)
    last = run("constants.", y=90)
    copyright = run("Copyright by SIAM. Unauthorized reproduction", y=65)
    mapping, kinds = recover(
        first + last + copyright, [detection("abandon", (0, 50, 600, 120))]
    )
    assert all(id(ch) in mapping for ch in first + last)
    assert len({mapping[id(ch)] for ch in first + last}) == 1
    assert all(id(ch) not in mapping for ch in copyright)
    assert kinds[mapping[id(first[0])]] == "footnote"


def test_symbol_author_notes_remain_preserved():
    chars = run("Received by the editors July 23; accepted for publication", y=100)
    mapping, _ = recover(chars, [detection("abandon", (0, 50, 600, 120))])
    assert not mapping


def test_table_text_cells_recover_separately_from_numeric_columns():
    left = run("Sparse vector recovery", x=20, y=500)
    right = run("Low rank matrix sensing", x=250, y=500)
    number = run("1234", x=470, y=500)
    mapping, kinds = recover(
        left + right + number, [detection("table", (0, 450, 599, 550))]
    )
    assert all(id(ch) in mapping for ch in left + right)
    assert mapping[id(left[0])] != mapping[id(right[0])]
    assert all(id(ch) not in mapping for ch in number)
    assert kinds[mapping[id(left[0])]] == "table_cell"


def test_formula_detector_algorithm_recovers_step_and_formula_together():
    command = run("For", x=20, y=500)
    math = run("k=1,2", x=50, y=500, font="CMMI10")
    step = run("Set the next iterate", x=20, y=485)
    mapping, kinds = recover(
        command + math + step, [detection("isolate_formula", (0, 450, 599, 550))]
    )
    assert all(id(ch) in mapping for ch in command + math + step)
    assert len(set(mapping.values())) == 1
    assert kinds[mapping[id(command[0])]] == "algorithm"
    pure = run("sin x + cos y = 1", y=500)
    mapping, _ = recover(pure, [detection("isolate_formula", (0, 450, 599, 550))])
    assert not mapping


def test_figure_labels_and_top_running_header_stay_preserved():
    chars = run("Sparse vector recovery", y=700)
    for name in ("figure", "abandon"):
        mapping, _ = recover(chars, [detection(name, (0, 650, 599, 750))])
        assert not mapping


def test_converter_exposes_cm_title_theorem_words_but_keeps_latex_glyphs():
    from pdfminer.layout import LTPage
    from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
    from pdf2zh.converter import PDFConverterEx, TranslateConverter

    class Font:
        char_width = staticmethod(lambda cid: 0.5)
        char_disp = staticmethod(lambda cid: 0)
        is_vertical = staticmethod(lambda: False)
        get_descent = staticmethod(lambda: 0.0)

        def __init__(self, name, mapping=None):
            self.fontname = name
            self.mapping = mapping or {}

        def to_unichr(self, cid):
            return self.mapping.get(cid, chr(cid))

    value = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(value, PDFResourceManager())
    value.cur_item = LTPage(0, (0.0, 0.0, 600.0, 800.0))
    value.layout = {0: np.ones((800, 600))}
    value.layout_region_types = {0: {1: "plain text"}}
    value.vfont = value.vchar = ""
    value.translator = SimpleNamespace(name="google", lang_out="zh")
    value.noto_name, value.noto = "noto", None
    value.fontmap, value.fontid = {}, {}
    x = 20.0

    def append(text, name, mapping=None):
        nonlocal x
        font = Font(name, mapping)
        name_id = "F" + str(len(value.fontid))
        value.fontid[font] = name_id
        value.fontmap[name_id] = font
        for char in text:
            if char == " ":
                x += 3.5
                continue
            value.render_char(
                (1, 0, 0, 1, x, 700), font, 10, 1, 0, ord(char), None, PDFGraphicState()
            )
            x += 5

    append("Definition 2.1. ", "CMCSC10")
    append("Let ", "CMTI10")
    append("@", "CMMI10", {ord("@"): r"\phi "})
    x += 3.5
    append("be a convex function.", "CMTI10")
    draft = value.receive_layout(value.cur_item, preview_only=True)
    source = " ".join(draft.sstk)
    assert "Definition 2.1. Let" in source
    assert "be a convex function." in source
    assert r"\phi" not in source
    assert any(r"\phi" in formula for formula in draft.formula_texts)
    assert all("convex" not in formula for formula in draft.formula_texts)


def test_isolated_text_italic_variable_delimiters_and_operators_stay_protected():
    for text in ("(x)", "[y]", "sin(x)", "cos(y)"):
        chars = run(text, font="CMTI10")
        _mark_prose_words(chars)
        assert not any(getattr(ch, "_pdf2zh_cm_prose", False) for ch in chars)


def test_footnote_clipped_word_can_extend_but_nearby_column_cannot():
    left = run("1An event has several constants", x=20, y=100)
    suffix = run("here.", x=left[-1].x1, y=100)
    right = run("Another column begins here", x=suffix[-1].x1 + 6, y=100)
    mapping, _ = recover(
        left + suffix + right, [detection("abandon", (0, 60, left[-1].x1 - 0.1, 120))]
    )
    assert all(id(ch) in mapping for ch in left + suffix)
    assert all(id(ch) not in mapping for ch in right)


def test_display_formula_with_set_operator_is_not_an_algorithm():
    chars = run("Set(x) = A", x=20, y=500)
    mapping, _ = recover(chars, [detection("isolate_formula", (0, 450, 599, 550))])
    assert not mapping


def test_short_roman_connective_needs_a_word_gap_to_stop_math():
    math = run("s", font="CMMI10", size=10)
    joined = run("in", x=math[-1].x1, font="CMR10", size=10)
    _mark_prose_words(math + joined)
    assert _should_extend_formula_run(math, joined[0])
    spaced = run("in", x=math[-1].x1 + 3, font="CMR10", size=10)
    _mark_prose_words(math + spaced)
    assert not _should_extend_formula_run(math, spaced[0])


def test_roman_math_operators_do_not_become_prose_boundaries():
    for name in (
        "argmin",
        "argmax",
        "arccos",
        "arcsin",
        "arctan",
        "cot",
        "csc",
        "sec",
        "dist",
        "sign",
        "supp",
        "prox",
    ):
        chars = run(name, font="CMR10")
        _mark_prose_words(chars)
        assert not getattr(chars[0], "_pdf2zh_prose_word_start", False)


def test_wide_abandon_box_keeps_two_columns_of_footnotes_independent():
    left = run("1A scientific footnote starts", x=20, y=100)
    right = run("2Another scientific note starts", x=300, y=100)
    left_next = run("and continues here.", x=20, y=90)
    right_next = run("and ends here.", x=300, y=90)
    mapping, _ = recover(
        left + right + left_next + right_next, [detection("abandon", (0, 70, 599, 120))]
    )
    assert mapping[id(left[0])] == mapping[id(left_next[0])]
    assert mapping[id(right[0])] == mapping[id(right_next[0])]
    assert mapping[id(left[0])] != mapping[id(right[0])]
