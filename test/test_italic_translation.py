from __future__ import annotations

import re
import unittest

from pdf2zh.converter import (
    ITALIC_SHEAR,
    Paragraph,
    TranslateConverter,
    _collect_translatable_italic_runs,
    _collect_reference_title_italic_runs,
    _gen_target_text_op,
    _has_inline_prose_context,
    _is_high_confidence_prose_italic,
    _split_formula_prose_boundaries,
    _split_punctuation_only_formula_run,
    _split_trailing_prose_openers,
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
    for char in text:
        if char.isspace():
            x += 2.5
            continue
        chars.append(FakeItalicChar(char, x, x + 4.0, fontname=fontname))
        x += 4.0
    return chars


def fake_mixed_run(parts: list[tuple[str, str]]):
    chars = []
    x = 0.0
    for text, fontname in parts:
        for char in text:
            if char.isspace():
                x += 2.5
                continue
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


def paragraph(*, region_kind: str = "") -> Paragraph:
    return Paragraph(
        700.0,
        50.0,
        50.0,
        550.0,
        680.0,
        710.0,
        10.0,
        False,
        region_kind=region_kind,
    )


def converter_with(translator) -> TranslateConverter:
    converter = TranslateConverter.__new__(TranslateConverter)
    converter.translator = translator
    converter.thread = 1
    converter.translation_policy = DocumentTranslationPolicy()
    return converter


class ItalicClassifierTests(unittest.TestCase):
    def test_theorem_prose_is_split_from_embedded_math_atoms(self) -> None:
        formulas = [
            fake_mixed_run(
                [
                    ("Let ", "TimesNewRoman-Italic"),
                    ("x=(x1,x2)", "CMMI10"),
                    (". Suppose that a function", "TimesNewRoman-Italic"),
                ]
            ),
            fake_mixed_run(
                [
                    ("f(x)", "CMMI10"),
                    (" admits a representation", "TimesNewRoman-Italic"),
                ]
            ),
            fake_mixed_run(
                [
                    ("as in Eq. ", "TimesNewRoman-Italic"),
                    ("(2.7)", "CMR10"),
                    (", where each one of the ", "TimesNewRoman-Italic"),
                    ("Φ", "CMR10"),
                    ("l,i,j", "CMMI10"),
                    (" are ", "TimesNewRoman-Italic"),
                    ("(k+1)", "CMR10"),
                    (
                        "-times continuously differentiable. such that for any",
                        "TimesNewRoman-Italic",
                    ),
                ]
            ),
            fake_mixed_run(
                [
                    ("≤m≤k", "CMSY10"),
                    (", we have the bound", "TimesNewRoman-Italic"),
                ]
            ),
            fake_run("Proof."),
        ]
        segments = [
            "Theorem 2.1 (Approximation theory). {v0} {v1}",
            "{v2}",
            "0 {v3}",
            "{v4}",
        ]
        formula_paragraphs = [0, 0, 1, 2, 3]
        paragraphs = [paragraph(region_kind="plain text") for _ in range(4)]

        _split_formula_prose_boundaries(
            segments,
            formulas,
            [[], [], [], [], []],
            [0.0, 0.0, 0.0, 0.0, 0.0],
            formula_paragraphs,
            paragraphs,
        )
        self.assertEqual("".join(char.get_text() for char in formulas[0]), "x=(x1,x2)")
        self.assertEqual("".join(char.get_text() for char in formulas[1]), "f(x)")
        released = " ".join(segments)
        self.assertIn("Let {v0}. Suppose that a function", released)
        self.assertIn("{v1} admits a representation", released)
        self.assertIn("as in Eq.", released)
        self.assertIn("are", released)
        self.assertIn("Proof.", released)
        self.assertTrue(paragraphs[1].brk)
        self.assertIn("such that for any 0", segments[1])
        self.assertEqual(segments[2], "")

    def test_reference_specific_italic_title_does_not_release_a_venue(self) -> None:
        runs = [
            fake_run("Neural networks: a comprehensive foundation"),
            fake_run("Physical review letters"),
        ]
        segments = [
            "[1] Simon Haykin. {v0}. Prentice Hall PTR, 1994. "
            "[2] S. John. Strong localization of photons. {v1}, 58(23), 1987."
        ]

        candidates = _collect_reference_title_italic_runs(
            runs,
            [0, 0],
            [paragraph()],
            segments,
        )

        self.assertEqual(
            candidates,
            {0: "Neural networks: a comprehensive foundation"},
        )

    def test_prose_parenthetical_and_closing_delimiter_are_released(self) -> None:
        mixed = fake_mixed_run(
            [("ϕ", "CMMI10"), ("(agreeing", "TimesNewRoman-Regular")]
        )
        formula, suffix = _split_trailing_prose_openers(mixed)
        self.assertEqual("".join(char.get_text() for char in formula), "ϕ")
        self.assertEqual(suffix, "(agreeing")

        closing = fake_run(")", fontname="TimesNewRoman-Regular")
        formula, suffix, released = _split_punctuation_only_formula_run(
            closing,
            has_prose_context=True,
            paragraph_layout_class=1,
        )
        self.assertEqual(formula, [])
        self.assertEqual(suffix, ")")
        self.assertEqual(released, closing)

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
        self.assertEqual(
            _is_high_confidence_prose_italic(
                fake_run(
                    "approximation theory",
                    fontname="NimbusRomNo9L-ReguItal",
                ),
                10.0,
            ),
            "approximation theory",
        )

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

    def test_page_edge_labels_and_adjacent_multiword_styles_are_released(self) -> None:
        runs = [
            fake_mixed_run(
                [
                    ("Abstract", "Times-BoldItalic"),
                    ("—In", "Times-Bold"),
                ]
            ),
            fake_run("SE class labels", fontname="Times-BoldItalic"),
            fake_run("enrollment audio samples", fontname="Times-BoldItalic"),
            fake_run("(or", fontname="Times-Bold"),
            fake_run(")", fontname="Times-Bold"),
            fake_mixed_run(
                [
                    ("IndexTerms", "Times-BoldItalic"),
                    ("—Deep", "Times-Bold"),
                ]
            ),
        ]
        segments = [
            "{v0}",
            (
                "many scenarios use target {v1} and {v2}{v3} "
                "audio queries{v4}, which are prerecorded examples."
            ),
            "{v5}",
        ]

        candidates = _collect_translatable_italic_runs(
            runs,
            [0, 1, 1, 1, 1, 2],
            [paragraph(), paragraph(), paragraph()],
            segments,
        )

        self.assertEqual(
            candidates,
            {
                0: "Abstract—In",
                1: "SE class labels",
                2: "enrollment audio samples",
                3: "(or",
                5: "IndexTerms—Deep",
            },
        )
        label_tagged, label_ids = _tag_translatable_italic_formulas(
            segments[0], candidates
        )
        tagged, ids = _tag_translatable_italic_formulas(segments[1], candidates)
        index_tagged, index_ids = _tag_translatable_italic_formulas(
            segments[2], candidates
        )
        self.assertEqual(label_ids, (0,))
        self.assertEqual(ids, (1, 2, 3))
        self.assertEqual(index_ids, (5,))
        self.assertIn(
            "[[PDF2ZH_ITALIC_0_BEGIN]]Abstract—In",
            label_tagged,
        )
        self.assertIn(
            "[[PDF2ZH_ITALIC_2_BEGIN]]enrollment audio samples",
            tagged,
        )
        self.assertIn("[[PDF2ZH_ITALIC_3_BEGIN]](or", tagged)
        self.assertIn("audio queries{v4}", tagged)
        self.assertIn(
            "[[PDF2ZH_ITALIC_5_BEGIN]]IndexTerms—Deep",
            index_tagged,
        )

    def test_math_identifiers_products_names_and_ieee_titles_stay_protected(
        self,
    ) -> None:
        protected = (
            "Senior Member IEEE",
            "Marc Delcroix",
            "SoundBeam model",
            "Q factor",
            "sin x",
            "iSWAP gate",
        )
        for text in protected:
            with self.subTest(text=text):
                self.assertIsNone(
                    _is_high_confidence_prose_italic(fake_run(text), 10.0)
                )

    def test_formula_only_subsections_and_runin_headings_are_released(self) -> None:
        runs = [
            fake_run("A. Data"),
            fake_run("C. Evaluation Metrics"),
            fake_mixed_run(
                [
                    (
                        "Extraction of new SE classes with few-shot adaptation:",
                        "Times-Italic",
                    ),
                ]
            ),
            fake_mixed_run(
                [
                    ("1) Class Label Encoder:", "Times-Italic"),
                ]
            ),
            fake_mixed_run(
                [
                    (
                        "2) Extraction Results With Real Mixtures:",
                        "Times-Italic",
                    ),
                ]
            ),
        ]
        segments = [f"{{v{index}}}" for index in range(len(runs))]

        candidates = _collect_translatable_italic_runs(
            runs,
            list(range(len(runs))),
            [
                paragraph(region_kind="title"),
                paragraph(region_kind="title"),
                paragraph(region_kind="plain text"),
                paragraph(region_kind="plain text"),
                paragraph(region_kind="plain text"),
            ],
            segments,
        )

        self.assertEqual(
            candidates,
            {
                0: "A. Data",
                1: "C. Evaluation Metrics",
                2: "Extraction of new SE classes with few-shot adaptation:",
                3: "1) Class Label Encoder:",
                4: "2) Extraction Results With Real Mixtures:",
            },
        )

    def test_section_shape_does_not_release_math_or_non_title_labels(self) -> None:
        runs = [
            fake_run("A. Data"),
            fake_run("A. x"),
            fake_mixed_run(
                [
                    ("Encoder: ", "Times-Italic"),
                    ("x", "CMMI10"),
                ]
            ),
        ]

        candidates = _collect_translatable_italic_runs(
            runs,
            [0, 1, 2],
            [
                paragraph(region_kind="plain text"),
                paragraph(region_kind="title"),
                paragraph(region_kind="plain text"),
            ],
            ["{v0}", "{v1}", "{v2}"],
        )

        self.assertEqual(candidates, {})


class ItalicMarkupTests(unittest.TestCase):
    def test_only_selected_formula_placeholder_is_unwrapped(self) -> None:
        tagged, ids = _tag_translatable_italic_formulas(
            "frequency tuned {v12} by flux while {v13} is fixed",
            {12: "in situ"},
        )
        self.assertEqual(ids, (12,))
        self.assertIn(
            "[[PDF2ZH_ITALIC_12_BEGIN]]in situ" "[[PDF2ZH_ITALIC_12_END]]",
            tagged,
        )
        self.assertIn("{v13}", tagged)

    def test_codex_validator_rejects_damaged_style_or_formula_markers(self) -> None:
        source = (
            "tuned [[PDF2ZH_ITALIC_12_BEGIN]]in situ" "[[PDF2ZH_ITALIC_12_END]] by {v1}"
        )
        valid = (
            "通过[[PDF2ZH_ITALIC_12_BEGIN]]原位" "[[PDF2ZH_ITALIC_12_END]]方式调谐{v1}"
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

    def test_styled_english_is_visible_to_translation_quality_gate(self) -> None:
        translator = CodexTranslator.__new__(CodexTranslator)
        translator.lang_in = "en"
        translator.lang_out = "zh-cn"
        source = (
            "use [[PDF2ZH_ITALIC_9_BEGIN]]enrollment audio samples"
            "[[PDF2ZH_ITALIC_9_END]] as clues"
        )
        untranslated_style = (
            "使用[[PDF2ZH_ITALIC_9_BEGIN]]enrollment audio samples"
            "[[PDF2ZH_ITALIC_9_END]]作为线索"
        )
        translated_style = (
            "使用[[PDF2ZH_ITALIC_9_BEGIN]]注册音频样本"
            "[[PDF2ZH_ITALIC_9_END]]作为线索"
        )

        self.assertIsNone(
            translator._accepted_styled_translation(
                source,
                untranslated_style,
                [],
            )
        )
        self.assertEqual(
            translator._accepted_styled_translation(
                source,
                translated_style,
                [],
            ),
            translated_style,
        )

    def test_styled_translation_and_plain_prose_fallback(self) -> None:
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
        )[
            0
        ]
        self.assertEqual(fallback, "frequency tuned in situ by flux")

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
