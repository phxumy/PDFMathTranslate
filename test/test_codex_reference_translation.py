from __future__ import annotations

import unittest

from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translator import CodexTranslator


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


def translator_stub() -> CodexTranslator:
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh-cn"
    translator.ignore_cache = False
    translator.cache = MemoryCache()
    return translator


class CodexReferenceTranslationTests(unittest.TestCase):
    def test_named_product_with_translated_explanation_is_cached(self) -> None:
        translator = translator_stub()
        title = (
            "Qiskit metal: an open-source framework for quantum device "
            "design & analysis (Q-EDA)"
        )
        translated = "Qiskit metal：用于量子器件设计与分析（Q-EDA）的开源框架"
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(title, translated)]
        ]

        result = translator.translate_reference_entries([entry])[0]

        self.assertIn(translated, result)
        self.assertEqual(len(translator.cache.values), 1)

    def test_lowercase_qubit_family_name_may_be_preserved(self) -> None:
        translator = translator_stub()
        title = (
            "Implementation of a transmon qubit using superconducting "
            "granular aluminum"
        )
        entry = (
            f"105. Winkel, P. et al. {title}. "
            "Phys. Rev. X 10, 031032 (2020). "
        )
        translated = "使用超导颗粒铝实现 transmon 量子比特"
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(title, translated)]
        ]

        result = translator.translate_reference_entries([entry])[0]

        self.assertIn(translated, result)
        self.assertEqual(len(translator.cache.values), 1)

    def test_ordinary_lowercase_qubit_modifier_is_not_exempt(self) -> None:
        translator = translator_stub()
        title = "Implementation of a quantum qubit architecture"
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        calls = 0

        def partial(entries):
            nonlocal calls
            calls += 1
            return [
                [ExactReplacement(title, "实现 quantum 量子比特架构")]
            ]

        translator._run_reference_title_batch = partial

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.values, {})

    def test_unchanged_english_work_title_is_not_cached(self) -> None:
        translator = translator_stub()
        entry = "[1] A. Smith, Quantum circuits. Nature 1, 10 (2020)."
        title = "Quantum circuits"
        calls = 0

        def unchanged(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(title, title)]]

        translator._run_reference_title_batch = unchanged

        result = translator.translate_reference_entries([entry])[0]

        self.assertEqual(result, entry)
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.values, {})

    def test_partially_translated_work_title_is_retried_without_cache(self) -> None:
        translator = translator_stub()
        title = "A scalable robust quantum circuit method"
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        calls = 0

        def partial(entries):
            nonlocal calls
            calls += 1
            return [
                [ExactReplacement(title, "可扩展且稳健的 quantum circuit method")]
            ]

        translator._run_reference_title_batch = partial

        result = translator.translate_reference_entries([entry])[0]

        self.assertEqual(result, entry)
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.values, {})

    def test_single_lowercase_word_residue_is_retried_without_cache(self) -> None:
        translator = translator_stub()
        title = "A scalable robust quantum circuit method"
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        calls = 0

        def partial(entries):
            nonlocal calls
            calls += 1
            return [
                [ExactReplacement(title, "可扩展且稳健的量子电路 method")]
            ]

        translator._run_reference_title_batch = partial

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.values, {})

    def test_truncated_title_source_span_is_retried_without_cache(self) -> None:
        title = "A scalable robust quantum circuit method"
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        cases = (
            "A scalable robust",
            "robust quantum circuit method",
            "robust quantum",
        )
        for selected in cases:
            with self.subTest(selected=selected):
                translator = translator_stub()
                calls = 0

                def truncated(entries):
                    nonlocal calls
                    calls += 1
                    return [[ExactReplacement(selected, "经翻译的标题")]]

                translator._run_reference_title_batch = truncated

                self.assertEqual(
                    translator.translate_reference_entries([entry]),
                    [entry],
                )
                self.assertEqual(calls, 2)
                self.assertEqual(translator.cache.values, {})

    def test_first_sentence_of_multisentence_title_is_not_a_complete_span(
        self,
    ) -> None:
        translator = translator_stub()
        title = "A scalable robust. Quantum circuit method"
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        selected = "A scalable robust"
        calls = 0

        def truncated(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(selected, "可扩展且稳健。")]]

        translator._run_reference_title_batch = truncated

        self.assertEqual(
            translator.translate_reference_entries([entry]),
            [entry],
        )
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.values, {})

    def test_colon_title_requires_both_main_title_and_subtitle(self) -> None:
        title = "Energy participation ratio: A general framework for circuits"
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        main = "Energy participation ratio"
        subtitle = "A general framework for circuits"
        cases = (
            [ExactReplacement(main, "能量参与比")],
            [ExactReplacement(subtitle, "电路的通用框架")],
        )
        for replacements in cases:
            with self.subTest(replacements=replacements):
                translator = translator_stub()
                translator._run_reference_title_batch = lambda entries: [
                    replacements
                ]

                self.assertEqual(
                    translator.translate_reference_entries([entry]),
                    [entry],
                )
                self.assertEqual(translator.cache.values, {})

        translator = translator_stub()
        translator._run_reference_title_batch = lambda entries: [
            [
                ExactReplacement(main, "能量参与比"),
                ExactReplacement(subtitle, "电路的通用框架"),
            ]
        ]

        self.assertEqual(
            translator.translate_reference_entries([entry]),
            ["[1] A. Smith, 能量参与比: 电路的通用框架. Nature 1, 10 (2020)."],
        )

    def test_multiple_work_titles_are_the_only_changed_spans(self) -> None:
        translator = translator_stub()
        entry = (
            "[1] A. Smith, First work title; Second work title. "
            "Physical Review A 12, 34–56 (2020). doi:10.1000/example"
        )
        translator._run_reference_title_batch = lambda entries: [
            [
                ExactReplacement("First work title", "第一项作品题名"),
                ExactReplacement("Second work title", "第二项作品题名"),
            ]
        ]

        result = translator.translate_reference_entries([entry])[0]

        self.assertEqual(
            result,
            "[1] A. Smith, 第一项作品题名; 第二项作品题名. "
            "Physical Review A 12, 34–56 (2020). doi:10.1000/example",
        )

    def test_et_al_author_terminator_and_common_metadata_tails_are_safe(
        self,
    ) -> None:
        cases = (
            (
                "[1] A. Smith et al. A complete work title. "
                "Nature 1, 10 (2020).",
                "A complete work title",
            ),
            (
                "[2] A. Smith, A complete work title. "
                "Appl. Phys. Lett. 101, 022601 (2012).",
                "A complete work title",
            ),
            (
                "[3] A. Smith, A complete work title. "
                "Supercond. Sci. Technol. 29, 044001 (2016).",
                "A complete work title",
            ),
            (
                "[4] A. Smith, A complete work title. "
                "J. Vacuum Sci. Technol. B 30, 010607 (2012).",
                "A complete work title",
            ),
            (
                "[5] A. Smith, A complete work title. "
                "N. J. Phys. 22, 013025 (2020).",
                "A complete work title",
            ),
            (
                "[6] A. Smith, A complete work title. "
                "Preprint at https://example.test/work (2020).",
                "A complete work title",
            ),
            (
                "[7] A. Smith, A complete work title. "
                "PhD thesis, Example Univ. (2020).",
                "A complete work title",
            ),
        )
        for entry, title in cases:
            with self.subTest(entry=entry):
                translator = translator_stub()
                translator._run_reference_title_batch = lambda entries, title=title: [
                    [ExactReplacement(title, "完整作品题名")]
                ]

                result = translator.translate_reference_entries([entry])[0]

                self.assertIn("完整作品题名", result)
                self.assertNotIn(title, result)

    def test_full_single_word_journal_name_is_a_safe_metadata_tail(self) -> None:
        translator = translator_stub()
        title = (
            "Junction fabrication by shadow evaporation without a sus- "
            "pended bridge"
        )
        entry = (
            f"107. Lecocq, F. et al. {title}. "
            "Nanotechnology 22, 315302 (2011). "
        )
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(title, "无需悬桥的阴影蒸发法结制备")]
        ]

        result = translator.translate_reference_entries([entry])[0]

        self.assertIn("无需悬桥的阴影蒸发法结制备", result)
        self.assertEqual(len(translator.cache.values), 1)

    def test_arbitrary_single_word_title_tail_is_not_a_venue(self) -> None:
        translator = translator_stub()
        entry = (
            "[1] A. Smith, A scalable framework. Method 12, 34 (2020)."
        )
        calls = 0

        def truncated(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement("A scalable framework", "可扩展框架")]]

        translator._run_reference_title_batch = truncated

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.values, {})

    def test_title_sentence_before_abbreviated_venue_is_not_a_safe_tail(
        self,
    ) -> None:
        translator = translator_stub()
        entry = (
            "[1] A. Smith, A scalable framework. Quantum circuit method. "
            "Appl. Phys. Lett. 101, 022601 (2012)."
        )
        selected = "A scalable framework"
        calls = 0

        def truncated(entries):
            nonlocal calls
            calls += 1
            return [[ExactReplacement(selected, "可扩展框架")]]

        translator._run_reference_title_batch = truncated

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(calls, 2)
        self.assertEqual(translator.cache.values, {})

    def test_et_al_inside_a_title_is_not_mistaken_for_an_author_prefix(
        self,
    ) -> None:
        translator = translator_stub()
        entry = (
            "[1] A. Smith, A study of Smith et al. Follow-up title. "
            "Nature 1, 10 (2020)."
        )
        selected = "Follow-up title"
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(selected, "后续题名")]
        ]

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(translator.cache.values, {})

    def test_title_case_prose_plus_volume_is_not_a_generic_venue(self) -> None:
        translator = translator_stub()
        entry = (
            "[1] A. Smith, A scalable framework. "
            "Quantum Circuit Method 12, 34 (2020)."
        )
        selected = "A scalable framework"
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(selected, "可扩展框架")]
        ]

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(translator.cache.values, {})

    def test_journal_name_selected_as_title_is_rejected(self) -> None:
        translator = translator_stub()
        entry = (
            "[1] A. Smith, A useful article title. "
            "Physical Review Letters 12, 34 (2020)."
        )
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement("Physical Review Letters", "物理评论快报")]
        ]

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])

    def test_no_title_and_unsafe_placeholders_preserve_entries(self) -> None:
        translator = translator_stub()
        no_title = "[1] A. Smith, Phys. Rev. A 12, 34 (2020)."
        with_formula = (
            "[2] B. Jones, Control of {v0} in a resonator. "
            "Nature Physics 2, 20 (2021)."
        )
        translator._run_reference_title_batch = lambda entries: [
            [],
            [ExactReplacement("Control of {v0} in a resonator", "谐振器控制")],
        ]

        self.assertEqual(
            translator.translate_reference_entries([no_title, with_formula]),
            [no_title, with_formula],
        )

    def test_reference_entry_is_not_split_at_generic_item_limit(self) -> None:
        translator = translator_stub()
        title = "A " + ("very long scientific title segment " * 14).strip()
        entry = f"[1] A. Smith, {title}. Nature 1, 10 (2020)."
        seen_batches: list[list[str]] = []

        def fake_reference_batch(entries: list[str]):
            seen_batches.append(list(entries))
            return [[ExactReplacement(title, "一个很长的科研作品题名")]]

        translator._run_reference_title_batch = fake_reference_batch
        result = translator.translate_reference_entries([entry])[0]

        self.assertGreater(len(entry), translator.MAX_ITEM_CHARS)
        self.assertEqual(seen_batches, [[entry]])
        self.assertIn("一个很长的科研作品题名", result)

    def test_formula_context_is_part_of_the_reference_cache_key(self) -> None:
        translator = translator_stub()
        entry = (
            "{v0} A. Smith, A title with a protected marker. "
            "Nature 1, 10 (2020)."
        )

        first = translator._reference_cache_key(entry, "{v0}=1")
        second = translator._reference_cache_key(entry, "{v0}=57")

        self.assertNotEqual(first, second)
        with self.assertRaises(ValueError):
            translator.translate_reference_entries(
                [entry], cache_contexts=["one", "extra"]
            )

    def test_empty_replacements_are_not_cached(self) -> None:
        translator = translator_stub()
        entry = "[1] A. Smith, An uncertain title. Nature 1, 10 (2020)."
        call_count = 0

        def empty_batch(entries: list[str]):
            nonlocal call_count
            call_count += 1
            return [[] for _ in entries]

        translator._run_reference_title_batch = empty_batch

        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(translator.translate_reference_entries([entry]), [entry])
        self.assertEqual(call_count, 2)
        self.assertEqual(translator.cache.values, {})

    def test_legacy_noop_cache_entry_is_ignored(self) -> None:
        translator = translator_stub()
        entry = "[1] A. Smith, A recoverable title. Nature 1, 10 (2020)."
        key = translator._reference_cache_key(entry)
        translator.cache.set(key, entry)
        call_count = 0

        def translated_batch(entries: list[str]):
            nonlocal call_count
            call_count += 1
            return [[ExactReplacement("A recoverable title", "可恢复的题名")]]

        translator._run_reference_title_batch = translated_batch

        self.assertIn(
            "可恢复的题名",
            translator.translate_reference_entries([entry])[0],
        )
        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()
