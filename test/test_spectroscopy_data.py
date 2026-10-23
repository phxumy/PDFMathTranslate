import pytest
from test_codex_formula_context import translator_stub

from pdf2zh.translation_quality import (
    has_suspicious_english_residue,
    is_spectroscopy_peak_list,
)

PEAKS = (
    "1H), 6.43 (s, 1H), 5.07 (d, {v0} Hz, 1H), 4.97{v1}, "
    "4.56 (d, {v2} Hz, 1H{v3} Hz, 1H{v4} {v5}. "
)


def test_fully_numeric_nmr_continuation_is_data_not_untranslated_prose():
    assert is_spectroscopy_peak_list(PEAKS)
    translator = translator_stub()
    translator._run_batch_translation = lambda *args, **kwargs: pytest.fail(
        "NMR data must remain original"
    )
    assert translator.translate_batch([PEAKS]) == [PEAKS]
    assert translator.translate_batch_with_formula_contexts(
        [PEAKS], [{"{v0}": "J=10"}]
    ) == [PEAKS]
    assert translator._accepted_translation(PEAKS, PEAKS) is not None
    assert (
        translator._accepted_translation(PEAKS, PEAKS.replace("6.43", "6.44")) is None
    )
    assert translator._accepted_translation(PEAKS, PEAKS.replace("(s,", "(d,")) is None


def test_compact_peak_list_and_mass_spectrum_labels_remain_original():
    source = "10.6, 1.7 Hz, 1H{v68} 4.05 (m, 1H{v69} Hz, 3H). MS (ESI{v71} [M {v72}."
    assert is_spectroscopy_peak_list(source)
    assert translator_stub().translate_batch([source]) == [source]
    assert not is_spectroscopy_peak_list(source + " The compound was purified.")
    assert is_spectroscopy_peak_list(
        "{v51} NMR (500 MHz, DMSO-{v52} 6.23 (m, 3H{v53} Hz, 1H), 5.11{v56} 2H), 4.17{v58} 4H)."
    )
    assert is_spectroscopy_peak_list(
        "{v11} NMR (400 MHz, DMSO-{v12} {v13} 2H{v14} Hz, 2H{v15}."
    )


@pytest.mark.parametrize(
    "text",
    [
        "The peaks are 6.43 and 5.07 ppm.",
        "6.43 and 5.07 ppm",
        "6.43 (s, 1H), 5.07 (d, 1H) in Chloroform-d",
        "The operation takes 6.43 s and reaches 5.07 Hz.",
        "6.43 (s, 1H), 5.07 (d, 1H), measured at room temperature",
        "6.43 (s, 1H)",
        "Figure 6.43 shows the 5.07 Hz result.",
        "数据为 6.43 (s, 1H), 5.07 (d, 1H)",
    ],
)
def test_nmr_evidence_cannot_exempt_surrounding_prose(text):
    assert not is_spectroscopy_peak_list(text)
    assert not translator_stub()._is_passthrough_text(text)


def test_unchanged_measurements_do_not_make_translated_analytical_labels_look_copied():
    peaks = "152.96, 151.87, 148.42, 137.61, 136.32, 134.28, 127.89, 127.26, 104.50, 100.94, 98.92, 91.74, 87.38, 80.41, 41.60, 40.66, 39.46, 37.39, 28.33, 17.33. "
    source = (
        peaks
        + "HRMS (ESI) for {v0} [M {v1}, calcd: 491.1460, found: 491.1466. HPLC purity: 98.96%."
    )
    target = (
        peaks
        + "HRMS（ESI）中{v0}的[M {v1}，计算值：491.1460，实测值：491.1466。HPLC纯度：98.96%。"
    )
    assert not has_suspicious_english_residue(source, target)
    assert translator_stub()._accepted_translation(source, target) is not None
    assert has_suspicious_english_residue(source, source)
    assert translator_stub()._accepted_translation(source, source) is None
    untranslated = peaks + "The measured result for the sample is the same as before."
    assert has_suspicious_english_residue(untranslated, untranslated)
