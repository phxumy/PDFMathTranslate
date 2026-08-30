from __future__ import annotations

import unittest

from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    ExactReplacement,
    ROLE_AFFILIATION,
    ROLE_PRESERVE,
    ROLE_REFERENCE,
    ROLE_TRANSLATE,
    SourceSegment,
    apply_exact_replacements,
    find_reference_markers,
    restore_affiliation_breaks,
    split_author_affiliation,
    split_numbered_reference_region,
)


def segment(text: str, index: int = 0) -> SourceSegment:
    return SourceSegment(index=index, text=text, page_width=612.0)


class ReferenceMarkerTests(unittest.TestCase):
    def test_square_bracket_formula_and_supplement_markers(self) -> None:
        source = (
            "[1] A. Alpha, First title. Phys. Rev. A 1, 10 (2020). "
            "{v0} B. Beta, Second title. Nature 2, 20 (2021). "
            "[S1] C. Gamma, Supplement title. Science 3, 30 (2022). "
            "S2. D. Delta, More supplement. Science 4, 40 (2023)."
        )
        markers = find_reference_markers(source, formula_texts=("2",))

        self.assertEqual([marker.label for marker in markers], ["1", "2", "S1", "S2"])
        self.assertEqual(
            [marker.kind for marker in markers],
            ["bracket", "formula", "supplement-bracket", "supplement"],
        )

    def test_parenthetical_marker_keeps_its_opening_parenthesis(self) -> None:
        markers = find_reference_markers("(1) A. Alpha, Title. Nature 1 (2020).")

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].kind, "parenthetical")
        self.assertEqual(markers[0].raw, "(1)")
        self.assertEqual(markers[0].start, 0)

    def test_fullwidth_parenthetical_marker_is_supported(self) -> None:
        markers = find_reference_markers("（1） A. Alpha, Title. Nature 1 (2020).")

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].kind, "parenthetical")
        self.assertEqual(markers[0].raw, "（1）")

    def test_tight_initial_marker_allows_spaces_after_periods(self) -> None:
        markers = find_reference_markers(
            "12A. B. Smith, A useful title. Nature 1, 10 (2020)."
        )

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].kind, "tight-initial")
        self.assertEqual(markers[0].number, 12)

    def test_author_affiliation_superscripts_are_not_reference_markers(self) -> None:
        source = (
            "T. I. Andersen{v0}, N. Astrakhantsev{v1}, "
            "A. H. Karamlou{v2}, J. Berndtsson{v3}, "
            "J. Motruk{v4}, A. Szasz{v5},"
        )

        markers = find_reference_markers(
            source,
            formula_texts=("1,16", "1,16", "1", "1", "2", "1"),
        )

        self.assertEqual(markers, ())

    def test_formula_marker_can_follow_reference_heading(self) -> None:
        markers = find_reference_markers(
            "References {v0} A. Alpha, A useful title. Nature 1, 10 (2020).",
            formula_texts=("1",),
        )

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].kind, "formula")
        self.assertEqual(markers[0].number, 1)


