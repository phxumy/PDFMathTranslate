from types import SimpleNamespace

import pytest

from pdf2zh.converter import _release_caption_prose_delimiters, _release_prose_slash_connectives
from test_formula_boundary_punctuation import FakeChar


def release(source, value, *, kind="figure_caption", font="Times-Roman"):
    chars = [FakeChar(c, x0=i * 5, x1=(i + 1) * 5, fontname=font,
                      layout_class=11) for i, c in enumerate(value)]
    segments, formulas = [source], [chars]
    paragraph = SimpleNamespace(region_kind=kind, layout_class=11, size=9)
    _release_caption_prose_delimiters(segments, formulas, [0], [paragraph])
    return segments[0], formulas[0], chars


def test_caption_panel_transition_is_visible_to_sentence_translation():
    text, formula, original = release(
        "FIG. 2. (a) Sensitivity (white arrow{v0} Pulse sequence.", ").(b)"
    )
    assert text == "FIG. 2. (a) Sensitivity (white arrow). (b) Pulse sequence."
    assert formula == original  # Unused formula slots retain original glyphs.


def test_caption_closing_parenthesis_leaves_variable_glyphs_unchanged():
    text, formula, original = release(
        "FIG. 2. (a) Detuning (controlled by amplitude {v0} and time.", "φlow)"
    )
    assert text.endswith("amplitude {v0}) and time.")
    assert formula == original[:-1]
    assert all(actual is expected for actual, expected in zip(formula, original))


@pytest.mark.parametrize("source,value,kind,font", [
    ("FIG. 2. (a) Value {v0} Pulse sequence.", ").(b)", "figure_caption", "Times-Roman"),
    ("FIG. 2. (a) Value (arrow{v0} Pulse sequence.", ").(d)", "figure_caption", "Times-Roman"),
    ("FIG. 2. (a) Value (arrow{v0} Pulse sequence.", ").(b)", "plain text", "Times-Roman"),
    ("FIG. 2. (a) Value (arrow{v0} Pulse sequence.", ").(b)", "figure_caption", "CMSY10"),
    ("FIG. 2. (a) Value (function {v0} and time.", "f(x)", "figure_caption", "Times-Roman"),
    ("FIG. 2. (a) Value (function {v0} and time.", "(x)", "figure_caption", "Times-Roman"),
    ("FIG. 2. (a) Value (function {v0} and time.", "x)", "figure_caption", "CMSY10"),
])
def test_mathematical_and_unproven_delimiters_are_preserved(source, value, kind, font):
    text, formula, original = release(source, value, kind=kind, font=font)
    assert text == source
    assert formula == original


def test_text_font_and_or_is_an_ordinary_prose_connective():
    chars = [FakeChar(c, fontname="Times-Roman", layout_class=4) for c in "/or"]
    formulas = [chars]
    paragraph = SimpleNamespace(region_kind="plain text", layout_class=4, size=9)
    text = ["through capacitance and{v0} Josephson junction currents"]
    _release_prose_slash_connectives(text, formulas, [0], [paragraph])
    assert text == ["through capacitance and/or Josephson junction currents"]
    assert formulas[0] == chars
    for source in ("value x{v0} and y", "and{v0} x", "and{v0}{v1}"):
        text = [source]
        _release_prose_slash_connectives(text, formulas, [0], [paragraph])
        assert text == [source]
