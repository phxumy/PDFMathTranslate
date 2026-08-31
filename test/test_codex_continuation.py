from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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

        def request(texts, normalized_contexts, join_kind, **kwargs):
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

        def request(texts, normalized_contexts, join_kind, **kwargs):
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

        translator._run_continuation_request = lambda *args, **kwargs: self.fail(
            "a validated continuation cache entry should be reused"
        )
        cached = translator.translate_continuation_fragments(
            rich_sources(),
            rich_contexts(),
            join_kind="cross-column",
        )
        self.assertEqual(cached, rich_targets())
        self.assertEqual(len(translator.cache.set_calls), 1)

    def test_continuation_cache_repairs_structural_repetitions(self) -> None:
        translator = translator_stub()
        sources = [
            "In the quantum setting, Eq. (5) links",
            "the parameter to the circuit state.",
        ]
        contexts = [{}, {}]
        normalized_contexts = [
            translator._normalize_formula_context(source, context)
            for source, context in zip(sources, contexts, strict=True)
        ]
        key = translator._continuation_cache_key(
            sources,
            normalized_contexts,
            "cross-page",
        )
        translator.cache.set(
            key,
            json.dumps(
                {
                    "translations": [
                        "在量子情形下，式式 (5) 将",
                        "参数与系统的的状态联系起来。",
                    ]
                },
                ensure_ascii=False,
            ),
        )
        translator._run_continuation_request = (
            lambda *args, **kwargs: self.fail(
                "a repairable continuation cache entry should be reused"
            )
        )

        result = translator.translate_continuation_fragments(
            sources,
            contexts,
            join_kind="cross-page",
        )

        self.assertEqual(
            result,
            ["在量子情形下，式 (5) 将", "参数与系统的状态联系起来。"],
        )

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
        expected = ["比较{v1}与{v0}，", "随后关闭该门。"]
        first = [
            {"{v0}": "ω_{c}", "{v1}": "g_{12}"},
            {},
        ]
        reordered = [
            {"{v1}": "g_{12}", "{v0}": "ω_{c}"},
            {},
        ]
        calls = 0

        def request(texts, normalized_contexts, join_kind, **kwargs):
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

        self.assertEqual(first_result, expected)
        self.assertEqual(second_result, expected)
        self.assertEqual(calls, 1)
        self.assertEqual(len(translator.cache.values), 1)

    def test_leading_cjk_punctuation_moves_to_the_previous_fragment(self) -> None:
        translator = translator_stub()
        sources = [
            "The first clause ends at this physical boundary",
            "and the following clause continues on the next page.",
        ]
        targets = ["第一分句在物理边界处结束", "，其中下一分句在后页继续。"]
        translator._run_continuation_request = (
            lambda *args, **kwargs: list(targets)
        )

        result = translator.translate_continuation_fragments(
            sources,
            join_kind="cross-page",
        )

        self.assertEqual(result, ["第一分句在物理边界处结束，", "其中下一分句在后页继续。"])
        cached = json.loads(translator.cache.set_calls[0][1])
        self.assertEqual(cached["translations"], result)

    def test_consecutive_closing_punctuation_moves_as_one_prefix(self) -> None:
        translator = translator_stub()
        sources = [
            "The parenthetical first clause ends here",
            "and then the explanation continues on the next page.",
        ]
        targets = ["括注的第一分句到此结束", "）”，然后说明在后页继续。"]
        translator._run_continuation_request = (
            lambda *args, **kwargs: list(targets)
        )

        result = translator.translate_continuation_fragments(
            sources,
            join_kind="cross-page",
        )

        self.assertEqual(result, ["括注的第一分句到此结束）”，", "然后说明在后页继续。"])

    def test_boundary_rebalance_keeps_all_protected_tokens_in_their_fragments(
        self,
    ) -> None:
        translator = translator_stub()
        sources = [
            f"The rate {{v0}} ends here{FLOW_0}",
            (
                f"{ITALIC_1_BEGIN}Next{ITALIC_1_END}{FLOW_1} continues "
                "with {v1}."
            ),
        ]
        targets = [
            f"速率{{v0}}到此结束{FLOW_0}",
            (
                f"，{ITALIC_1_BEGIN}下一项{ITALIC_1_END}{FLOW_1}"
                "继续包含{v1}。"
            ),
        ]
        translator._run_continuation_request = (
            lambda *args, **kwargs: list(targets)
        )

        result = translator.translate_continuation_fragments(
            sources,
            [{"{v0}": "ω"}, {"{v1}": "g"}],
            join_kind="cross-page",
        )

        self.assertEqual(
            result,
            [
                f"速率{{v0}}到此结束{FLOW_0}，",
                (
                    f"{ITALIC_1_BEGIN}下一项{ITALIC_1_END}{FLOW_1}"
                    "继续包含{v1}。"
                ),
            ],
        )
        self.assertEqual(
            [CodexTranslator._formula_token_sequence(item) for item in result],
            [("{v0}",), ("{v1}",)],
        )
        self.assertIn(FLOW_0, result[0])
        self.assertNotIn(FLOW_0, result[1])
        self.assertIn(ITALIC_1_BEGIN, result[1])
        self.assertIn(ITALIC_1_END, result[1])

    def test_non_chinese_target_does_not_rebalance_punctuation(self) -> None:
        translator = translator_stub()
        translator.lang_out = "en"
        sources = ["The first clause", ", and the second clause."]
        translator._run_continuation_request = (
            lambda *args, **kwargs: list(sources)
        )

        result = translator.translate_continuation_fragments(
            sources,
            join_kind="cross-page",
        )

        self.assertEqual(result, sources)

    def test_non_chinese_continuation_preserves_one_boundary_space(self) -> None:
        translator = translator_stub()
        translator.lang_out = "en"
        sources = ["first", "term"]
        translator._run_continuation_request = (
            lambda *args, **kwargs: ["first ", "term"]
        )

        result = translator.translate_continuation_fragments(
            sources,
            join_kind="cross-page",
        )

        self.assertEqual(result, ["first ", "term"])
        self.assertEqual("".join(result), "first term")

    def test_continuation_json_loader_preserves_fragment_edge_space(self) -> None:
        translator = translator_stub()
        translator.lang_out = "en"
        with TemporaryDirectory() as directory:
            output = Path(directory) / "translation.json"
            output.write_text(
                json.dumps({"translations": ["first ", "term"]}),
                encoding="utf-8",
            )

            result = translator._load_batch_translations(
                str(output),
                2,
                preserve_edge_whitespace=True,
            )

        self.assertEqual(result, ["first ", "term"])

    def test_chinese_continuation_keeps_compact_boundary_behavior(self) -> None:
        translator = translator_stub()
        sources = ["The first clause", "continues in the next fragment."]
        translator._run_continuation_request = (
            lambda *args, **kwargs: ["第一分句  ", "  在下一片段继续。"]
        )

        result = translator.translate_continuation_fragments(
            sources,
            join_kind="cross-page",
        )

        self.assertEqual(result, ["第一分句", "在下一片段继续。"])

    def test_boundary_space_normalization_preserves_flow_tokens(self) -> None:
        translator = translator_stub()
        translator.lang_out = "en"
        sources = [f"first{FLOW_0}", f"{FLOW_1}term"]
        targets = [f"first{FLOW_0}\t", f" {FLOW_1}term"]
        translator._run_continuation_request = (
            lambda *args, **kwargs: list(targets)
        )

        result = translator.translate_continuation_fragments(
            sources,
            join_kind="cross-page",
        )

        self.assertEqual(result, [f"first{FLOW_0} ", f"{FLOW_1}term"])
        self.assertEqual(
            [CodexTranslator.FLOW_TOKEN_RE.findall(item) for item in result],
            [[FLOW_0], [FLOW_1]],
        )

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

    def test_unchanged_english_group_is_rejected_without_cache(self) -> None:
        sources = [
            "The measured response is controlled by",
            "the external coupling strength in this circuit.",
        ]

        self._assert_group_rejected(sources, sources)

    def test_short_grammar_residue_rejects_the_whole_continuation(self) -> None:
        sources = [
            "The cavity field {v0} is evaluated at",
            "the transmon junction.",
        ]
        invalid_targets = [
            "腔体电场{v0} is transmon",
            "结处的电场。",
        ]

        self._assert_group_rejected(
            sources,
            invalid_targets,
            [{"{v0}": r"\vec{E}"}, {}],
        )

    def test_clear_cross_reference_misplacement_is_repaired_in_fragment(
        self,
    ) -> None:
        translator = translator_stub()
        sources = ["See Fig. 5 for", "the device layout."]
        targets = ["器件布局见图。5，", "如下所示。"]
        expected = ["器件布局见图5，", "如下所示。"]
        translator._run_continuation_request = (
            lambda *args, **kwargs: list(targets)
        )

        result = translator.translate_continuation_fragments(
            sources,
            [{}, {}],
            join_kind="cross-page",
        )

        self.assertEqual(result, expected)
        self.assertEqual(len(translator.cache.set_calls), 1)

    def test_capitalized_eponym_continuation_keeps_page_local_formulas(self) -> None:
        translator = translator_stub()
        sources = [
            "The Josephson energy E{v36} can be computed from the",
            "Ambegaokar–Baratoff formula adapted to the measured "
            "room-temperature resistance of the junction{v0}.",
        ]
        targets = [
            "约瑟夫森能量E{v36}可由",
            "根据测得的结室温电阻修正的Ambegaokar–Baratoff公式{v0}计算。",
        ]
        translator._run_continuation_request = (
            lambda *args, **kwargs: list(targets)
        )

        result = translator.translate_continuation_fragments(
            sources,
            [{"{v36}": "E_J"}, {"{v0}": "49"}],
            join_kind="page:normal",
        )

        self.assertEqual(result, targets)
        self.assertEqual(len(translator.cache.set_calls), 1)

    def test_cross_fragment_cross_reference_repair_fails_closed(self) -> None:
        sources = ["See Fig.", "5 for the device layout."]
        invalid_targets = ["器件布局见图。", "5如下所示。"]

        self._assert_group_rejected(sources, invalid_targets)

    def test_short_unchanged_group_is_rejected_after_boundary_compaction(
        self,
    ) -> None:
        sources = ["Quantum ", "circuits"]
        # CJK boundary normalization removes the physical source space before
        # validation; the joined identity check must still recognize this copy.
        self._assert_group_rejected(sources, ["Quantum", "circuits"])

    def test_short_unchanged_continuation_cache_is_ignored_and_replaced(
        self,
    ) -> None:
        translator = translator_stub()
        sources = ["Quantum ", "circuits"]
        contexts = [{}, {}]
        normalized_contexts = [
            translator._normalize_formula_context(source, context)
            for source, context in zip(sources, contexts, strict=True)
        ]
        key = translator._continuation_cache_key(
            sources,
            normalized_contexts,
            "cross-page",
        )
        translator.cache.set(
            key,
            json.dumps({"translations": ["Quantum", "circuits"]}),
        )
        calls = 0

        def request(texts, normalized_contexts, join_kind, **kwargs):
            nonlocal calls
            calls += 1
            return ["量子", "电路"]

        translator._run_continuation_request = request

        result = translator.translate_continuation_fragments(
            sources,
            contexts,
            join_kind="cross-page",
        )

        self.assertEqual(result, ["量子", "电路"])
        self.assertEqual(calls, 1)
        self.assertEqual(
            json.loads(translator.cache.get(key)),
            {"translations": result},
        )

    def test_one_invalid_fragment_prevents_partial_group_cache_writes(self) -> None:
        translator = translator_stub()
        sources = ["The rate {v0} controls", "the gate response."]
        contexts = [{"{v0}": "ω_{c}"}, {}]
        invalid = ["速率控制", "门响应。"]
        observations: list[int] = []

        def request(texts, normalized_contexts, join_kind, **kwargs):
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

        def request(texts, normalized_contexts, join_kind, **kwargs):
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
