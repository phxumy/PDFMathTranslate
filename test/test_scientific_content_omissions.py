import pytest

from pdf2zh.translation_quality import (
    has_omitted_scientific_content,
    has_changed_section_identifier,
)
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source,bad,good",
    [
        (
            "The denominator in Eq. (F4) cancels with the prefactor {v0}.",
            "式(F4)与前因子{v0}相消。",
            "式(F4)的分母与前因子{v0}相消。",
        ),
        (
            "We model the decoherence of the second excited state as in Ref. [28].",
            "我们按照参考文献[28]建模。",
            "我们按照参考文献[28]对第二激发态的退相干建模。",
        ),
        (
            "We perform a reference experiment bench-marking only {v0}.",
            "我们进行一项仅对{v0}。",
            "我们进行一个仅对{v0}开展基准测试的参考实验。",
        ),
        (
            "The cost is dominated by computing the subgradient of",
            "成本主要来自计算……的次梯度",
            "成本主要来自计算以下表达式的次梯度：",
        ),
    ],
)
def test_missing_scientific_objects_are_not_accepted_into_cache(source, bad, good):
    translator = translator_stub()
    assert has_omitted_scientific_content(source, bad)
    assert translator._accepted_translation(source, bad) is None
    assert not has_omitted_scientific_content(source, good)
    assert translator._accepted_translation(source, good) == good


@pytest.mark.parametrize(
    "source,target",
    [
        ("The denominator is constant.", "分母为常数。"),
        ("The denominator is constant.", "除數為常數。"),
        ("We use a reference experiment.", "使用一项对照实验。"),
        ("We use a reference experiment.", "我们以一项实验作为参照。"),
        ("This is our benchmark.", "这是我们的对标对象。"),
        ("We benchmark the gate.", "我们评估该门的性能。"),
        ("We are benchmarking the gate.", "我们正在评估该门的性能。"),
        ("We benchmark the gate.", "我们对该门进行性能评估。"),
        ("The second excited state is measured.", "測量第二激發態。"),
        ("We measure {v0}, ..., {v1}.", "我们测量{v0}、……、{v1}。"),
        ("The array is $[1, ... ,n]$.", "数组为 $[1, ... ,n]$。"),
        (
            "The URL is https://example.org/denominator.",
            "地址是 https://example.org/denominator。",
        ),
    ],
)
def test_valid_paraphrases_ellipsis_and_opaque_content_are_allowed(source, target):
    assert not has_omitted_scientific_content(source, target)


def test_subsection_decimal_must_not_split_across_sentences():
    source = "The data follow subsection 4.1{v0}."
    assert has_changed_section_identifier(source, "数据依据第4小节。1{v0}。")
    assert not has_changed_section_identifier(source, "数据依据第4.1小节{v0}。")
    assert not has_changed_section_identifier(source, "数据依据小节4.1{v0}。")
    assert not has_changed_section_identifier(
        "Use `subsection 4.1` as text.", "使用此文本。"
    )


def test_fitted_curve_legend_does_not_invent_a_straight_line():
    from pdf2zh.translation_quality import has_incomplete_scientific_caption

    source = (
        "FIG. 16. Amplified leakage (dots), and ﬁt (line) using the model in Eq. (F7)."
    )
    assert has_incomplete_scientific_caption(
        source, "图16. 放大泄漏（点）以及拟合（直线）。"
    )
    assert not has_incomplete_scientific_caption(
        source, "图16. 放大泄漏（点）以及拟合（曲线）。"
    )
    assert not has_incomplete_scientific_caption(
        source, "图16. 放大泄漏（点）以及拟合（线）。"
    )
    assert not has_incomplete_scientific_caption(
        source.replace("the model", "the linear model"), "图16. 拟合（直线）。"
    )
    assert not has_incomplete_scientific_caption(
        "The fit (line) is linear.", "拟合（直线）是线性的。"
    )
    assert (
        translator_stub()._accepted_translation(
            source, "图16. 放大泄漏（点），使用式(F7)中模型拟合（直线）。"
        )
        is None
    )


def test_population_recovery_near_a_quantum_gate_set():
    source = "We now study the behavior of the population recovery in the neighborhood of the continuous gate set."
    translator = translator_stub()
    assert (
        translator._accepted_translation(
            source, "我们现在研究连续门集邻域中种群恢复的行为。"
        )
        is None
    )
    assert (
        translator._accepted_translation(
            source, "我们现在研究连续门集邻域中布居恢复的行为。"
        )
        is not None
    )
    assert (
        translator._accepted_translation(
            "The population near the city gate is growing.", "城门附近的人口正在增长。"
        )
        is not None
    )


def test_constructive_interference_is_a_physics_relation():
    source = "For constructive interference, the denominator cancels."
    translator = translator_stub()
    assert (
        translator._accepted_translation(source, "对于建设性干涉，分母相消。") is None
    )
    assert (
        translator._accepted_translation(source, "对于相长干涉，分母相消。") is not None
    )
    assert (
        translator._accepted_translation(
            "The feedback is constructive.", "反馈具有建设性。"
        )
        is not None
    )


@pytest.mark.parametrize("noun", ["次梯度", "子梯度", "亚梯度", "次导数"])
def test_subgradient_lead_in_retains_relation_to_following_display(noun):
    source = "The cost is dominated by computing the subgradient of"
    translator = translator_stub()
    assert translator._accepted_translation(source, f"成本主要来自计算{noun}的") is None
    assert (
        translator._accepted_translation(source, f"成本主要来自计算下式的{noun}：")
        is not None
    )
    assert (
        translator._accepted_translation(
            "Computing the subgradient of this norm is easy.",
            "计算该范数的次梯度很容易。",
        )
        is not None
    )


def test_iid_adjective_does_not_leave_a_detached_gaussian_noun_phrase():
    source = (
        "The entries are i.i.d. Gaussian random variables with appropriate scaling."
    )
    translator = translator_stub()
    assert (
        translator._accepted_translation(
            source, "元素均为独立同分布的（i.i.d.）。经过适当缩放的高斯随机变量。"
        )
        is None
    )
    assert (
        translator._accepted_translation(
            source, "元素均为独立同分布（i.i.d.）的。经过适当缩放的高斯随机变量。"
        )
        is None
    )
    assert (
        translator._accepted_translation(
            source, "元素均为独立同分布的（i.i.d.）。具有适当缩放的高斯随机变量。"
        )
        is None
    )
    assert (
        translator._accepted_translation(
            source,
            "元素均为独立同分布的（i.i.d.）。适当缩放后的高斯随机变量（见第4.1小节）。",
        )
        is None
    )
    assert (
        translator._accepted_translation(
            source,
            "元素是独立同分布的（i.i.d.）。这些经过适当缩放的高斯随机变量满足条件。",
        )
        is not None
    )
    assert (
        translator._accepted_translation(
            source, "元素均为经过适当缩放的独立同分布（i.i.d.）高斯随机变量。"
        )
        is not None
    )
    assert (
        translator._accepted_translation(
            source, "元素是独立同分布的（i.i.d.）。它们是经过适当缩放的高斯随机变量。"
        )
        is not None
    )
