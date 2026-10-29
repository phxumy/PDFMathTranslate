from pdf2zh.translator import CodexTranslator


def test_percentage_and_field_threshold_cannot_swap_parenthetical_scopes():
    source = "Most defects ({v0} occur for fields {v1} V/m."
    context = [
        {"placeholder": "{v0}", "unicode_formula": ">50%)"},
        {"placeholder": "{v1}", "unicode_formula": "E<4"},
    ]
    valid = "大多数缺陷（{v0}出现在场强{v1} V/m时。"
    wrong = "在范围{v0} V/m内，大多数缺陷（{v1}会出现）。"
    assert CodexTranslator._validate_contextual_translation(source, valid, context)
    assert not CodexTranslator._validate_contextual_translation(source, wrong, context)


def test_closer_inside_formula_cannot_be_duplicated_outside():
    source = "The maximum ({v0} is observed."
    context = [{"placeholder": "{v0}", "unicode_formula": "T1)"}]
    assert CodexTranslator._validate_contextual_translation(
        source, "观测到最大值（{v0}。", context
    )
    assert not CodexTranslator._validate_contextual_translation(
        source, "观测到最大值（{v0}）。", context
    )


def test_inherited_fragment_delimiters_and_balanced_math_remain_valid():
    for source, target, context in (
        (
            "for {v0} after cooling)",
            "冷却后对于{v0}）",
            [{"placeholder": "{v0}", "unicode_formula": "T1"}],
        ),
        (
            "We compute {v0}.",
            "我们计算{v0}。",
            [{"placeholder": "{v0}", "unicode_formula": "f(x)"}],
        ),
        (
            "The parameter (see {v0}",
            "该参数（见{v0}",
            [{"placeholder": "{v0}", "unicode_formula": "T1"}],
        ),
    ):
        assert CodexTranslator._validate_contextual_translation(source, target, context)