class ReferencePlanningTests(unittest.TestCase):
    def test_methods_enumeration_is_not_a_reference_region(self) -> None:
        source = (
            "(1) Nearest-neighbour qubits separated by a coupler. "
            "(2) Diagonally separated qubits in the northwest direction. "
            "(3) Diagonally separated qubits in the northeast direction."
        )
        policy = DocumentTranslationPolicy()

        self.assertIsNone(split_numbered_reference_region(source))
        plan = policy.plan_segment(segment(source))

        self.assertEqual([part.role for part in plan.parts], [ROLE_TRANSLATE])
        self.assertIsNone(policy.last_reference_number)

    def test_single_parenthetical_entry_requires_reference_context(self) -> None:
        source = (
            "(1) A. Alpha, A reliable article title. "
            "Nature 1, 10–20 (2020). https://doi.org/10.1000/example"
        )

        self.assertIsNone(split_numbered_reference_region(source))
        self.assertIsNotNone(
            split_numbered_reference_region(source, heading_hint=True)
        )

    def test_single_closing_entry_can_continue_expected_numbering(self) -> None:
        source = (
            "57) A. Alpha, A reliable article title. "
            "Nature 1, 10–20 (2020). https://doi.org/10.1000/example"
        )

        region = split_numbered_reference_region(
            source,
            expected_number=57,
        )

        self.assertIsNotNone(region)

    def test_formula_affiliations_do_not_split_an_author_list(self) -> None:
        source = (
            "T. I. Andersen{v0}, N. Astrakhantsev{v1}, "
            "A. H. Karamlou{v2}, J. Berndtsson{v3}, "
            "J. Motruk{v4}, A. Szasz{v5},"
        )
        formula_texts = ("1,16", "1,16", "1", "1", "2", "1")

        plan = DocumentTranslationPolicy().plan_segment(
            segment(source),
            formula_texts=formula_texts,
        )

        self.assertEqual([part.role for part in plan.parts], [ROLE_PRESERVE])
        self.assertEqual(plan.parts[0].text, source)

    def test_single_superscript_reference_block_is_detected_conservatively(self) -> None:
        source = (
            "{v0} Altman, E. et al. Quantum simulators: architectures and "
            "opportunities. PRX Quantum 2, 017003 (2021)."
        )

        region = split_numbered_reference_region(
            source, formula_texts=("1",)
        )

        self.assertIsNotNone(region)
        assert region is not None
        self.assertEqual(region.markers[0].number, 1)

    def test_single_numbered_equation_is_not_a_reference(self) -> None:
        source = "{v0} The effective coupling is obtained by diagonalization."

        self.assertIsNone(
            split_numbered_reference_region(source, formula_texts=("1",))
        )

    def test_nature_references_resume_after_methods(self) -> None:
        policy = DocumentTranslationPolicy()
        policy.last_reference_number = 54

        main_tail = (
            "55. Metlitski, M. A. & Grover, T. Entanglement entropy of systems. "
            "Preprint at https://arxiv.org/abs/1112.5166 (2015). "
            "56. Manovitz, T. et al. Quantum coarsening and collective dynamics. "
            "Nature 638, 100–110 (2024)."
        )
        main_plan = policy.plan_segment(segment(main_tail))
        self.assertEqual(policy.last_reference_number, 56)
        self.assertEqual(
            sum(part.role == ROLE_REFERENCE for part in main_plan.parts), 2
        )
        self.assertEqual("".join(part.text for part in main_plan.parts), main_tail)

        methods_heading = policy.plan_segment(segment("Methods", 1))
        self.assertEqual(methods_heading.parts[0].role, ROLE_TRANSLATE)
        self.assertEqual(policy.expected_reference_number, 57)

        methods_steps = (
            "(1) Nearest-neighbour qubits. (2) Diagonally separated qubits. "
            "(3) Readout correction."
        )
        steps_plan = policy.plan_segment(segment(methods_steps, 2))
        self.assertEqual([part.role for part in steps_plan.parts], [ROLE_TRANSLATE])
        self.assertEqual(policy.expected_reference_number, 57)

        methods_references = (
            "57. Bravyi, S., DiVincenzo, D. P. & Loss, D. "
            "Schrieffer-Wolff transformation. Ann. Phys. 326, 2793–2826 (2011). "
            "58. Smith, A. W. R. et al. Qubit readout error mitigation. "
            "Sci. Adv. 7, eabi8009 (2021). "
            "59. Andersen, T. I. Thermalization and criticality. "
            "Zenodo https://doi.org/10.5281/zenodo.14060446 (2024)."
        )
        methods_plan = policy.plan_segment(segment(methods_references, 3))

        self.assertEqual(policy.last_reference_number, 59)
        self.assertEqual(
            sum(part.role == ROLE_REFERENCE for part in methods_plan.parts), 3
        )
        self.assertEqual(
            "".join(part.text for part in methods_plan.parts), methods_references
        )

    def test_weak_long_sequence_does_not_hide_expected_real_references(self) -> None:
        policy = DocumentTranslationPolicy()
        policy.last_reference_number = 56
        source = (
            "(1) Prepare the device. (2) Measure the qubits. (3) Fit the data. "
            "57. A. Alpha, Reliable citation title. Nature 1, 10 (2020). "
            "58. B. Beta, Another citation title. Science 2, 20 (2021)."
        )

        plan = policy.plan_segment(segment(source))

        self.assertEqual(policy.last_reference_number, 58)
        self.assertEqual(
            sum(part.role == ROLE_REFERENCE for part in plan.parts), 2
        )

    def test_heading_and_single_entry_can_share_one_segment(self) -> None:
        source = (
            "References [1] A. Alpha, A reliable article title. "
            "Phys. Rev. A 1, 10 (2020)."
        )

        plan = DocumentTranslationPolicy().plan_segment(segment(source))

        self.assertEqual(
            [part.role for part in plan.parts],
            [ROLE_TRANSLATE, ROLE_REFERENCE],
        )
        self.assertEqual("".join(part.text for part in plan.parts), source)

    def test_explicit_new_reference_heading_may_restart_numbering(self) -> None:
        policy = DocumentTranslationPolicy()
        policy.last_reference_number = 59
        policy.plan_segment(segment("References"))
        source = (
            "[1] A. Alpha, Supplementary work title. Nature 1, 10 (2020). "
            "[2] B. Beta, Another supplementary title. Science 2, 20 (2021)."
        )

        plan = policy.plan_segment(segment(source, 1))

        self.assertEqual(policy.last_reference_number, 2)
        self.assertEqual(
            sum(part.role == ROLE_REFERENCE for part in plan.parts), 2
        )

    def test_supplement_prefix_continues_independently(self) -> None:
        policy = DocumentTranslationPolicy()
        first = "[S1] A. Alpha, Supplement title. Nature 1, 10 (2020)."
        second = "S2. B. Beta, More supplement. Science 2, 20 (2021)."

        self.assertEqual(
            policy.plan_segment(segment(first)).parts[0].role, ROLE_REFERENCE
        )
        self.assertEqual(policy.last_reference_prefix, "S")
        self.assertEqual(policy.expected_reference_number, 2)
        self.assertEqual(
            policy.plan_segment(segment(second, 1)).parts[0].role,
            ROLE_REFERENCE,
        )
        self.assertEqual(policy.last_reference_number, 2)


