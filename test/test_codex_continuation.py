from __future__ import annotations

import json
import unittest

from pdf2zh.translator import CodexTranslator


ITALIC_0_BEGIN = "[[PDF2ZH_ITALIC_0_BEGIN]]"
ITALIC_0_END = "[[PDF2ZH_ITALIC_0_END]]"
ITALIC_1_BEGIN = "[[PDF2ZH_ITALIC_1_BEGIN]]"
ITALIC_1_END = "[[PDF2ZH_ITALIC_1_END]]"
FLOW_0 = "[[PDF2ZH_FLOW_0]]"
FLOW_1 = "[[PDF2ZH_FLOW_1]]"


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self.values[key] = value


def translator_stub() -> CodexTranslator:
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_in = "en"
    translator.lang_out = "zh-cn"
    translator.ignore_cache = False
    translator.prompttext = None
    translator.cache = MemoryCache()
    return translator


def rich_sources() -> list[str]:
    return [
        (
            f"The rate {{v0}} is tuned {ITALIC_0_BEGIN}in situ"
            f"{ITALIC_0_END}{FLOW_0}"
        ),
        f"{FLOW_1} and the coupling {{v1}} remains fixed.",
    ]


def rich_targets() -> list[str]:
    return [
        f"速率{{v0}}通过{ITALIC_0_BEGIN}原位{ITALIC_0_END}调谐{FLOW_0}",
        f"{FLOW_1}，耦合{{v1}}保持不变。",
    ]


def rich_contexts() -> list[dict[str, str]]:
    return [{"{v0}": "ω_{c}"}, {"{v1}": "g_{12}"}]


