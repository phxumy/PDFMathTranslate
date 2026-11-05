import pytest

from pdf2zh.translation_quality import has_suspicious_biomedical_translation
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "solvent", ["氯甲烷", "一氯甲烷", "单氯甲烷", "三氯甲烷", "四氯甲烷"]
)
def test_dichloromethane_cannot_change_chlorine_count(solvent):
    source = "The product was purified by silica chromatography (dichloromethane)."
    assert has_suspicious_biomedical_translation(
        source, f"产物经硅胶色谱（{solvent}）纯化。"
    )


@pytest.mark.parametrize(
    "source,target",
    [
        ("Dichloromethane (5 mL) was added.", "加入二氯甲烷（5 mL）。"),
        ("Dichloromethane {v0} was added.", "加入二氯甲烷{v0}。"),
        ("Dichloromethane {v0} was used.", "使用CH{v0}Cl2。"),
        ("Dichloromethane was added.", "加入DCM。"),
        ("CH{v0}Cl{v1} was used.", "使用CH{v0}Cl{v1}。"),
        (
            "Dichloromethane and chloromethane were compared.",
            "比较了二氯甲烷和氯甲烷。",
        ),
        ("Dichloromethane was mixed with chloroform.", "二氯甲烷与三氯甲烷混合。"),
        (
            "Dichloromethane and carbon tetrachloride were used.",
            "使用二氯甲烷和四氯甲烷。",
        ),
        ("A dichloromethane-based model was used.", "使用氯甲烷模型。"),
        ("The sample was dissolved in methanol.", "样品溶于氯甲烷。"),
        (
            "Dichloromethane was added.",
            "加入[[PDF2ZH_ITALIC_0_BEGIN]]二[[PDF2ZH_ITALIC_0_END]]氯甲烷。",
        ),
    ],
)
def test_solvent_guard_keeps_explicit_other_solvents_and_protected_formulas(
    source, target
):
    assert not has_suspicious_biomedical_translation(source, target)


def test_historical_wrong_solvent_cache_is_revalidated_before_reuse():
    source = "The product was purified by silica chromatography (dichloromethane)."
    bad = "产物经硅胶色谱（氯甲烷）纯化。"
    good = "产物经硅胶色谱（二氯甲烷）纯化。"
    translator = translator_stub()
    translator.cache.set(translator._prose_cache_key(source), bad)
    calls = []

    def request(text, **kwargs):
        calls.append(text)
        return good

    translator._run_single_translation = request
    assert translator.translate(source) == good
    assert calls == [source]
    assert translator.cache.get(translator._prose_cache_key(source)) == good
