import pytest

from pdf2zh.source_segmentation import scientific_sentence_parts
from pdf2zh.translation_quality import has_suspicious_english_residue
from test_codex_formula_context import translator_stub


@pytest.mark.parametrize(
    "source,bad,good",
    [
        (
            "The problem is {v0}-sharp around {v1}.",
            "该问题在{v1}附近具有{v0}-sharp 性质。",
            "该问题在{v1}附近具有{v0}-尖锐性。",
        ),
        (
            'In the case "Covariance estimation I", the bound holds.',
            "在“Covariance estimation I”情形下，该界成立。",
            "在“协方差估计 I”情形下，该界成立。",
        ),
        (
            'We use the case "Sparse vector recovery II" below.',
            "下面使用“Sparse vector recovery II”情形。",
            "下面使用“稀疏向量恢复 II”情形。",
        ),
        (
            "The proof of Theorem 4.1 follows from Theorem 3.2.",
            "Theorem 4.1 的证明由 Theorem 3.2 得到。",
            "定理 4.1 的证明由定理 3.2 得到。",
        ),
        (
            "Proposition A.2 extends Lemma 2.1.",
            "Proposition A.2 推广了引理 2.1。",
            "命题 A.2 推广了引理 2.1。",
        ),
    ],
)
def test_translatable_math_terms_and_statement_names_do_not_enter_cache(
    source, bad, good
):
    assert has_suspicious_english_residue(source, bad)
    assert not has_suspicious_english_residue(source, good)
    translator = translator_stub()
    assert translator._accepted_translation(source, bad) is None


@pytest.mark.parametrize(
    "source,target",
    [
        ("We use [41, Lemma 2.1] for the bound.", "该界使用 [41, Lemma 2.1]。"),
        (
            "Use [41, Theorem 2.1] and [52, Corollary 5.35].",
            "使用 [41, Theorem 2.1] 和 [52, Corollary 5.35]。",
        ),
        (
            "Sharp and Lipschitz developed this method.",
            "Sharp 和 Lipschitz 提出了此方法。",
        ),
        ("We use the SHARP package.", "我们使用 SHARP 软件包。"),
        (
            'The "Sharp Instruments" device measures the signal.',
            "“Sharp Instruments”器件测量该信号。",
        ),
        ('The "TensorFlow II" package is used.', "使用“TensorFlow II”软件包。"),
        ('The "Josephson junction" is calibrated.', "对“Josephson junction”进行校准。"),
        (
            "The symbol $sharp$ occurs in `Theorem 4.1`.",
            "符号 $sharp$ 出现在 `Theorem 4.1` 中。",
        ),
        (
            "See https://example.org/Theorem4.1 for sharp results.",
            "结果见 https://example.org/Theorem4.1。",
        ),
    ],
)
def test_names_opaque_text_and_external_citation_locators_remain_valid(source, target):
    assert not has_suspicious_english_residue(source, target)


@pytest.mark.parametrize("abbreviation", ["w.h.p.", "w. h. p.", "i.i.d.", "i. i. d."])
@pytest.mark.parametrize(
    "continuation", ["whenever {v0} holds.", "{v0} holds.", ", {v0} holds."]
)
def test_statistical_abbreviation_stays_with_following_predicate(
    abbreviation, continuation
):
    first = f"The estimate holds {abbreviation} {continuation} "
    source = first + "The next assertion follows."
    assert scientific_sentence_parts(source) == [first, "The next assertion follows."]
    translator = translator_stub()
    assert translator._split_formula_context_text(source) == [
        first,
        "The next assertion follows.",
    ]


@pytest.mark.parametrize("abbreviation", ["w.h.p.", "i.i.d."])
def test_terminal_statistical_abbreviation_still_ends_a_sentence(abbreviation):
    source = f"This holds {abbreviation} The next claim follows."
    assert scientific_sentence_parts(source) == [
        f"This holds {abbreviation} ",
        "The next claim follows.",
    ]


