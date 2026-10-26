import pytest

from pdf2zh.translation_quality import is_assay_measurement_label
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source",
    [
        "WRN ADP-Glo IC{v10}",
        "BLM ADP-Glo ({v13}",
        "HCT116 p21 EC{v18}(MSI, {v19}",
        "CL (mL/min/kg{v42}",
        "HCT116 CTG IC50 (MSI, μM)",
        "SW620 CTG IC50 (MSS, μM)",
        "H, Mu, R, D, Hep Cl{v73}",
        "Caco-2 {v71}",
        "HCT116 CTG (MSI, {v64}",
    ],
)
def test_assay_symbols_keep_identity_units_and_values_without_model_calls(source):
    translator = translator_stub()
    translator._run_batch_translation = lambda *a, **kw: pytest.fail(
        "label needs no translation"
    )
    assert is_assay_measurement_label(source)
    assert translator.translate_batch([source]) == [source]
    assert translator._accepted_translation(source, source) == source
    assert translator._accepted_translation(source, source + " 10") is None


@pytest.mark.parametrize(
    "source",
    [
        "WRN C727A mutant ADP-Glo IC{v0}",
        "WRN ADP-Glo inhibition IC50",
        "HCT116 p21 expression increases",
        "CL was reduced in mice.",
        "CL (mL/min/kg) is high",
        "IC50 VALUE",
        "solubility (μM)",
        "WRN ADP-Glo IC50 和细胞活性",
        "IC50 = unknown",
        "The assay uses ADP-Glo.",
    ],
)
def test_assay_label_recognition_does_not_exempt_prose(source):
    assert not is_assay_measurement_label(source)
