from __future__ import annotations

import unittest

from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    ExactReplacement,
    ROLE_AFFILIATION,
    ROLE_PRESERVE,
    ROLE_REFERENCE,
    ROLE_TRANSLATE,
    RUNNING_HEADER_REGION_KIND,
    SourceSegment,
    apply_exact_replacements,
    find_reference_markers,
    looks_like_reference_author_block,
    restore_affiliation_breaks,
    split_author_affiliation,
    split_numbered_reference_region,
)


def segment(text: str, index: int = 0) -> SourceSegment:
    return SourceSegment(index=index, text=text, page_width=612.0)


class TitleBadgeTests(unittest.TestCase):
    def test_publication_badges_are_preserved_separately_from_title(self) -> None:
        source = "ARTICLE OPEN Energy-participation quantization of circuits"
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                page_width=612.0,
                break_offsets=(12,),
                region_kind="title",
            )
        )

        self.assertEqual(
            [(part.role, part.text, part.break_before) for part in plan.parts],
            [
                (ROLE_PRESERVE, "ARTICLE OPEN ", False),
                (
                    ROLE_TRANSLATE,
                    "Energy-participation quantization of circuits",
                    True,
                ),
            ],
        )

    def test_title_starting_with_open_is_not_treated_as_a_badge(self) -> None:
        source = "Open quantum systems in a microwave cavity"
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                page_width=612.0,
                region_kind="title",
            )
        )

        self.assertEqual(
            [(part.role, part.text) for part in plan.parts],
            [(ROLE_TRANSLATE, source)],
        )

    def test_title_phrase_matching_badge_words_needs_a_source_break(self) -> None:
        source = "Review article design for scientific publishing"
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                page_width=612.0,
                region_kind="title",
            )
        )

        self.assertEqual(
            [(part.role, part.text) for part in plan.parts],
            [(ROLE_TRANSLATE, source)],
        )

    def test_detector_confirmed_long_title_bypasses_author_heuristics(self) -> None:
        source = (
            "SoundBeam: Target Sound Extraction Conditioned on Sound-Class "
            "Labels and Enrollment Clues for Increased Performance and "
            "Continuous Learning"
        )
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                page_width=612.0,
                region_kind="title",
            )
        )

        self.assertEqual(
            [(part.role, part.text) for part in plan.parts],
            [(ROLE_TRANSLATE, source)],
        )

    def test_short_upper_page_byline_mislabeled_as_title_is_preserved(self) -> None:
        source = "John Smith and Jane Doe"
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                y0=610.0,
                y1=624.0,
                size=11.0,
                page_width=612.0,
                page_height=792.0,
                region_kind="title",
            )
        )

        self.assertEqual(
            [(part.role, part.text) for part in plan.parts],
            [(ROLE_PRESERVE, source)],
        )

    def test_short_title_case_paper_title_is_still_translated(self) -> None:
        source = "Deep Learning and Neural Networks"
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                y0=610.0,
                y1=642.0,
                size=20.0,
                page_width=612.0,
                page_height=792.0,
                region_kind="title",
            )
        )

        self.assertEqual(
            [(part.role, part.text) for part in plan.parts],
            [(ROLE_TRANSLATE, source)],
        )


class RunningHeaderTests(unittest.TestCase):
    def test_top_margin_short_line_is_preserved(self) -> None:
        source = "IEEE/ACM TRANSACTIONS ON AUDIO, SPEECH, AND LANGUAGE PROCESSING"
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                y0=753.18,
                y1=760.15,
                size=6.97,
                page_width=594.0,
                page_height=792.0,
            )
        )

        self.assertEqual(
            [(part.role, part.text) for part in plan.parts],
            [(ROLE_PRESERVE, source)],
        )

    def test_explicit_running_header_kind_is_preserved(self) -> None:
        source = "A short running title"
        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                region_kind=RUNNING_HEADER_REGION_KIND,
            )
        )

        self.assertEqual([part.role for part in plan.parts], [ROLE_PRESERVE])

    def test_top_title_and_caption_are_not_reclassified(self) -> None:
        for kind in ("title", "figure_caption", "table_caption"):
            with self.subTest(kind=kind):
                source = "A semantic region that should be translated"
                plan = DocumentTranslationPolicy().plan_segment(
                    SourceSegment(
                        index=0,
                        text=source,
                        y0=753.0,
                        y1=761.0,
                        size=7.0,
                        page_width=594.0,
                        page_height=792.0,
                        region_kind=kind,
                    )
                )
                self.assertEqual(
                    [part.role for part in plan.parts],
                    [ROLE_TRANSLATE],
                )

    def test_body_and_bottom_note_are_not_running_headers(self) -> None:
        cases = ((719.0, 729.0), (49.0, 66.0))
        for y0, y1 in cases:
            with self.subTest(y0=y0):
                source = "This ordinary scientific sentence requires translation."
                plan = DocumentTranslationPolicy().plan_segment(
                    SourceSegment(
                        index=0,
                        text=source,
                        y0=y0,
                        y1=y1,
                        size=9.96,
                        page_width=594.0,
                        page_height=792.0,
                        region_kind="plain text",
                    )
                )
                self.assertEqual(
                    [part.role for part in plan.parts],
                    [ROLE_TRANSLATE],
                )


