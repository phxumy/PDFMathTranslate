import pytest

from pdf2zh.codex_errors import CodexTranslationValidationError
from pdf2zh.converter import _retryable_translation_error
from test_codex_formula_context import translator_stub


def test_dane_paragraph_retries_complete_sentences_after_whole_paragraph_rejection():
    first = (
        "Further, we demonstrate how AC TLS control can be used to precisely "
        "measure qubit energy loss and reveal subtle trends."
    )
    second = (
        "In Figure 5(c) we plot the results of running the no-control, fast-random, "
        "and AC control measurements versus temperature on one qubit."
    )
    source = first + " " + second
    targets = {
        first: "此外，我们展示了如何利用 AC TLS 控制精确测量量子比特能量损耗并揭示细微趋势。",
        second: "图 5(c) 展示了对一个量子比特进行无控制、快速随机和交流控制测量所得的温度依赖关系。",
    }
    translator = translator_stub()
    calls = []

    def backend(texts, **kwargs):
        calls.extend(texts)
        return [targets.get(text.strip(), text) for text in texts]

    translator._run_batch_translation = backend
    result = translator.translate_batch([source])[0]
    assert result == "".join(targets.values())
    assert len(calls) == 4
    assert translator.cache.get(source) == result


@pytest.mark.parametrize("method", ["single", "batch", "contextual"])
def test_untranslated_prose_cannot_be_reported_as_success_or_cached(method):
    source = "A private unpublished scientific result."
    translator = translator_stub()
    translator._run_single_translation = lambda text, **kwargs: text
    translator._run_batch_translation = lambda texts, **kwargs: list(texts)
    translator._run_formula_context_batch_request = (
        lambda texts, contexts, **kwargs: list(texts)
    )
    with pytest.raises(CodexTranslationValidationError) as failure:
        if method == "single":
            translator.translate(source)
        elif method == "batch":
            translator.translate_batch([source])
        else:
            translator.translate_batch_with_formula_contexts(
                [source + " {v0}"], [{"{v0}": "x"}]
            )
    assert "private unpublished" not in str(failure.value)
    assert "完整性校验失败" in str(failure.value)
    assert not _retryable_translation_error(failure.value)
    assert translator.cache.values == {}


def test_pure_formula_and_whitespace_still_pass_without_model_requests():
    translator = translator_stub()
    translator._run_batch_translation = lambda *args, **kwargs: pytest.fail(
        "Protected formulas must not be sent to the model"
    )
    assert translator.translate_batch(["{v0}", " "]) == ["{v0}", " "]
