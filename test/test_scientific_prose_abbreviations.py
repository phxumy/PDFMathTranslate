import pytest

from pdf2zh.source_segmentation import scientific_sentence_parts
from pdf2zh.translation_quality import has_detached_norm_relation
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize("abbreviation", ["w.r.t.", "w. r. t.", "i.e.", "e.g.", "viz.", "cf."])
def test_dependent_abbreviation_does_not_split_its_clause(abbreviation):
    sentence = f"The property holds {abbreviation} the {{v0}} norm; "
    assert scientific_sentence_parts(sentence + "the result follows.") == [
        sentence, "the result follows."
    ]


def test_abbreviation_does_not_hide_actual_sentence_ends_or_change_source():
    source = "Consider examples, e.g., {v0}. Another relation w.r.t. {v1} holds. Next."
    assert scientific_sentence_parts(source) == [
        "Consider examples, e.g., {v0}. ",
        "Another relation w.r.t. {v1} holds. ",
        "Next.",
    ]
    assert scientific_sentence_parts("Values etc. Next sentence.") == [
        "Values etc. ", "Next sentence."
    ]


def test_size_boundary_does_not_split_inside_spaced_abbreviation():
    translator = translator_stub()
    source = "context " * 10 + "w. r. t. the {v0} norm"
    for limit in range(81, 89):
        translator.MAX_ITEM_CHARS = limit
        chunks = translator._split_long_text(source)
        assert "".join(chunks) == source
        assert sum("w. r. t." in chunk for chunk in chunks) == 1


@pytest.mark.parametrize("relation", ["w.r.t.", "w. r. t.", "with respect to"])
def test_detached_norm_fragment_is_rejected_without_prescribing_word_order(relation):
    source = f"The function {{v0}} is sharp around {{v1}} {relation} the {{v2}} norm; it converges."
    assert has_detached_norm_relation(source, "函数{v0}在{v1}附近是尖锐的。{v2}范数；它收敛。")
    for valid in (
        "函数{v0}在{v1}附近相对于{v2}范数具有尖锐性；它收敛。",
        "函数{v0}在{v1}附近是尖锐的，所用范数是{v2}范数；它收敛。",
        "函数{v0}在{v1}附近具有尖锐性。这是相对于{v2}范数而言的。",
        "函数{v0}在{v1}附近具有尖锐性。{v2}范数用于定义这一性质。",
        "函数{v0}在{v1}附近具有尖锐性。{v2}范数，正是这里定义尖锐性所依据的范数；它收敛。",
    ):
        assert not has_detached_norm_relation(source, valid)
    assert not has_detached_norm_relation(
        "The function {v0} is sharp. The {v2} norm is different.",
        "函数{v0}是尖锐的。{v2}范数；后面继续说明。",
    )


def test_cached_broken_relation_is_retranslated_as_one_contextual_request():
    translator = translator_stub()
    source = "We show that {v0} is sharp around {v1} w.r.t. the {v2} norm;"
    bad = "我们证明{v0}在{v1}附近是尖锐的。{v2}范数；"
    good = "我们证明{v0}在{v1}附近相对于{v2}范数具有尖锐性；"
    formulas = {"{v0}": "f", "{v1}": "x", "{v2}": "p"}
    context = translator._normalize_formula_context(source, formulas)
    key = translator._formula_context_cache_key(source, context)
    translator.cache.set(key, bad)
    calls = []

    def translate(texts, contexts, **kwargs):
        calls.append((texts, contexts))
        return [good]

    translator._run_formula_context_batch_request = translate
    assert translator._accepted_translation(source, bad) is None
    assert translator.translate_batch_with_formula_contexts([source], [formulas]) == [good]
    assert len(calls) == 1
    assert calls[0][0] == [source]
    assert calls[0][1] == [context]
    assert translator.cache.get(key) == good
