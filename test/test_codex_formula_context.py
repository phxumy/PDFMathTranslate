from __future__ import annotations

import json
import unittest

from pdf2zh.converter import (
    Paragraph,
    TranslateConverter,
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
                "先看{{v1}}，再比较{v0}与{v0}",
            )
        )
        invalid = [
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

    def test_invalid_contextual_output_fails_closed_and_is_not_cached(self) -> None:
        translator = translator_stub()
        responses = iter(
            [
                ["频率由{v1}决定"],
                ["频率由ω_{c}决定"],
            ]
        )
        translator._run_formula_context_batch_request = (
            lambda texts, contexts: next(responses)
        )
        source = "The frequency {v0} controls the gate."

        result = translator.translate_batch_with_formula_contexts(
            [source],
            [{"{v0}": "ω_{c}"}],
        )

        self.assertEqual(result, [source])
        self.assertEqual(translator.cache.values, {})

    def test_long_text_is_split_with_only_each_chunks_live_context(self) -> None:
        translator = translator_stub()
        calls: list[tuple[list[str], list[list[dict[str, str]]]]] = []

        def translate_chunks(texts, contexts):
            calls.append((list(texts), list(contexts)))
            return [
                text.replace("The frequency", "频率").replace(
                    "The coupling",
                    "耦合",
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
        translator._run_formula_context_batch_request = lambda texts, contexts: [
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

        self.assertEqual(result, source)
        self.assertEqual(
            translator.ordinary_calls,
            [([source], [{"{v2}": "g_{12}"}])],
        )


if __name__ == "__main__":
    unittest.main()
