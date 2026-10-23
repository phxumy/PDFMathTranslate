import pytest
from test_codex_formula_context import translator_stub

from pdf2zh.translation_quality import is_math_expression_only


@pytest.mark.parametrize(
    "source", ["cos {v26}", "(sin {v1} + cos {v2})/2", "exp(-{v0})", "log {v4}."]
)
def test_pure_math_operators_are_not_mistaken_for_untranslated_prose(source):
    translator = translator_stub()
    translator._run_batch_translation = lambda *args, **kwargs: pytest.fail(
        "Pure math needs no model"
    )
    assert is_math_expression_only(source)
    assert translator.translate_batch([source]) == [source]
    assert translator._accepted_translation(source, source) is not None
    assert translator._accepted_translation(source, source + " equals one") is None


@pytest.mark.parametrize(
    "source",
    [
        "cos {v0} is positive",
        "log {v0} messages",
        "The result is exp({v0}).",
        "cos x",
        "cos",
        "min {v0} and max {v1}",
        "余弦 cos {v0}",
    ],
)
def test_mathematical_word_does_not_exempt_ordinary_prose(source):
    assert not is_math_expression_only(source)
    assert not translator_stub()._is_passthrough_text(source)
