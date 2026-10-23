import pytest
from test_codex_formula_context import translator_stub

from pdf2zh.converter import TranslateConverter
from pdf2zh.line_breaking import is_protected_literal_only, iter_protected_literals
from pdf2zh.translation_quality import (
    has_changed_author_scope,
    has_changed_section_identifier,
)


@pytest.mark.parametrize(
    "url",
    [
        "pubs.acs.org/jmc",
        "journals.aps.org/prl",
        "example.com/path?q=x",
        "med.umich.edu",
    ],
)
def test_bare_publisher_url_is_protected_without_model_request(url):
    translator = translator_stub()
    translator._run_batch_translation = lambda *args, **kwargs: pytest.fail(
        "A URL is not translatable prose"
    )
    assert is_protected_literal_only(url)
    assert translator.translate_batch([url]) == [url]
    converter = TranslateConverter.__new__(TranslateConverter)
    masked, values = converter._mask_protected_literals(url)
    assert masked.startswith("[[PDF2ZH_FLOW_")
    assert converter._restore_protected_literals(masked, values) == url


@pytest.mark.parametrize(
    "prose",
    [
        "pubs.acs.org/jmc contains supplementary results",
        "med.umich.edu provides data",
        "ratio_2.0/day",
        "Eq.3/results",
        "word.Next",
        "quantum.circuit/measurement",
        "main.py/function",
    ],
)
def test_url_detection_does_not_preserve_complete_prose_or_scientific_expressions(
    prose,
):
    assert not is_protected_literal_only(prose)
    assert not translator_stub()._is_passthrough_text(prose)


def test_inline_bare_url_is_preserved_but_surrounding_prose_must_translate():
    source = "See pubs.acs.org/jmc for the supplementary results."
    translator = translator_stub()
    assert translator._accepted_translation(source, source) is None
    assert (
        translator._accepted_translation(source, "补充结果见 pubs.acs.org/jmc。")
        is not None
    )
    assert [item.text for item in iter_protected_literals(source)] == [
        "pubs.acs.org/jmc"
    ]


def test_authors_of_letter_is_not_a_corresponding_author_statement():
    source = "The authors of this Letter have a financial interest in the field of cryogenic engineering."
    bad = "本通讯作者在低温工程领域拥有经济利益。"
    good = "本文作者在低温工程领域拥有经济利益。"
    assert has_changed_author_scope(source, bad)
    assert not has_changed_author_scope(source, good)
    translator = translator_stub()
    translator.cache.set(source, bad)
    translator._run_batch_translation = lambda *args, **kwargs: [good]
    assert translator.translate_batch([source]) == [good]
    assert translator.cache.get(source) == good


def test_explicit_corresponding_author_statement_is_allowed():
    source = "The corresponding author of this Letter has a financial interest."
    target = "本文通讯作者拥有经济利益。"
    assert not has_changed_author_scope(source, target)
    assert translator_stub()._accepted_translation(source, target) is not None


def test_truncated_section_label_at_column_continuation_requires_retranslation():
    source = "adjacent traces (see Sec. I in Ref. [42]); thus, we focus on the two-line case."
    bad = "相邻走线之间（见第 I（见文献[42]）；因此，我们关注两线情形。"
    assert has_changed_section_identifier(source, bad)
    assert translator_stub()._accepted_translation(source, bad) is None
    duplicated_label = "相邻走线之间（见第文献[42]第I节）；因此，我们关注两线情形。"
    assert has_changed_section_identifier(source, duplicated_label)
    assert translator_stub()._accepted_translation(source, duplicated_label) is None
    for good in (
        "相邻走线之间（见文献[42]第 I 节）；因此，我们关注两线情形。",
        "相邻走线之间（见文献[42]第一节）；因此，我们关注两线情形。",
    ):
        assert not has_changed_section_identifier(source, good)
        assert translator_stub()._accepted_translation(source, good) is not None


def test_english_preposition_attached_to_translated_figure_label_requires_retry():
    source = (
        "In Figure 3(b), we plot the cumulative harmonic means of the measurements."
    )
    bad = "In图3(b)中，我们绘出了测量值的累积调和平均值。"
    good = "在图3(b)中，我们绘出了测量值的累积调和平均值。"
    translator = translator_stub()
    assert translator._accepted_translation(source, bad) is None
    assert translator._accepted_translation(source, good) is not None
