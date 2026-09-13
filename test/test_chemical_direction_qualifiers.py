from __future__ import annotations

import pytest

from pdf2zh.converter import _release_chemical_direction_qualifiers
from test_toc_layout import converter, joined, text


def formula(word="upper", *, gap=2.5, subscripts=True, prefix="3Bi2I9"):
    value = converter()
    x = 60.0
    for char in prefix:
        script = subscripts and char.isdigit()
        x = text(value, char, x, 698 if script else 700, size=6 if script else 10)
    text(value, "(" + word, x + gap, 700)
    chars = list(value.cur_item)
    for char in chars:
        char._pdf2zh_layout_class = 1
    return chars


@pytest.mark.parametrize(
    "direction,following",
    [("upper", "left"), ("bottom", "right"), ("lower", "left"), ("top", "right")],
)
def test_direction_is_prose_while_subscripted_chemical_glyphs_stay_original(
    direction, following
):
    chars = formula(direction)
    original_prefix = chars[:6]
    source = [f"Samples Cs{{v0}} {following}, 0D dimer structure)"]
    formulas = [chars]
    prose_chars = [[]]
    _release_chemical_direction_qualifiers(source, formulas, [0], prose_chars)
    assert source == [f"Samples Cs{{v0}} ({direction} {following}, 0D dimer structure)"]
    assert formulas[0] == original_prefix
    assert joined(formulas[0]) == "3Bi2I9"
    assert joined(prose_chars[0]) == "(" + direction
    assert [char.size for char in formulas[0]] == [6, 10, 10, 6, 10, 6]


def test_closing_parenthesis_in_next_formula_remains_protected():
    chars = formula("bottom")
    closer_converter = converter()
    text(closer_converter, ").c", 300, 700)
    closer = list(closer_converter.cur_item)
    source = ["Samples Cs{v0} right, 2D layered structure{v1}, maps"]
    formulas = [chars, closer]
    _release_chemical_direction_qualifiers(source, formulas, [0, 0], [[]])
    assert source == ["Samples Cs{v0} (bottom right, 2D layered structure{v1}, maps"]
    assert formulas[1] is closer


@pytest.mark.parametrize(
    "case",
    [
        "mathematical_prefix",
        "no_scripts",
        "adjacent_parenthesis",
        "large_gap",
        "protected_figure",
        "math_font",
        "italic_word",
        "unmatched_bracket",
        "different_following_word",
        "other_word",
        "rotated_word",
    ],
)
def test_ambiguous_math_and_protected_labels_remain_untouched(case):
    chars = formula(
        "uppermost" if case == "other_word" else "upper",
        gap=0 if case == "adjacent_parenthesis" else 15 if case == "large_gap" else 2.5,
        subscripts=case != "no_scripts",
        prefix="x2" if case == "mathematical_prefix" else "3Bi2I9",
    )
    if case == "protected_figure":
        chars[-1]._pdf2zh_layout_class = 0
    if case in {"math_font", "italic_word"}:
        chars[-1].fontname = "CMI10" if case == "math_font" else "Times-Italic"
    if case == "rotated_word":
        chars[-1].matrix = (0, 1, -1, 0, chars[-1].x0, chars[-1].y0)
    following = "bound" if case == "different_following_word" else "left"
    tail = "" if case == "unmatched_bracket" else ")"
    source = [f"Samples Cs{{v0}} {following}, structure{tail}"]
    before = list(source)
    formulas = [chars]
    prose_chars = [[]]
    _release_chemical_direction_qualifiers(source, formulas, [0], prose_chars)
    assert source == before
    assert formulas[0] is chars
    assert prose_chars == [[]]


@pytest.mark.parametrize("backend", ["google", "bing", "codex"])
def test_parser_releases_spaced_direction_qualifier_for_every_backend(backend):
    value = converter()
    value.translator.name = backend
    x = text(value, "Samples of Cs", 60, 700)
    for char in "3Bi2I9":
        script = char.isdigit()
        x = text(value, char, x, 698 if script else 700, size=6 if script else 10)
    text(value, "(upper left, 0D dimer structure)", x + 2.5, 700)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    assert len(draft.sstk) == 1
    assert "(upper left," in draft.sstk[0]
    assert "upper" not in "".join(draft.formula_texts)
    assert "3Bi2I9" in draft.formula_texts