@pytest.mark.parametrize("abbreviation", ["w.h.p.", "i.i.d."])
def test_cached_abbreviation_split_into_chinese_sentence_is_rejected(abbreviation):
    source = f"We may set {abbreviation} RIP{{v0}} to this value."
    bad = f"我们可以设 {abbreviation[:-1]}。RIP{{v0}}为该值。"
    valid = f"我们可以 {abbreviation} 将RIP{{v0}}设为该值。"
    assert has_suspicious_english_residue(source, bad)
    assert not has_suspicious_english_residue(source, valid)
    assert translator_stub()._accepted_translation(source, bad) is None


@pytest.mark.parametrize("abbreviation", ["w.h.p.", "i.i.d."])
def test_moved_condition_allows_abbreviation_at_complete_sentence_end(abbreviation):
    source = f"The bound holds {abbreviation} whenever {{v0}} holds."
    for target in (
        f"当{{v0}}成立时，该界成立 {abbreviation[:-1]}。",
        f"当{{v0}}成立时，该界成立 {abbreviation}。",
        f"当{{v0}}成立时，该界成立 {abbreviation[:-1]}。这是一个充分条件。",
    ):
        assert not has_suspicious_english_residue(source, target)
        assert translator_stub()._accepted_translation(source, target) is not None
    source = f"We may set {abbreviation} RIP{{v0}} to this value."
    target = f"我们可以将 RIP{{v0}} 设为该值 {abbreviation[:-1]}。"
    assert not has_suspicious_english_residue(source, target)
    assert translator_stub()._accepted_translation(source, target) is not None


def test_actual_probability_qualifier_with_rip_operator_keeps_the_predicate():
    first = "where each measurement matrix {v30} has Gaussian entries. "
    sampling = (
        "Each entry of each matrix is sampled i.i.d. according to {v31} "
        "when {v32}, and according to {v33} when {v34}. "
    )
    condition = (
        "In both settings, we may set w.h.p. RIP{v35} and RIP{v36} "
        "as long as {v37}, where {v38} and {v39} are constants independent "
        "of {v40} satisfying {v41} 1{v42} and {v43} 1. "
    )
    proof = "This fact is proved in the {v44} setting in [12, Theorem 2.3]."
    assert scientific_sentence_parts(first + sampling + condition + proof) == [
        first,
        sampling,
        condition,
        proof,
    ]
    bad = (
        "在这两种设置下，我们可以设 w.h.p。 RIP{v35}和 RIP{v36}，"
        "当{v37}时成立，其中{v38}和{v39}与{v40}无关且满足{v41} 1{v42}和{v43} 1。"
    )
    assert has_suspicious_english_residue(condition, bad)
    good = (
        "在这两种设置下，只要{v37}，就可以以高概率设 RIP{v35}和 RIP{v36}，"
        "其中{v38}和{v39}与{v40}无关且满足{v41} 1{v42}和{v43} 1。"
    )
    assert not has_suspicious_english_residue(condition, good)
    assert scientific_sentence_parts("This holds w.h.p. RIP is defined next.") == [
        "This holds w.h.p. ",
        "RIP is defined next.",
    ]


def test_rejected_short_term_cache_is_retranslated_as_normal_prose():
    translator = translator_stub()
    source = "The problem has the {v0}-sharp property."
    bad = "该问题具有{v0}-sharp 性质。"
    good = "该问题具有{v0}-尖锐性。"
    formulas = {"{v0}": "mu"}
    context = translator._normalize_formula_context(source, formulas)
    key = translator._formula_context_cache_key(source, context)
    translator.cache.set(key, bad)
    calls = []

    def translate(texts, contexts, **kwargs):
        calls.append(texts)
        return [good]

    translator._run_formula_context_batch_request = translate
    assert translator.translate_batch_with_formula_contexts([source], [formulas]) == [
        good
    ]
    assert calls == [[source]]
    assert translator.cache.get(key) == good
