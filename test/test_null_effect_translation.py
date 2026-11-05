from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import (
    has_omitted_scientific_content,
    is_reaction_condition_label,
)
from test_codex_formula_context import translator_stub

SOURCE = (
    "The rotations produce null effect if the phase {v0} is reversed "
    "halfway through the interaction time {v1}."
)
MISSING_RESULT = "如果相位{v0}在相互作用时间{v1}的中点反转。"
COMPLETE = "若相位{v0}在相互作用时间{v1}的中点反转，则旋转的净效应为零。"
CONTEXT = {"{v0}": "φ", "{v1}": "τ"}


@pytest.mark.parametrize(
    "source,target",
    [
        (SOURCE, MISSING_RESULT),
        ("The two pulses have no net effect.", "施加两个脉冲。"),
        ("The net effect is zero after reversal.", "反转后净效应非零。"),
        ("The rotations produce zero net effect.", "执行了旋转。"),
        ("The terms produce zero effect.", "这些项不能抵消。"),
    ],
)
def test_missing_zero_effect_result_is_rejected(source, target):
    assert has_omitted_scientific_content(source, target)
    assert translator_stub()._accepted_translation(source, target) is None


@pytest.mark.parametrize(
    "target",
    [
        "这些项的净效应为零。",
        "這些項的淨效應為零。",
        "这些项的净作用等于 0。",
        "这些项没有净效应。",
        "这些项不产生任何影响。",
        "这些项不起作用。",
        "这些项的效应相互抵消。",
        "这些项的作用相消。",
        "这些项的效应完全消失。",
        "这些项无效应。",
        "这些项产生零净效应。",
        "这些项不影响结果。",
    ],
)
def test_zero_effect_paraphrases_are_allowed(target):
    assert not has_omitted_scientific_content("The terms produce null effect.", target)


@pytest.mark.parametrize(
    "source,target",
    [
        ("The terms produce a nonzero effect.", "这些项产生了效应。"),
        ("The terms do not produce null effect.", "这些项产生了净效应。"),
        ("The control is in the null space.", "控制位于零空间。"),
        ("The label is `null effect`.", "该标签如图所示。"),
        ("The noise is nonzero. The pulses cancel.", "存在噪声。脉冲抵消。"),
    ],
)
def test_unrelated_or_negated_statements_do_not_require_zero_effect(source, target):
    assert not has_omitted_scientific_content(source, target)


def test_formula_context_cache_is_revalidated_and_only_bad_entry_is_retranslated():
    translator = translator_stub()
    translator.max_concurrency = 1
    context = translator._normalize_formula_context(SOURCE, CONTEXT)
    key = translator._formula_context_cache_key(SOURCE, context)
    translator.cache.set(key, MISSING_RESULT)
    translator.cache.set("A stable circuit.", "稳定的电路。")
    translator._run_formula_context_batch_request = Mock(return_value=[COMPLETE])
    translator._run_batch_translation = Mock()

    assert translator.translate_batch_with_formula_contexts(
        [SOURCE, "A stable circuit."], [CONTEXT, {}]
    ) == [COMPLETE, "稳定的电路。"]
    assert translator.cache.get(key) == COMPLETE
    translator._run_formula_context_batch_request.assert_called_once()
    translator._run_batch_translation.assert_not_called()

    translator._run_formula_context_batch_request.reset_mock()
    assert translator.translate_batch_with_formula_contexts([SOURCE], [CONTEXT]) == [
        COMPLETE
    ]
    translator._run_formula_context_batch_request.assert_not_called()


def test_new_incomplete_formula_result_is_retried_before_caching():
    translator = translator_stub()
    translator.max_concurrency = 1
    translator._run_formula_context_batch_request = Mock(
        side_effect=[[MISSING_RESULT], [COMPLETE]]
    )
    assert translator.translate_batch_with_formula_contexts([SOURCE], [CONTEXT]) == [
        COMPLETE
    ]
    assert translator._run_formula_context_batch_request.call_count == 2
    assert all(value != MISSING_RESULT for value in translator.cache.values.values())


@pytest.mark.parametrize(
    "source",
    [
        "Na{v11}, 80°C, 18 h;",
        "{v5}, 80°C, 18 h;",
        "(v) HCO{v7}, 100°C, 1.5 h;",
        "(iv) [[PDF2ZH_FLOW_3]], 80°C, 18 h;",
        "HBr, 100{v8}, 12 h;",
        "HBr, 100[[PDF2ZH_FLOW_8]], 12 h;",
        "K2CO3, 90 °C, 2.5 h",
        "Na₂CO₃, 80°C, 18 h;",
        "Li{v2}, -20°C, 30 min",
        "Pd, 300 K, 60 s",
    ],
)
def test_formula_temperature_duration_labels_are_preserved_without_requests(source):
    translator = translator_stub()
    translator._run_formula_context_batch_request = Mock()
    translator._run_batch_translation = Mock()
    assert is_reaction_condition_label(source)
    assert translator.translate_batch_with_formula_contexts(
        [source], [{"{v11}": "_{2}CO_{3}", "{v2}": "H"}]
    ) == [source]
    assert translator._accepted_translation(source, source) == source
    translator._run_formula_context_batch_request.assert_not_called()
    translator._run_batch_translation.assert_not_called()


@pytest.mark.parametrize(
    "source",
    [
        "sodium carbonate, 80°C, 18 h;",
        "Na{v11}, heat at 80°C, 18 h;",
        "Na{v11}, 80°C, 18 h then cool;",
        "Na{v11} catalyst, 80°C, 18 h;",
        "Results, 80°C, 18 h;",
        "Na{v11}, 80°C, 18 h, 95% yield",
        "(iv) protected amine, 80°C, 18 h;",
    ],
)
def test_reaction_prose_is_not_treated_as_symbol_data(source):
    assert not is_reaction_condition_label(source)
    assert not translator_stub()._is_passthrough_text(source)


@pytest.mark.parametrize(
    "target",
    [
        "Na{v11}, 80°C, 8 h;",
        "Na{v11}, 90°C, 18 h;",
        "Na{v11}, 80°C, 18 min;",
        "Na{v11}, 80°C;",
        "K{v11}, 80°C, 18 h;",
    ],
)
def test_reaction_condition_validation_rejects_changed_data(target):
    assert (
        translator_stub()._accepted_translation("Na{v11}, 80°C, 18 h;", target) is None
    )
