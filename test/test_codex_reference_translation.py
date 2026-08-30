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
