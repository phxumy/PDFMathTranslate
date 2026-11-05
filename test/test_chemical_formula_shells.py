from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import chemical_formula_elements
from pdf2zh.translator import CodexTranslator
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source,value,target",
    [
        ("Use CH{v0} as solvent.", "_{3}CN", "使用CH{v0}CN作为溶剂。"),
        ("Use CH{v0} as solvent.", "_{3}CN", "使用{v0}作为溶剂。"),
        ("Use CH{v0} as solvent.", "_{3}CN", "使用NH{v0}作为溶剂。"),
        ("Add Na{v0}.", "_{2}CO_{3}", "加入K{v0}。"),
        ("Add Na{v0}.", "_{2}CO_{3}", "加入Na{v0}CO3。"),
        ("Use CH{v0}CN.", "_{3}", "使用CH{v0}。"),
        ("Use CH{v0}CN.", "_{3}", "使用CH{v0}CO。"),
        ("Use {v0}.", "CH_{3}CN", "使用CH3{v0}CN。"),
        ("Use H{v0}O.", "_{2}", "使用H{v0}O2。"),
    ],
)
def test_split_chemical_formula_shell_cannot_gain_lose_or_change_atoms(
    source, value, target
):
    translator = translator_stub()
    context = translator._normalize_formula_context(source, {"{v0}": value})
    assert translator._accepted_contextual_translation(source, target, context) is None


@pytest.mark.parametrize(
    "source,value,target",
    [
        ("Use CH{v0} as solvent.", "_{3}CN", "使用CH{v0}作为溶剂。"),
        ("Add Na{v0}.", "_{2}CO_{3}", "加入Na{v0}。"),
        ("Use CH{v0}CN.", "_{3}", "使用CH{v0}CN。"),
        ("Measure CO{v0}with care.", "_{2}", "仔细测量CO{v0}。"),
        ("The field {v0}MHz is applied.", "ω", "施加{v0}MHz的场。"),
        ("The concentration{v0} is measured.", "c", "测量浓度{v0}。"),
        ("The noise{v0} is measured.", "N", "测量噪声{v0}。"),
        ("Use Na{v0} at 2 mM.", "_{2}CO_{3}", "使用2 mM的Na{v0}。"),
    ],
)
def test_unchanged_chemical_shells_and_adjacent_prose_units_are_allowed(
    source, value, target
):
    translator = translator_stub()
    context = translator._normalize_formula_context(source, {"{v0}": value})
    assert (
        translator._accepted_contextual_translation(source, target, context) == target
    )


def test_plain_route_protects_multi_element_prefix_without_readonly_context():
    source = "Use CH{v0} as solvent."
    assert not CodexTranslator._validate_formula_translation(
        source, "使用{v0}作为溶剂。"
    )
    assert not CodexTranslator._validate_formula_translation(
        source, "使用NH{v0}作为溶剂。"
    )
    assert CodexTranslator._validate_formula_translation(source, "使用CH{v0}作为溶剂。")
    assert "CH{v0}" in CodexTranslator._compact_formula_atoms(source)
    assert not CodexTranslator._compact_formula_atoms(
        "The concentration{v0} is measured."
    )


def test_formula_cache_revalidates_partial_context_expansion():
    translator = translator_stub()
    translator.max_concurrency = 1
    source = "Use CH{v0} as solvent."
    context = {"{v0}": "_{3}CN"}
    valid = "使用CH{v0}作为溶剂。"
    key = translator._formula_context_cache_key(source, context)
    translator.cache.set(key, "使用CH{v0}CN作为溶剂。")
    translator._run_formula_context_batch_request = Mock(return_value=[valid])
    assert translator.translate_batch_with_formula_contexts([source], [context]) == [
        valid
    ]
    translator._run_formula_context_batch_request.assert_called_once()
    assert translator.cache.get(key) == valid


def test_styled_context_cache_and_new_candidates_both_reject_partial_expansion():
    translator = translator_stub()
    translator.max_concurrency = 1
    begin = "[[PDF2ZH_ITALIC_0_BEGIN]]"
    end = "[[PDF2ZH_ITALIC_0_END]]"
    source = f"Use {begin}dry{end} CH{{v0}} as solvent."
    valid = f"使用{begin}干燥的{end}CH{{v0}}作为溶剂。"
    bad = f"使用{begin}干燥的{end}CH{{v0}}CN作为溶剂。"
    context = {"{v0}": "_{3}CN"}
    key = translator._styled_cache_key(source, context)
    translator.cache.set(key, bad)
    translator._run_styled_batch_request = Mock(side_effect=[[bad], [valid]])
    assert translator.translate_styled_batch([source], [context]) == [valid]
    assert translator._run_styled_batch_request.call_count == 2
    assert translator.cache.get(key) == valid


def test_element_parser_is_shared_and_does_not_treat_prose_as_a_formula():
    assert chemical_formula_elements("Na₂CO₃") == ("Na", "C", "O")
    assert chemical_formula_elements("CH3CN") == ("C", "H", "C", "N")
    assert chemical_formula_elements("MHz") == ()
    assert chemical_formula_elements("concentration") == ()
