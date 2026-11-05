import pytest

from pdf2zh.translation_quality import has_suspicious_biomedical_translation
from test_codex_formula_context import translator_stub

SOURCE = "A solution of compound 8 in DMA (3 mL) and CuCl{v12} (123 mg, 0.9 mmol) was heated."


@pytest.mark.parametrize(
    "target",
    [
        "将化合物8溶于DMA（3 mL）和CuCl{v12}中并加热。",
        "将化合物8溶于DMA（3 mL）和CuCl{v12}（123 mg）中并加热。",
        "将化合物8溶于DMA（3 mL）和CuCl{v12}（0.9 mmol）中并加热。",
        "将化合物8溶于DMA（3 mL）和CuCl{v123}（0.9 mmol）中并加热。",
        "将化合物8溶于DMA和CuCl{v12}（0.9 mmol）中[[PDF2ZH_ITALIC_123_BEGIN]]加热[[PDF2ZH_ITALIC_123_END]]。",
    ],
)
def test_reagent_pair_cannot_lose_either_number(target):
    assert has_suspicious_biomedical_translation(SOURCE, target)


@pytest.mark.parametrize(
    "target",
    [
        "将化合物8溶于DMA（3mL）和CuCl{v12}（123mg，0.9mmol）中并加热。",
        "将化合物8溶于DMA（3毫升）和CuCl{v12}（123.0毫克，.90毫摩尔）中并加热。",
        "加入CuCl{v12}（1.23e2 mg，9e-1 mmol），与化合物8的DMA溶液加热。",
    ],
)
def test_equivalent_numbers_and_chinese_units_pass(target):
    assert not has_suspicious_biomedical_translation(SOURCE, target)


def test_volume_pair_and_unrelated_numbers():
    source = "Two samples were used. Reagent A (2.5 mL, 3 mmol) was added."
    assert not has_suspicious_biomedical_translation(
        source, "使用两份样品，加入试剂A（2.50毫升，3.0毫摩尔）。"
    )
    assert has_suspicious_biomedical_translation(
        source, "使用两份样品，加入试剂A（3毫摩尔）。"
    )
    assert not has_suspicious_biomedical_translation(
        "The result was discussed in sections 2 and 3.", "在第二节和第三节讨论了结果。"
    )


def test_omitted_quantity_cache_retries():
    bad = "将化合物8溶于DMA（3 mL）和CuCl{v12}中并加热。"
    good = "将化合物8溶于DMA（3 mL）和CuCl{v12}（123 mg，0.9 mmol）中并加热。"
    translator = translator_stub()
    translator.cache.set(translator._prose_cache_key(SOURCE), bad)
    calls = []
    translator._run_single_translation = lambda text, **kw: calls.append(text) or good
    assert translator.translate(SOURCE) == good
    assert calls == [SOURCE]
