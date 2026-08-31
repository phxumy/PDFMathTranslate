from __future__ import annotations

import unittest

from pdf2zh.translation_quality import (
    has_unchanged_reference_title_fragment,
    has_suspicious_english_residue,
    has_suspicious_reference_title_residue,
    has_unchanged_translatable_english,
    normalize_cjk_compatibility_ideographs,
    normalize_scientific_cross_reference_placement,
    normalize_cjk_structural_repetitions,
)


class CjkCompatibilityNormalizationTests(unittest.TestCase):
    def test_only_cjk_compatibility_ideographs_are_normalized(self) -> None:
        source = "变量、电路和器件\U0002f800"

        result = normalize_cjk_compatibility_ideographs(source)

        self.assertEqual(result, "变量、电路和器件丽")

    def test_other_nfkc_compatible_characters_remain_unchanged(self) -> None:
        source = "ＡＢＣ ① ㎏ Å ﬃ"

        self.assertEqual(normalize_cjk_compatibility_ideographs(source), source)

    def test_empty_text_is_unchanged(self) -> None:
        self.assertEqual(normalize_cjk_compatibility_ideographs(""), "")


class CjkStructuralRepetitionNormalizationTests(unittest.TestCase):
    def test_duplicate_equation_designator_is_collapsed(self) -> None:
        source = "在量子情形下，式式 (5) 将 p{v31}与电路状态联系起来。"

        self.assertEqual(
            normalize_cjk_structural_repetitions(source),
            "在量子情形下，式 (5) 将 p{v31}与电路状态联系起来。",
        )

    def test_duplicate_attribute_particle_is_collapsed(self) -> None:
        source = "这是系统的 的响应，并保留公式{v2}和内部标记。"

        self.assertEqual(
            normalize_cjk_structural_repetitions(source),
            "这是系统的响应，并保留公式{v2}和内部标记。",
        )

    def test_duplicate_equation_cues_are_collapsed(self) -> None:
        source = (
            "在式在式（17）中可以看出，由式由式（26）可见该结论；"
            "根据式由式（30）同样成立。"
        )

        self.assertEqual(
            normalize_cjk_structural_repetitions(source),
            "在式（17）中可以看出，由式（26）可见该结论；"
            "根据式（30）同样成立。",
        )

    def test_legitimate_word_boundaries_and_reduplication_are_preserved(self) -> None:
        source = (
            "给出显式式子；这是研究目的的确切描述，也是标的的价值；"
            "有的的 确需要保留。"
        )

        self.assertEqual(normalize_cjk_structural_repetitions(source), source)


