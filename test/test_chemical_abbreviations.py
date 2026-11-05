from __future__ import annotations

import unittest

from pdf2zh.source_segmentation import (
    chemical_abbreviation_spans,
    safe_chunk_boundary,
    scientific_sentence_parts,
)
from pdf2zh.translation_quality import has_omitted_scientific_content
from pdf2zh.translator import CodexTranslator


class ChemicalAbbreviationTests(unittest.TestCase):
    def test_aqueous_wash_keeps_reagents_and_action_in_one_sentence(self):
        source = (
            "The mixture was washed sequentially with saturated aq. "
            "NaHCO{v2} (25 mL) and brine (40 mL). "
            "The organic layer was dried."
        )
        expected = [
            source[: source.index("The organic")],
            "The organic layer was dried.",
        ]
        self.assertEqual(scientific_sentence_parts(source), expected)
        translator = CodexTranslator.__new__(CodexTranslator)
        self.assertEqual(translator._split_formula_context_text(source), expected)
        self.assertEqual("".join(expected), source)

    def test_dependent_chemical_qualifiers_preserve_values_and_following_prose(self):
        for qualifier, reagent in (
            ("aq.", "NaOH (2 M, 0.25 mL)"),
            ("Aq.", "2-chloroacetaldehyde (50%)"),
            ("aq.", "sodium thiosulfate (5 mL)"),
            ("aq.", "{v0} (5 mL)"),
            ("sat.", "aq. NH{v4} (15 mL)"),
            ("conc.", "HCl (0.5 mL)"),
        ):
            with self.subTest(qualifier=qualifier, reagent=reagent):
                first = f"Treatment with {qualifier} {reagent} gave compound 9. "
                source = first + "The yield was 85.5%."
                self.assertEqual(
                    scientific_sentence_parts(source), [first, "The yield was 85.5%."]
                )
                self.assertEqual("".join(scientific_sentence_parts(source)), source)

    def test_real_sentence_ends_and_nonchemical_words_keep_their_boundaries(self):
        for source, expected in (
            ("Meeting on Sat. Next item.", ["Meeting on Sat. ", "Next item."]),
            (
                "Reported conc. The value follows.",
                ["Reported conc. ", "The value follows."],
            ),
            (
                "This is syntaxaq. Sodium follows.",
                ["This is syntaxaq. ", "Sodium follows."],
            ),
            ("This is aq.", ["This is aq."]),
        ):
            with self.subTest(source=source):
                self.assertEqual(scientific_sentence_parts(source), expected)
                self.assertEqual(chemical_abbreviation_spans(source), ())

    def test_hard_chunk_boundaries_do_not_separate_qualifier_and_reagent(self):
        source = "prefix sat. aq. NaHCO{v2} (25 mL) and more text"
        spans = chemical_abbreviation_spans(source)
        self.assertEqual(len(spans), 2)
        for limit in range(source.index("sat."), source.index(" (25")):
            cut = safe_chunk_boundary(source, limit, ())
            self.assertFalse(any(start < cut < end for start, end in spans))

    def test_cache_invalidation_is_scoped_to_previously_severed_qualifiers(self):
        source = "The solid was washed with aq. NaOH and then washed with water."
        self.assertEqual(
            CodexTranslator._prose_cache_key(source),
            "pdf2zh:chemical-abbreviation-sentences:v1\n" + source,
        )
        for unchanged in (
            "The solid was washed with water.",
            "Meeting on Sat. Next item.",
        ):
            self.assertEqual(CodexTranslator._prose_cache_key(unchanged), unchanged)

    def test_context_and_styled_cache_keys_invalidate_the_same_chemical_sources(self):
        changed = (
            "The mixture was washed with aq. NaHCO{v2} and then washed with water."
        )
        ordinary = "The mixture was washed with NaHCO{v2} and then washed with water."
        for key_builder in (
            CodexTranslator._formula_context_cache_key,
            CodexTranslator._styled_cache_key,
        ):
            with self.subTest(cache=key_builder.__name__):
                self.assertIn(
                    '"chemical_abbreviation_chunking":"v1"',
                    key_builder(changed, {"{v2}": "3"}),
                )
                self.assertNotIn(
                    "chemical_abbreviation_chunking",
                    key_builder(ordinary, {"{v2}": "3"}),
                )


class LaboratoryWashValidationTests(unittest.TestCase):
    def test_aqueous_washing_action_cannot_disappear(self):
        for source in (
            "The mixture was washed sequentially with saturated aq. NaHCO{v2} (25 mL) and brine (40 mL).",
            "The solid was washed with aq. HCl (5 mL).",
            "The solid was washed with aq. NaHCO3 (5 mL).",
            "The organic layer was washed further with water and brine.",
            "The mixture was extracted with ether, washed with saturated aq. ",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    has_omitted_scientific_content(
                        source, "然后依次用饱和水溶液和盐水。"
                    )
                )
                for target in (
                    "用上述溶液洗涤。",
                    "依次以水和鹽水清洗。",
                    "再用水冲净。",
                ):
                    self.assertFalse(has_omitted_scientific_content(source, target))

    def test_nonwashing_physics_and_nonliquid_metaphors_are_not_lab_operations(self):
        for source, target in (
            (
                "The oscillations were washed out with increasing noise.",
                "振荡随噪声增强而消失。",
            ),
            ("The result was washed with fresh data.", "结果经过新数据修正。"),
            ("The sample was dissolved in water.", "将样品溶于水。"),
        ):
            with self.subTest(source=source):
                self.assertFalse(has_omitted_scientific_content(source, target))

    def test_combined_translation_can_cover_repeated_washes_with_one_verb(self):
        source = "The solid was washed with water and then washed with ethanol."
        self.assertFalse(
            has_omitted_scientific_content(source, "该固体依次用水和乙醇洗涤。")
        )


if __name__ == "__main__":
    unittest.main()
