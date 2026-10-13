import pytest

from pdf2zh.translation_quality import has_suspicious_physics_translation
from pdf2zh.translator import CodexTranslator
from pdf2zh.translation_policy import ExactReplacement


@pytest.mark.parametrize("source", [
    "High-fidelity gates for near term processors",
    "High-fidelity gates for near-term quantum processors",
])
def test_near_term_processors_retain_temporal_meaning_in_reference_titles(source):
    entry = f"[15] A. Smith, {source}, Phys. Rev. A 12, 123 (2020)."
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh-cn"
    assert has_suspicious_physics_translation(source, "近端量子处理器的高保真门")
    assert not translator._apply_reference_title_replacements(
        entry, [ExactReplacement(source, "近端量子处理器的高保真门")]
    )[1]
    assert translator._apply_reference_title_replacements(
        entry, [ExactReplacement(source, "近期量子处理器的高保真门")]
    )[1]


@pytest.mark.parametrize("source,target", [
    ("Near-term processors", "近期可用的处理器"),
    ("Proximal processors", "近端处理器"),
    ("A proximal algorithm for near-term processors", "近期处理器的近端算法"),
    ("A near-term proximal processor", "近期的近端处理器"),
])
def test_proximal_or_valid_temporal_wording_is_allowed(source, target):
    assert not has_suspicious_physics_translation(source, target)