class ScientificCrossReferencePlacementTests(unittest.TestCase):
    def test_clear_figure_number_misplacements_are_repaired(self) -> None:
        cases = (
            (
                "The results of Fig. 4 prove the scaling law.",
                "图的结果。4证明了标度律。",
                "图4的结果证明了标度律。",
            ),
            ("See Fig. 5 for the layout.", "布局见图。5。", "布局见图5。"),
            (
                "Fig. 7 Illustration of the device.",
                "图。 7器件示意图。",
                "图7器件示意图。",
            ),
            (
                "As shown in Fig. 7, the layers are separated.",
                "如图所示。7，各层彼此分离。",
                "如图7所示，各层彼此分离。",
            ),
        )
        for source, target, expected in cases:
            with self.subTest(target=target):
                self.assertEqual(
                    normalize_scientific_cross_reference_placement(
                        source,
                        target,
                    ),
                    expected,
                )

    def test_equation_and_reference_placeholders_are_repaired(self) -> None:
        self.assertEqual(
            normalize_scientific_cross_reference_placement(
                "According to Eq. (6), the response is linear.",
                "根据式。(6)，响应是线性的。",
            ),
            "根据式(6)，响应是线性的。",
        )
        self.assertEqual(
            normalize_scientific_cross_reference_placement(
                "The design was reported in ref. {v20}. The values agree.",
                "该设计已在参考文献中报道。{v20}。这些数值一致。",
            ),
            "该设计已在参考文献{v20}中报道。这些数值一致。",
        )

    def test_missing_chinese_cross_reference_label_is_rejected(self) -> None:
        self.assertIsNone(
            normalize_scientific_cross_reference_placement(
                "Replace D with B in Eq. 34. Thus the ratio follows.",
                "将标签D替换为B。34.于是可得该比值。",
            )
        )

    def test_correct_cross_references_are_unchanged(self) -> None:
        cases = (
            ("See Fig. 5.", "见图5。"),
            ("Equation (6) gives the rate.", "方程（6）给出该速率。"),
            ("See reference {v20}.", "见参考文献{v20}。"),
        )
        for source, target in cases:
            with self.subTest(target=target):
                self.assertEqual(
                    normalize_scientific_cross_reference_placement(source, target),
                    target,
                )

    def test_unbalanced_equation_reference_parentheses_are_balanced(self) -> None:
        source = "This generalizes the result obtained in Eq. (8)."

        self.assertEqual(
            normalize_scientific_cross_reference_placement(
                source,
                "这是对式(8中所得结果的推广。",
            ),
            "这是对式(8)中所得结果的推广。",
        )
        self.assertEqual(
            normalize_scientific_cross_reference_placement(
                source,
                "这是对式8)中所得结果的推广。",
            ),
            "这是对式(8)中所得结果的推广。",
        )
        self.assertEqual(
            normalize_scientific_cross_reference_placement(
                source,
                "这是对式(8中所得结果的推广).",
            ),
            "这是对式(8)中所得结果的推广.",
        )

    def test_terminal_figure_reference_drops_only_stray_locative_artifact(self) -> None:
        source = (
            "The coefficients are known from fabrication, see Fig. 3c. "
            "For example, a junction has a cosine potential."
        )
        target = "这些系数可由制造参数确定，见图。3c。中。例如，结具有余弦势。"

        self.assertEqual(
            normalize_scientific_cross_reference_placement(source, target),
            "这些系数可由制造参数确定，见图3c。例如，结具有余弦势。",
        )

    def test_figure_example_cleanup_requires_exact_source_and_target_evidence(self) -> None:
        cases = (
            (
                "See Fig. 3c. The example is discussed next.",
                "见图3c。中。例如下文所述。",
            ),
            (
                "See Fig. 3c. For example, the device is symmetric.",
                "见图3c。图中。例如，该器件是对称的。",
            ),
            (
                "For example, see Fig. 3c for the device.",
                "例如，见图3c中的器件。",
            ),
        )
        for source, target in cases:
            with self.subTest(source=source, target=target):
                self.assertEqual(
                    normalize_scientific_cross_reference_placement(source, target),
                    target,
                )

    def test_repeated_equation_identifier_is_matched_by_occurrence(self) -> None:
        source = (
            "Normal ordering must be used in Eq. (6); otherwise the result "
            "changes. Simplifying Eq. (6), the Hamiltonian follows."
        )
        target = (
            "在式中必须使用正规序。（6）；否则结果会改变。"
            "化简方程通过式（6），可得到哈密顿量。"
        )

        self.assertEqual(
            normalize_scientific_cross_reference_placement(source, target),
            "在式（6）中必须使用正规序；否则结果会改变。"
            "化简式（6），可得到哈密顿量。",
        )

    def test_delayed_equation_repair_fails_closed_for_long_or_ambiguous_prose(self) -> None:
        source = "The value is constrained in Eq. (6)."
        target = "在式中" + ("这是一段不能安全移动编号的长文本" * 12) + "。（6）"

        self.assertIsNone(
            normalize_scientific_cross_reference_placement(source, target)
        )

    def test_unrelated_numbers_urls_dois_and_math_are_untouched(self) -> None:
        cases = (
            ("The value is 3.14.", "该值为3.14。"),
            (
                "See https://example.test/Fig.5 for version 2.0.",
                "参见https://example.test/Fig.5，版本为2.0。",
            ),
            (
                "The DOI is 10.1000/Fig.5.",
                "DOI为10.1000/Fig.5。",
            ),
            (r"The symbol $Fig. 5$ is literal.", r"符号$Fig. 5$是字面量。"),
        )
        for source, target in cases:
            with self.subTest(source=source):
                self.assertEqual(
                    normalize_scientific_cross_reference_placement(source, target),
                    target,
                )


