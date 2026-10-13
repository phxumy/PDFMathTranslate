from types import SimpleNamespace
import re

import numpy as np
import pytest
from pdfminer.layout import LTPage
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager

from pdf2zh.converter import (
    PDFConverterEx,
    TranslateConverter,
    _formula_horizontal_geometry,
)


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


def converter(algorithm=False):
    value = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(value, PDFResourceManager())
    value.cur_item = LTPage(0, (0, 0, 600, 800))
    value.layout = {0: np.full((800, 600), 0 if algorithm else 2)}
    value.layout_region_types = {
        0: {2: "isolate_formula" if algorithm else "plain text"}
    }
    if algorithm:
        value.layout_detections = {
            0: [
                SimpleNamespace(
                    class_name="isolate_formula", value=2, bounds=(10, 500, 550, 750)
                )
            ]
        }
    value.vfont = value.vchar = ""
    value.translator = SimpleNamespace(name="google", lang_out="zh")
    value.noto_name, value.noto = "noto", None
    value.fontmap, value.fontid = {}, {}
    return value


def append(value, text, x, y, font="CMR10", mapping=None, size=10):
    resource = Font(font, mapping)
    name = "F" + str(len(value.fontid))
    value.fontid[resource] = name
    value.fontmap[name] = resource
    for char in text:
        if char == " ":
            x += size * 0.35
            continue
        value.render_char(
            (1, 0, 0, 1, x, y),
            resource,
            size,
            1,
            0,
            ord(char),
            None,
            PDFGraphicState(),
        )
        x += size * 0.5
    return x


@pytest.mark.parametrize("marker", ["•", "\\bullet ", "--", "–", "\\ast "])
def test_each_physical_list_item_keeps_anchor_and_its_wrapped_continuation(marker):
    value = converter()
    for y, text in [(700, "First item wraps"), (664, "Second item remains separate.")]:
        append(value, "@", 40, y, "CMSY10", {ord("@"): marker})
        append(value, text, 50, y)
        if y == 700:
            append(value, "onto the following line.", 50, 688)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    prose = [(s, p) for s, p in zip(draft.sstk, draft.pstk) if "item" in s]
    assert [s for s, _ in prose] == [
        "First item wraps onto the following line.",
        "Second item remains separate.",
    ]
    assert [(p.x, p.y) for _, p in prose] == [(50, 700), (50, 664)]
    assert [p.brk for _, p in prose] == [True, False]
    markers = [run for run in draft.var if any(ch.get_text() == marker for ch in run)]
    assert len(markers) == 2
    assert all(len(run) == 1 for run in markers)
    assert all(ch._pdf2zh_layout_class == 0 for run in markers for ch in run)


def test_inline_bullet_and_asterisk_remain_in_mathematical_expression():
    value = converter()
    x = append(value, "The relation is ", 50, 700)
    x = append(value, "x@y@z", x, 700, "CMMI10", {ord("@"): "\\bullet "})
    append(value, " and has three factors.", x, 700)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    assert len([s for s in draft.sstk if "relation" in s]) == 1
    assert any("x\\bullet y\\bullet z" == text for text in draft.formula_texts)


def test_nested_algorithm_steps_keep_their_source_indents():
    value = converter(algorithm=True)
    rows = [
        (40, 50, 700, "\\bullet ", "For k steps"),
        (65, 75, 688, "--", "Set the next iterate."),
        (85, 95, 676, "\\ast ", "Let the auxiliary variable vanish."),
        (40, 50, 664, "\\bullet ", "Output the result."),
    ]
    for mx, x, y, marker, text in rows:
        append(value, "@", mx, y, "CMSY10", {ord("@"): marker})
        append(value, text, x, y)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    actual = [
        (s, p.x, p.y) for s, p in zip(draft.sstk, draft.pstk) if not s.startswith("{v")
    ]
    assert actual == [(text, x, y) for _, x, y, _, text in rows]


def test_displayed_algorithm_equation_stays_at_source_baseline_with_fraction():
    value = converter(algorithm=True)
    append(value, "@", 40, 700, "CMSY10", {ord("@"): "\\bullet "})
    append(value, "For all iterations", 50, 700)
    append(value, "@", 65, 688, "CMR10", {ord("@"): "--"})
    x = append(value, "Set ", 75, 688)
    x = append(value, "x=f(y)", x, 688, "CMMI10")
    append(value, ", where", x, 688)
    append(value, "h(x)=", 130, 660, "CMMI10")
    append(value, "1", 160, 667)
    append(value, "2", 160, 653)
    append(value, "x", 170, 660, "CMMI10")
    draft = value.receive_layout(value.cur_item, preview_only=True)
    display_runs = [
        (run, draft.pstk[draft.varp[i]])
        for i, run in enumerate(draft.var)
        if any(ch.get_text() == "h" for ch in run)
    ]
    assert len(display_runs) == 1
    run, paragraph = display_runs[0]
    assert "".join(ch.get_text() for ch in run) == "h(x)=12x"
    assert paragraph.layout_class == 0
    assert all(ch._pdf2zh_layout_class == 0 for ch in run)
    assert paragraph.x == 130 and paragraph.y == 660
    assert not any(
        "2" in re.sub(r"\{v\d+\}", "", s) for s in draft.sstk if "where" in s
    )


def test_inline_fraction_does_not_become_a_displayed_algorithm_equation():
    value = converter(algorithm=True)
    append(value, "@", 40, 700, "CMSY10", {ord("@"): "\\bullet "})
    x = append(value, "Let ", 50, 700)
    x = append(value, "K=", x, 700, "CMMI10")
    append(value, "1", x, 707)
    append(value, "2", x, 693)
    append(value, " and continue.", x + 8, 700)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    assert len([s for s in draft.sstk if "Let" in s and "continue" in s]) == 1
    for index, run in enumerate(draft.var):
        if any(ch.get_text() == "K" for ch in run):
            assert draft.pstk[draft.varp[index]].layout_class != 0


def test_display_followed_by_a_leftward_list_marker_keeps_first_glyph_anchor():
    value = converter(algorithm=True)
    append(value, "@", 40, 700, "CMSY10", {ord("@"): "\\bullet "})
    append(value, "Set the next iterate, where", 50, 700)
    append(value, "h(x)=y", 130, 672, "CMMI10")
    append(value, "@", 40, 648, "CMSY10", {ord("@"): "\\bullet "})
    append(value, "Output the result.", 50, 648)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    display = next(run for run in draft.var if any(ch.get_text() == "h" for ch in run))
    assert _formula_horizontal_geometry(display)[0] == 130
    index = draft.var.index(display)
    paragraph = draft.pstk[draft.varp[index]]
    # The renderer places each glyph relative to this first-glyph anchor.
    assert paragraph.x == 130
    for char in display:
        assert (
            paragraph.x + char.x0 - _formula_horizontal_geometry(display)[0] == char.x0
        )
    output = next(p for s, p in zip(draft.sstk, draft.pstk) if "Output" in s)
    assert (output.x, output.y) == (50, 648)


@pytest.mark.parametrize("font", ["CMMI10", "CMSY10"])
def test_line_start_math_operator_without_prose_is_not_a_list_item(font):
    value = converter()
    append(value, "@", 40, 700, font, {ord("@"): "\\bullet "})
    append(value, "x=y", 50, 700, "CMMI10")
    draft = value.receive_layout(value.cur_item, preview_only=True)
    assert len(draft.var) == 1
    assert draft.formula_texts == ["\\bullet x=y"]
    assert draft.pstk[draft.varp[0]].layout_class == 2
