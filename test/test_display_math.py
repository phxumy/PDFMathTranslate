import pymupdf as fitz
import pytest
from pdfminer.layout import LTLine
from test_cm_prose_and_region_recovery import detection, run
from test_source_stroke_fidelity import extract_strokes

from pdf2zh.display_math import detect_display_math
from pdf2zh.pdfinterp import _page_linewidth


def formula():
    lhs = run("m=m-m=", x=20, y=100, size=10, font="Times-Italic")
    top = run("L", x=58, y=110, size=10, font="Times-Italic")
    bottom = run("V", x=58, y=90, size=10, font="Times-Italic")
    ones = run("1", x=80, y=110, size=10) + run("1", x=105, y=110, size=10)
    denominators = run("t", x=80, y=90, size=10, font="Times-Italic") + run(
        "t", x=105, y=90, size=10, font="Times-Italic"
    )
    delimiters = (
        run("(", x=72, y=100, size=10)
        + run("-", x=94, y=100, size=10)
        + run(")", x=115, y=100, size=10)
    )
    lines = [LTLine(0.46, (x, 103), (x + 10, 103)) for x in (55, 77, 102)]
    # Real equation editors often emit symbols and subscripts before roman
    # numerator digits. Preserve the entire display independent of stream order.
    chars = lhs + top + bottom + denominators + delimiters + ones
    return chars, lines, ones


def test_display_fraction_includes_late_roman_numerator_digits_and_all_rules():
    chars, lines, ones = formula()
    definitions = run("the effective mobility of the analyte", y=65, size=10)
    result = detect_display_math(chars + definitions, lines)
    assert result.preserved_char_ids == {id(c) for c in chars}
    assert {id(c) for c in ones} <= result.preserved_char_ids
    assert result.line_ids == {id(line) for line in lines}
    assert len(result.regions) == 1
    assert result.annotation_groups == []


def test_filled_fraction_keeps_conditions_together_and_math_operators_fixed():
    label = run("Inhibition", x=30, y=100, size=10, font="Helvetica-BoldOblique")
    prefix = run("%", x=20, y=100, size=10) + run("=[1-", x=80, y=100, size=10)
    numerator = run("% control activity NADPH", x=110, y=110, size=7)
    denominator = run("% control activity No NADPH", x=108, y=94, size=7)
    suffix = run("]x100", x=205, y=100, size=10)
    chars = label + prefix + numerator + suffix + denominator
    before = [(c.x0, c.y0, c.x1, c.y1) for c in chars]
    result = detect_display_math(chars, filled_rules=[(105, 103, 200, 104)])
    labels = [
        "".join(c.get_text() for c in group) for group in result.annotation_groups
    ]
    assert labels == ["controlactivityNADPH", "Inhibition", "controlactivityNoNADPH"]
    annotation_ids = {id(c) for group in result.annotation_groups for c in group}
    assert annotation_ids | result.preserved_char_ids == {id(c) for c in chars}
    assert not annotation_ids & result.preserved_char_ids
    assert {id(c) for c in prefix + suffix} <= result.preserved_char_ids
    assert (
        "".join(c.get_text() for c in numerator if id(c) in result.preserved_char_ids)
        == "%"
    )
    assert before == [(c.x0, c.y0, c.x1, c.y1) for c in chars]


@pytest.mark.parametrize("font", ["Times-Italic", "Times-Roman"])
@pytest.mark.parametrize("last_glyph_first", [False, True])
def test_single_word_fraction_identifier_retains_source_glyphs(font, last_glyph_first):
    prefix = run("CL=", x=85, y=100, size=10, font="Times-Italic")
    numerator = run("1000", x=145, y=110, size=10)
    half = run("T1/2*", x=110, y=90, size=10)
    identifier = run("proteinconc", x=140, y=90, size=10, font=font)
    # The real S8 stream draws the final 'c' before the other ten letters.
    # A positive annotation class incorrectly reanchors and shrinks that run.
    emitted_identifier = (
        identifier[-1:] + identifier[:-1] if last_glyph_first else identifier
    )
    chars = prefix + numerator + half + emitted_identifier
    before = [(id(c), c.x0, c.y0, c.x1, c.y1, c.size) for c in chars]
    line = LTLine(0.47, (105, 103), (210, 103))
    result = detect_display_math(chars, [line])
    assert result.regions
    assert result.annotation_groups == []
    assert result.preserved_char_ids == {id(c) for c in chars}
    assert result.line_ids == {id(line)}
    assert before == [(id(c), c.x0, c.y0, c.x1, c.y1, c.size) for c in chars]


