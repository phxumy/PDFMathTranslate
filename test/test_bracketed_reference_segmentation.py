import pytest

from pdf2zh.source_segmentation import scientific_sentence_parts, safe_chunk_boundary


@pytest.mark.parametrize(
    "reference",
    [
        "Ref. [29]",
        "Refs. [2, 5–7]",
        "Ref.[33]",
        "Ref. ［28］",
        "Ref. [41, Lemma 2.1]",
    ],
)
def test_bracketed_reference_is_part_of_its_sentence(reference):
    first = f"We use the method in {reference} to model this state. "
    assert scientific_sentence_parts(first + "The error is small.") == [
        first,
        "The error is small.",
    ]
    prefix = "The method in "
    text = prefix + reference + " is useful."
    boundary = safe_chunk_boundary(text, len(prefix) + len(reference) - 2, ())
    assert text[:boundary].strip() == prefix.strip()


def test_when_not_only_condition_keeps_citation_before_but_clause():
    sentence = "When not only calibrating a single gate, as in Ref. [29], but an entire gate set, the cost grows."
    assert scientific_sentence_parts(sentence) == [sentence]


def test_terminal_citation_and_unrelated_periods_still_split():
    assert scientific_sentence_parts("Use Ref. [29]. A new sentence.") == [
        "Use Ref. [29]. ",
        "A new sentence.",
    ]
    assert scientific_sentence_parts("This is brief. Another sentence.") == [
        "This is brief. ",
        "Another sentence.",
    ]
