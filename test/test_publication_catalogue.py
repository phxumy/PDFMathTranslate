import pytest

from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    SourceSegment,
    ROLE_PRESERVE,
    is_publication_catalogue,
)
from pdf2zh.translator import CodexTranslator


@pytest.mark.parametrize(
    "tail",
    ["(Springer, 2013). ", "Prentice Hall, 2005.", "(Wiley, Hoboken, NJ, 2011)."],
)
def test_publisher_year_fields_remain_verbatim(tail):
    assert is_publication_catalogue(tail)
    assert (
        DocumentTranslationPolicy().plan_segment(SourceSegment(0, tail)).parts[0].role
        == ROLE_PRESERVE
    )


def test_reference_column_prefix_is_catalogue_not_prose():
    text = "(Springer, 2013). 14. Liu, Y. & Nori, F. Coupled flux qubits. Nature 12, 34 (2020)."
    policy = DocumentTranslationPolicy()
    policy.last_reference_number = 13
    assert policy.plan_segment(SourceSegment(0, text)).parts[0].role == ROLE_PRESERVE


def test_edition_and_publisher_are_outside_book_title():
    entry = (
        "[34] S. Ross, A First Course in Probability, 7th, Ed., Prentice Hall, 2005."
    )
    assert CodexTranslator._structured_reference_title_spans(entry) == (
        "A First Course in Probability",
    )
    for text in (
        "Springer proposed a new method in 2013.",
        "(Springer, 2013) explains the experiment.",
    ):
        assert not is_publication_catalogue(text)


def test_italic_journal_after_article_title_is_not_a_book_title():
    entry = "1. Vidal, G., Hammerer, K. & Cirac, J. I. Interaction cost of nonlocal gates. [[PDF2ZH_ITALIC_10_BEGIN]]Phys. Rev. Lett[[PDF2ZH_ITALIC_10_END]]. 88, 237902 (2002)."
    assert CodexTranslator._styled_reference_title_spans(entry) == ()
    assert CodexTranslator._structured_reference_title_spans(entry) == (
        "Interaction cost of nonlocal gates",
    )
