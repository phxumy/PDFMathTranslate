from __future__ import annotations

import json
import unittest

from pdf2zh.converter import (
    Paragraph,
    TranslateConverter,
    _collect_readonly_formula_contexts,
    _formula_context_for_text,
    _serialize_safe_inline_formula,
)
from pdf2zh.translation_policy import DocumentTranslationPolicy
from pdf2zh.translator import CodexTranslator


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeFormulaFont:
    def __init__(self, mapping: dict[int, str]) -> None:
        self.mapping = mapping

    def to_unichr(self, cid: int) -> str:
        return self.mapping[cid]


class FakeFormulaChar:
    def __init__(
        self,
        text: str,
        cid: int,
        font: FakeFormulaFont,
        x0: float,
        x1: float,
        *,
        size: float = 10.0,
        baseline: float = 100.0,
        layout_class: int = 1,
    ) -> None:
        self._text = text
        self.cid = cid
        self.font = font
        self.x0 = x0
        self.x1 = x1
        self.y0 = baseline
        self.y1 = baseline + size
        self.size = size
        self.matrix = (1.0, 0.0, 0.0, 1.0, x0, baseline)
        self._pdf2zh_layout_class = layout_class

    def get_text(self) -> str:
        return self._text


def paragraph() -> Paragraph:
    return Paragraph(700.0, 50.0, 50.0, 550.0, 680.0, 710.0, 10.0, False)


def translator_stub() -> CodexTranslator:
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_in = "en"
    translator.lang_out = "zh-cn"
    translator.ignore_cache = False
    translator.prompttext = None
    translator.cache = MemoryCache()
    return translator