@pytest.mark.parametrize(
    "text,gap,size,baseline,font,separator",
    [
        ("N", 2.5, 7, 110, "CMR8", ""),
        ("X", 2.5, 7, 110, "CMR8", ""),
        ("ATP", 2.5, 7, 110, "CMR8", ""),
        ("NADPH", 9, 7, 110, "CMR8", ""),
        ("NADPH", 2.5, 5, 110, "CMR8", ""),
        ("NADPH", 2.5, 7, 112, "CMR8", ""),
        ("NADPH", 2.5, 7, 110, "CMMI8", ""),
        ("NADPH", 5, 7, 110, "CMR8", "+"),
    ],
)
def test_fraction_abbreviation_requires_adjacent_matching_prose(
    text, gap, size, baseline, font, separator
):
    prefix = run("x=", x=90, y=100, size=10)
    numerator = run("% control activity", x=110, y=110, size=7)
    abbreviation = run(text, x=numerator[-1].x1 + gap, y=baseline, size=size, font=font)
    operator = run(separator, x=numerator[-1].x1 + 0.5, y=110, size=7)
    denominator = run("% control activity", x=110, y=94, size=7)
    result = detect_display_math(
        prefix + numerator + operator + abbreviation + denominator,
        filled_rules=[(105, 103, 220, 104)],
    )
    assert result.regions
    assert {id(c) for c in abbreviation + operator} <= result.preserved_char_ids


def test_plain_underlines_table_separators_and_inline_prose_are_not_displays():
    underlined = run("Effective mobility", x=20, y=110, size=10)
    line = LTLine(0.4, (20, 107), (110, 107))
    assert not detect_display_math(underlined, [line]).regions
    table = run("123", x=40, y=110, size=10) + run("456", x=40, y=90, size=10)
    assert not detect_display_math(table, [LTLine(0.4, (20, 103), (110, 103))]).regions
    chars, lines, _ = formula()
    assert not detect_display_math(
        chars, lines, detections=[detection("table", (0, 70, 140, 130))]
    ).regions
    inline = chars + run("is the measured value", x=125, y=100, size=10)
    assert not detect_display_math(inline, lines).regions
    assert not detect_display_math(chars).regions


@pytest.mark.parametrize(
    "ctm,start,end,expected",
    [
        ((0.24, 0, 0, 0.24, 0, 0), (0, 0), (100, 0), 0.46464),
        ((2, 0, 0, 3, 0, 0), (0, 0), (100, 0), 5.808),
        ((0, 2, -3, 0, 0, 0), (0, 0), (0, 100), 3.872),
        ((2, 0, 1, 3, 0, 0), (0, 0), (100, 0), 5.808),
    ],
)
def test_linewidth_uses_transformed_normal(ctm, start, end, expected):
    assert _page_linewidth(ctm, start, end, 1.936) == pytest.approx(expected)


def test_real_interpreter_records_filled_rule_without_repainting_and_scales_stroke():
    with fitz.open() as document:
        page = document.new_page(width=240, height=200)
        page.draw_line((30, 40), (50, 40))
        document.update_stream(
            page.get_contents()[0],
            b"q .24 0 0 .24 0 0 cm 1.936 w 100 400 m 200 400 l S Q "
            b"q 0 g 30 80 120 1.32 re f* Q",
        )
        converter = extract_strokes(document)
        assert converter.extracted[0][0].linewidth == pytest.approx(0.46464)
        assert converter.cur_item._pdf2zh_filled_rules == [(30, 80, 150, 81.32)]
        drawings = document[0].get_drawings()
        assert len(drawings) == 1
        assert drawings[0]["type"] == "f"
        assert tuple(drawings[0]["rect"]) == pytest.approx((30, 118.68, 150, 120))


@pytest.mark.parametrize(
    "path",
    [
        b"1 g 30 80 120 1 re f",  # white background
        b"0 g 30 80 120 8 re f",  # thick filled shape
        b"0 g 30 80 m 150 81 l 150 82 l 30 81 l h f",  # slanted quadrilateral
        b"0 g 30 80 120 1 re 30 90 120 1 re f",  # compound path
    ],
)
def test_filled_rule_records_reject_unrelated_paths(path):
    with fitz.open() as document:
        page = document.new_page(width=240, height=200)
        page.draw_line((30, 40), (50, 40))
        document.update_stream(page.get_contents()[0], path)
        converter = extract_strokes(document)
        assert not getattr(converter.cur_item, "_pdf2zh_filled_rules", [])
