from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import is_spectroscopy_peak_list
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize("broad", ["bs", "bd", "bt", "bq", "bm"])
@pytest.mark.parametrize("multiplier", ["2 x", "2 ×", "2"])
def test_broad_peak_multiplicity_and_counts_are_preserved(broad, multiplier):
    source = f"{{v0}} NMR {{v1}} 1.17 (9H, {multiplier} {broad}), 7.50 (1H, bm);"
    translator = translator_stub()
    translator._run_formula_context_batch_request = Mock()
    translator._run_batch_translation = Mock()
    assert is_spectroscopy_peak_list(source)
    assert translator._accepted_translation(source, source) == source
    assert translator.translate_batch_with_formula_contexts(
        [source], [{"{v0}": "1H", "{v1}": "δ"}]
    ) == [source]
    translator._run_formula_context_batch_request.assert_not_called()
    translator._run_batch_translation.assert_not_called()


@pytest.mark.parametrize(
    "source",
    [
        "1.16 and 1.32 (9H, 2 x bs), 1.76 (1H, bm), 7.56 (1H, bt);",
        "1.16 and 1.32 and 1.45 (9H, 3 x bs), 1.76 (1H, bm);",
        "1.16 - 1.24 and 1.32 (9H, 2 x bs), 7.95 (1H, s);",
        "[[PDF2ZH_FLOW_0]] NMR [[PDF2ZH_FLOW_1]] 1.16 and 1.32 (9H, 2 x bs), 7.95 (1H, s);",
        "1.30 (9H, 2 x s), 2.20 (2H, dd), 2.50 (1H, dt), 3.00 (1H, td), 3.50 (1H, tt), 7.95 (1H, ddd);",
    ],
)
def test_numeric_shift_conjunctions_and_observed_multiplicities_are_data(source):
    assert is_spectroscopy_peak_list(source)
    assert translator_stub()._accepted_translation(source, source) == source


@pytest.mark.parametrize(
    "source",
    [
        "1.16 and DMSO (9H, bs), 1.32 (1H, s);",
        "1.16 (9H, bs) and 1.32 (1H, s);",
        "1.16 (9H, 2 x DMSO), 1.32 (1H, s);",
        "1.16 (9H, x bs), 1.32 (1H, s);",
        "1.16 (9H, 2 × 3), 1.32 (1H, s);",
        "1.16 (9H, bs), 1.32 (1H, m, partially obscured by DMSO peak);",
        "NMR at 1.16 and 1.32 ppm confirms the structure.",
        "1.16 (9H, broad singlet), 1.32 (1H, m);",
    ],
)
def test_multiplicity_allowlist_does_not_release_prose_or_unscoped_operators(source):
    assert not is_spectroscopy_peak_list(source)
    assert not translator_stub()._is_passthrough_text(source)


@pytest.mark.parametrize(
    "target",
    [
        "1.16 and 1.33 (9H, 2 x bs), 7.56 (1H, bt);",
        "1.16 and 1.32 (8H, 2 x bs), 7.56 (1H, bt);",
        "1.16 and 1.32 (9H, 3 x bs), 7.56 (1H, bt);",
        "1.16 and 1.32 (9H, 2 x s), 7.56 (1H, bt);",
        "1.16 and 1.32 (9H, 2 × bs), 7.56 (1H, bt);",
    ],
)
def test_data_validation_retains_every_shift_count_multiplicity_and_symbol(target):
    source = "1.16 and 1.32 (9H, 2 x bs), 7.56 (1H, bt);"
    assert translator_stub()._accepted_translation(source, target) is None
