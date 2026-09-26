import pytest

from pdf2zh.reading_flow import (
    ContinuationEdge,
    SegmentRef,
    build_continuation_groups,
)


def spans(left, right):
    first, second = SegmentRef(1, 0), SegmentRef(2, 0)
    edge = ContinuationEdge(first, second, "page", "normal", 0.95, ())
    groups = build_continuation_groups([edge], {first: left, second: right})
    return [
        text[span.start : span.end]
        for text, span in zip((left, right), groups[0].fragments)
    ]


def test_xu_pulse_sequence_continuation_keeps_figure_panel_in_joint_translation():
    left = (
        "We first demonstrate the single-qubit nonadiabatic geo-metric gates "
        "on qubit Q{v37}, with the experimental pulse"
    )
    right = (
        "sequence shown in Fig. 1(c). As a demonstration, here we fix {v0}, "
        "and realize single-qubit rotations."
    )
    assert spans(left, right) == [left, "sequence shown in Fig. 1(c)."]


@pytest.mark.parametrize(
    "reference", ["Fig. 1(c)", "Figs. 3(b)–3(g)", "Eq. (2)", "Eqs. (S1) and (S2)"]
)
def test_reference_abbreviation_does_not_cut_either_continuation_span(reference):
    left = "A complete sentence. As shown in " + reference + ", we observe"
    right = "the result in " + reference + ". Another sentence follows."
    assert spans(left, right) == [
        "As shown in " + reference + ", we observe",
        "the result in " + reference + ".",
    ]


def test_abbreviation_without_following_reference_number_remains_sentence_end():
    assert spans("A sentence ends in Fig. The experiment", "continues here.") == [
        "The experiment",
        "continues here.",
    ]
