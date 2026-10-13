import pytest

from pdf2zh.translator import CodexTranslator
from test_codex_formula_context import translator_stub


SOURCE = "Thus, if {v64} is {v65}Lipschitz w.r.t. the {v66} norm, the output {v67} has suboptimality bounded by"


@pytest.mark.parametrize("target", [
    "因此，如果{v64}关于{v66}范数是{v65}Lipschitz 连续的，则输出{v67}的次优性上界为",
    "因此，若{v64}是关于{v66}范数的{v65}-Lipschitz 函数，则输出{v67}的次优性上界为",
    "若{v64}关于{v66}範數是{v65}Lipschitz 連續的，則輸出{v67}的次優性上界為",
    "若{v64}相对于{v66}范数而言是{v65}-Lipschitz 连续的，则输出{v67}的次优性上界为",
])
def test_norm_qualifier_order_preserves_each_labelled_mathematical_object(target):
    assert CodexTranslator._validate_formula_translation(SOURCE, target)
    assert translator_stub()._accepted_translation(SOURCE, target) == target


@pytest.mark.parametrize("target", [
    "若{v64}是{v66}Lipschitz连续的，关于{v65}范数，则输出{v67}有界",
    "若{v66}关于{v64}范数是{v65}Lipschitz连续的，则输出{v67}有界",
    "若{v64}关于{v66}范数，则输出{v67}是{v65}Lipschitz连续的",
    "若{v64}关于{v66}范数。另一函数是{v65}Lipschitz的，输出{v67}有界",
    "若{v64}关于{v66}范数是{v65}Lipschitz的，输出{v67}、{v66}有界",
    "若{v64}关于{v66}范数是Lipschitz的，输出{v67}有界",
    "若{v64}关于{v66}范数小于{v65}Lipschitz，输出{v67}有界",
    "若{v64}关于{v66}范数，与此同时另一个函数是{v65}Lipschitz的，输出{v67}有界",
    "若{v64}关于{v66}范数：是{v65}Lipschitz的，输出{v67}有界",
])
def test_unlabelled_changed_or_nonlocal_formula_order_is_rejected(target):
    assert not CodexTranslator._validate_formula_translation(SOURCE, target)


def test_other_mathematical_relations_keep_strict_sequence():
    assert not CodexTranslator._validate_formula_translation(
        "{v0} is bounded by {v1} in the {v2} norm", "{v0}在{v2}范数下由{v1}界定"
    )
    assert not CodexTranslator._validate_formula_translation(
        SOURCE + " and {v65}", "{v64}关于{v66}范数是{v65}Lipschitz的，输出{v67}和{v65}"
    )
