from types import SimpleNamespace

import pytest

from pdf2zh.converter import (
    _release_tex_accented_prose_words,
    _release_prose_slash_connectives,
    _release_standalone_prose_periods,
    _restore_drawn_uri_underscores,
)


def char(value, x, y=100, font="CMR10", size=10):
    return SimpleNamespace(
        get_text=lambda: value, x0=x, x1=x + 5, y0=y - 2, y1=y + 8,
        size=size, fontname=font, matrix=(1, 0, 0, 1, x, y),
        _pdf2zh_layout_class=2,
    )


def paragraph():
    return SimpleNamespace(
        layout_class=2, region_kind="plain text", size=10,
        x0=0, x1=500, y0=95, y1=110,
    )


@pytest.mark.parametrize("prefix,accent,tail,expected", [
    ("H", '\\"', "older", "Hölder"),
    ("Cand", "\\`", "es", "Candès"),
    ("G", "\\\"", "odel", "Gödel"),
])
def test_accented_word_becomes_one_complete_source_token(prefix, accent, tail, expected):
    prose = [char(v, i * 5) for i, v in enumerate(prefix)]
    x = len(prefix) * 5
    formula = [char(accent, x)] + [char(v, x + i * 5) for i, v in enumerate(tail)]
    segments = ["The " + prefix + "{v0} dual is defined."]
    _release_tex_accented_prose_words(segments, [formula], [0], [paragraph()], [prose])
    assert segments == ["The " + expected + " dual is defined."]
    assert formula[0].get_text() == accent


@pytest.mark.parametrize("valid", [True, False])
def test_overprinted_dotless_i_is_decoded_from_the_actual_source_glyph(valid):
    # The PDF has one acute-accent glyph and one {\i} glyph, not five letters.
    prose = [char("D", 0), char(r"{\i}", 5), char("a", 10), char("z", 15)]
    formula = [char("\\'", 4.4)]
    source = r"M. D{v0}{\i}az, Numerical analysis."
    if not valid:
        prose[1].x0 += 4
    segments = [source]
    _release_tex_accented_prose_words(segments, [formula], [0], [paragraph()], [prose])
    assert segments == (["M. Díaz, Numerical analysis."] if valid else [source])


@pytest.mark.parametrize("change", [
    "math_font", "code_font", "protected", "small", "rotated", "not_overprinted",
    "prefix_gap", "tail_gap", "no_prefix", "variable", "figure", "different_font",
    "lowercase_variables",
])
def test_accent_recovery_keeps_mathematical_or_unproven_runs(change):
    prose = [char("H", 0)]
    formula = [char('\\"', 5)] + [char(v, 5 + i * 5) for i, v in enumerate("older")]
    p = paragraph()
    source = "The H{v0} dual is defined."
    if change in {"math_font", "code_font", "different_font"}:
        formula[1].fontname = {"math_font": "CMMI10", "code_font": "CMTT10", "different_font": "CMR9"}[change]
    elif change == "protected":
        formula[0]._pdf2zh_layout_class = 0
    elif change == "small":
        formula[1].size = 6
    elif change == "rotated":
        formula[1].matrix = (0, 1, -1, 0, 5, 100)
    elif change == "not_overprinted":
        formula[0].x0 -= 4
    elif change == "prefix_gap":
        prose[0].x1 -= 4
    elif change == "tail_gap":
        formula[2].x0 += 4
    elif change == "no_prefix":
        source = "The {v0} dual is defined."
    elif change == "variable":
        formula = formula[:2]
    elif change == "figure":
        p.region_kind = "figure"
    elif change == "lowercase_variables":
        source = "The h{v0} term is defined."
        prose[0].get_text = lambda: "h"
    segments = [source]
    _release_tex_accented_prose_words(segments, [formula], [0], [p], [prose])
    assert segments == [source]


def uri_case():
    uri = "https://example.test/one_two_three"
    chars, lines = [], []
    x = 0
    for value in uri:
        if value == "_":
            lines.append(SimpleNamespace(pts=[(x, 100), (x + 3, 100)]))
            x += 3
        else:
            chars.append(char(value, x))
            x += 5
    return uri, chars, lines, (0, 96, x, 109)


def test_vector_uri_underscores_require_source_link_and_are_removed_after_recovery():
    uri, chars, lines, bounds = uri_case()
    unrelated = SimpleNamespace(pts=[(0, 98), (400, 98)])
    lines.append(unrelated)
    segments = ["See " + uri.replace("_", " ") + " for implementation."]
    _restore_drawn_uri_underscores(segments, [paragraph()], [chars], lines, [(uri, bounds)])
    assert segments == ["See " + uri + " for implementation."]
    assert lines == [unrelated]