class SuspiciousEnglishResidueTests(unittest.TestCase):
    def test_unchanged_english_sentence_is_rejected(self) -> None:
        source = "The measured response is controlled by the coupling strength."

        self.assertTrue(has_suspicious_english_residue(source, source))

    def test_near_copy_with_minor_punctuation_changes_is_rejected(self) -> None:
        source = "The measured response is controlled by the coupling strength."
        target = "the measured response is controlled by the coupling strength"

        self.assertTrue(has_suspicious_english_residue(source, target))

    def test_long_shared_english_clause_inside_target_is_rejected(self) -> None:
        source = (
            "The response of the circuit is controlled by external magnetic flux "
            "at low temperature."
        )
        target = (
            "电路响应中仍出现 The response of the circuit is controlled by external "
            "magnetic flux，随后才恢复中文。"
        )

        self.assertTrue(has_suspicious_english_residue(source, target))

    def test_long_english_paraphrase_is_rejected(self) -> None:
        source = "The measured response is controlled by the coupling strength."
        target = (
            "External coupling governs how the device responds during measurement."
        )

        self.assertTrue(has_suspicious_english_residue(source, target))

    def test_normal_chinese_translation_is_accepted(self) -> None:
        source = "The measured response is controlled by the coupling strength."
        target = "测得的响应由耦合强度控制。"

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_short_technical_name_is_accepted(self) -> None:
        source = "The device uses a Josephson junction."
        target = "该器件采用 Josephson junction。"

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_english_technical_phrase_inside_chinese_is_accepted(self) -> None:
        source = "The device uses a fault tolerant quantum computing architecture."
        target = "该器件采用 fault tolerant quantum computing architecture 方案。"

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_preserved_author_initials_do_not_look_like_english_prose(self) -> None:
        source = (
            "We thank S. M. Girvin, R. J. Schoelkopf, A. Blais, A. Petrescu, "
            "and A. Eickbusch for valuable discussions."
        )
        target = (
            "我们感谢 S. M. Girvin、R. J. Schoelkopf、A. Blais、A. Petrescu 和 "
            "A. Eickbusch 进行的宝贵讨论。"
        )

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_single_english_predicate_embedded_in_chinese_is_rejected(self) -> None:
        source = "where {v0} denotes spatial position."
        target = "其中 {v0} denotes 空间位置。"

        self.assertTrue(has_suspicious_english_residue(source, target))

    def test_inflected_grammar_glue_inside_chinese_is_rejected(self) -> None:
        cases = (
            ("denote", "该符号denote物理位置"),
            ("denoted", "该符号denoted物理位置"),
            ("represent", "该变量represent能量"),
            ("represents", "该变量represents能量"),
            ("represented", "该变量represented能量"),
            ("correspond", "这些模式correspond本征态"),
            ("corresponds", "该模式corresponds本征态"),
            ("corresponded", "该模式corresponded本征态"),
            ("respectively", "两者respectively对应这些值"),
            ("where", "其中where变量保持不变"),
        )
        for word, target in cases:
            with self.subTest(word=word):
                source = f"The source uses {word} in this clause."
                self.assertTrue(has_suspicious_english_residue(source, target))

    def test_copula_before_preserved_term_inside_chinese_is_rejected(self) -> None:
        source = (
            "The cavity field {v0} is evaluated at the transmon junction."
        )
        target = "腔体电场{v0} is transmon 结处的电场。"

        self.assertTrue(has_suspicious_english_residue(source, target))

    def test_so_called_modifier_attached_to_chinese_is_rejected(self) -> None:
        source = "The total dispersive shift is the so-called cross-Kerr term."
        target = "总色散频移是so-called交叉克尔项。"

        self.assertTrue(has_suspicious_english_residue(source, target))

    def test_short_grammar_gate_preserves_non_prose_and_names(self) -> None:
        cases = (
            (
                "The expression $the field is transmon localized$ is useful.",
                "表达式$the field is transmon localized$很有用。",
            ),
            (
                "The URL https://example.test/is/so-called is available.",
                "链接为https://example.test/is/so-called。",
            ),
            (
                "The record doi:10.1000/is.so-called is available.",
                "记录见doi:10.1000/is.so-called。",
            ),
            (
                "The device uses the This Is Transmon controller.",
                "该器件采用This Is Transmon控制器。",
            ),
            (
                "The transmon junction sets the response.",
                "transmon结决定响应。",
            ),
            (
                "The PYEPR model is open source.",
                "PYEPR模型已经开源。",
            ),
        )
        for source, target in cases:
            with self.subTest(target=target):
                self.assertFalse(has_suspicious_english_residue(source, target))

    def test_grammar_word_absent_from_source_is_not_rejected(self) -> None:
        source = "The parameter gives the spatial position."
        target = "该参数 denotes 空间位置。"

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_capitalized_names_products_and_journals_are_not_rejected(self) -> None:
        source = "Where toolkit was created by Represent Inc. for Nature Physics."
        target = "我们使用 Where 工具包、Represent Inc. 产品和 Nature Physics 期刊。"

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_formula_placeholders_and_internal_markers_do_not_count(self) -> None:
        source = (
            "{v0} {{v1}} [[PDF2ZH_FLOW_0]] "
            "[[PDF2ZH_ITALIC_3_BEGIN]] [[PDF2ZH_ITALIC_3_END]] "
            "[[PDF2ZH_REF_BOUNDARY_2]]"
        )

        self.assertFalse(has_suspicious_english_residue(source, source))

    def test_formula_context_with_many_english_words_does_not_count(self) -> None:
        source = (
            r"The value $the sum of all energy levels in the resonator$ is fixed "
            r"by \(the product of six English words here\)."
        )
        target = (
            r"该值 $the sum of all energy levels in the resonator$ 由"
            r"\(the product of six English words here\)确定。"
        )

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_display_formula_context_does_not_count(self) -> None:
        source = r"$$the sum of all the energy levels in a resonator$$"
        target = r"$$the sum of all the energy levels in a resonator$$"

        self.assertFalse(has_suspicious_english_residue(source, target))

    def test_identifiers_and_code_do_not_count(self) -> None:
        source = (
            "https://example.org/the/long/path doi:10.1038/s41534-021-00461-8 "
            "author.name@example.edu `the result is returned by this function`"
        )

        self.assertFalse(has_suspicious_english_residue(source, source))

    def test_short_unchanged_sentence_is_not_rejected(self) -> None:
        source = "Results and discussion"

        self.assertFalse(has_suspicious_english_residue(source, source))

    def test_four_word_technical_phrase_without_function_word_is_accepted(self) -> None:
        source = "Josephson energy participation ratio"

        self.assertFalse(has_suspicious_english_residue(source, source))


