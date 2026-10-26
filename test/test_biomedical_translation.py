import pytest

from pdf2zh.translation_quality import has_suspicious_biomedical_translation
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source,bad,good",
    [
        (
            "Compound incubation with a WRN apoprotein confirmed covalent binding.",
            "化合物与WRN脱辅基蛋白孵育证实了共价结合。",
            "化合物与未结合配体的WRN蛋白孵育证实了共价结合。",
        ),
        (
            "WRN apo protein was incubated with the ligand.",
            "WRN脫輔基蛋白與配體孵育。",
            "WRN無配體蛋白與配體孵育。",
        ),
        (
            "A direct-to-biology (D2B) optimization approach was used.",
            "采用了直接生物学（D2B）优化方法。",
            "采用了直接生物测试（D2B）优化策略。",
        ),
        ("n.d. —not determined.", "日期不详—未确定。", "n.d.—未测定。"),
        (
            "WRN intact mass spectrometry confirmed covalent binding.",
            "WRN完整质量质谱证实共价结合。",
            "WRN完整蛋白质谱证实共价结合。",
        ),
        (
            "The reaction was initiated by the addition of 2x substrate mixture.",
            "向2x底物混合物中加入反应物以启动反应。",
            "加入2x底物混合物以启动反应。",
        ),
        (
            "SFC column: Lux 3 um Cellulose-4.",
            "SFC色谱柱：勒克斯3 um纤维素-4。",
            "SFC色谱柱：Lux 3 um Cellulose-4。",
        ),
    ],
)
def test_contextual_biomedical_errors_are_retried_including_cached_results(
    source, bad, good
):
    translator = translator_stub()
    translator.cache.set(translator._prose_cache_key(source), bad)
    calls = []

    def translate(text, **kwargs):
        calls.append(text)
        return good

    translator._run_single_translation = translate
    assert has_suspicious_biomedical_translation(source, bad)
    assert not has_suspicious_biomedical_translation(source, good)
    assert translator.translate(source) == good
    assert calls == [source]
    assert translator.cache.get(translator._prose_cache_key(source)) == good


@pytest.mark.parametrize(
    "source,target",
    [
        ("The apoprotein lacks its heme cofactor.", "脱辅基蛋白缺少血红素辅因子。"),
        ("The WRN apo protein lacks a cofactor.", "WRN脱辅基蛋白缺少辅因子。"),
        ("The direct biological effects were measured.", "测量了直接生物学效应。"),
        ("The WRN ligand was characterized.", "对WRN配体进行了表征。"),
        ("Lux is a unit of illumination.", "勒克斯是照度单位。"),
        (
            "The reaction was initiated by the addition of ATP to substrate mixture.",
            "向底物混合物中加入反应物ATP以启动反应。",
        ),
    ],
)
def test_biomedical_guard_does_not_rewrite_unrelated_or_explicit_cofactor_contexts(
    source, target
):
    assert not has_suspicious_biomedical_translation(source, target)
