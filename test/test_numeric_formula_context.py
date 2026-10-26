import pytest

from pdf2zh.translator import CodexTranslator


def test_protected_subscript_is_not_confused_with_repeated_compound_numbers():
    source = "Methylation of CH{v0} improved 29. It was more stable than 21."
    target = "对CH{v0}甲基化改善了29。29比21更稳定。"
    context = [{"placeholder": "{v0}", "unicode_formula": "2"}]
    assert CodexTranslator._validate_contextual_translation(source, target, context)


@pytest.mark.parametrize(
    "target", ["对CH2{v0}进行了甲基化。", "数值2对应CH{v0}的下标。"]
)
def test_actual_subscript_expansion_remains_rejected(target):
    assert not CodexTranslator._validate_contextual_translation(
        "Methylation of CH{v0} was studied.",
        target,
        [{"placeholder": "{v0}", "unicode_formula": "2"}],
    )


def test_placeholder_number_cannot_hide_an_expanded_numeric_formula():
    assert not CodexTranslator._validate_contextual_translation(
        "The number is {v2}.",
        "该数为2{v2}。",
        [{"placeholder": "{v2}", "unicode_formula": "2"}],
    )
