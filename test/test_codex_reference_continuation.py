from __future__ import annotations

import unittest

from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translator import CodexTranslator


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value
        self.set_calls.append((key, value))


def translator_stub() -> CodexTranslator:
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_in = "en"
    translator.lang_out = "zh-cn"
    translator.ignore_cache = False
    translator.cache = MemoryCache()
    return translator


class CodexReferenceContinuationTests(unittest.TestCase):
    def test_named_product_fragment_can_remain_when_explanation_is_translated(
        self,
    ) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Author. Qiskit metal"
        right = (
            ": an open-source framework for quantum device design & analysis "
            "(Q-EDA). Nature 1, 2 (2020)."
        )
        source = (
            f"Qiskit metal{boundary}: an open-source framework for quantum "
            "device design & analysis (Q-EDA)"
        )
        target = f"Qiskit metal{boundary}：用于量子器件设计与分析" "（Q-EDA）的开源框架"
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(source, target)]
        ]

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertEqual(
            result,
            (
                ExactReplacement("Qiskit metal", "Qiskit metal"),
                ExactReplacement(
                    ": an open-source framework for quantum device design & "
                    "analysis (Q-EDA)",
                    "：用于量子器件设计与分析（Q-EDA）的开源框架",
                ),
            ),
        )
        self.assertEqual(len(translator.cache.set_calls), 1)

    def test_ordinary_english_prefix_is_not_a_cross_page_product_name(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Author. Quantum circuit method"
        right = ": an open-source analysis framework. Nature 1, 2 (2020)."
        source = f"Quantum circuit method{boundary}: an open-source analysis framework"
        target = f"Quantum circuit method{boundary}：一种开源分析框架"
        calls = 0

        def partial(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(source, target)]]

        translator._run_reference_title_batch = partial

        self.assertIsNone(
            translator.translate_reference_continuation_fragments(left, right)
        )
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.set_calls, [])

    def test_short_chinese_page_tail_is_moved_to_the_following_page(self) -> None:
        translator = translator_stub()

        result = translator._rebalance_reference_continuation(
            (
                ExactReplacement("low", "中低"),
                ExactReplacement("anharmonicity", "非谐性量子比特"),
            )
        )

        self.assertEqual(
            result,
            (
                ExactReplacement("low", ""),
                ExactReplacement("anharmonicity", "中低非谐性量子比特"),
            ),
        )

    def test_long_left_title_moves_only_its_unfinished_modifier_suffix(
        self,
    ) -> None:
        translator = translator_stub()

        result = translator._rebalance_reference_continuation(
            (
                ExactReplacement(
                    "effective Hamiltonians of low",
                    "有效哈密顿量中低",
                ),
                ExactReplacement(
                    "anharmonicity superconducting qubits",
                    "非谐性超导量子比特",
                ),
            )
        )

        self.assertEqual(
            result,
            (
                ExactReplacement("effective Hamiltonians of low", "有效哈密顿量"),
                ExactReplacement(
                    "anharmonicity superconducting qubits",
                    "中低非谐性超导量子比特",
                ),
            ),
        )

    def test_complete_left_word_or_punctuation_is_not_moved(self) -> None:
        translator = translator_stub()
        right = ExactReplacement("following phrase", "后续标题")
        for translated in ("完整标题", "有效模型低。", "QED低"):
            with self.subTest(translated=translated):
                replacements = (
                    ExactReplacement("complete phrase", translated),
                    right,
                )
                self.assertEqual(
                    translator._rebalance_reference_continuation(replacements),
                    replacements,
                )

    def test_internal_boundary_token_is_never_moved_by_rebalancing(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        replacements = (
            ExactReplacement("phrase ending low", f"有效模型{boundary}低"),
            ExactReplacement("following phrase", "后续标题"),
        )

        self.assertEqual(
            translator._rebalance_reference_continuation(replacements),
            replacements,
        )

    def test_long_or_non_chinese_page_tail_is_not_rebalanced(self) -> None:
        translator = translator_stub()
        right = ExactReplacement("right", "后续标题")
        for translated in ("五个中文字", "low"):
            with self.subTest(translated=translated):
                replacements = (
                    ExactReplacement("left", translated),
                    right,
                )
                self.assertEqual(
                    translator._rebalance_reference_continuation(replacements),
                    replacements,
                )

    def test_cross_boundary_title_is_split_into_exact_physical_replacements(
        self,
    ) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = (
            "17. A. Researcher, Simple impedance response formulas for the "
            "dispersive interaction rates in the effective Hamiltonians of low"
        )
        right = (
            "anharmonicity superconducting qubits. IEEE Trans. Microwave Theory "
            "Tech. 67, 928–948 (2019)."
        )
        source_title = (
            "Simple impedance response formulas for the dispersive interaction "
            f"rates in the effective Hamiltonians of low{boundary}"
            "anharmonicity superconducting qubits"
        )
        translated_title = f"低{boundary}非谐性超导量子比特的色散相互作用率公式"
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(source_title, translated_title)]
        ]

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertEqual(
            result,
            (
                ExactReplacement(
                    "Simple impedance response formulas for the dispersive "
                    "interaction rates in the effective Hamiltonians of low",
                    "",
                ),
                ExactReplacement(
                    "anharmonicity superconducting qubits",
                    "低非谐性超导量子比特的色散相互作用率公式",
                ),
            ),
        )
        self.assertEqual(len(translator.cache.set_calls), 1)

    def test_boundary_damage_rejects_the_whole_group_without_cache(self) -> None:
        translator = translator_stub()
        left = "17. A. Researcher, A title ending with low"
        right = "anharmonicity qubits. Nature 1, 2 (2020)."
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement("A title ending with low", "一个中文标题")]
        ]

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertIsNone(result)
        self.assertEqual(translator.cache.set_calls, [])

    def test_unchanged_english_title_is_rejected_without_cache(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Researcher, The title is split across the"
        right = "physical page boundary. Nature 1, 2 (2020)."
        title = f"The title is split across the{boundary}physical page boundary"
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(title, title)]
        ]

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertIsNone(result)
        self.assertEqual(translator.cache.set_calls, [])

    def test_partially_translated_title_is_retried_without_cache(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Author. A scalable robust "
        right = "quantum circuit method. Nature 1, 2 (2020)."
        source = f"A scalable robust {boundary}quantum circuit method"
        target = f"可扩展且稳健的{boundary}quantum circuit method"
        calls = 0

        def partial(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(source, target)]]

        translator._run_reference_title_batch = partial

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertIsNone(result)
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.set_calls, [])

    def test_single_lowercase_right_fragment_is_retried_without_cache(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Author. A scalable robust quantum circuit "
        right = "method. Nature 1, 2 (2020)."
        source = f"A scalable robust quantum circuit {boundary}method"
        target = f"可扩展且稳健的量子电路{boundary}method"
        calls = 0

        def partial(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(source, target)]]

        translator._run_reference_title_batch = partial

        self.assertIsNone(
            translator.translate_reference_continuation_fragments(left, right)
        )
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.set_calls, [])

    def test_single_lowercase_left_fragment_is_retried_without_cache(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Author. method"
        right = "for quantum circuits. Nature 1, 2 (2020)."
        source = f"method{boundary}for quantum circuits"
        target = f"method{boundary}用于量子电路"
        calls = 0

        def partial(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(source, target)]]

        translator._run_reference_title_batch = partial

        self.assertIsNone(
            translator.translate_reference_continuation_fragments(left, right)
        )
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.set_calls, [])

    def test_partial_legacy_cache_is_ignored_and_replaced_atomically(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Author. A scalable robust "
        right = "quantum circuit method. Nature 1, 2 (2020)."
        key = translator._reference_continuation_cache_key(left, right)
        translator.cache.values[key] = (
            '{"source_left":"A scalable robust ",'
            '"source_right":"quantum circuit method",'
            '"target_left":"可扩展且稳健的",'
            '"target_right":"quantum circuit method"}'
        )
        source = f"A scalable robust {boundary}quantum circuit method"
        target = f"可扩展且稳健的{boundary}量子电路方法"
        calls = 0

        def complete(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(source, target)]]

        translator._run_reference_title_batch = complete

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertEqual(
            result,
            (
                ExactReplacement("A scalable robust ", "可扩展且稳健的"),
                ExactReplacement("quantum circuit method", "量子电路方法"),
            ),
        )
        self.assertEqual(calls, 1)
        self.assertEqual(len(translator.cache.set_calls), 1)

    def test_truncated_cross_boundary_source_span_is_never_cached(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Author. A scalable robust "
        right = "quantum circuit method. Nature 1, 2 (2020)."
        source = f"robust {boundary}quantum"
        target = f"稳健的{boundary}量子"
        calls = 0

        def truncated(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(source, target)]]

        translator._run_reference_title_batch = truncated

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertIsNone(result)
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.set_calls, [])

    def test_formula_placeholder_fails_closed_before_model_call(self) -> None:
        translator = translator_stub()

        result = translator.translate_reference_continuation_fragments(
            "17. A. Researcher, A title with {v0}",
            "continued here. Nature 1, 2 (2020).",
        )

        self.assertIsNone(result)
        self.assertEqual(translator.cache.set_calls, [])

    def test_boundary_moved_to_either_title_edge_is_rejected(self) -> None:
        translator = translator_stub()
        boundary = translator.REFERENCE_BOUNDARY_TOKEN
        left = "17. A. Researcher, The title is split across the"
        right = "physical page boundary. Nature 1, 2 (2020)."
        source = f"The title is split across the{boundary}physical page boundary"
        for translated in (f"{boundary}中文标题", f"中文标题{boundary}"):
            with self.subTest(translated=translated):
                translator.cache.values.clear()
                translator.cache.set_calls.clear()
                translator._run_reference_title_batch = lambda entries: [
                    [ExactReplacement(source, translated)]
                ]

                result = translator.translate_reference_continuation_fragments(
                    left,
                    right,
                )

                self.assertIsNone(result)
                self.assertEqual(translator.cache.set_calls, [])

    def test_cached_sources_must_touch_the_physical_boundary(self) -> None:
        translator = translator_stub()
        left = "17. A. Researcher, A title ending with low"
        right = "anharmonicity qubits. Nature 1, 2 (2020)."
        key = translator._reference_continuation_cache_key(left, right)
        translator.cache.values[key] = (
            '{"source_left":"A. Researcher","source_right":"Nature",'
            '"target_left":"研究者","target_right":"自然"}'
        )
        translator._run_reference_title_batch = lambda entries: [None]

        result = translator.translate_reference_continuation_fragments(left, right)

        self.assertIsNone(result)
        self.assertEqual(translator.cache.set_calls, [])


if __name__ == "__main__":
    unittest.main()
