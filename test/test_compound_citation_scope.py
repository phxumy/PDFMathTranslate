from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import (
    has_changed_numeric_citations,
    normalize_scientific_cross_reference_placement,
)
from test_codex_formula_context import translator_stub

SOURCE = "The inhibitors include AB-123 (1){v0}. We seek a profile such as that described for 1."
BAD = "抑制剂包括AB-123 (1){v0}。我们寻找文献1所述的特性。"
GOOD = "抑制剂包括AB-123 (1){v0}。我们寻找化合物1所述的特性。"


@pytest.mark.parametrize(
    "source,target",
    [
        (SOURCE, BAD),
        (SOURCE, BAD.replace("文献1", "參考文獻 1")),
        (
            "Compound (7) was selected. Its profile is described for 7.",
            "选择化合物(7)。特性见文献7。",
        ),
        ("The drug ZX900 (2a) was tested.", "测试了ZX900 (2a)，见文献2a。"),
    ],
)
def test_bound_compound_number_cannot_become_a_new_bibliographic_reference(
    source, target
):
    assert has_changed_numeric_citations(source, target)
    assert translator_stub()._accepted_translation(source, target) is None


@pytest.mark.parametrize(
    "source,target",
    [
        (SOURCE, GOOD),
        (SOURCE + " See Reference 1.", GOOD + "参见文献1。"),
        (SOURCE + " See Ref. 1.", GOOD + "参见文献1。"),
        (
            "The inhibitor AB-123 (2) was used. See Refs. 1-3.",
            "使用了抑制剂AB-123 (2)。参见文献1-3，其中包括文献2。",
        ),
        (
            SOURCE + " Prior work [1] confirms this.",
            GOOD + "此前的工作[1]证实了这一点，参见文献1。",
        ),
        (
            SOURCE + " Prior work [1-3] confirms this.",
            GOOD + "此前的工作[1-3]证实了这一点，参见文献1。",
        ),
        (
            "The inhibitors include AB-123 (1). See Eq. (1).",
            "抑制剂包括AB-123 (1)。参见式(1)。",
        ),
        ("See Reference 1 and Eq. (2).", "参见文献1和式(2)。"),
        (
            "The group AB123 (1) describes the sequence.",
            "AB123 (1)描述该序列，参见文献1。",
        ),
        ("An inhibitor profile was described for 1.", "文献1描述了一种抑制剂特性。"),
    ],
)
def test_real_citations_equations_and_unbound_numbers_do_not_trigger_inference(
    source, target
):
    assert not has_changed_numeric_citations(source, target)


def test_formula_context_cache_with_invented_compound_citation_is_retranslated():
    translator = translator_stub()
    translator.max_concurrency = 1
    context = {"{v0}": "10"}
    key = translator._formula_context_cache_key(SOURCE, context)
    translator.cache.set(key, BAD)
    translator._run_formula_context_batch_request = Mock(
        side_effect=lambda texts, contexts, **kwargs: [
            GOOD if texts[0] == SOURCE else "抑制剂包括AB-123 (1){v0}。"
        ]
    )
    translator._run_batch_translation = Mock(return_value=["我们寻找文献1所述的特性。"])
    assert translator.translate_batch_with_formula_contexts([SOURCE], [context]) == [
        GOOD
    ]
    assert translator._run_formula_context_batch_request.call_count == 2
    assert translator._run_formula_context_batch_request.call_args.args[0] == [SOURCE]
    assert translator.cache.get(key) == GOOD


def test_unrelated_good_prose_cache_remains_reusable():
    translator = translator_stub()
    translator.cache.set("A stable compound.", "稳定的化合物。")
    translator._run_batch_translation = Mock()
    assert translator.translate_batch(["A stable compound."]) == ["稳定的化合物。"]
    translator._run_batch_translation.assert_not_called()


@pytest.mark.parametrize(
    "source,target",
    [
        (
            "Compound 10 and its aza-homologue 11 showed similar activity to reference 3.",
            "化合物10及其氮杂同系物11的活性与参比化合物3相似。",
        ),
        (
            "Compound 13 is the exact analogue of reference 3, with a different scaffold.",
            "化合物13是参考化合物3的对应类似物，具有不同骨架。",
        ),
        (
            "The potency and physicochemical property profile is indistinguishable from reference 3.",
            "效力和理化性质特征与参照化合物3无法区分。",
        ),
        (
            "Compound 13 is an analogue of reference 3, as described in Reference 8.",
            "化合物13是参比物3的类似物，如文献8所述。",
        ),
    ],
)
def test_explicit_compound_reference_relation_accepts_chemical_comparator(
    source, target
):
    assert normalize_scientific_cross_reference_placement(source, target) == target
    assert translator_stub()._accepted_translation(source, target) == target
    assert (
        normalize_scientific_cross_reference_placement(source, target.replace("3", "4"))
        is None
    )


@pytest.mark.parametrize(
    "source,target",
    [
        ("The assay is described in reference 3.", "测定方法见文献3。"),
        ("Compound 13 was reported in reference 3.", "化合物13已在文献3中报道。"),
        ("The analogue is described in reference 3.", "类似物见文献3。"),
        ("See Ref. 3 and Eq. (1).", "参见文献3和式(1)。"),
    ],
)
def test_normal_bibliographic_reference_is_not_exempted_by_chemical_words(
    source, target
):
    assert normalize_scientific_cross_reference_placement(source, target) == target
    assert (
        normalize_scientific_cross_reference_placement(
            source, target.replace("文献3", "化合物3")
        )
        is None
    )


def test_reference_compound_cannot_be_relabelled_as_a_paper():
    source = "Compound 13 is the exact analogue of reference 3."
    assert (
        normalize_scientific_cross_reference_placement(
            source, "化合物13是文献3的精确类似物。"
        )
        is None
    )


@pytest.mark.parametrize(
    "source,target",
    [
        (
            "Reference 3 was first benchmarked. This compound showed high clearance.",
            "首先评估参照化合物3。该化合物清除率高。",
        ),
        (
            "A selection of analogues was examined in DMPK assays. Reference 3 was first benchmarked.",
            "对选定类似物进行DMPK测定。首先评估参考化合物3。",
        ),
    ],
)
def test_benchmarked_reference_with_explicit_compound_context_is_a_comparator(
    source, target
):
    assert normalize_scientific_cross_reference_placement(source, target) == target
    assert (
        normalize_scientific_cross_reference_placement(
            source, target.replace("化合物3", "文献3")
        )
        is None
    )


def test_benchmark_word_alone_does_not_change_a_publication_reference():
    source = "Reference 3 was first benchmarked. This method is efficient."
    target = "首先对文献3进行基准评估。该方法效率高。"
    assert normalize_scientific_cross_reference_placement(source, target) == target
    assert (
        normalize_scientific_cross_reference_placement(
            source, target.replace("文献3", "化合物3")
        )
        is None
    )


def test_benchmarked_reference_bad_cache_is_revalidated():
    source = "Reference 3 was first benchmarked. This compound showed high clearance."
    wrong = "首先评估文献3。该化合物清除率高。"
    right = "首先评估参照化合物3。该化合物清除率高。"
    translator = translator_stub()
    translator.cache.set(source, wrong)
    translator._run_single_translation = Mock(return_value=right)
    assert translator.translate(source) == right
    translator._run_single_translation.assert_called_once()
    assert translator.cache.get(source) == right
