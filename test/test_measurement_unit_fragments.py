from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import is_measurement_unit_fragment
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source", [" cm/s). ", "mm/s", "(m/s)", ", cm / s；", "[mm/s]。"]
)
def test_closed_velocity_unit_fragments_are_kept_exactly(source):
    translator = translator_stub()
    translator._run_batch_translation = Mock()
    assert is_measurement_unit_fragment(source)
    assert translator.translate_batch([source]) == [source]
    assert translator._accepted_translation(source, source) == source.strip()
    assert translator._retry_contextual_item(source, []) == source
    translator._run_batch_translation.assert_not_called()


@pytest.mark.parametrize(
    "source",
    [
        "measured in cm/s",
        "cm/s is higher",
        "cm/speed",
        "CM/S",
        "cm//s",
        "1 cm/s",
        "cm/s at 37 C",
        "cm/s增加",
    ],
)
def test_unit_fragment_does_not_exempt_prose_or_other_syntax(source):
    assert not is_measurement_unit_fragment(source)


@pytest.mark.parametrize("target", ["cm/s）。", "mm/s).", "cm/s", "cm/min)."])
def test_accepted_unit_result_must_preserve_symbols_punctuation_and_scale(target):
    assert translator_stub()._accepted_translation(" cm/s). ", target) is None


def test_formula_gap_fallback_keeps_unit_suffix_out_of_model_request():
    translator = translator_stub()
    source = "The rate ({v0} cm/s)."
    context = [{"placeholder": "{v0}", "unicode_formula": "4.2"}]
    translator._run_batch_translation = Mock(return_value=["速率（"])
    result = translator._retry_contextual_item_by_prose_spans(source, context)
    assert result == "速率（{v0} cm/s)."
    assert translator._run_batch_translation.call_args.args[0] == ["The rate ("]
