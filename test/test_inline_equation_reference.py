from pdf2zh.translation_quality import normalize_scientific_cross_reference_placement
from test_codex_formula_context import translator_stub

SOURCE = "The data fit the equation ({v0} Bottom {v1}(Top {v2} Bottom)/(1 {v3}Hill Slope, where {v4} is inhibition)."
TARGET = "数据拟合于方程（{v0} Bottom {v1}(Top {v2} Bottom)/(1 {v3}Hill Slope，其中{v4}表示抑制率）。"


def test_inline_expression_is_not_a_numbered_equation_reference():
    assert normalize_scientific_cross_reference_placement(SOURCE, TARGET) == TARGET
    assert translator_stub()._accepted_translation(SOURCE, TARGET) == TARGET
    assert (
        translator_stub()._accepted_translation(SOURCE, TARGET.replace("{v2}", ""))
        is None
    )


def test_closed_equation_reference_remains_required_even_before_an_expression():
    source = (
        "The equation ({v0}) is written as {v1}/{v2}, where the variables are known."
    )
    assert (
        normalize_scientific_cross_reference_placement(
            source, "将其写作{v1}/{v2}，变量已知。{v0}"
        )
        is None
    )
