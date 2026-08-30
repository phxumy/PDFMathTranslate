from __future__ import annotations

import re
import unittest

from pdf2zh.converter import (
    ITALIC_SHEAR,
    Paragraph,
    TranslateConverter,
    _gen_target_text_op,
    _has_inline_prose_context,
    _is_high_confidence_prose_italic,
    _tag_translatable_italic_formulas,
)
from pdf2zh.translation_policy import DocumentTranslationPolicy
from pdf2zh.translator import CodexTranslator


class FakeItalicChar:
    def __init__(
        self,
        text: str,
        x0: float,
        x1: float,
        *,
        fontname: str = "TimesNewRoman-Italic",
        size: float = 10.0,
        layout_class: int = 1,
        baseline: float = 100.0,
    ) -> None:
        self._text = text
        self.x0 = x0
        self.x1 = x1
        self.y0 = baseline
        self.size = size
        self.fontname = fontname
        self.matrix = (1.0, 0.0, 0.0, 1.0, x0, baseline)
        self._pdf2zh_layout_class = layout_class

    def get_text(self) -> str:
        return self._text


def fake_run(text: str, *, fontname: str = "TimesNewRoman-Italic"):
    chars = []
    x = 0.0
    for index, char in enumerate(text.replace(" ", "")):
        if text == "in situ" and index == 2:
            x += 2.5
        chars.append(FakeItalicChar(char, x, x + 4.0, fontname=fontname))
        x += 4.0
    return chars


class FakeStyledTranslator:
    name = "codex"
    lang_out = "zh-cn"

    def __init__(self, fail_styled: bool = False) -> None:
        self.fail_styled = fail_styled

    def translate_batch(self, texts: list[str]) -> list[str]:
        return list(texts)

    def translate_styled_batch(
        self,
        texts: list[str],
        formula_contexts: list[dict[str, str]] | None = None,
    ):
        if self.fail_styled:
            return [None] * len(texts)
        return [
            text.replace("frequency tuned ", "频率可")
            .replace("in situ", "原位")
            .replace(" by flux", "通过磁通调谐")
            for text in texts
        ]


def paragraph() -> Paragraph:
    return Paragraph(700.0, 50.0, 50.0, 550.0, 680.0, 710.0, 10.0, False)


def converter_with(translator) -> TranslateConverter:
    converter = TranslateConverter.__new__(TranslateConverter)
    converter.translator = translator
    converter.thread = 1
    converter.translation_policy = DocumentTranslationPolicy()
    return converter


class ItalicClassifierTests(unittest.TestCase):
    def test_inline_natural_language_is_reconstructed_and_released(self) -> None:
        self.assertEqual(
            _is_high_confidence_prose_italic(fake_run("in situ"), 10.0),
            "in situ",
        )
        self.assertEqual(
            _is_high_confidence_prose_italic(fake_run("direct"), 10.0),
            "direct",
        )
        self.assertTrue(_has_inline_prose_context("tuned {v12} by", 12))

    def test_math_names_products_and_protected_regions_are_not_released(self) -> None:
        self.assertIsNone(_is_high_confidence_prose_italic(fake_run("sin"), 10.0))
        self.assertIsNone(_is_high_confidence_prose_italic(fake_run("et al."), 10.0))
        self.assertIsNone(_is_high_confidence_prose_italic(fake_run("iSWAP"), 10.0))
        self.assertIsNone(
            _is_high_confidence_prose_italic(
                fake_run("direct", fontname="RMTMI"),
                10.0,
            )
        )
        protected = fake_run("direct")
        for char in protected:
            char._pdf2zh_layout_class = 0
        self.assertIsNone(_is_high_confidence_prose_italic(protected, 10.0))
        self.assertFalse(_has_inline_prose_context("g={v3}+x", 3))


class ItalicMarkupTests(unittest.TestCase):
    def test_only_selected_formula_placeholder_is_unwrapped(self) -> None:
        tagged, ids = _tag_translatable_italic_formulas(
            "frequency tuned {v12} by flux while {v13} is fixed",
            {12: "in situ"},
        )
        self.assertEqual(ids, (12,))
        self.assertIn(
            "[[PDF2ZH_ITALIC_12_BEGIN]]in situ"
            "[[PDF2ZH_ITALIC_12_END]]",
            tagged,
        )
        self.assertIn("{v13}", tagged)

    def test_codex_validator_rejects_damaged_style_or_formula_markers(self) -> None:
        source = (
            "tuned [[PDF2ZH_ITALIC_12_BEGIN]]in situ"
            "[[PDF2ZH_ITALIC_12_END]] by {v1}"
        )
        valid = (
            "通过[[PDF2ZH_ITALIC_12_BEGIN]]原位"
            "[[PDF2ZH_ITALIC_12_END]]方式调谐{v1}"
        )
        self.assertTrue(CodexTranslator._validate_styled_translation(source, valid))
        self.assertFalse(
            CodexTranslator._validate_styled_translation(
                source,
                valid.replace("[[PDF2ZH_ITALIC_12_END]]", ""),
            )
        )
        self.assertFalse(
            CodexTranslator._validate_styled_translation(
                source,
                valid.replace("{v1}", "{v2}"),
            )
        )
        self.assertFalse(
            CodexTranslator._validate_styled_translation(
                source,
                valid.replace("{v1}", "{{v1}}"),
            )
        )
        self.assertFalse(
            CodexTranslator._validate_styled_translation(
                source,
                valid.replace("原位", "原位{v1}").replace("调谐{v1}", "调谐"),
            )
        )

    def test_styled_translation_and_fail_closed_fallback(self) -> None:
        source = "frequency tuned {v12} by flux"
        converted = converter_with(FakeStyledTranslator())._translate_planned_segments(
            [source],
            [paragraph()],
            ["insitu"],
            612.0,
            italic_candidates={12: "in situ"},
        )[0]
        self.assertIn("[[PDF2ZH_ITALIC_12_BEGIN]]原位", converted)

        fallback = converter_with(
            FakeStyledTranslator(fail_styled=True)
        )._translate_planned_segments(
            [source],
            [paragraph()],
            ["insitu"],
            612.0,
            italic_candidates={12: "in situ"},
        )[0]
        self.assertEqual(fallback, source)

    def test_target_text_operator_uses_and_resets_synthetic_italic(self) -> None:
        italic = _gen_target_text_op("noto", 10.0, 20.0, 30.0, "4e2d", True)
        regular = _gen_target_text_op("noto", 10.0, 40.0, 30.0, "6587", False)
        self.assertRegex(
            italic,
            rf"1 0 {re.escape(f'{ITALIC_SHEAR:f}')} 1 20\.000000 30\.000000 Tm",
        )
        self.assertIn("1 0 0 1 40.000000 30.000000 Tm", regular)


if __name__ == "__main__":
    unittest.main()
