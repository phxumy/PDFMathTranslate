from types import SimpleNamespace

import pytest

from pdf2zh.converter import _formula_horizontal_geometry, _merge_split_radical_numbers


def glyph(text, x0, x1, y0, font="AdvP4C4E74", size=10):
    return SimpleNamespace(
        get_text=lambda: text,
        x0=x0,
        x1=x1,
        y0=y0,
        y1=y0 + size,
        size=size,
        fontname=font,
    )


def radical():
    # Content order is prefix, elevated overbar, normal-font 2, then radical.
    left = [glyph("=", 100, 108, 50), glyph("ffi", 117, 122, 58)]
    number = glyph("2", 117, 122, 50, "Times-Roman")
    right = [glyph("p", 108, 117, 58), glyph(")", 122, 126, 50)]
    return left, number, right


def test_out_of_order_radical_keeps_original_number_and_geometry():
    left, number, right = radical()
    segments = ["States {v0}2{v1} are normalized."]
    formulas = [left, right]
    lines = [[], [object()]]
    _merge_split_radical_numbers(segments, formulas, lines, [0, 0], [[number]])
    assert segments == ["States {v0} are normalized."]
    assert formulas[0] == left + [number] + right
    assert _formula_horizontal_geometry(formulas[0]) == (100, 26)
    assert lines[0] == lines[1]


@pytest.mark.parametrize(
    "change",
    [
        "adjacent",
        "baseline",
        "different_number",
        "other_paragraph",
        "protected",
        "ordinary_font",
    ],
)
def test_numbers_outside_a_confirmed_radical_stay_in_prose(change):
    left, number, right = radical()
    owners = [0, 0]
    if change == "adjacent":
        right[0].x0 = 123
    elif change == "baseline":
        left[1].y0 = right[0].y0 = 50
    elif change == "different_number":
        number.get_text = lambda: "3"
    elif change == "other_paragraph":
        owners[1] = 1
    elif change == "protected":
        right[0]._pdf2zh_layout_class = 0
    elif change == "ordinary_font":
        left[1].fontname = right[0].fontname = "Times-Roman"
    segments = ["{v0}2{v1}"]
    formulas = [left, right]
    _merge_split_radical_numbers(segments, formulas, [[], []], owners, [[number]])
    assert segments == ["{v0}2{v1}"]
    assert formulas == [left, right]
