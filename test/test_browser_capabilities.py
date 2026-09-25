"""Browser backends share PDF safeguards without requiring Codex execution."""

from types import SimpleNamespace
from unittest import mock

import pytest
from pdfminer.pdfinterp import PDFResourceManager

from pdf2zh.converter import (
    TranslateConverter,
    _retryable_translation_error,
    _supports_scientific_translation,
)
from pdf2zh.reading_flow import FragmentSpan, SegmentRef
from pdf2zh.translator import (
    CodexTranslator,
    ScientificTranslator,
    TranslationAbortedError,
)


class BrowserRules(ScientificTranslator):
    name = "chatgpt-browser"

    def __init__(self, lang_in="en", lang_out="zh", model="web", envs=None, **kwargs):
        super().__init__(lang_in, lang_out, model, **kwargs)

    def set_concurrency(self, value):
        self.max_concurrency = 1


@pytest.fixture
def rules():
    with (
        mock.patch("pdf2zh.translator.TranslationCache"),
        mock.patch("pdf2zh.translator.find_codex_executable") as find,
        mock.patch("pdf2zh.translator.subprocess.run") as execute,
    ):
        translator = BrowserRules()
        find.assert_not_called()
        execute.assert_not_called()
        yield translator


def converter(translator):
    result = TranslateConverter.__new__(TranslateConverter)
    result.translator = translator
    result.thread = 8
    return result


def test_shared_rules_do_not_inherit_codex_transport(rules):
    assert not isinstance(rules, CodexTranslator)
    assert not hasattr(rules, "codex_bin")
    assert not hasattr(rules, "_probe_cli")
    assert rules.scientific_translation
    assert rules.batch_output_schema["required"] == ["translations"]
    with pytest.raises(NotImplementedError):
        rules._execute_codex_request("prompt", {}, lambda path: None)
    assert rules._validate_formula_translation("value {v0}", "值 {v0}")
    assert not rules._validate_formula_translation("value {v0}", "值 {v1}")


def test_browser_registration_is_lazy_and_serialized(rules):
    with (
        mock.patch.dict(
            "sys.modules",
            {"pdf2zh.chatgpt": SimpleNamespace(ChatGPTTranslator=BrowserRules)},
        ),
        mock.patch("pdf2zh.translator.TranslationCache"),
    ):
        result = TranslateConverter(
            PDFResourceManager(),
            service="chatgpt-browser:web",
            lang_in="en",
            lang_out="zh",
            thread=8,
        )
    assert isinstance(result.translator, BrowserRules)
    assert result.translator.max_concurrency == 1
    assert result._reading_flow_enabled()


def test_scientific_capability_controls_dispatch_and_preserves_legacy_adapters():
    assert _supports_scientific_translation(SimpleNamespace(name="codex"))
    assert not _supports_scientific_translation(SimpleNamespace(name="google"))
    assert not _supports_scientific_translation(
        SimpleNamespace(name="codex", scientific_translation=False)
    )
    translator = SimpleNamespace(
        name="independent-browser",
        scientific_translation=True,
        translate_batch=mock.Mock(return_value=["科学结果"]),
        translate_batch_with_formula_contexts=mock.Mock(return_value=["结果 {v0}"]),
        translate_reference_entries=mock.Mock(return_value=["作者，作品题名。"]),
    )
    result = converter(translator)
    assert result._translate_text_segments(["Scientific result"]) == ["科学结果"]
    assert result._translate_text_segments(["Result {v0}"], [{"{v0}": "x"}]) == [
        "结果 {v0}"
    ]
    assert result._translate_reference_segments(["Author, Work title."], [""]) == [
        "作者，作品题名。"
    ]
    translator.translate_batch.assert_called_once()
    translator.translate_batch_with_formula_contexts.assert_called_once()
    translator.translate_reference_entries.assert_called_once()


@pytest.mark.parametrize(
    "method,args",
    [
        ("_run_batch_translation", (["one", "two"],)),
        ("_run_formula_context_batch_request", (["one", "two"], [[], []])),
        ("_run_styled_batch_request", (["one", "two"], [[], []])),
        ("_run_exact_reference_title_batch", (["one", "two"],)),
        ("_run_discovered_reference_title_batch", (["one", "two"],)),
        ("_run_continuation_request", (["one", "two"], [[], []], "page")),
    ],
)
def test_browser_intervention_never_splits_or_retries_a_request(rules, method, args):
    with mock.patch.object(
        rules,
        "_execute_codex_request",
        side_effect=TranslationAbortedError("Browser closed"),
    ) as execute:
        with pytest.raises(TranslationAbortedError, match="Browser closed"):
            getattr(rules, method)(*args)
    execute.assert_called_once()
    assert not _retryable_translation_error(TranslationAbortedError("login needed"))


