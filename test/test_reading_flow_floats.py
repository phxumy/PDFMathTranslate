from dataclasses import replace

from pdf2zh.reading_flow import detect_page_edges, detect_cross_page_edge
from test_reading_flow import segment


def test_body_with_acronym_followed_by_sentence_is_not_a_bibliography():
    left = segment(0, 0, "The loss is caused by the most", column=0, y0=80, y1=210)
    right = segment(
        0,
        1,
        "strongly coupled TLS. Moreover, the quantum circuit fluctuates, and the noise changes.",
        column=1,
        y0=400,
        y1=510,
    )
    assert len(detect_page_edges([left, right])) == 1


def test_narrow_offset_abstract_does_not_hide_the_first_body_column():
    abstract = replace(
        segment(
            0,
            0,
            "Preliminary quantum measurements are reported.",
            column=1,
            y0=290,
            y1=510,
        ),
        x0=215,
        x1=560,
    )
    left = segment(
        0, 1, "We observe variation in the noise. In", column=0, y0=80, y1=125
    )
    right = segment(
        0, 2, "these quantum devices the loss fluctuates.", column=1, y0=180, y1=230
    )
    # Same-column body starts below the abstract; the width check must not
    # choose the abstract as the start of the right body column.
    from pdf2zh.reading_flow import _ordered_column_segments

    assert _ordered_column_segments([abstract, left, right], 1) == [right]


def test_caption_halves_join_and_do_not_mask_body_below_them():
    caption = replace(
        segment(
            2,
            0,
            "Fig. 2. The signal is plotted at",
            column=0,
            y0=320,
            y1=480,
            kind="figure_caption",
        ),
        size=7,
    )
    continuation = replace(
        segment(
            2,
            1,
            "different times. The curves show the decay.",
            column=1,
            y0=318,
            y1=480,
        ),
        size=7,
    )
    left = segment(2, 2, "The data show a correlation", column=0, y0=50, y1=260)
    right = segment(2, 3, "between the two quantities.", column=1, y0=200, y1=295)
    edges = detect_page_edges([caption, continuation, left, right])
    assert [(e.left, e.right) for e in edges] == [
        (caption.ref, continuation.ref),
        (left.ref, right.ref),
    ]


def test_body_can_continue_above_a_bottom_float_without_skipping_other_prose():
    left = segment(1, 0, "The noise may", column=1, y0=435, y1=670)
    caption = segment(
        1,
        1,
        "Fig. 1. The measured noise.",
        column=1,
        y0=70,
        y1=170,
        kind="figure_caption",
    )
    following = segment(2, 0, "not be exponential in time.", column=0, y0=700, y1=740)
    assert detect_cross_page_edge([left, caption], [following]) is not None
    other = segment(1, 2, "An unrelated final paragraph.", column=1, y0=200, y1=250)
    assert detect_cross_page_edge([left, caption, other], [following]) is None


def test_short_two_column_page_keeps_a_split_word_in_one_sentence():
    left = segment(
        10,
        0,
        "The full data were measured at all qubit reso-",
        column=0,
        y0=700,
        y1=721,
    )
    right = replace(
        segment(10, 1, "nance frequencies.", column=1, y0=730, y1=739), x1=390
    )
    edges = detect_page_edges([left, right])
    assert len(edges) == 1
    assert edges[0].join_mode == "line_hyphen_candidate"
    assert not detect_page_edges(
        [replace(left, text="An independent short paragraph."), right]
    )


def test_publication_dates_do_not_hide_body_below_abstract():
    abstract = replace(
        segment(
            0,
            0,
            "The experiment measures quantum coherence. " * 8,
            column=1,
            y0=290,
            y1=510,
        ),
        x0=215,
        x1=560,
    )
    date = segment(0, 1, "Received: 1 August 2024", column=0, y0=570, y1=580)
    first = segment(0, 2, "We first describe the experiment.", column=0, y0=140, y1=260)
    tail = segment(
        0, 3, "The model is difficult to maintain. In", column=0, y0=80, y1=125
    )
    right = segment(
        0, 4, "superconducting devices noise fluctuates.", column=1, y0=180, y1=260
    )
    edges = detect_page_edges([abstract, date, first, tail, right])
    assert len(edges) == 1
    assert edges[0].left == tail.ref and edges[0].right == right.ref