class SafeFormulaSerializationTests(unittest.TestCase):
    def test_short_inline_unicode_formula_gets_script_hints(self) -> None:
        font = FakeFormulaFont({1: "ω", 2: "c"})
        chars = [
            FakeFormulaChar("ω", 1, font, 0.0, 5.0),
            FakeFormulaChar(
                "c",
                2,
                font,
                5.0,
                8.0,
                size=6.0,
                baseline=97.0,
            ),
        ]

        serialized = _serialize_safe_inline_formula(
            0,
            chars,
            [],
            0,
            [paragraph()],
            ["The transition {v0} controls the gate response."],
        )

        self.assertEqual(serialized, "ω_{c}")

    def test_unsafe_or_non_inline_formula_is_never_partially_uploaded(self) -> None:
        control_font = FakeFormulaFont({1: "\x05"})
        unsafe = [FakeFormulaChar("\x05", 1, control_font, 0.0, 5.0)]
        args = (0, unsafe, [], 0, [paragraph()])

        self.assertIsNone(
            _serialize_safe_inline_formula(
                *args,
                ["The transition {v0} controls the gate response."],
            )
        )
        self.assertIsNone(
            _serialize_safe_inline_formula(*args, ["{v0}"])
        )
        self.assertIsNone(
            _serialize_safe_inline_formula(
                0,
                [
                    FakeFormulaChar(
                        "g",
                        1,
                        FakeFormulaFont({1: "g"}),
                        0.0,
                        5.0,
                        layout_class=0,
                    )
                ],
                [],
                0,
                [paragraph()],
                ["The transition {v0} controls the gate response."],
            )
        )
        self.assertIsNone(
            _serialize_safe_inline_formula(
                0,
                [
                    FakeFormulaChar(
                        "g",
                        1,
                        FakeFormulaFont({1: "g"}),
                        0.0,
                        5.0,
                    )
                ],
                [object()],
                0,
                [paragraph()],
                ["The transition {v0} controls the gate response."],
            )
        )

    def test_per_item_context_contains_only_present_placeholders(self) -> None:
        selected = _formula_context_for_text(
            "The rate {v2} is compared with {v0}.",
            {0: "ω_{c}", 1: "g_{12}", 2: "κ"},
        )
        self.assertEqual(selected, {"{v2}": "κ", "{v0}": "ω_{c}"})

    def test_latin_fragment_joined_to_identifier_is_not_formula_context(self) -> None:
        font = FakeFormulaFont({1: "P", 2: "Y"})
        chars = [
            FakeFormulaChar("P", 1, font, 0.0, 5.0),
            FakeFormulaChar("Y", 2, font, 5.0, 10.0),
        ]

        contexts = _collect_readonly_formula_contexts(
            [chars],
            [[]],
            [0],
            [paragraph()],
            ["The PYEPR method appears as {v0}EPR in this paragraph."],
        )

        self.assertEqual(contexts, {})

    def test_independent_uppercase_product_identifier_is_not_formula_context(
        self,
    ) -> None:
        text = "PYEPR"
        font = FakeFormulaFont(
            {index: character for index, character in enumerate(text, start=1)}
        )
        chars = [
            FakeFormulaChar(character, index, font, offset, offset + 5.0)
            for offset, (index, character) in enumerate(
                enumerate(text, start=1),
                start=0,
            )
        ]

        contexts = _collect_readonly_formula_contexts(
            [chars],
            [[]],
            [0],
            [paragraph()],
            ["The early versions of {v0} were used by several groups."],
        )

        self.assertEqual(contexts, {})

    def test_latin_formula_without_joined_identifier_keeps_context(self) -> None:
        font = FakeFormulaFont({1: "P", 2: "Y"})
        chars = [
            FakeFormulaChar("P", 1, font, 0.0, 5.0),
            FakeFormulaChar("Y", 2, font, 5.0, 10.0),
        ]

        spaced = _collect_readonly_formula_contexts(
            [chars],
            [[]],
            [0],
            [paragraph()],
            ["The variable {v0} EPR controls the measured response."],
        )
        isolated = _collect_readonly_formula_contexts(
            [chars],
            [[]],
            [0],
            [paragraph()],
            ["The variable {v0} controls the measured response."],
        )

        self.assertEqual(spaced, {0: "PY"})
        self.assertEqual(isolated, {0: "PY"})

    def test_non_ascii_formula_joined_to_identifier_keeps_context(self) -> None:
        font = FakeFormulaFont({1: "ω"})
        chars = [FakeFormulaChar("ω", 1, font, 0.0, 5.0)]

        contexts = _collect_readonly_formula_contexts(
            [chars],
            [[]],
            [0],
            [paragraph()],
            ["The transition {v0}EPR controls the measured response."],
        )

        self.assertEqual(contexts, {0: "ω"})