@pytest.mark.parametrize("path", ["ordinary", "contextual", "reference", "styled"])
def test_converter_intervention_is_not_retried_or_silently_preserved(path):
    failure = mock.Mock(side_effect=TranslationAbortedError("Browser closed"))
    translator = SimpleNamespace(
        name="chatgpt-browser",
        scientific_translation=True,
        translate_batch=failure,
        translate_batch_with_formula_contexts=failure,
        translate_reference_entries=failure,
        translate_styled_batch=failure,
    )
    result = converter(translator)
    with pytest.raises(TranslationAbortedError):
        if path == "ordinary":
            result._translate_text_segments(["Scientific prose"])
        elif path == "contextual":
            result._translate_text_segments(["Result {v0}"], [{"{v0}": "x"}])
        elif path == "reference":
            result._translate_reference_segments(["A title"], [""])
        else:
            result._translate_styled_segments(["Scientific prose"])
    failure.assert_called_once()


@pytest.mark.parametrize("reference", [False, True])
def test_continuation_intervention_is_not_silently_preserved(reference):
    failure = mock.Mock(side_effect=TranslationAbortedError("Browser closed"))
    result = converter(
        SimpleNamespace(
            translate_continuation_fragments=failure,
            translate_reference_continuation_fragments=failure,
        )
    )
    left, right = SegmentRef(1, 0), SegmentRef(2, 0)
    group = SimpleNamespace(
        fragments=(FragmentSpan(left, 0, 3), FragmentSpan(right, 0, 3)),
        edge=SimpleNamespace(kind="page", join_mode="normal"),
    )
    draft = SimpleNamespace(
        sstk=["one"], italic_candidates={}, readonly_formula_contexts={}
    )
    with pytest.raises(TranslationAbortedError):
        method = (
            result._translate_reference_continuation_group
            if reference
            else result._translate_continuation_group
        )
        method(group, {left: draft, right: draft})
    failure.assert_called_once()


@pytest.mark.parametrize(
    "method,args,source_result",
    [
        (
            "do_translate",
            ("The circuit uses a shared resonator.",),
            "The circuit uses a shared resonator.",
        ),
        (
            "translate",
            ("The circuit uses a shared resonator.",),
            "The circuit uses a shared resonator.",
        ),
        (
            "translate_batch",
            (["The circuit uses a shared resonator."],),
            ["The circuit uses a shared resonator."],
        ),
        (
            "translate_batch_with_formula_contexts",
            (["The circuit uses {v0}."], [{"{v0}": "x"}]),
            ["The circuit uses {v0}."],
        ),
    ],
)
def test_browser_does_not_accept_exhausted_unchanged_prose(method, args, source_result):
    from pdf2zh.chatgpt import ChatGPTTranslator

    translator = ChatGPTTranslator.__new__(ChatGPTTranslator)
    translator.lang_in, translator.lang_out = "en", "zh"
    with mock.patch.object(
        ScientificTranslator, method, return_value=source_result
    ) as translate:
        with pytest.raises(TranslationAbortedError, match="未完整翻译"):
            getattr(translator, method)(*args)
    translate.assert_called_once()


def test_browser_preserves_formula_identifiers_and_names_within_translated_prose():
    from pdf2zh.chatgpt import ChatGPTTranslator

    translator = ChatGPTTranslator.__new__(ChatGPTTranslator)
    translator.lang_in, translator.lang_out = "en", "zh"
    sources = ["{v0}", "CNOT", "GPT-6", "The Josephson junction is measured."]
    targets = ["{v0}", "CNOT", "GPT-6", "测量 Josephson 结。"]
    assert translator._require_translated_prose(sources, targets) == targets


def test_browser_keeps_reference_metadata_preservation_separate_from_prose():
    from pdf2zh.chatgpt import ChatGPTTranslator

    translator = ChatGPTTranslator.__new__(ChatGPTTranslator)
    translator.lang_in, translator.lang_out = "en", "zh"
    reference = "A. Einstein, Ann. Phys. 17, 891 (1905)."
    with mock.patch.object(
        ScientificTranslator, "translate_reference_entries", return_value=[reference]
    ) as translate:
        assert translator.translate_reference_entries([reference]) == [reference]
    translate.assert_called_once()
