from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import is_spectroscopy_peak_list
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source",
    [
        "m/z (ES{v15} (M{v16} {v17} 201.",
        "m/z (ES+) (M+H)+ = 201.",
        "m/z (ES-) [M-H]- = 199.",
        "m/z (ESI+) 201.1234 [M+H]+.",
        "HRMS: m/z (ESI) [M+Na]+ = 223.1053",
        "MS m/z (MALDI-TOF) [M+H]+ 201.1234",
        "m/z = 201.1234",
        "m/z (ES{v36} (M{v37} - tBu {v38} 310.",
        "m/z (ES[[PDF2ZH_FLOW_0]] (M[[PDF2ZH_FLOW_1]] = 201.",
    ],
)
def test_complete_mass_readouts_need_no_translation(source):
    translator = translator_stub()
    translator._run_formula_context_batch_request = Mock()
    translator._run_batch_translation = Mock()
    assert is_spectroscopy_peak_list(source)
    assert translator._accepted_translation(source, source) == source
    assert translator.translate_batch_with_formula_contexts(
        [source], [{"{v15}": "+)", "{v16}": "+H)+", "{v17}": "="}]
    ) == [source]
    translator._run_formula_context_batch_request.assert_not_called()
    translator._run_batch_translation.assert_not_called()


@pytest.mark.parametrize(
    "source",
    [
        "m/z (ES+) found 201.1234.",
        "m/z (ES+) calculated for the product 201.1234.",
        "m/z (ES+) = 201, which confirms the product.",
        "A peak at m/z 201 was observed.",
        "m/z is the mass-to-charge ratio.",
        "m/z (ES+) = 201; purified by chromatography.",
    ],
)
def test_mass_readout_prose_still_requires_translation(source):
    assert not is_spectroscopy_peak_list(source)
    assert not translator_stub()._is_passthrough_text(source)


@pytest.mark.parametrize(
    "target",
    [
        "m/z (ES{v15} (M{v16} {v17} 202.",
        "m/z (ES{v15} (M{v16} 201.",
        "m/z (EI{v15} (M{v16} {v17} 201.",
        "m/z (ES{v15} (M{v16} {v17} 201.1",
    ],
)
def test_mass_readout_numbers_symbols_and_placeholders_cannot_change(target):
    source = "m/z (ES{v15} (M{v16} {v17} 201."
    assert translator_stub()._accepted_translation(source, target) is None


def test_mass_neutral_loss_group_must_remain_exact():
    source = "m/z (ES+) (M - tBu + H)+ 310."
    translator = translator_stub()
    assert translator._accepted_translation(source, source) == source
    assert translator._accepted_translation(source, source.replace("tBu", "Bu")) is None
    assert (
        translator._accepted_translation(source, source.replace("tBu", "叔丁基"))
        is None
    )