@pytest.mark.parametrize("change", [
    "missing_line", "wrong_destination", "wrong_link_rect", "figure", "wrapped",
    "ambiguous_line", "duplicate_text", "nonhorizontal", "different_glyph", "longer_uri",
])
def test_uri_recovery_does_not_guess_source_or_destination(change):
    uri, chars, lines, bounds = uri_case()
    p = paragraph()
    source = "See " + uri.replace("_", " ") + " for implementation."
    if change == "missing_line":
        lines.pop()
    elif change == "wrong_destination":
        uri = uri.replace("one", "other")
    elif change == "wrong_link_rect":
        bounds = (300, 96, 490, 109)
    elif change == "figure":
        p.region_kind = "figure"
    elif change == "wrapped":
        chars[-1].matrix = (1, 0, 0, 1, 0, 80)
        chars[-1].y0 -= 20
        chars[-1].y1 -= 20
    elif change == "ambiguous_line":
        lines.append(SimpleNamespace(pts=lines[0].pts))
    elif change == "duplicate_text":
        source += source
    elif change == "nonhorizontal":
        chars[-1].matrix = (0, 1, -1, 0, 0, 100)
    elif change == "different_glyph":
        chars[-1].get_text = lambda: "X"
    elif change == "longer_uri":
        source = source.replace("three for", "three/extra for")
    segments = [source]
    before = list(lines)
    _restore_drawn_uri_underscores(segments, [p], [chars], lines, [(uri, bounds)])
    assert segments == [source]
    assert lines == before


@pytest.mark.parametrize("change", [None, "math_letter", "code_letter", "far_gap", "other_line", "protected", "no_and"])
def test_chow_math_font_slash_in_and_or_requires_touching_text_letters(change):
    # Measured glyph positions, fonts and size from Chow p4; its slash comes
    # from RMTMI while the two ordinary letters come from Times-Roman.
    chars = [
        char("/", 192.678, font="RMTMI", size=11.955),
        char("o", 198.297, font="Times-Roman", size=11.955),
        char("r", 204.2745, font="Times-Roman", size=11.955),
    ]
    for c, right in zip(chars, [198.29685, 204.2745, 208.25552]):
        c.x1 = right
    p = paragraph()
    p.size = 11.955
    source = "through capacitance and{v0} Josephson junction critical currents"
    if change == "math_letter":
        chars[1].fontname = "CMMI10"
    elif change == "code_letter":
        chars[1].fontname = "CMTT10"
    elif change == "far_gap":
        chars[1].x0 += 5
    elif change == "other_line":
        chars[1].y0 -= 15
    elif change == "protected":
        chars[0]._pdf2zh_layout_class = 0
    elif change == "no_and":
        source = "the value x{v0} Josephson junction critical currents"
    segments = [source]
    formulas = [chars]
    _release_prose_slash_connectives(segments, formulas, [0], [p])
    assert segments == ([source.replace("and{v0}", "and/or")] if change is None else [source])
    assert formulas[0] == chars


@pytest.mark.parametrize("left,right", [
    ("The map follows subsection 4.1", " The number of observations is fixed."),
    ("The threshold estimate is described in [17]", " See the implementation details."),
    ("The rate is predicted by Theorem 6.6 and Proposition 6.9", ""),
])
def test_math_caption_text_font_period_is_released_before_translation(left, right):
    period = char(".", 100, font="CMTI8", size=8)
    previous = char(left[-1], 95, font="CMR8", size=8)
    p = paragraph()
    p.size, p.region_kind = 8, "figure_caption"
    source = left + "{v0}" + right
    segments, formulas = [source], [[period]]
    text_chars = [previous]
    if left.endswith("]"):
        preceding_digit = char("7", 90, font="CMR8", size=8)
        preceding_digit.x1 = 97.7
        text_chars.insert(0, preceding_digit)
    _release_standalone_prose_periods(segments, formulas, [0], [p], [text_chars])
    assert segments == [left + "." + right]
    assert formulas == [[period]]


@pytest.mark.parametrize("change", [
    "decimal", "math_font", "math_neighbor", "code_font", "protected", "small",
    "rotated", "different_line", "gap", "abbreviation", "math_fragment", "figure",
])
def test_unproven_periods_and_decimal_points_stay_in_original_glyph_islands(change):
    period = char(".", 100, font="CMTI8", size=8)
    previous = char("1", 95, font="CMR8", size=8)
    p = paragraph()
    p.size = 8
    source = "The map follows subsection 4.1{v0} The next statement follows."
    if change == "decimal":
        source = "The value is 4{v0}1 times larger."
    elif change == "math_font":
        period.fontname = "CMMI8"
    elif change == "math_neighbor":
        previous.fontname = "CMMI8"
    elif change == "code_font":
        period.fontname = "CMTT8"
    elif change == "protected":
        period._pdf2zh_layout_class = 0
    elif change == "small":
        period.size = 5
    elif change == "rotated":
        period.matrix = (0, 1, -1, 0, 100, 100)
    elif change == "different_line":
        previous.y0 -= 15
    elif change == "gap":
        previous.x1 -= 8
    elif change == "abbreviation":
        source = "The description follows, e.g{v0} The next statement follows."
    elif change == "math_fragment":
        source = "x{v0}"
    elif change == "figure":
        p.region_kind = "figure"
    segments = [source]
    _release_standalone_prose_periods(segments, [[period]], [0], [p], [[previous]])
    assert segments == [source]
