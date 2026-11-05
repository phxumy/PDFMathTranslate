from unittest.mock import Mock
from types import SimpleNamespace

from pdf2zh.converter import (
    Paragraph,
    TranslateConverter,
    _release_inline_prose_closers,
)
from pdf2zh.translation_policy import DocumentTranslationPolicy
from test_cm_prose_and_region_recovery import run
from pdf2zh.translation_quality import is_assay_measurement_label


def test_single_letter_table_footnote_is_preserved_in_layout_only():
    converter = TranslateConverter.__new__(TranslateConverter)
    converter.translator = SimpleNamespace(name="codex", lang_in="en", lang_out="zh")
    converter.translation_policy = DocumentTranslationPolicy()
    converter.scan_backgrounds = {}
    paragraph = Paragraph(
        210,
        280,
        280,
        282,
        210,
        214.25,
        4.25,
        False,
        region_kind="table_cell",
        layout_class=3,
    )
    converter._translate_text_segments = Mock(return_value=[])
    assert converter._translate_planned_segments(["c"], [paragraph], [], 612, 792) == [
        "c"
    ]
    assert converter._translate_text_segments.call_args.args[0] == []


def test_numeric_qualifier_returns_unmatched_prose_closer_without_unicode_guessing():
    source = ["A change in lipophilicity ({v0} was observed."]
    chars = run("DLogDP+1.0)", font="Times-Roman", size=10)
    formulas = [chars]
    p = SimpleNamespace(region_kind="plain text")
    text_chars = [[]]
    _release_inline_prose_closers(source, formulas, [0], [p], text_chars)
    assert source == ["A change in lipophilicity ({v0}) was observed."]
    assert "".join(c.get_text() for c in formulas[0]) == "DLogDP+1.0"
    assert text_chars[0] == [chars[-1]]


def test_function_parentheses_remain_math_owned():
    for before in ("sin (", "f("):
        source = [before + "{v0}"]
        chars = run("x+1)", font="Times-Roman", size=10)
        formulas = [chars]
        _release_inline_prose_closers(
            source, formulas, [0], [SimpleNamespace(region_kind="plain text")], [[]]
        )
        assert formulas[0] == chars
        assert source == [before + "{v0}"]


def test_logd_qualifier_closer_is_prose_even_with_visible_variable_prefix():
    source = ["compound 40 (cLogD {v0} was tested."]
    chars = run("=-3.6)", font="Times-Roman", size=10)
    formulas = [chars]
    _release_inline_prose_closers(
        source, formulas, [0], [SimpleNamespace(region_kind="plain text")], [[]]
    )
    assert source == ["compound 40 (cLogD {v0}) was tested."]


def test_compact_hpbmc_assay_header_stays_unexpanded():
    assert is_assay_measurement_label("hPBMC IC")
    assert not is_assay_measurement_label("hPBMC inhibition increased")
