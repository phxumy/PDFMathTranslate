import pytest

from pdf2zh.codex_errors import CodexTranslationValidationError
from pdf2zh.translation_policy import ExactReplacement
from test_codex_formula_context import translator_stub


def test_mixed_case_biomarker_pair_is_not_untranslated_title_prose():
    source = "Resistance to Treatment in dMMR/MSI-H Metastatic Cancers"
    target = "dMMR/MSI-H转移性癌症对治疗的耐药性"
    translator = translator_stub()
    assert translator._accepted_exact_reference_title(source, target) == target
    assert (
        translator._accepted_exact_reference_title(
            source, "dMMR/MSI-H Metastatic Cancers的治疗耐药"
        )
        is None
    )


def test_biomedical_terminology_guard_applies_to_reference_title_cache():
    source = "Direct-to-Biology Drives Drug Optimization"
    translator = translator_stub()
    assert (
        translator._accepted_exact_reference_title(source, "直接进入生物学推动药物优化")
        is None
    )
    assert (
        translator._accepted_exact_reference_title(source, "直接生物测试推动药物优化")
        is not None
    )
    assert "直接生物测试" in translator._build_exact_reference_title_prompt([source])


@pytest.mark.parametrize(
    "response",
    [
        [],
        [ExactReplacement("A clear scientific title", "A clear scientific title")],
        [ExactReplacement("A clear scientific title", "清晰的 scientific title")],
    ],
)
def test_known_title_cannot_silently_remain_english_after_empty_model_response(
    response,
):
    translator = translator_stub()
    translator._run_reference_title_batch = lambda entries: [response for _ in entries]
    with pytest.raises(CodexTranslationValidationError):
        translator.translate_reference_entries(
            ["(1) Smith, A. A clear scientific title. Nature 2020, 10 (2), 10-20."]
        )
    assert not translator.cache.values


def test_one_sided_continued_title_accepts_complete_supplement_issue_metadata():
    translator = translator_stub()
    left = "(25) Rao, Y.; Jones, B.; "
    title = "Abstract 6393: Patient Selection and Target Engagement"
    right = "Smith, A. " + title + ". CancerRes. 2025, 85 (8_Supplement_1),6393."
    assert (
        translator._validated_reference_continuation_payload(
            left,
            right,
            {
                "source_left": "",
                "target_left": "",
                "source_right": title,
                "target_right": "摘要6393：患者筛选与靶点结合",
            },
        )
        is not None
    )
    assert not translator._reference_complete_metadata_tail_is_safe(
        "CancerRes. 2025, 85 (8_Supplement_1),6393. Additional title text."
    )