class ExactReplacementTests(unittest.TestCase):
    def test_only_exact_title_span_changes(self) -> None:
        source = (
            "[1] A. Smith, Interesting quantum title. "
            "Phys. Rev. A 12, 34–56 (2020). doi:10.1000/example"
        )
        replacement = ExactReplacement(
            "Interesting quantum title", "有趣的量子题名"
        )

        result = apply_exact_replacements(source, (replacement,))

        self.assertEqual(
            result,
            "[1] A. Smith, 有趣的量子题名. "
            "Phys. Rev. A 12, 34–56 (2020). doi:10.1000/example",
        )

    def test_invalid_or_ambiguous_replacements_fail_safely(self) -> None:
        source = "A title occurs here; the same title occurs here."

        self.assertIsNone(
            apply_exact_replacements(
                source, (ExactReplacement("title occurs here", "中文"),)
            )
        )
        self.assertIsNone(
            apply_exact_replacements(
                source, (ExactReplacement("missing title", "中文"),)
            )
        )
        self.assertIsNone(
            apply_exact_replacements(
                "A title with {v0}.",
                (ExactReplacement("title with {v0}", "含有公式的题名"),),
            )
        )

    def test_empty_replacement_list_preserves_source(self) -> None:
        source = "A. Smith, Phys. Rev. A 1, 2 (2020)."
        self.assertEqual(apply_exact_replacements(source, ()), source)


class AuthorAffiliationTests(unittest.TestCase):
    def test_combined_author_and_affiliation_are_split_exactly(self) -> None:
        source = (
            "Zijun Chen, A. Megrant, J. Kelly {v0}"
            "Department of Physics, University of California, USA"
        )
        split = split_author_affiliation(source)

        self.assertIsNotNone(split)
        assert split is not None
        authors, affiliation = split
        self.assertEqual(authors + affiliation, source)
        self.assertEqual(authors, "Zijun Chen, A. Megrant, J. Kelly ")
        self.assertTrue(affiliation.startswith("{v0}Department"))

        plan = DocumentTranslationPolicy().plan_segment(segment(source))
        self.assertEqual(
            [part.role for part in plan.parts],
            [ROLE_PRESERVE, ROLE_AFFILIATION],
        )
        self.assertTrue(plan.parts[1].break_before)
        self.assertEqual("".join(part.text for part in plan.parts), source)

    def test_author_protection_can_be_limited_to_front_matter(self) -> None:
        source = "Zijun Chen, A. Megrant, J. Kelly"
        policy = DocumentTranslationPolicy()

        plan = policy.plan_segment(segment(source), protect_authors=False)

        self.assertEqual([part.role for part in plan.parts], [ROLE_TRANSLATE])

    def test_named_affiliation_sentence_starts_on_a_new_line(self) -> None:
        source = "G. E. Ponchak is with NASA Glenn Research Center, Cleveland."

        plan = DocumentTranslationPolicy().plan_segment(segment(source))

        self.assertEqual(
            [part.role for part in plan.parts],
            [ROLE_PRESERVE, ROLE_TRANSLATE],
        )
        self.assertTrue(plan.parts[1].break_before)
        self.assertEqual("".join(part.text for part in plan.parts), source)

    def test_equal_contribution_note_gets_its_own_affiliation_line(self) -> None:
        source = (
            "15Department of Physics, Princeton University. "
            "16These authors contributed equally: A. Alpha, B. Beta."
        )
        translated = (
            "15美国普林斯顿大学物理系。 "
            "16以下作者贡献相同：A. Alpha, B. Beta。"
        )

        result = restore_affiliation_breaks(source, translated)

        self.assertIn("。\n16以下作者贡献相同", result)


if __name__ == "__main__":
    unittest.main()
