from types import SimpleNamespace

import pytest

from pdf2zh.converter import Paragraph, _release_formula_case_explanations


def setup():
    def ch(text, x0, x1, font="CMR10"):
        return SimpleNamespace(
            get_text=lambda: text,
            x0=x0,
            x1=x1,
            y0=100,
            size=10,
            fontname=font,
            matrix=(1, 0, 0, 1, x0, 100),
            _pdf2zh_layout_class=3,
        )

    prefix = [ch("h(x)", 100, 120, "CMMI10")]
    suffix = [ch("(", 124, 128), ch("i", 128, 131), ch("n", 131, 136)]
    p = Paragraph(
        100,
        60,
        60,
        430,
        80,
        110,
        10,
        True,
        page_id=22,
        layout_class=3,
        region_kind="plain text",
    )
    return prefix, suffix, p


def test_case_prose_opener_leaves_mathematical_formula_unchanged():
    prefix, suffix, p = setup()
    source, formulas, text = ["Take {v0} the unbounded case)."], [prefix + suffix], [[]]
    _release_formula_case_explanations(source, formulas, [0], [p], text)
    assert source == ["Take {v0} (in the unbounded case)."]
    assert formulas == [prefix]
    assert text == [suffix]


def test_case_closing_parenthesis_can_be_an_independent_text_font_island():
    prefix, suffix, p = setup()
    closer = SimpleNamespace(**vars(suffix[0]))
    closer.get_text = lambda: ")"
    source, formulas, text = (
        ["Take {v0} the bounded case{v1}."],
        [prefix + suffix, [closer]],
        [[]],
    )
    _release_formula_case_explanations(source, formulas, [0, 0], [p], text)
    assert source == ["Take {v0} (in the bounded case)."]
    assert formulas[0] == prefix


@pytest.mark.parametrize(
    "change",
    [
        "argument",
        "math_font",
        "code_font",
        "figure",
        "far",
        "script",
        "no_case",
        "rotated",
    ],
)
def test_operator_arguments_and_protected_glyphs_stay_opaque(change):
    prefix, suffix, p = setup()
    source = ["Take {v0} the bounded case)."]
    if change == "argument":
        suffix[0].x0 = prefix[0].x1
    elif change == "math_font":
        suffix[1].fontname = "CMMI10"
    elif change == "code_font":
        suffix[1].fontname = "CMTT10"
    elif change == "figure":
        p.layout_class = 0
    elif change == "far":
        suffix[0].x0 += 20
    elif change == "script":
        suffix[1].size = 7
    elif change == "no_case":
        source = ["Take {v0} the value)."]
    elif change == "rotated":
        suffix[0].matrix = (0, 1, -1, 0, 124, 100)
    before = source.copy()
    formulas = [prefix + suffix]
    _release_formula_case_explanations(source, formulas, [0], [p], [[]])
    assert source == before
    assert formulas == [prefix + suffix]
