import pytest

from pdf2zh.translation_quality import normalize_scientific_cross_reference_placement
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize("target", [
    "误差增大（见图 1(a)）。", "误差增大(参见图1(a))。", "误差增大（详见图1(a)的结果）。",
    "误差增大[参见图1(a)]。", "误差增大（详见[图1(a)]）。", "误差增大，见图1(a)。",
])
def test_balanced_prose_wrapper_does_not_acquire_an_extra_parenthesis(target):
    source = "The error grows (see Figure 1(a))."
    assert normalize_scientific_cross_reference_placement(source, target) == target
    assert translator_stub()._accepted_translation(source, target) == target


@pytest.mark.parametrize("source,target", [
    ("See Fig. 1(d).", "参见（图1(d)。"),
    ("See Fig. 1(d).", "参见[图1(d)。"),
    ("See Fig. 1(d).", "参见图1(d))。"),
    ("See Fig. 1(d).", "参见[图1(d)]]。"),
])
def test_unmatched_outer_delimiters_still_cannot_be_accepted(source, target):
    result = translator_stub()._accepted_translation(source, target)
    assert result is None or result.count('(') == result.count(')')


def test_actual_parenthetical_figure_reference_keeps_all_scientific_prose():
    source = "The performance of methods based on the {v7} norm deteriorates with dimension (see Figure 1(a))."
    target = "基于{v7}范数的方法的性能随维数增加而下降（见图1(a)）。"
    assert translator_stub()._accepted_translation(source, target) == target


@pytest.mark.parametrize("source,target", [
    # The original opaque formula includes the matching ] or ) glyph.
    ("Set the phase at [{v3} interval in Eq. (2).",
     "设置相位（位于式（2）中的 [{v3}区间内）。"),
    ("The population ({v0} is shown in Fig. 1.",
     "布居（{v0}如图1所示。"),
])
def test_opaque_formula_delimiters_do_not_cause_spurious_reference_repair(source, target):
    assert normalize_scientific_cross_reference_placement(source, target) == target
    assert translator_stub()._accepted_translation(source, target) == target
