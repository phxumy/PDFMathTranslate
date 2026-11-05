from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import has_omitted_scientific_content
from test_codex_formula_context import translator_stub

PREFIX = "A significant improvement in permeability was also observed for"
SOURCE = (
    PREFIX + " the triazine 41 ({v0} cm/s) compared to the pyrimidine "
    "40 ({v1} cm/s)."
)
MISSING = "对于三嗪41（{v0} cm/s），与嘧啶40（{v1} cm/s）相比。"
COMPLETE = "三嗪41（{v0} cm/s）与嘧啶40（{v1} cm/s）相比，渗透性也显著提高。"


def test_prose_gap_cannot_drop_the_observed_result():
    assert has_omitted_scientific_content(PREFIX, "对于")
    assert translator_stub()._accepted_translation(PREFIX, "对于") is None


@pytest.mark.parametrize("word", ["permeabil-ity", "permeabil- ity", "permeabil-\nity"])
def test_line_wrapped_quantity_still_requires_the_result(word):
    assert has_omitted_scientific_content(SOURCE.replace("permeability", word), MISSING)


def test_another_compounds_improvement_does_not_hide_a_missing_result():
    source = "The permeability of compound 1 improved. " + SOURCE
    target = "化合物1的渗透性显著提高。" + MISSING
    assert has_omitted_scientific_content(source, target)
    assert not has_omitted_scientific_content(
        source, "化合物1的渗透性显著提高。" + COMPLETE
    )


@pytest.mark.parametrize(
    "target",
    [
        COMPLETE,
        "还观察到三嗪41（{v0} cm/s）相较于嘧啶40（{v1} cm/s）的通透性明显改善。",
        "三嗪41（{v0} cm/s）的通透性更高。对照物是嘧啶40（{v1} cm/s）。",
        "三嗪41（{v0} cm/s）为目标化合物。相较于嘧啶40（{v1} cm/s），其渗透性显著提高。",
        "三嗪41（{v0} cm/s）的滲透性顯著增強，優於嘧啶40（{v1} cm/s）。",
    ],
)
def test_complete_comparison_paraphrases_are_allowed(target):
    assert not has_omitted_scientific_content(SOURCE, target)


@pytest.mark.parametrize(
    "source,target",
    [
        (
            "No significant improvement in permeability was observed.",
            "未观察到渗透性改善。",
        ),
        ("An increase in permeability was not observed.", "未观察到通透性增加。"),
        ("An improvement in permeability was expected.", "预计通透性会提高。"),
        ("Improvement in permeability is discussed below.", "下文讨论渗透性改善。"),
    ],
)
def test_negated_or_unobserved_results_are_not_inferred(source, target):
    assert not has_omitted_scientific_content(source, target)


def test_cached_incomplete_comparison_is_revalidated():
    translator = translator_stub()
    translator.max_concurrency = 1
    context = {"{v0}": "30", "{v1}": "18"}
    normalized = translator._normalize_formula_context(SOURCE, context)
    key = translator._formula_context_cache_key(SOURCE, normalized)
    translator.cache.set(key, MISSING)
    translator._run_formula_context_batch_request = Mock(return_value=[COMPLETE])
    assert translator.translate_batch_with_formula_contexts([SOURCE], [context]) == [
        COMPLETE
    ]
    assert translator.cache.get(key) == COMPLETE
    translator._run_formula_context_batch_request.assert_called_once()