class NamedProseSectionTests(unittest.TestCase):
    def test_acknowledgement_name_list_is_translated_as_prose(self) -> None:
        policy = DocumentTranslationPolicy()
        heading = policy.plan_segment(segment("Acknowledgements"))
        source = (
            "We thank S. M. Girvin, R. J. Schoelkopf, and A. Blais for "
            "valuable discussions and support from the research office."
        )
        paragraph = policy.plan_segment(segment(source, index=1))

        self.assertEqual([part.role for part in heading.parts], [ROLE_TRANSLATE])
        self.assertEqual(
            [(part.role, part.text) for part in paragraph.parts],
            [(ROLE_TRANSLATE, source)],
        )


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
    def test_reference_author_block_accepts_undotted_middle_initials(self) -> None:
        accepted = (
            "David A Sprecher and Sorin Draghici.",
            "Jonathan W Siegel.",
            "J Biddle and S Das Sarma.",
            "Ronald A DeVore, George Kyriazis, Dany Leviatan, and Vladimir M Tikhomirov.",
        )
        for value in accepted:
            with self.subTest(value=value):
                self.assertTrue(looks_like_reference_author_block(value))
        self.assertFalse(
            looks_like_reference_author_block(
                "Representation properties of networks and learning systems."
            )
        )

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
        self.assertIsNotNone(split_numbered_reference_region(source, heading_hint=True))

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

    def test_single_superscript_reference_block_is_detected_conservatively(
        self,
    ) -> None:
        source = (
            "{v0} Altman, E. et al. Quantum simulators: architectures and "
            "opportunities. PRX Quantum 2, 017003 (2021)."
        )

        region = split_numbered_reference_region(source, formula_texts=("1",))

        self.assertIsNotNone(region)
        assert region is not None
        self.assertEqual(region.markers[0].number, 1)

    def test_single_numbered_equation_is_not_a_reference(self) -> None:
        source = "{v0} The effective coupling is obtained by diagonalization."

        self.assertIsNone(split_numbered_reference_region(source, formula_texts=("1",)))

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
        self.assertEqual(sum(part.role == ROLE_REFERENCE for part in plan.parts), 2)

    def test_italic_formula_metadata_keeps_a_late_reference_page_structured(
        self,
    ) -> None:
        policy = DocumentTranslationPolicy()
        policy.last_reference_number = 53
        source = (
            "[54] S. John. Strong localization of photons. {v0}. "
            "[55] Y. Lahini and R. Pugatch. Observation of a transition. {v1}. "
            "[56] S. Vaidya and M. Rechtsman. Reentrant delocalization. {v2}."
        )
        formula_texts = (
            "Physicalreviewletters,58(23):2486,1987",
            "Physicalreviewletters,103(1):013901,2009",
            "PhysicalReviewResearch,5(3):033170,2023",
        )

        plan = policy.plan_segment(
            segment(source),
            formula_texts=formula_texts,
        )

        self.assertEqual(
            [part.role for part in plan.parts],
            [ROLE_REFERENCE, ROLE_REFERENCE, ROLE_REFERENCE],
        )
        self.assertTrue(plan.parts[1].break_before)
        self.assertTrue(plan.parts[2].break_before)
        self.assertEqual(policy.last_reference_number, 56)

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
        self.assertEqual(sum(part.role == ROLE_REFERENCE for part in plan.parts), 2)

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
        replacement = ExactReplacement("Interesting quantum title", "有趣的量子题名")

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
    def test_detected_caption_bypasses_author_name_heuristics(self) -> None:
        source = (
            "Fig. 1 Additional FE results from The Hamiltonian Group, "
            "Supplementary Section Center, and Quantum Research Institute."
        )

        plan = DocumentTranslationPolicy().plan_segment(
            SourceSegment(
                index=0,
                text=source,
                page_width=612.0,
                region_kind="figure_caption",
            )
        )

        self.assertEqual(
            [(part.role, part.text) for part in plan.parts],
            [(ROLE_TRANSLATE, source)],
        )

    def test_capitalized_scientific_prose_is_not_mistaken_for_authors(self) -> None:
        sources = (
            (
                "Fig. 1 Conceptual overview. Additional FE simulations are "
                "unnecessary. The Hamiltonian is computed directly from the EPRs."
            ),
            (
                "mode by quantum fluctuations of the fields. The Hamiltonian "
                "parameters are calculated in Supplementary Section B2."
            ),
            (
                "Experimental values are found in Supplementary Section D. "
                "Equation (29) provides a dissipation budget."
            ),
        )

        for source in sources:
            with self.subTest(source=source):
                plan = DocumentTranslationPolicy().plan_segment(segment(source))
                self.assertEqual(
                    [(part.role, part.text) for part in plan.parts],
                    [(ROLE_TRANSLATE, source)],
                )

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
            "15美国普林斯顿大学物理系。 " "16以下作者贡献相同：A. Alpha, B. Beta。"
        )

        result = restore_affiliation_breaks(source, translated)

        self.assertIn("。\n16以下作者贡献相同", result)


if __name__ == "__main__":
    unittest.main()