class CodexContinuationTests(unittest.TestCase):
    def _assert_group_rejected(
        self,
        sources: list[str],
        invalid_targets: list[str],
        contexts: list[dict[str, str]] | None = None,
    ) -> None:
        translator = translator_stub()
        calls: list[tuple[list[str], str]] = []

        def request(texts, normalized_contexts, join_kind):
            calls.append((list(texts), join_kind))
            return list(invalid_targets)

        translator._run_continuation_request = request

        result = translator.translate_continuation_fragments(
            sources,
            contexts,
            join_kind="cross-page",
        )

        self.assertIsNone(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(translator.cache.set_calls, [])
        self.assertEqual(translator.cache.values, {})

    def test_successful_group_is_returned_and_cached_as_one_atomic_value(self) -> None:
        translator = translator_stub()
        calls = 0

        def request(texts, normalized_contexts, join_kind):
            nonlocal calls
            calls += 1
            self.assertEqual(texts, rich_sources())
            self.assertEqual(join_kind, "cross-column")
            return rich_targets()

        translator._run_continuation_request = request

        result = translator.translate_continuation_fragments(
            rich_sources(),
            rich_contexts(),
            join_kind="cross-column",
        )

        self.assertEqual(result, rich_targets())
        self.assertEqual(calls, 1)
        self.assertEqual(len(translator.cache.set_calls), 1)
        self.assertEqual(len(translator.cache.values), 1)
        cache_key, cache_value = translator.cache.set_calls[0]
        self.assertTrue(
            cache_key.startswith(CodexTranslator.CONTINUATION_CACHE_PREFIX)
        )
        self.assertEqual(
            json.loads(cache_value),
            {"translations": rich_targets()},
        )

        translator._run_continuation_request = lambda *args: self.fail(
            "a validated continuation cache entry should be reused"
        )
        cached = translator.translate_continuation_fragments(
            rich_sources(),
            rich_contexts(),
            join_kind="cross-column",
        )
        self.assertEqual(cached, rich_targets())
        self.assertEqual(len(translator.cache.set_calls), 1)

    def test_cache_key_is_context_sensitive_and_mode_isolated(self) -> None:
        sources = ["The rate {v0} controls", "the gate response."]
        first_context = [
            CodexTranslator._normalize_formula_context(
                sources[0], {"{v0}": "ω_{c}"}
            ),
            [],
        ]
        changed_context = [
            CodexTranslator._normalize_formula_context(
                sources[0], {"{v0}": "κ"}
            ),
            [],
        ]

        key = CodexTranslator._continuation_cache_key(
            sources, first_context, "cross-page"
        )

        self.assertNotEqual(
            key,
            CodexTranslator._continuation_cache_key(
                sources, changed_context, "cross-page"
            ),
        )
        self.assertNotEqual(
            key,
            CodexTranslator._continuation_cache_key(
                sources, first_context, "cross-column"
            ),
        )
        self.assertNotEqual(
            key,
            CodexTranslator._continuation_cache_key(
                [sources[0], "a different continuation."],
                first_context,
                "cross-page",
            ),
        )
        self.assertNotEqual(
            key,
            CodexTranslator._formula_context_cache_key(
                sources[0], first_context[0]
            ),
        )

    def test_prompt_explains_formula_aware_target_boundary_placement(self) -> None:
        translator = translator_stub()

        prompt = translator._build_continuation_prompt(
            ["the virtual", "exchange interaction via the state {v0}"],
            [[], [{"placeholder": "{v0}", "unicode_formula": "|010⟩"}]],
            "page:normal",
        )

        self.assertIn("经由态{v0}的虚交换相互作用", prompt)
        self.assertIn("protected formula token", prompt)
        self.assertIn("not `进行虚拟`", prompt)

    def test_formula_dictionary_order_does_not_change_cache_identity(self) -> None:
        translator = translator_stub()
        sources = ["Compare {v1} with {v0}", "before the gate closes."]
        targets = ["比较{v1}与{v0}", "，随后关闭该门。"]
        first = [
            {"{v0}": "ω_{c}", "{v1}": "g_{12}"},
            {},
        ]
        reordered = [
            {"{v1}": "g_{12}", "{v0}": "ω_{c}"},
            {},
        ]
        calls = 0

        def request(texts, normalized_contexts, join_kind):
            nonlocal calls
            calls += 1
            return list(targets)

        translator._run_continuation_request = request
        first_result = translator.translate_continuation_fragments(
            sources,
            first,
            join_kind="cross-page",
        )
        second_result = translator.translate_continuation_fragments(
            sources,
            reordered,
            join_kind="cross-page",
        )

        self.assertEqual(first_result, targets)
        self.assertEqual(second_result, targets)
        self.assertEqual(calls, 1)
        self.assertEqual(len(translator.cache.values), 1)

    def test_formula_crossing_or_loss_rejects_the_whole_group(self) -> None:
        sources = ["The rate {v0} controls", "the coupling {v1}."]
        contexts = [{"{v0}": "ω_{c}"}, {"{v1}": "g_{12}"}]
        cases = {
            "crossed": ["速率{v1}控制", "耦合{v0}。"],
            "missing": ["速率控制", "耦合{v1}。"],
        }
        for name, targets in cases.items():
            with self.subTest(name=name):
                self._assert_group_rejected(sources, targets, contexts)

    def test_italic_markers_crossing_or_loss_rejects_the_whole_group(self) -> None:
        sources = [
            f"Tuned {ITALIC_0_BEGIN}in situ{ITALIC_0_END}",
            f"with {ITALIC_1_BEGIN}direct{ITALIC_1_END} coupling.",
        ]
        cases = {
            "crossed": [
                f"采用{ITALIC_1_BEGIN}原位{ITALIC_1_END}调谐",
                f"并使用{ITALIC_0_BEGIN}直接{ITALIC_0_END}耦合。",
            ],
            "missing": [
                f"采用{ITALIC_0_BEGIN}原位调谐",
                f"并使用{ITALIC_1_BEGIN}直接{ITALIC_1_END}耦合。",
            ],
        }
        for name, targets in cases.items():
            with self.subTest(name=name):
                self._assert_group_rejected(sources, targets)

    def test_flow_tokens_crossing_or_loss_rejects_the_whole_group(self) -> None:
        sources = [f"The first clause{FLOW_0}", f"{FLOW_1}continues here."]
        cases = {
            "crossed": [f"第一分句{FLOW_1}", f"{FLOW_0}在此继续。"],
            "missing": ["第一分句", f"{FLOW_1}在此继续。"],
        }
        for name, targets in cases.items():
            with self.subTest(name=name):
                self._assert_group_rejected(sources, targets)

    def test_one_invalid_fragment_prevents_partial_group_cache_writes(self) -> None:
        translator = translator_stub()
        sources = ["The rate {v0} controls", "the gate response."]
        contexts = [{"{v0}": "ω_{c}"}, {}]
        invalid = ["速率控制", "门响应。"]
        observations: list[int] = []

        def request(texts, normalized_contexts, join_kind):
            observations.append(len(translator.cache.set_calls))
            return list(invalid)

        translator._run_continuation_request = request

        result = translator.translate_continuation_fragments(
            sources,
            contexts,
            join_kind="cross-page",
        )

        self.assertIsNone(result)
        self.assertEqual(observations, [0, 0])
        self.assertEqual(translator.cache.set_calls, [])
        self.assertEqual(translator.cache.values, {})

    def test_retry_success_is_the_first_and_only_atomic_cache_write(self) -> None:
        translator = translator_stub()
        sources = ["The rate {v0} controls", "the gate response."]
        contexts = [{"{v0}": "ω_{c}"}, {}]
        valid = ["速率{v0}控制", "门响应。"]
        attempts = iter(
            [
                ["速率控制", "门响应。"],
                valid,
            ]
        )
        observations: list[int] = []

        def request(texts, normalized_contexts, join_kind):
            observations.append(len(translator.cache.set_calls))
            return list(next(attempts))

        translator._run_continuation_request = request

        result = translator.translate_continuation_fragments(
            sources,
            contexts,
            join_kind="cross-page",
        )

        self.assertEqual(result, valid)
        self.assertEqual(observations, [0, 0])
        self.assertEqual(len(translator.cache.set_calls), 1)
        self.assertEqual(len(translator.cache.values), 1)
        cached_payload = json.loads(translator.cache.set_calls[0][1])
        self.assertEqual(cached_payload, {"translations": valid})


if __name__ == "__main__":
    unittest.main()