class CodexFormulaContextTests(unittest.TestCase):
    def test_prompt_uses_structured_ascii_safe_json(self) -> None:
        translator = translator_stub()
        source = 'The rate {v0} controls "quoted" behavior.'
        raw_context = {"{v0}": 'ω_{1} "quoted" \\ path'}
        normalized = translator._normalize_formula_context(source, raw_context)
        prompt = translator._build_formula_context_batch_prompt(
            [source],
            [normalized],
        )

        self.assertNotIn("ω", prompt)
        self.assertIn(r"\u03c9", prompt)
        payload_text = prompt.split("Source Texts JSON: ", 1)[1].split(
            "\n\nReturn JSON", 1
        )[0]
        payload = json.loads(payload_text)
        self.assertEqual(payload[0]["text"], source)
        self.assertEqual(
            payload[0]["read_only_formulas"][0]["unicode_formula"],
            raw_context["{v0}"],
        )

    def test_control_bidi_private_and_surrogate_contexts_are_rejected(self) -> None:
        source = "The rate {v0} controls the gate response."
        invalid_values = ["g\x00", "g\n", "g\u202e", "g\ue000", "g\ud800"]
        for value in invalid_values:
            with self.subTest(value=repr(value)):
                self.assertEqual(
                    CodexTranslator._normalize_formula_context(
                        source,
                        {"{v0}": value},
                    ),
                    [],
                )

    def test_cache_key_is_canonical_context_sensitive_and_mode_isolated(self) -> None:
        source = "The rate {v0} differs from {v1}."
        first = {"{v0}": "ω_{c}", "{v1}": "g_{12}"}
        reordered = {"{v1}": "g_{12}", "{v0}": "ω_{c}"}
        changed = {"{v0}": "ω_{1}", "{v1}": "g_{12}"}

        key = CodexTranslator._formula_context_cache_key(source, first)
        self.assertEqual(
            key,
            CodexTranslator._formula_context_cache_key(source, reordered),
        )
        self.assertNotEqual(
            key,
            CodexTranslator._formula_context_cache_key(source, changed),
        )
        self.assertNotEqual(
            key,
            CodexTranslator._styled_cache_key(source, first),
        )

    def test_placeholder_validation_requires_exact_ids_counts_and_forms(self) -> None:
        source = "A {v0}, then {{v1}}, repeat {v0}"
        self.assertTrue(CodexTranslator._validate_formula_translation(source, source))
        self.assertTrue(
            CodexTranslator._validate_formula_translation(
                source,
                "先看{v0}，再比较{{v1}}与{v0}",
            )
        )
        invalid = [
            "先看{{v1}}，再比较{v0}与{v0}",
            "A {v0}, then {{v1}}",
            "A {v0}, then {{v1}}, repeat {v0} {v0}",
            "A {v2}, then {{v1}}, repeat {v0}",
            "A {{v0}}, then {{v1}}, repeat {v0}",
        ]
        for target in invalid:
            with self.subTest(target=target):
                self.assertFalse(
                    CodexTranslator._validate_formula_translation(source, target)
                )

    def test_compact_formula_atoms_must_keep_textual_affixes_adjacent(self) -> None:
        source = "The parameter p{v0} was introduced in {v1}EPR."
        self.assertTrue(
            CodexTranslator._validate_formula_translation(
                source,
                "参数p{v0}是在{v1}EPR中引入的。",
            )
        )
        for target in (
            "参数p发生变化后{v0}是在{v1}EPR中引入的。",
            "参数p{v0}是在{v1}版本EPR中引入的。",
        ):
            with self.subTest(target=target):
                self.assertFalse(
                    CodexTranslator._validate_formula_translation(source, target)
                )

    def test_contextual_retry_reuses_strictly_validated_ordinary_cache(
        self,
    ) -> None:
        translator = translator_stub()
        source = "The frequency {v0} controls the measured gate response."
        cached = "频率{v0}控制测得的门响应。"
        context = [{"placeholder": "{v0}", "unicode_formula": "ω_c"}]
        translator.cache.set(source, cached)

        def fail_formula_request(*args, **kwargs):
            self.fail("a valid ordinary cache entry should avoid a model retry")

        translator._run_formula_context_batch_request = fail_formula_request
        translator._run_batch_translation = fail_formula_request

        self.assertEqual(
            translator._retry_contextual_item(source, context),
            cached,
        )

    def test_contextual_retry_rejects_ordinary_cache_that_expands_formula(
        self,
    ) -> None:
        translator = translator_stub()
        source = "The frequency {v0} controls the measured gate response."
        context = [{"placeholder": "{v0}", "unicode_formula": "ω_c"}]
        translator.cache.set(source, "频率ω_c{v0}控制测得的门响应。")
        translator._run_formula_context_batch_request = (
            lambda texts, contexts, **kwargs: ["频率{v0}控制测得的门响应。"]
        )

        self.assertEqual(
            translator._retry_contextual_item(source, context),
            "频率{v0}控制测得的门响应。",
        )

    def test_invalid_contextual_output_fails_closed_and_is_not_cached(self) -> None:
        translator = translator_stub()
        translator._run_formula_context_batch_request = (
            lambda texts, contexts, **kwargs: ["频率由{v1}决定"]
        )
        source = "The frequency {v0} controls the gate."
        translator._run_batch_translation = lambda texts, **kwargs: [source]

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "ω_{c}"}],
        )

        self.assertEqual(result, [source])
        self.assertEqual(translator.cache.values, {})

    def test_failed_context_chunk_retries_as_ordinary_placeholder_text(self) -> None:
        translator = translator_stub()
        source = "The frequency {v0} controls the measured gate response."
        translator._run_formula_context_batch_request = (
            lambda texts, contexts, **kwargs: [None]
        )
        ordinary_calls: list[tuple[list[str], bool]] = []

        def translate_ordinary(texts, *, require_complete_translation=False):
            ordinary_calls.append((list(texts), require_complete_translation))
            return ["频率{v0}控制测得的门响应。"]

        translator._run_batch_translation = translate_ordinary

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "ω_{c}"}],
        )

        self.assertEqual(result, ["频率{v0}控制测得的门响应。"])
        self.assertIn(([source], True), ordinary_calls)
        key = translator._formula_context_cache_key(
            source,
            {"{v0}": "ω_{c}"},
        )
        self.assertEqual(translator.cache.get(key), result[0])

    def test_formula_reordering_falls_back_to_ordered_flow_guards(self) -> None:
        translator = translator_stub()
        source = "The {v0} response is controlled by {v1} in the circuit."
        translator._run_formula_context_batch_request = (
            lambda texts, contexts, **kwargs: [
                "电路中的{v1}控制{v0}响应。"
            ]
        )

        def translate_ordinary(texts, *, require_complete_translation=False):
            text = texts[0]
            if "[[PDF2ZH_FLOW_900000000]]" in text:
                return [
                    "电路中的[[PDF2ZH_FLOW_900000000]]响应由"
                    "[[PDF2ZH_FLOW_900000001]]控制。"
                ]
            return ["电路中的{v1}控制{v0}响应。"]

        translator._run_batch_translation = translate_ordinary

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "ω_c", "{v1}": "g"}],
        )

        self.assertEqual(result, ["电路中的{v0}响应由{v1}控制。"])

    def test_flow_guards_mask_complete_compact_formula_atoms(self) -> None:
        translator = translator_stub()
        source = "The parameter p{v0} was introduced in {v1}EPR."
        masked_sources: list[str] = []
        translator._run_formula_context_batch_request = (
            lambda texts, contexts, **kwargs: ["参数p被引入{v0}，并用于{v1}版本EPR。"]
        )

        def translate_ordinary(texts, *, require_complete_translation=False):
            text = texts[0]
            if "[[PDF2ZH_FLOW_900000000]]" in text:
                masked_sources.append(text)
                return [
                    "参数[[PDF2ZH_FLOW_900000000]]是在"
                    "[[PDF2ZH_FLOW_900000001]]中引入的。"
                ]
            return ["参数p被引入{v0}，并用于{v1}版本EPR。"]

        translator._run_batch_translation = translate_ordinary

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "_m", "{v1}": "PY"}],
        )

        self.assertEqual(result, ["参数p{v0}是在{v1}EPR中引入的。"])
        self.assertEqual(len(masked_sources), 1)
        self.assertNotIn("p{v0}", masked_sources[0])
        self.assertNotIn("{v1}EPR", masked_sources[0])

    def test_reordered_flow_guards_fall_back_to_formula_free_prose_spans(
        self,
    ) -> None:
        translator = translator_stub()
        source = (
            "The frequency {v0} controls the resonator, while the rate {v1} "
            "sets the linewidth and the coupling {v2} remains fixed."
        )
        translator._run_formula_context_batch_request = (
            lambda texts, contexts, **kwargs: [
                "速率{v1}设置线宽，频率{v0}控制谐振器，耦合{v2}保持不变。"
            ]
        )

        def translate_ordinary(texts, *, require_complete_translation=False):
            if any("[[PDF2ZH_FLOW_" in text for text in texts):
                return [
                    "[[PDF2ZH_FLOW_900000001]]设置线宽，"
                    "[[PDF2ZH_FLOW_900000000]]控制谐振器，"
                    "[[PDF2ZH_FLOW_900000002]]保持不变。"
                ]
            if texts == [source]:
                return [
                    "速率{v1}设置线宽，频率{v0}控制谐振器，"
                    "耦合{v2}保持不变。"
                ]
            translations = {
                "The frequency ": "频率",
                " controls the resonator, while the rate ": "控制谐振器，而速率",
                " sets the linewidth and the coupling ": "设置线宽，耦合",
                " remains fixed.": "保持不变。",
            }
            return [translations[text] for text in texts]

        translator._run_batch_translation = translate_ordinary

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "ω", "{v1}": "κ", "{v2}": "g"}],
        )

        self.assertEqual(
            result,
            ["频率{v0}控制谐振器，而速率{v1}设置线宽，耦合{v2}保持不变。"],
        )

    def test_unchanged_english_context_cache_is_ignored(self) -> None:
        translator = translator_stub()
        source = "Quantum {v0} circuits"
        context = {"{v0}": "ω_{c}"}
        key = translator._formula_context_cache_key(source, context)
        translator.cache.set(key, source)
        translator._run_formula_context_batch_request = (
            lambda texts, contexts, **kwargs: ["量子{v0}电路"]
        )

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [context],
        )

        self.assertEqual(result, ["量子{v0}电路"])
        self.assertEqual(translator.cache.get(key), result[0])

    def test_short_unchanged_formula_context_fallback_is_not_cached(self) -> None:
        translator = translator_stub()
        source = "Quantum {v0} circuits"
        context = {"{v0}": "ω_{c}"}
        contextual_calls: list[bool] = []

        def contextual(texts, contexts, **kwargs):
            contextual_calls.append(
                kwargs.get("require_complete_translation", False)
            )
            return list(texts)

        translator._run_formula_context_batch_request = contextual
        translator._run_batch_translation = lambda texts, **kwargs: list(texts)

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [context],
        )

        self.assertEqual(result, [source])
        self.assertIn(True, contextual_calls)
        self.assertEqual(translator.cache.values, {})

    def test_short_unchanged_styled_cache_is_ignored_and_replaced(self) -> None:
        translator = translator_stub()
        begin = "[[PDF2ZH_ITALIC_0_BEGIN]]"
        end = "[[PDF2ZH_ITALIC_0_END]]"
        source = f"{begin}Quantum{end} circuits"
        target = f"{begin}量子{end}电路"
        key = translator._styled_cache_key(source, {})
        translator.cache.set(key, source)
        translator._run_styled_batch_request = (
            lambda texts, contexts, **kwargs: [target]
        )

        result = translator.translate_styled_batch([source], [{}])

        self.assertEqual(result, [target])
        self.assertEqual(translator.cache.get(key), target)

    def test_short_unchanged_styled_fallback_is_not_cached(self) -> None:
        translator = translator_stub()
        begin = "[[PDF2ZH_ITALIC_0_BEGIN]]"
        end = "[[PDF2ZH_ITALIC_0_END]]"
        source = f"{begin}Quantum{end} circuits"
        translator._run_styled_batch_request = (
            lambda texts, contexts, **kwargs: list(texts)
        )

        result = translator.translate_styled_batch([source], [{}])

        self.assertEqual(result, [None])
        self.assertEqual(translator.cache.values, {})

    def test_short_grammar_residue_is_shared_by_context_and_styled_paths(
        self,
    ) -> None:
        translator = translator_stub()
        contextual_source = (
            "The cavity field {v0} is evaluated at the transmon junction."
        )
        contextual_target = "腔体电场{v0} is transmon结处的电场。"
        context = translator._normalize_formula_context(
            contextual_source,
            {"{v0}": r"\vec{E}"},
        )

        self.assertIsNone(
            translator._accepted_contextual_translation(
                contextual_source,
                contextual_target,
                context,
            )
        )

        begin = "[[PDF2ZH_ITALIC_0_BEGIN]]"
        end = "[[PDF2ZH_ITALIC_0_END]]"
        styled_source = f"The {begin}so-called{end} cross-Kerr term."
        styled_target = f"{begin}so-called{end}交叉克尔项。"

        self.assertIsNone(
            translator._accepted_styled_translation(
                styled_source,
                styled_target,
                [],
            )
        )

    def test_wordlike_formula_cluster_restores_source_boundary_space(self) -> None:
        translator = translator_stub()
        source = "We introduce {v7}{v8}, a TSE model."
        target = "我们引入一种 TSE 模型{v7}{v8}。"
        context = translator._normalize_formula_context(
            source,
            {"{v7}": "Sound-", "{v8}": "Beam"},
        )

        self.assertEqual(
            translator._accepted_contextual_translation(
                source,
                target,
                context,
            ),
            "我们引入一种 TSE 模型 {v7}{v8}。",
        )

    def test_formula_spacing_repair_ignores_math_and_source_adjacency(self) -> None:
        translator = translator_stub()
        cases = (
            ("The value {v0} is fixed.", "值{v0}固定。", {"{v0}": "x^S"}),
            ("target{v0} class", "目标{v0} 类别", {"{v0}": "FSD50K"}),
            ("Class {v0} is active.", "类别{v0}处于活动状态。", {"{v0}": "N"}),
            ("Use {v0} samples.", "使用{v0}个样本。", {"{v0}": "1234"}),
        )
        for source, target, raw_context in cases:
            with self.subTest(source=source):
                context = translator._normalize_formula_context(
                    source,
                    raw_context,
                )
                self.assertEqual(
                    translator._accepted_contextual_translation(
                        source,
                        target,
                        context,
                    ),
                    target,
                )

    def test_formula_context_path_repairs_reference_placeholder_placement(
        self,
    ) -> None:
        translator = translator_stub()
        source = "The design was reported in ref. {v20}. The values agree."
        target = "该设计已在参考文献中报道。{v20}。这些数值一致。"
        context = translator._normalize_formula_context(
            source,
            {"{v20}": "28"},
        )

        self.assertEqual(
            translator._accepted_contextual_translation(
                source,
                target,
                context,
            ),
            "该设计已在参考文献{v20}中报道。这些数值一致。",
        )

    def test_context_cache_repairs_duplicate_equation_designator(self) -> None:
        translator = translator_stub()
        source = "In the quantum setting, Eq. (5) links p{v0} to the circuit state."
        context = {"{v0}": "_m"}
        key = translator._formula_context_cache_key(source, context)
        translator.cache.set(
            key,
            "在量子情形下，式式 (5) 将 p{v0}与电路状态联系起来。",
        )
        translator._run_formula_context_batch_request = (
            lambda *args, **kwargs: self.fail(
                "a repairable context cache entry should be reused"
            )
        )

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [context],
        )

        self.assertEqual(
            result,
            ["在量子情形下，式 (5) 将 p{v0}与电路状态联系起来。"],
        )

    def test_long_text_is_split_with_only_each_chunks_live_context(self) -> None:
        translator = translator_stub()
        calls: list[tuple[list[str], list[list[dict[str, str]]]]] = []

        def translate_chunks(texts, contexts, **kwargs):
            calls.append((list(texts), list(contexts)))
            return [
                (
                    "频率{v0}控制谐振器响应。"
                    if "{v0}" in text
                    else "耦合{v1}决定双量子比特门响应。"
                )
                for text in texts
            ]

        translator._run_formula_context_batch_request = translate_chunks
        source = (
            "The frequency {v0} controls "
            + "the resonator response " * 7
            + ". The coupling {v1} determines "
            + "the two qubit gate response " * 7
            + "."
        )

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "ω_{c}", "{v1}": "g_{12}"}],
        )[0]

        self.assertGreater(len(source), translator.MAX_ITEM_CHARS)
        flattened = [
            (text, context)
            for texts, contexts in calls
            for text, context in zip(texts, contexts, strict=True)
        ]
        self.assertEqual(len(flattened), 2)
        self.assertEqual(
            flattened[0][1],
            [{"placeholder": "{v0}", "unicode_formula": "ω_{c}"}],
        )
        self.assertEqual(
            flattened[1][1],
            [{"placeholder": "{v1}", "unicode_formula": "g_{12}"}],
        )
        self.assertIn("频率{v0}", result)
        self.assertIn("耦合{v1}", result)

    def test_plain_caption_sentence_is_not_bundled_with_formula_sentence(self) -> None:
        translator = translator_stub()
        translator._run_batch_translation = lambda texts: [
            text.replace("Circuit diagram", "电路图") for text in texts
        ]
        translator._run_formula_context_batch_request = lambda texts, contexts, **kwargs: [
            text.replace("The rate", "速率") for text in texts
        ]
        source = "Circuit diagram of the device. The rate {v0} changes smoothly."

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "g_{12}"}],
        )[0]

        self.assertIn("电路图", result)
        self.assertIn("速率{v0}", result)


