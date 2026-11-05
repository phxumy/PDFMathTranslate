from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import has_suspicious_biomedical_translation
from test_codex_formula_context import translator_stub

DESIGN = "In designing new targets with improved physical properties, our selection of substituents was guided by cLogD."
WRONG_DESIGN = "在设计物理性质改善的新靶标时，取代基选择以cLogD为指导。"
RIGHT_DESIGN = "在设计物理性质改善的新目标化合物时，取代基选择以cLogD为指导。"


@pytest.mark.parametrize("wrong", ["靶标", "靶点", "靶標", "靶點"])
def test_synthetic_target_compounds_are_not_biological_targets(wrong):
    assert has_suspicious_biomedical_translation(
        DESIGN, WRONG_DESIGN.replace("靶标", wrong)
    )
    assert not has_suspicious_biomedical_translation(DESIGN, RIGHT_DESIGN)


@pytest.mark.parametrize(
    "source,target",
    [
        (
            "We identified new biological targets for these inhibitors.",
            "我们发现了这些抑制剂的新生物靶标。",
        ),
        (
            "The protein targets have different physical properties.",
            "蛋白质靶标具有不同物理性质。",
        ),
        (
            "In designing new targets with improved physical properties, we adjusted the protein surface.",
            "在设计物理性质改善的新靶标时，我们调整了蛋白表面。",
        ),
        (
            "The substituents improve binding to new targets.",
            "取代基改善了与新靶点的结合。",
        ),
    ],
)
def test_biological_or_underspecified_target_contexts_remain_allowed(source, target):
    assert not has_suspicious_biomedical_translation(source, target)


def test_synthetic_target_cache_is_revalidated_and_replaced():
    translator = translator_stub()
    translator.cache.set(DESIGN, WRONG_DESIGN)
    translator._run_single_translation = Mock(return_value=RIGHT_DESIGN)
    assert translator.translate(DESIGN) == RIGHT_DESIGN
    translator._run_single_translation.assert_called_once()
    assert translator.cache.get(DESIGN) == RIGHT_DESIGN


@pytest.mark.parametrize(
    "source,target",
    [
        ("The solid was dissolved in dioxane.", "将固体溶于dioxane。"),
        ("The reaction used dioxane (2 mL).", "反应使用dioxane (2 mL)。"),
        ("Reagents: Na{v0}dioxane, 100 °C, 1 h.", "试剂：Na{v0}dioxane，100 °C，1 h。"),
        ("The mixture was heated in 1,4-dioxane.", "混合物在1,4-dioxane中加热。"),
    ],
)
def test_standalone_procedural_dioxane_must_be_translated(source, target):
    assert has_suspicious_biomedical_translation(source, target)
    assert translator_stub()._accepted_translation(source, target) is None


@pytest.mark.parametrize(
    "source,target",
    [
        ("The solid was dissolved in dioxane.", "将固体溶于二氧六环。"),
        ("The benzodioxane scaffold was investigated.", "研究了benzodioxane骨架。"),
        ("The dioxane-2-yl derivative was heated.", "对dioxane-2-yl衍生物加热。"),
        ("The supplier is Dioxane Research Ltd.", "供应商是Dioxane Research Ltd。"),
        (
            "The dioxane ring system has two oxygen atoms.",
            "dioxane环系含有两个氧原子。",
        ),
    ],
)
def test_solvent_guard_does_not_expand_into_ring_names_or_supplier_names(
    source, target
):
    assert not has_suspicious_biomedical_translation(source, target)


def test_dioxane_next_to_protected_locants_revalidates_old_context_cache():
    translator = translator_stub()
    translator.max_concurrency = 1
    source = "The reagents were Na{v0}dioxane, 100 °C, 1 h."
    context = {"{v0}": "_{2}CO_{3},1,4-"}
    wrong = "试剂为Na{v0}dioxane，100 °C，1 h。"
    right = "试剂为Na{v0}二氧六环，100 °C，1 h。"
    key = translator._formula_context_cache_key(source, context)
    translator.cache.set(key, wrong)
    translator._run_formula_context_batch_request = Mock(return_value=[right])
    assert translator.translate_batch_with_formula_contexts([source], [context]) == [
        right
    ]
    translator._run_formula_context_batch_request.assert_called_once()
    assert translator.cache.get(key) == right


def test_metabolism_table_title_cannot_invent_microsomal_test_system():
    source = (
        "Table 4. Rat{v1} and Human in Vitro Metabolism and Rat in Vivo DMPK Properties"
    )
    wrong = "表4. 大鼠{v1}和人肝微粒体体外代谢及大鼠体内DMPK性质"
    right = "表4. 大鼠{v1}和人体外代谢及大鼠体内DMPK性质"
    translator = translator_stub()
    assert has_suspicious_biomedical_translation(source, wrong)
    translator.cache.set(source, wrong)
    translator._run_single_translation = Mock(return_value=right)
    assert translator.translate(source) == right
    assert translator.cache.get(source) == right
    translator._run_single_translation.assert_called_once()


@pytest.mark.parametrize("system", ["Liver Microsomes", "HLM", "RLM"])
def test_explicit_microsomal_source_is_allowed_in_metabolism_table_title(system):
    source = f"Table 4. Rat and Human in Vitro Metabolism in {system}"
    assert not has_suspicious_biomedical_translation(
        source, "表4. 大鼠和人肝微粒体体外代谢"
    )
