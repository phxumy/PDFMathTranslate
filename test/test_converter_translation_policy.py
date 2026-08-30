from __future__ import annotations

import unittest

from pdf2zh.converter import Paragraph, TranslateConverter
from pdf2zh.translation_policy import DocumentTranslationPolicy


class FakeCodexTranslator:
    name = "codex"
    lang_out = "zh-cn"

    def __init__(self) -> None:
        self.reference_contexts: list[str] = []

    def translate_batch(self, texts: list[str]) -> list[str]:
        return [
            text.replace("Department of Physics", "物理系").replace(
                "Institute for Quantum Science", "量子科学研究所"
            )
            for text in texts
        ]

    def translate_reference_entries(
        self,
        entries: list[str],
        cache_contexts: list[str] | None = None,
    ) -> list[str]:
        self.reference_contexts.extend(cache_contexts or [])
        return [
            entry.replace("First work title", "第一项作品题名").replace(
                "Second work title", "第二项作品题名"
            )
            for entry in entries
        ]


class FakeOtherTranslator:
    name = "google"
    lang_out = "zh-cn"

    def translate(self, text: str) -> str:
        return "普通翻译"


def paragraph() -> Paragraph:
    return Paragraph(700.0, 50.0, 50.0, 550.0, 680.0, 710.0, 10.0, False)


def converter_with(translator) -> TranslateConverter:
    converter = TranslateConverter.__new__(TranslateConverter)
    converter.translator = translator
    converter.thread = 1
    converter.translation_policy = DocumentTranslationPolicy()
    return converter


class ConverterPolicyIntegrationTests(unittest.TestCase):
    def test_authors_affiliations_and_references_follow_separate_roles(self) -> None:
        translator = FakeCodexTranslator()
        converter = converter_with(translator)
        byline_and_affiliations = (
            "Zijun Chen, A. Megrant, J. Kelly "
            "{v0}Department of Physics, Example University "
            "{v1}Institute for Quantum Science"
        )
        references = (
            "[1] A. Alpha, First work title. Nature 1, 10 (2020). "
            "[2] B. Beta, Second work title. Science 2, 20 (2021)."
        )

        result = converter._translate_planned_segments(
            [byline_and_affiliations, references],
            [paragraph(), paragraph()],
            formula_texts=["1", "2"],
            page_width=612.0,
        )

        self.assertTrue(result[0].startswith("Zijun Chen, A. Megrant, J. Kelly\n"))
        self.assertIn("{v0}物理系", result[0])
        self.assertIn("\n{v1}量子科学研究所", result[0])
        self.assertIn("第一项作品题名", result[1])
        self.assertIn("\n[2]", result[1])
        self.assertEqual(translator.reference_contexts, ["", ""])

    def test_non_codex_backend_preserves_complete_reference_entries(self) -> None:
        converter = converter_with(FakeOtherTranslator())
        references = (
            "[1] A. Alpha, First work title. Nature 1, 10 (2020). "
            "[2] B. Beta, Second work title. Science 2, 20 (2021)."
        )

        result = converter._translate_planned_segments(
            [references],
            [paragraph()],
            formula_texts=[],
            page_width=612.0,
        )[0]

        self.assertEqual(result.replace("\n", " "), references)
        self.assertIn("\n[2]", result)

    def test_formula_value_is_forwarded_as_reference_cache_context(self) -> None:
        translator = FakeCodexTranslator()
        converter = converter_with(translator)

        converter._translate_reference_segments(
            ["{v0} A. Smith, First work title. Nature 1 (2020)."],
            ["{v0}=57"],
        )

        self.assertEqual(translator.reference_contexts, ["{v0}=57"])


if __name__ == "__main__":
    unittest.main()
