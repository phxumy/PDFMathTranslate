from __future__ import annotations

import json
import unittest

from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translator import CodexTranslator


class MemoryCache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


def translator_stub():
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh-cn"
    translator.ignore_cache = False
    translator.cache = MemoryCache()
    return translator


def payload(source: str, translated: str) -> str:
    return json.dumps(
        {
            "format": "reference-title-replacements-v1",
            "replacements": [{"source": source, "translated": translated}],
        },
        ensure_ascii=False,
    )


class CodexReferenceCacheTests(unittest.TestCase):
    def test_valid_legacy_complete_title_caches_are_revalidated_and_reused(self):
        title = "A complete scientific work"
        for entry in (
            f"[1] J. Smith, “{title},” in Nature 1, 10 (2020).",
            f"[1] J. Smith, [[PDF2ZH_ITALIC_7_BEGIN]]{title}"
            "[[PDF2ZH_ITALIC_7_END]]. Academic Press, 2020.",
            f"[1] Jane Smith. {title}. Nature 1, 10 (2020).",
        ):
            with self.subTest(entry=entry):
                translator = translator_stub()
                cached = entry.replace(title, "完整的科研作品")
                translator.cache.set(translator._reference_cache_key(entry), cached)
                translator._run_reference_title_batch = lambda entries: self.fail(
                    "valid legacy cache should not call the model"
                )
                self.assertEqual(
                    translator.translate_reference_entries([entry]), [cached]
                )

    def test_invalid_legacy_titles_cannot_bypass_current_quality_checks(self):
        for source, unsafe, safe in (
            (
                "A scalable robust quantum circuit method",
                "可扩展的 robust 量子电路方法",
                "可扩展的稳健量子电路方法",
            ),
            (
                "AI Feynman 2.0: A method for symbolic regression",
                "AI Feynman：一种符号回归方法",
                "AI Feynman 2.0：一种符号回归方法",
            ),
            (
                "Interpretable machine learning with SymbolicRegression.jl",
                "使用 SymbolicRegression 的可解释机器学习",
                "使用 SymbolicRegression.jl 的可解释机器学习",
            ),
        ):
            with self.subTest(source=source):
                translator = translator_stub()
                entry = f"[1] J. Smith, “{source},” in Nature 1, 10 (2020)."
                legacy_key = translator._reference_cache_key(entry)
                translator.cache.set(legacy_key, entry.replace(source, unsafe))
                calls = []

                def translate(entries):
                    calls.append(entries)
                    return [[ExactReplacement(source, safe)]]

                translator._run_reference_title_batch = translate
                expected = entry.replace(source, safe)
                self.assertEqual(
                    translator.translate_reference_entries([entry]), [expected]
                )
                self.assertEqual(
                    translator.translate_reference_entries([entry]), [expected]
                )
                self.assertEqual(len(calls), 1)
                # Structured records must not appear at an older app's key.
                self.assertEqual(
                    translator.cache.get(legacy_key), entry.replace(source, unsafe)
                )

    def test_legacy_author_and_venue_changes_are_not_reused(self):
        title = "A complete scientific work"
        entry = f"[1] Jane Smith. {title}. Nature 1, 10 (2020)."
        translated = entry.replace(title, "完整的科研作品")
        for cached in (
            translated.replace("Jane Smith", "简史密斯"),
            translated.replace("Nature", "自然"),
            translated.replace("2020", "2021"),
        ):
            with self.subTest(cached=cached):
                translator = translator_stub()
                translator.cache.set(translator._reference_cache_key(entry), cached)
                translator._run_reference_title_batch = lambda entries: [[]]
                self.assertEqual(
                    translator.translate_reference_entries([entry]), [entry]
                )

    def test_legacy_numeric_partial_title_is_not_reused(self):
        title = "A scalable framework, 3D integration and its applications"
        entry = f"[1] Jane Smith. {title}. Nature 12, 34 (2020)."
        translator = translator_stub()
        translator.cache.set(
            translator._reference_cache_key(entry),
            entry.replace("A scalable framework", "可扩展框架"),
        )
        translator._run_reference_title_batch = lambda entries: [
            [ExactReplacement(title, "可扩展框架、三维集成及其应用")]
        ]
        self.assertEqual(
            translator.translate_reference_entries([entry]),
            [entry.replace(title, "可扩展框架、三维集成及其应用")],
        )

    def test_discovery_cache_records_full_boundaries_and_reuses_them(self):
        title = "A complete scientific work"
        entry = f"[2] Jane Smith. {title}. Mathematics of {{v2}}."
        context = "{v2}=control,signalsandsystems,2(4):303–314,1989"
        translator = translator_stub()
        self.assertFalse(translator._structured_reference_title_spans(entry))
        expected = entry.replace(title, "完整的科研作品")
        legacy_key = translator._reference_cache_key(entry, context)
        translator.cache.set(legacy_key, expected)
        calls = []

        def translate(entries):
            calls.append(entries)
            return [[ExactReplacement(title, "完整的科研作品")]]

        translator._run_reference_title_batch = translate
        for _ in range(2):
            self.assertEqual(
                translator.translate_reference_entries(
                    [entry], cache_contexts=[context]
                ),
                [expected],
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(translator.cache.get(legacy_key), expected)
        proof = translator.cache.get(
            translator._reference_replacements_cache_key(entry, context)
        )
        self.assertEqual(json.loads(proof)["replacements"][0]["source"], title)
        # A complete font-run catalogue is still required when replaying.
        self.assertIsNone(
            translator._accepted_reference_cache(
                entry, proof, formula_context={"{v2}": "x+y"}
            )
        )

    def test_retry_caches_only_the_final_valid_replacements(self):
        title = "A scalable robust quantum circuit method"
        entry = f"[1] J. Smith, “{title},” in Nature 1, 10 (2020)."
        translator = translator_stub()
        calls = []

        def translate(entries):
            calls.append(entries)
            target = (
                "可扩展的 robust 量子电路方法"
                if len(calls) == 1
                else "可扩展的稳健量子电路方法"
            )
            return [[ExactReplacement(title, target)]]

        translator._run_reference_title_batch = translate
        expected = entry.replace(title, "可扩展的稳健量子电路方法")
        self.assertEqual(translator.translate_reference_entries([entry]), [expected])
        self.assertEqual(translator.translate_reference_entries([entry]), [expected])
        self.assertEqual(len(calls), 2)
        proof = translator.cache.get(
            translator._reference_replacements_cache_key(entry)
        )
        self.assertEqual(
            json.loads(proof)["replacements"][0]["translated"],
            "可扩展的稳健量子电路方法",
        )
        self.assertIsNone(translator.cache.get(translator._reference_cache_key(entry)))

    def test_invalid_structured_records_are_cache_misses(self):
        title = "A scalable robust quantum circuit method"
        entry = f"[1] J. Smith, “{title},” in Nature 1, 10 (2020)."
        for cached in (
            "{broken",
            "[]",
            "null",
            '{"format":"unknown","replacements":[]}',
            '{"format":"reference-title-replacements-v1","replacements":{}}',
            '{"format":"reference-title-replacements-v1","replacements":[null]}',
            '{"format":"reference-title-replacements-v1","replacements":[{"source":7,"translated":"中文"}]}',
            payload(title, ""),
            payload(title, "可扩展的 robust 量子电路方法"),
            payload("A scalable", "可扩展的"),
            payload("Nature", "自然"),
            payload("J. Smith", "简史密斯"),
            payload(title, "量子电路方法 {v99}"),
        ):
            with self.subTest(cached=cached):
                translator = translator_stub()
                translator.cache.set(
                    translator._reference_replacements_cache_key(entry), cached
                )
                translator._run_reference_title_batch = lambda entries: [[]]
                self.assertEqual(
                    translator.translate_reference_entries([entry]), [entry]
                )

    def test_existing_structured_record_is_revalidated_after_quality_rules_change(self):
        source = "AI Feynman 2.0: A method for symbolic regression"
        entry = f"[1] J. Smith, “{source},” in Nature 1, 10 (2020)."
        for unsafe in (
            "AI Feynman：一种符号回归方法",
            "AI Feynman 12.0：一种符号回归方法",
        ):
            with self.subTest(unsafe=unsafe):
                translator = translator_stub()
                translator.cache.set(
                    translator._reference_replacements_cache_key(entry),
                    payload(source, unsafe),
                )
                translator._run_reference_title_batch = lambda entries: [[]]
                self.assertEqual(
                    translator.translate_reference_entries([entry]), [entry]
                )


if __name__ == "__main__":
    unittest.main()