class UnchangedRoleTranslationTests(unittest.TestCase):
    def test_short_unchanged_headings_and_technical_phrases_are_rejected(
        self,
    ) -> None:
        for source in (
            "Results and discussion",
            "Josephson energy participation ratio",
            "Quantum circuits",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    has_unchanged_translatable_english(source, source)
                )


class ReferenceTitleResidueTests(unittest.TestCase):
    def test_named_product_can_remain_before_a_translated_explanation(self) -> None:
        source = (
            "Qiskit metal: an open-source framework for quantum device "
            "design & analysis (Q-EDA)"
        )
        target = "Qiskit metal：用于量子器件设计与分析（Q-EDA）的开源框架"

        self.assertFalse(has_suspicious_reference_title_residue(source, target))
        self.assertFalse(
            has_unchanged_reference_title_fragment(
                "Qiskit metal",
                "Qiskit metal",
                title_source=source,
                title_target=target,
            )
        )

    def test_stylized_product_rule_is_not_specific_to_one_brand(self) -> None:
        self.assertFalse(
            has_suspicious_reference_title_residue(
                "TensorLab core: a toolkit for tensor network analysis",
                "TensorLab core：用于张量网络分析的工具包",
            )
        )

    def test_ordinary_english_title_prefix_and_full_copy_are_rejected(self) -> None:
        source = "Quantum circuit method: an open-source analysis framework"
        target = "Quantum circuit method：一种开源分析框架"

        self.assertTrue(has_suspicious_reference_title_residue(source, target))
        self.assertTrue(has_suspicious_reference_title_residue(source, source))

    def test_partial_source_clause_inside_chinese_title_is_rejected(self) -> None:
        source = "A scalable robust quantum circuit method"
        target = "可扩展且稳健的 quantum circuit method"

        self.assertTrue(
            has_suspicious_reference_title_residue(source, target)
        )

    def test_two_word_partial_source_clause_is_rejected(self) -> None:
        source = "A scalable method for quantum circuits"
        target = "可扩展方法用于 quantum circuits"

        self.assertTrue(
            has_suspicious_reference_title_residue(source, target)
        )

    def test_fully_translated_title_is_accepted(self) -> None:
        self.assertFalse(
            has_suspicious_reference_title_residue(
                "A scalable robust quantum circuit method",
                "可扩展且稳健的量子电路方法",
            )
        )

    def test_named_product_with_acronym_can_remain(self) -> None:
        self.assertFalse(
            has_suspicious_reference_title_residue(
                "Control with Google Quantum AI processors",
                "使用 Google Quantum AI 处理器进行控制",
            )
        )

    def test_single_lowercase_source_word_left_in_chinese_title_is_rejected(
        self,
    ) -> None:
        self.assertTrue(
            has_suspicious_reference_title_residue(
                "A scalable robust quantum circuit method",
                "可扩展且稳健的量子电路 method",
            )
        )

    def test_numeric_scientific_unit_can_remain_in_chinese_title(self) -> None:
        self.assertFalse(
            has_suspicious_reference_title_residue(
                "A qubit with a coherence time approaching 0.1 ms",
                "相干时间接近 0.1 ms 的量子比特",
            )
        )
        self.assertTrue(
            has_suspicious_reference_title_residue(
                "A scalable robust quantum circuit method",
                "可扩展且稳健的量子电路 method",
            )
        )

    def test_physical_fragment_identity_preserves_names_and_identifiers(
        self,
    ) -> None:
        self.assertTrue(has_unchanged_reference_title_fragment("method", "method"))
        for value in ("Josephson", "QED", "R9C1"):
            with self.subTest(value=value):
                self.assertFalse(
                    has_unchanged_reference_title_fragment(value, value)
                )

    def test_a_real_translation_is_not_rejected(self) -> None:
        self.assertFalse(
            has_unchanged_translatable_english(
                "Quantum {v0} circuits",
                "量子{v0}电路",
            )
        )

    def test_read_only_content_does_not_create_visible_english(self) -> None:
        source = (
            "{v0} [[PDF2ZH_FLOW_0]] [[PDF2ZH_ITALIC_3_BEGIN]] "
            "[[PDF2ZH_ITALIC_3_END]] [[PDF2ZH_REF_BOUNDARY_2]] "
            "https://example.org/English doi:10.1038/s41534-021-00461-8 "
            "author.name@example.edu `English code words`"
        )

        self.assertFalse(has_unchanged_translatable_english(source, source))

    def test_pure_abbreviations_and_model_identifiers_are_exempt(self) -> None:
        for source in (
            "PYEPR",
            "QED EPR",
            "NASA",
            "IEEE",
            "R9C1",
            "gpt-5.6-sol",
        ):
            with self.subTest(source=source):
                self.assertFalse(
                    has_unchanged_translatable_english(source, source)
                )

    def test_all_caps_headings_are_not_mistaken_for_abbreviations(self) -> None:
        for source in (
            "RESULTS AND DISCUSSION",
            "QUANTUM CIRCUITS",
            "TABLE 3",
        ):
            with self.subTest(source=source):
                self.assertTrue(
                    has_unchanged_translatable_english(source, source)
                )


if __name__ == "__main__":
    unittest.main()