class RecordingContextTranslator:
    name = "codex"
    lang_out = "zh-cn"

    def __init__(self, *, fail_styled: bool = False) -> None:
        self.fail_styled = fail_styled
        self.ordinary_calls: list[
            tuple[list[str], list[dict[str, str]]]
        ] = []
        self.styled_calls: list[
            tuple[list[str], list[dict[str, str]]]
        ] = []

    def translate_batch(self, texts: list[str]) -> list[str]:
        return list(texts)

    def translate_batch_with_formula_contexts(
        self,
        texts: list[str],
        contexts: list[dict[str, str]],
    ) -> list[str]:
        self.ordinary_calls.append((list(texts), list(contexts)))
        return list(texts)

    def translate_styled_batch(
        self,
        texts: list[str],
        formula_contexts: list[dict[str, str]] | None = None,
    ) -> list[str | None]:
        self.styled_calls.append((list(texts), list(formula_contexts or [])))
        if self.fail_styled:
            return [None] * len(texts)
        return [text.replace("in situ", "原位") for text in texts]


def converter_with(translator) -> TranslateConverter:
    converter = TranslateConverter.__new__(TranslateConverter)
    converter.translator = translator
    converter.thread = 1
    converter.translation_policy = DocumentTranslationPolicy()
    return converter


