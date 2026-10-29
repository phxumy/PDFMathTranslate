from pdf2zh.converter import (
    _release_formula_prose_suffixes,
    _merge_encoded_formula_gaps,
    _release_parenthetical_prose_articles,
    _release_inline_prose_closers,
    _collect_reference_title_italic_runs,
)
from test_cm_prose_and_region_recovery import run
from test_italic_translation import paragraph


def test_axis_name_is_released_even_when_closer_has_its_own_placeholder():
    p = paragraph(region_kind="figure_caption")
    p.layout_class = 3
    formulas = [run("Pk(y-", x=20, y=100), run(")", x=60, y=100)]
    texts = ["The quantity {v0}axis{v1} is plotted."]
    _release_formula_prose_suffixes(texts, formulas, [0, 0], [p], [[]])
    assert texts == ["The quantity {v0} (y-axis{v1} is plotted."]
    assert "".join(c.get_text() for c in formulas[0]) == "Pk"


def test_named_design_can_move_as_prose_relative_to_its_authors():
    p = paragraph(region_kind="plain text")
    texts = ["It uses the {v0}design by Smith et al.{v1}."]
    formulas = [run("XMon-", x=20, y=100, font="Times-Italic"), run("4", x=100, y=100)]
    _release_formula_prose_suffixes(texts, formulas, [0, 0], [p], [[]])
    assert texts == ["It uses the XMon-design by Smith et al.{v1}."]


def test_explicit_formula_reference_returns_to_prose_but_function_argument_does_not():
    p = paragraph(region_kind="figure_caption")
    for value, expected in [
        ("σ(Eq.3,", "Sum {v0} (Eq. 3, colorscale)."),
        ("f(x)", "Sum {v0} colorscale)."),
    ]:
        formulas = [run(value, x=20, y=100, font="Times-Roman")]
        texts = ["Sum {v0} colorscale)."]
        _release_formula_prose_suffixes(texts, formulas, [0], [p], [[]])
        assert texts == [expected]


def test_hidden_closer_and_subscript_do_not_hide_parenthetical_article():
    prefix = run("P", x=20, y=100, font="Symbol", size=10)
    prefix += run("e", x=26, y=97.5, font="Times-Italic", size=7)
    suffix = run("(a", x=prefix[-1].x1 + 5.5, y=100, font="Times-Roman", size=10)
    tail = run("T1)", x=100, y=100, font="Times-Roman", size=10)
    for c in prefix + suffix + tail:
        c._pdf2zh_layout_class = 3
    texts = ["We use {v0} proxy of {v1} as the metric."]
    formulas = [prefix + suffix, tail]
    _release_parenthetical_prose_articles(texts, formulas, [0, 0], [[]])
    assert texts == ["We use {v0} (a proxy of {v1} as the metric."]
    assert "".join(c.get_text() for c in formulas[0]) == "Pe"


def test_misencoded_math_delimiter_uses_original_glyphs_without_decoding():
    p = paragraph(region_kind="plain text")
    left = run("w=", x=20, y=100, size=10)
    gap = run("ð1", x=left[-1].x1 + 2, y=100, size=10)
    right = run("+expð2λÞÞ", x=gap[-1].x1 + 2, y=100, size=10)
    for source, merged in [
        ("The weight is {v0} ð1 {v1}.", True),
        ("The weight is {v0} and {v1}.", False),
    ]:
        texts = [source]
        formulas = [left[:], right[:]]
        _merge_encoded_formula_gaps(texts, formulas, [0, 0], [p], [gap], [[], []])
        assert (texts[0] == "The weight is {v0}.") == merged
        if merged:
            assert formulas[0] == left + gap + right


def test_quantity_qualifier_and_noun_are_not_a_formula():
    p = paragraph(region_kind="plain text")
    prefix = run("instantaneous", x=20, y=100, font="Times-Italic")
    symbol = run("T1", x=prefix[-1].x1 + 3, y=100, font="Times-Italic")
    noun = run("gain", x=symbol[-1].x1 + 3, y=100, font="Times-Italic")
    formulas = [prefix + symbol + noun]
    texts = ["We report the {v0} at each time."]
    _release_formula_prose_suffixes(texts, formulas, [0], [p], [[]])
    assert texts == ["We report the instantaneous {v0} gain at each time."]
    assert "".join(ch.get_text() for ch in formulas[0]) == "T1"


