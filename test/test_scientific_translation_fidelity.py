import pytest

from pdf2zh.translation_quality import (
    has_changed_numeric_citations,
    has_suspicious_physics_translation,
    has_reversed_not_only_condition,
    has_incomplete_scientific_caption,
    has_new_unbalanced_prose_parentheses,
)
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source,bad,good",
    [
        (
            "We measure population recovery and conditional phase.",
            "我们测量人口恢复和条件相位。",
            "我们测量布居恢复和条件相位。",
        ),
        (
            "The pulse gives maximum population sensitivity to {v0}.",
            "该脉冲使布居对{v0}达到最大。",
            "该脉冲使布居对{v0}的灵敏度达到最大。",
        ),
    ],
)
def test_quantum_quantity_errors_reject_cache_without_rewriting(source, bad, good):
    assert has_suspicious_physics_translation(source, bad)
    assert not has_suspicious_physics_translation(source, good)
    translator = translator_stub()
    assert translator._accepted_translation(source, bad) is None
    assert translator._accepted_translation(source, good) == good


@pytest.mark.parametrize(
    "source,target",
    [
        ("The population recovery rate varies.", "人口恢复率有所变化。"),
        (
            "We model human population using quantum methods.",
            "我们用量子方法模拟人口。",
        ),
        ("The qubit population sensitivity is maximal.", "量子比特布居的敏感度最大。"),
        (
            "The pulse gives maximum population sensitivity.",
            "脈衝使布居的靈敏度達到最大。",
        ),
    ],
)
def test_demographic_and_valid_physics_wording_is_not_rejected(source, target):
    assert not has_suspicious_physics_translation(source, target)


@pytest.mark.parametrize(
    "source,bad",
    [
        ("Calibration points [51] determine the state.", "校准点决定该状态。"),
        ("The method [2, 4–6] works.", "该方法 [2, 4–5] 有效。"),
        ("Use [2] then [2].", "使用 [2]。"),
        ("Use [2].", "使用 [2, 3]。"),
    ],
)
def test_bare_numeric_citations_cannot_disappear_change_or_duplicate(source, bad):
    assert has_changed_numeric_citations(source, bad)
    assert translator_stub()._accepted_translation(source, bad) is None


@pytest.mark.parametrize(
    "source,target",
    [
        ("Use [2,4–6].", "使用［２，４—６］。"),
        ("Use [2,4–6].", "使用 [2, 4, 5, 6]。"),
        ("Use [2] and [3].", "使用 [2, 3]。"),
        ("Use `[2]` and $[3]$.", "使用 `[2]` 和 $[3]$。"),
        ("Use [41, Lemma 2.1].", "使用 [41, Lemma 2.1]。"),
    ],
)
def test_citation_typography_and_opaque_math_are_allowed(source, target):
    assert not has_changed_numeric_citations(source, target)


def test_old_bad_context_cache_is_retranslated_normally():
    translator = translator_stub()
    source = "We measure population recovery and conditional phase {v0} [51]."
    bad = "我们测量人口恢复和条件相位{v0}。"
    good = "我们测量布居恢复和条件相位{v0} [51]。"
    formulas = {"{v0}": "phi"}
    context = translator._normalize_formula_context(source, formulas)
    key = translator._formula_context_cache_key(source, context)
    translator.cache.set(key, bad)
    calls = []

    def translate(texts, contexts, **kwargs):
        calls.append(texts)
        return [good]

    translator._run_formula_context_batch_request = translate
    assert translator.translate_batch_with_formula_contexts([source], [formulas]) == [
        good
    ]
    assert calls == [[source]]
    assert translator.cache.get(key) == good


def test_expanded_calibration_condition_does_not_become_single_gate_only():
    source = (
        "When not only calibrating a single CZ{v4} gate, as in Ref. [29], "
        "but an entire continuous gate set, this variant has the same drawbacks."
    )
    bad = "在文献[29]中只校准一个CZ{v4}门的情况下，与校准整个连续门集不同，这一变体会有相同缺点。"
    assert has_reversed_not_only_condition(source, bad)
    assert translator_stub()._accepted_translation(source, bad) is None
    for good in (
        "当不仅校准文献[29]中的单个CZ{v4}门，而是校准整个连续门集时，此变体有相同缺点。",
        "在校准整个门集而非只校准文献[29]中的单个CZ{v4}门时，此变体有相同缺点。",
        "如果只校准文献[29]中的单个CZ{v4}门之外，还要校准整个门集，此变体有相同缺点。",
    ):
        assert not has_reversed_not_only_condition(source, good)
    assert not has_reversed_not_only_condition(
        "When only calibrating a single gate, this variant is efficient.",
        "在只校准单个门的情况下，这一变体很高效。",
    )


def test_caption_panels_and_cut_quantity_cannot_disappear():
    source = "Figure 1. Level diagram. (a) Energy levels. (b) Density plot."
    assert has_incomplete_scientific_caption(source, "图1。能级图。能级。密度图。")
    assert not has_incomplete_scientific_caption(
        source, "图1。能级图。（a）能级。（b）密度图。"
    )
    source = "The arrows show one-dimensional cuts approximated in Eq. (E5)."
    assert has_incomplete_scientific_caption(source, "箭头表示在式(E5)。")
    assert not has_incomplete_scientific_caption(
        source, "箭头表示由式(E5)近似的一维截线。"
    )


def test_arg_operator_cannot_become_an_unrelated_parameter():
    source = "The conditional phase arg {v0} is color coded."
    assert has_incomplete_scientific_caption(source, "条件相位参数{v0}以颜色编码。")
    for target in (
        "条件相位arg {v0}以颜色编码。",
        "条件相位，即{v0}的辐角，以颜色编码。",
    ):
        assert not has_incomplete_scientific_caption(source, target)


@pytest.mark.parametrize(
    "source,target,bad",
    [
        ("The gap, denoted {v0}, differs.", "能隙（记为{v0}）有所不同。", False),
        ("The gap, denoted {v0}, differs.", "能隙（记为{v0}有所不同。", True),
        ("The phase (with {v0} is set.", "相位（其中{v0}被设定。", False),
        ("The phase (with {v0} is set.", "相位（其中{v0}）（已设定）", False),
        ("The phase (with {v0} is set.", "相位（其中（{v0}被设定。", True),
        ("Use $f(x)$ and `f(x)`.", "使用 $f(x)$ 和 `f(x)`。", False),
    ],
)
def test_parenthesis_guard_allows_only_source_inherited_imbalance(source, target, bad):
    assert has_new_unbalanced_prose_parentheses(source, target) is bad


def test_quantum_population_does_not_become_biological_population():
    assert has_suspicious_physics_translation(
        "We measure qubit population recovery.", "我们测量量子比特的种群恢复。"
    )
