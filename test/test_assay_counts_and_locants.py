from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import (
    has_suspicious_biomedical_translation,
    is_measurement_unit_fragment,
)
from test_codex_formula_context import translator_stub


def test_complete_cyp_abundance_unit_is_preserved_without_requests():
    source = "(pmol CYP/mg HLM)"
    translator = translator_stub()
    translator._run_batch_translation = Mock()
    assert is_measurement_unit_fragment(source)
    assert translator.translate_batch([source]) == [source]
    assert translator._accepted_translation(source, source) == source
    translator._run_batch_translation.assert_not_called()
    for target in (
        source.replace("pmol", "nmol"),
        source.replace("mg", "g"),
        source.replace("HLM", "RLM"),
    ):
        assert translator._accepted_translation(source, target) is None
    for prose in (
        source + " was measured",
        "in pmol CYP/mg HLM",
        "pmol CYP/mg HLM content",
    ):
        assert not is_measurement_unit_fragment(prose)


@pytest.mark.parametrize(
    "source,wrong,right",
    [
        (
            "The result is taken from the 0 to 4 cells.",
            "结果取自0至4号细胞。",
            "结果取自0至4个细胞。",
        ),
        (
            "The marker compound 5, 5-diethyl-1, 3-diphenyl acid was used.",
            "使用标记化合物5，5,5-二乙基-1,3-二苯基酸。",
            "使用标记化合物5,5-二乙基-1,3-二苯基酸。",
        ),
    ],
)
def test_actual_count_and_locant_error_shapes_revalidate_cached_output(
    source, wrong, right
):
    assert has_suspicious_biomedical_translation(source, wrong)
    assert not has_suspicious_biomedical_translation(source, right)
    translator = translator_stub()
    translator.cache.set(source, wrong)
    translator._run_single_translation = Mock(return_value=right)
    assert translator.translate(source) == right
    translator._run_single_translation.assert_called_once()
    assert translator.cache.get(source) == right


@pytest.mark.parametrize(
    "source,target",
    [
        ("The 0 to 4 cells were numbered before use.", "使用前将0至4号细胞编号。"),
        ("Cells numbered 0 to 4 were selected.", "选择0至4号细胞。"),
        ("The result is taken from 0 to 4 cells.", "结果取自0到4个细胞。"),
        (
            "Compound 5 was used with 5,5-diethyl acid.",
            "化合物5与5,5-二乙基酸一起使用。",
        ),
        ("The compound 5,5-diethyl acid was used.", "使用了5,5-二乙基酸化合物。"),
        ("The compound 5,5-diethyl acid was used.", "使用了化合物5,5-二乙基酸。"),
    ],
)
def test_numbered_cells_and_legitimate_compound_names_are_not_reinterpreted(
    source, target
):
    assert not has_suspicious_biomedical_translation(source, target)