class ConverterFormulaContextTests(unittest.TestCase):
    def test_ordinary_and_styled_parts_receive_only_their_live_context(self) -> None:
        translator = RecordingContextTranslator()
        converter = converter_with(translator)
        sources = [
            "The transition {v0} controls the gate response.",
            "The device is tuned {v1} while {v2} remains fixed.",
        ]

        converter._translate_planned_segments(
            sources,
            [paragraph(), paragraph()],
            ["ωc", "insitu", "g12"],
            612.0,
            italic_candidates={1: "in situ"},
            readonly_formula_contexts={
                0: "ω_{c}",
                1: "in situ",
                2: "g_{12}",
            },
        )

        self.assertEqual(
            translator.ordinary_calls,
            [([sources[0]], [{"{v0}": "ω_{c}"}])],
        )
        self.assertEqual(len(translator.styled_calls), 1)
        styled_texts, styled_contexts = translator.styled_calls[0]
        self.assertIn("[[PDF2ZH_ITALIC_1_BEGIN]]in situ", styled_texts[0])
        self.assertNotIn("{v1}", styled_texts[0])
        self.assertEqual(styled_contexts, [{"{v2}": "g_{12}"}])

    def test_styled_failure_fallback_keeps_formula_context(self) -> None:
        translator = RecordingContextTranslator(fail_styled=True)
        converter = converter_with(translator)
        source = "The device is tuned {v1} while {v2} remains fixed."

        result = converter._translate_planned_segments(
            [source],
            [paragraph()],
            ["insitu", "g12"],
            612.0,
            italic_candidates={1: "in situ"},
            readonly_formula_contexts={2: "g_{12}"},
        )[0]

        plain_source = "The device is tuned in situ while {v2} remains fixed."
        self.assertEqual(result, plain_source)
        self.assertEqual(
            translator.ordinary_calls,
            [([plain_source], [{"{v2}": "g_{12}"}])],
        )


if __name__ == "__main__":
    unittest.main()
