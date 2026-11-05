from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import has_omitted_scientific_content
from test_codex_formula_context import translator_stub

SOURCE = (
    "The half-lives were <24 min; except for 35, half-life {v1} min "
    "in rat hepatocytes."
)
WRONG = "半衰期低于24min；除化合物35外，大鼠肝细胞中的半衰期为{v1}min。"
RIGHT = "半衰期低于24min；化合物35例外，其在大鼠肝细胞中的半衰期为{v1}min。"


def test_exception_value_cannot_be_assigned_to_the_excluded_group():
    assert has_omitted_scientific_content(SOURCE, WRONG)
    assert has_omitted_scientific_content(
        SOURCE.replace("{v1}", "=55"), WRONG.replace("{v1}", "55")
    )


@pytest.mark.parametrize(
    "target",
    [
        RIGHT,
        "除化合物35（半衰期为{v1}min）外，半衰期均低于24min。",
        "半衰期低于24min，35除外，它在大鼠肝细胞中的半衰期为{v1}min。",
        "半衰期低于24min；除化合物35外，该化合物的半衰期为{v1}min。",
    ],
)
def test_explicit_exception_and_anaphor_are_allowed(target):
    assert not has_omitted_scientific_content(SOURCE, target)


def test_a_source_that_really_excludes_the_compound_keeps_that_scope():
    source = "Except for compound 35, the half-lives of all compounds were 55 min."
    assert not has_omitted_scientific_content(
        source, "除化合物35外，所有化合物的半衰期为55min。"
    )


def test_actual_cached_exception_is_revalidated():
    translator = translator_stub()
    translator.max_concurrency = 1
    source = "Except for 35, half-life {v1} min in rat hepatocytes."
    wrong = "除化合物35外，大鼠肝细胞中的半衰期为{v1}min。"
    right = "化合物35例外，其在大鼠肝细胞中的半衰期为{v1}min。"
    context = {"{v1}": "=55"}
    key = translator._formula_context_cache_key(
        source, translator._normalize_formula_context(source, context)
    )
    translator.cache.set(key, wrong)
    translator._run_formula_context_batch_request = Mock(return_value=[right])
    translator._run_batch_translation = Mock(return_value=[right])
    assert translator.translate_batch_with_formula_contexts([source], [context]) == [
        right
    ]
    assert (
        translator._run_formula_context_batch_request.call_count
        + translator._run_batch_translation.call_count
    ) == 1


DIOXANE = "Catalyst was added to substrate (3.9 mmol) in dioxane (31 mL) and the mixture stirred at 100 °C."


def test_existing_solvent_is_not_recast_as_a_later_addition():
    assert has_omitted_scientific_content(
        DIOXANE, "将催化剂加入底物中，再加入二氧六环（31 mL），然后搅拌。"
    )
    assert not has_omitted_scientific_content(
        DIOXANE, "将催化剂加入底物在二氧六环（31 mL）中的溶液，随后搅拌。"
    )


def test_real_later_addition_and_different_volume_are_not_inferred():
    assert not has_omitted_scientific_content(
        "Catalyst was added to substrate. Dioxane (31 mL) was then added and the mixture stirred.",
        "将催化剂加入底物，然后加入二氧六环（31 mL）。",
    )
    assert not has_omitted_scientific_content(DIOXANE, "随后加入二氧六环（2 mL）洗涤。")
