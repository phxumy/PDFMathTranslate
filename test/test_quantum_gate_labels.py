import pytest

from pdf2zh.translation_policy import is_quantum_gate_label


@pytest.mark.parametrize(
    "text",
    [
        "iSWAP",
        "SWAP",
        "CNOT",
        "CZ",
        "CCX",
        "QFT",
        "ECP†",
        "√iSWAP",
        "√SWAP",
        "SWAP1/4",
        "SWAP^1/4",
        "SWAP^(1/4)",
        "SWAP^{1/4}",
        " fSim ",
    ],
)
def test_standalone_standard_gate_identifiers_are_preserved(text):
    assert is_quantum_gate_label(text)


@pytest.mark.parametrize(
    "text",
    [
        "iSWAP gate",
        "The CNOT gate",
        "CNOT and iSWAP",
        "swap",
        "CV analysis",
        "SWAP gates are efficient",
        "QFT results",
        "SWAP^0/4",
        "SWAP1/0",
        "SWAP^(1/4",
        "SWAP^1/4)",
        "swap text",
        "SWAP{x}",
        "",
        "CV.",
    ],
)
def test_prose_and_unknown_math_suffixes_still_need_translation(text):
    assert not is_quantum_gate_label(text)