def test_quantity_phrase_across_a_source_line_keeps_only_symbol_protected():
    p = paragraph(region_kind="plain text")
    adjective = run("instantaneous", x=230, y=112, font="Times-Italic")
    symbol = run("T1", x=50, y=100, font="Times-Italic")
    noun = run("gain", x=symbol[-1].x1 + 3, y=100, font="Times-Italic")
    formulas = [adjective, symbol + noun]
    texts = ["The {v0}{v1} is measured at each time."]
    _release_formula_prose_suffixes(texts, formulas, [0, 0], [p], [[]])
    assert texts == ["The instantaneous {v1} gain is measured at each time."]
    assert formulas[1] == symbol


def test_contiguous_identifier_is_not_split_into_a_quantity_label():
    p = paragraph(region_kind="plain text")
    formulas = [run("meanT1rate", x=20, y=100, font="Times-Italic")]
    texts = ["The identifier {v0} is defined here."]
    _release_formula_prose_suffixes(texts, formulas, [0], [p], [[]])
    assert texts == ["The identifier {v0} is defined here."]


def test_prose_explanation_closer_can_move_with_its_translated_clause():
    p = paragraph(region_kind="plain text")
    formulas = [run("T1)", x=20, y=100, font="Times-Roman")]
    texts = ["We use P (a proxy of {v0} as the metric."]
    _release_inline_prose_closers(texts, formulas, [0], [p], [[]])
    assert texts == ["We use P (a proxy of {v0}) as the metric."]
    assert "".join(ch.get_text() for ch in formulas[0]) == "T1"


def test_percentage_qualifier_closes_in_prose_not_in_the_formula_token():
    p = paragraph(region_kind="plain text")
    formulas = [run(">50%)", x=20, y=100, font="Times-Roman")]
    texts = ["Most TLS ({v0} occur at the surface."]
    _release_inline_prose_closers(texts, formulas, [0], [p], [[]])
    assert texts == ["Most TLS ({v0}) occur at the surface."]
    assert "".join(ch.get_text() for ch in formulas[0]) == ">50%"


def test_math_owned_and_already_closed_parentheses_are_not_released():
    p = paragraph(region_kind="plain text")
    for source, first, last in (
        ("We evaluate {v0} at two times {v1}.", "f(", "T1)"),
        ("The note (first clause {v0} gives {v1}.", "T1)", "f(x)"),
        ("The note (first clause {v0} gives {v1}.", "T1)", "y)"),
    ):
        formulas = [
            run(first, x=20, y=100, font="Times-Roman"),
            run(last, x=100, y=100, font="Times-Roman"),
        ]
        texts = [source]
        _release_inline_prose_closers(texts, formulas, [0, 0], [p], [[]])
        assert "".join(ch.get_text() for ch in formulas[1]) == last


def test_italic_thesis_title_with_superscript_reference_label_is_translatable():
    p = paragraph(region_kind="plain text")
    p.size = 8
    formulas = [
        run("40", x=20, y=103, size=7),
        run("Fast", x=40, y=100, font="Times-Italic"),
        run("accurate state measurement", x=100, y=100, font="Times-Italic"),
    ]
    for chars in formulas:
        for char in chars:
            char._pdf2zh_layout_class = 3
    source = "{v0}Smith, D. T. {v1}, {v2}. Phd thesis, Example University (2024)."
    candidates = _collect_reference_title_italic_runs(
        formulas, [0, 0, 0], [p], [source]
    )
    assert candidates == {1: "Fast", 2: "accurate state measurement"}


def test_tight_reference_label_does_not_turn_the_venue_into_a_title():
    p = paragraph(region_kind="plain text")
    p.size = 8
    formulas = [run("Physical Review Letters", x=40, y=100, font="Times-Italic")]
    for char in formulas[0]:
        char._pdf2zh_layout_class = 3
    source = "4Wu, Y. Quantum processor performance. {v0}, 10, 20 (2024)."
    assert _collect_reference_title_italic_runs(formulas, [0], [p], [source]) == {}


def test_italic_author_abbreviation_does_not_hide_the_actual_title():
    p = paragraph(region_kind="plain text")
    p.size = 8
    for label in ("1", "1.", "{v0}"):
        formulas = [
            run("1", x=20, y=103, size=7),
            run("et al", x=40, y=100, font="Times-Italic"),
        ]
        for chars in formulas:
            for char in chars:
                char._pdf2zh_layout_class = 3
        source = f"{label}Smith, D. {{v1}}. A quantum processor with stable noise. Nature 600, 10 (2024)."
        assert (
            _collect_reference_title_italic_runs(formulas, [0, 0], [p], [source]) == {}
        )
