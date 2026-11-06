from types import SimpleNamespace
from unittest.mock import Mock

from test_codex_formula_context import converter_with, paragraph, translator_stub

SOURCE = "CL{v1} is calculated through "
CONTEXT = {"position": "before-math", "left_math": "", "right_math": "CLint ="}


def test_before_math_calculation_prefix_cannot_drop_its_calculation_relation():
    translator = translator_stub()
    assert translator._accepted_annotation(SOURCE, "CL{v1}通过", CONTEXT) is None
    for target in ("CL{v1}按下式计算：", "CL{v1}由下式给出：", "CL{v1}的计算公式为"):
        assert translator._accepted_annotation(SOURCE, target, CONTEXT) == target
    assert (
        translator._accepted_annotation(
            "The sample passes through", "样品通过", CONTEXT
        )
        == "样品通过"
    )


def test_cached_calculation_prefix_is_revalidated_using_its_fixed_math_context():
    translator = translator_stub()
    key = translator._annotation_cache_key(SOURCE, CONTEXT)
    translator.cache.set(key, "CL{v1}通过")
    translator.cache.set(SOURCE, "CL{v1}通过")
    translator._run_annotation_batch = Mock(return_value=["CL{v1}按下式计算："])
    assert translator.translate_formula_annotations([SOURCE], [CONTEXT]) == [
        "CL{v1}按下式计算："
    ]
    translator._run_annotation_batch.assert_called_once()
    assert translator.cache.get(key) == "CL{v1}按下式计算："


def test_only_image_side_prose_with_right_math_uses_the_annotation_contract():
    calls = []
    translator = SimpleNamespace(name="codex", lang_out="zh-cn")
    translator.translate_formula_annotations = lambda texts, contexts: calls.append(
        ("annotation", texts, contexts)
    ) or ["CL{v1}按下式计算："]
    translator.translate_batch = (
        lambda texts: calls.append(("ordinary", texts)) or texts
    )
    converter = converter_with(translator)
    prefix, suffix = paragraph(), paragraph()
    prefix.region_kind = suffix.region_kind = "formula_prose"
    prefix.formula_annotation_context = CONTEXT
    suffix.formula_annotation_context = {
        "position": "label",
        "left_math": "",
        "right_math": "",
    }
    outputs = converter._translate_planned_segments(
        [SOURCE, "and the values follow"], [prefix, suffix], [], 600
    )
    assert outputs == ["CL{v1}按下式计算：", "and the values follow"]
    assert calls == [
        ("annotation", [SOURCE], [CONTEXT]),
        ("ordinary", ["and the values follow"]),
    ]
