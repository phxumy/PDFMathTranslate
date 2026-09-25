from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tenacity import RetryError, wait_none
import pdf2zh.translator as mod
from pdf2zh.codex_errors import (
    CodexExecutionError,
    CodexPermanentError,
    CodexTimeoutError,
    CodexOutputError,
    codex_cli_error,
)
from pdf2zh.converter import TranslateConverter, _retryable_translation_error

C = mod.CodexTranslator


def stub():
    obj = C.__new__(C)
    obj.lang_in = "en"
    obj.lang_out = "zh"
    obj.prompttext = None
    obj.batch_output_schema = {}
    obj.single_output_schema = {}
    obj.reference_title_output_schema = {}
    obj.timeout = 10
    obj.codex_bin = "codex"
    obj.preferred_command_mode = "fast"
    obj._build_command = lambda *args: ["codex", "exec"]
    obj._iter_command_modes = lambda: ["fast"]
    return obj


class CodexTypedFailureTests(unittest.TestCase):
    def test_cli_usage_limit_cannot_masquerade_as_a_translations_shape_error(self):
        obj = stub()
        detail = 'Source Texts JSON: [{"text":"test"}]\nReturn {"translations": []}.\nERROR: You\'ve hit your usage limit. Try again later.'
        completed = subprocess.CompletedProcess(["codex"], 1, "", detail)
        with mock.patch.object(mod.subprocess, "run", return_value=completed) as run:
            with self.assertRaises(CodexPermanentError):
                obj._run_batch_translation(["one", "two", "three", "four"])
        self.assertEqual(run.call_count, 1)

    def test_user_prompt_mentioning_a_limit_does_not_classify_a_network_error_as_permanent(
        self,
    ):
        detail = 'Source: ERROR: You\'ve hit your usage limit\nReturn {"translations": []}\nERROR: connection reset by peer'
        self.assertIs(type(codex_cli_error(1, detail)), CodexExecutionError)
        obj = stub()
        with mock.patch.object(
            obj, "_execute_codex_request", side_effect=codex_cli_error(1, detail)
        ) as request:
            with self.assertRaises(CodexExecutionError):
                obj._run_batch_translation(["one", "two"])
        self.assertEqual(request.call_count, 1)

    def test_timeout_and_invalid_output_can_still_split_a_batch(self):
        for error in (
            CodexOutputError("invalid shape"),
            CodexTimeoutError("timed out"),
        ):
            obj = stub()
            with mock.patch.object(
                obj, "_execute_codex_request", side_effect=[error, ["一"], ["二"]]
            ) as request:
                self.assertEqual(
                    obj._run_batch_translation(["one", "two"]), ["一", "二"]
                )
            self.assertEqual(request.call_count, 3)

    def test_single_item_invalid_output_retains_single_response_fallback(self):
        obj = stub()
        with mock.patch.object(
            obj,
            "_execute_codex_request",
            side_effect=[CodexOutputError("invalid shape"), "一"],
        ) as request:
            self.assertEqual(obj._run_batch_translation(["one"]), ["一"])
        self.assertEqual(request.call_count, 2)

    def test_all_structured_paths_propagate_permanent_failures_without_splitting(self):
        calls = (
            ("_run_formula_context_batch_request", (["one", "two"], [[], []])),
            ("_run_styled_batch_request", (["one", "two"], [[], []])),
            ("_run_exact_reference_title_batch", (["one", "two"],)),
            ("_run_discovered_reference_title_batch", (["one", "two"],)),
            ("_run_continuation_request", (["one", "two"], [[], []], "page")),
        )
        for name, args in calls:
            obj = stub()
            with (
                self.subTest(name=name),
                mock.patch.object(
                    obj,
                    "_execute_codex_request",
                    side_effect=CodexPermanentError("limit"),
                ) as request,
            ):
                with self.assertRaises(CodexPermanentError):
                    getattr(obj, name)(*args)
                self.assertEqual(request.call_count, 1)

    def test_successful_execution_invalid_payload_is_a_typed_output_error(self):
        obj = stub()
        for payload in (
            "not-json",
            "[]",
            '{"translations":["only one"]}',
            '{"translations":["one",""]}',
        ):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "out.json"
                path.write_text(payload, encoding="utf8")
                with self.assertRaises(CodexOutputError):
                    obj._load_batch_translations(str(path), 2)

    def test_missing_executable_is_permanent_and_timeout_is_recoverable(self):
        for failure, kind in (
            (FileNotFoundError("codex"), CodexPermanentError),
            (subprocess.TimeoutExpired("codex", 10), CodexTimeoutError),
        ):
            obj = stub()
            with (
                self.subTest(kind=kind),
                mock.patch.object(mod.subprocess, "run", side_effect=failure),
            ):
                with self.assertRaises(kind):
                    obj._execute_codex_request("test", {}, lambda path: None)


class ConverterFailurePolicyTests(unittest.TestCase):
    def converter(self, translator):
        converter = TranslateConverter.__new__(TranslateConverter)
        converter.translator = translator
        converter.thread = 1
        return converter

    def test_permanent_errors_stop_each_codex_converter_path_after_one_call(self):
        for method in ("ordinary", "contextual", "reference"):
            failure = mock.Mock(side_effect=CodexPermanentError("usage limit"))
            translator = SimpleNamespace(
                name="codex",
                translate_batch=failure,
                translate_batch_with_formula_contexts=failure,
                translate_reference_entries=failure,
            )
            converter = self.converter(translator)
            with (
                self.subTest(method=method),
                mock.patch("pdf2zh.converter.wait_fixed", return_value=wait_none()),
            ):
                with self.assertRaises(CodexPermanentError):
                    if method == "ordinary":
                        converter._translate_text_segments(["Scientific prose."])
                    elif method == "contextual":
                        converter._translate_text_segments(
                            ["The result {v0}."], [{"{v0}": "x"}]
                        )
                    else:
                        converter._translate_reference_segments(
                            ["An article title."], [""]
                        )
                self.assertEqual(failure.call_count, 1)

    def test_other_cli_execution_errors_keep_the_existing_three_attempt_limit(self):
        failure = mock.Mock(side_effect=CodexExecutionError("connection reset"))
        converter = self.converter(
            SimpleNamespace(name="codex", translate_batch=failure)
        )
        with mock.patch("pdf2zh.converter.wait_fixed", return_value=wait_none()):
            with self.assertRaises(RetryError):
                converter._translate_text_segments(["Scientific prose."])
        self.assertEqual(failure.call_count, 3)

    def test_other_services_keep_their_retry_behavior(self):
        translate = mock.Mock(side_effect=[RuntimeError("network reset"), "translated"])
        converter = self.converter(SimpleNamespace(name="google", translate=translate))
        with mock.patch("pdf2zh.converter.wait_fixed", return_value=wait_none()):
            self.assertEqual(
                converter._translate_text_segments(["Scientific prose."]),
                ["translated"],
            )
        self.assertEqual(translate.call_count, 2)

    def test_interrupts_are_still_not_retryable(self):
        self.assertFalse(_retryable_translation_error(KeyboardInterrupt()))
        self.assertFalse(_retryable_translation_error(SystemExit()))
        self.assertTrue(_retryable_translation_error(RuntimeError("temporary")))


if __name__ == "__main__":
    unittest.main()
