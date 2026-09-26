from dataclasses import replace

import pytest

from pdf2zh.reading_flow import build_continuation_groups, detect_page_edges
from test_reading_flow import segment


def figure_page():
    left = segment(
        2,
        2,
        "The measured fidelity decays with the number of single-qubit Cliffords. "
        "The measured interleaved gate fidelities F{v21} of the four specific "
        "geometric gates X, Y, X{v22}, and Y{v23}, inserted in the random",
        column=0,
        y0=77.3,
        y1=260.9,
    )
    right = segment(
        2,
        3,
        "Cliffords in the interleaved RB experiment, are 0.9976(1), 0.9975(1), "
        "0.9981(1), and 0.9975(1), respectively.",
        column=1,
        y0=259.4,
        y1=282.0,
    )
    caption = segment(
        2,
        8,
        "FIG. 3. Noise-resilient feature of single-qubit geometric gates.",
        column=1,
        y0=301.5,
        y1=487.0,
        kind="figure_caption",
    )
    return left, right, caption


def test_low_column_start_below_figure_joins_incomplete_repeated_term():
    left, right, caption = figure_page()
    edges = detect_page_edges([left, right, caption])
    assert len(edges) == 1
    assert "body-below-column-figure" in edges[0].reasons
    assert "repeated-term-after-dangling-modifier" in edges[0].reasons
    groups = build_continuation_groups(edges, {s.ref: s.text for s in (left, right)})
    first, second = groups[0].fragments
    assert left.text[first.start : first.end].startswith("The measured interleaved")
    assert right.text[second.start : second.end] == right.text


@pytest.mark.parametrize(
    "change",
    [
        "no-caption",
        "remote-caption",
        "narrow-caption",
        "other-column",
        "finished-left",
        "new-term",
        "new-sentence",
        "no-dangling-modifier",
        "different-size",
    ],
)
def test_float_continuation_requires_geometry_and_grammar(change):
    left, right, caption = figure_page()
    if change == "no-caption":
        assert not detect_page_edges([left, right])
        return
    if change == "remote-caption":
        caption = replace(caption, y0=410)
    elif change == "narrow-caption":
        caption = replace(caption, x1=caption.x0 + 60)
    elif change == "other-column":
        caption = replace(caption, x0=left.x0, x1=left.x1)
    elif change == "finished-left":
        left = replace(left, text=left.text + ".")
    elif change == "new-term":
        right = replace(right, text=right.text.replace("Cliffords", "Rotations"))
    elif change == "new-sentence":
        right = replace(right, text="The interleaved experiments are described next.")
    elif change == "no-dangling-modifier":
        left = replace(left, text="The observed Cliffords remain stable over time")
    elif change == "different-size":
        right = replace(right, size=right.size * 2)
    assert not detect_page_edges([left, right, caption])


@pytest.mark.parametrize("kind", ["plain text", "title", "figure_caption"])
def test_intervening_block_prevents_skipping_into_a_middle_paragraph(kind):
    left, right, caption = figure_page()
    obstacle = segment(
        2,
        9,
        "An independent intervening paragraph.",
        column=1,
        y0=285,
        y1=295,
        kind=kind,
    )
    if kind == "figure_caption":
        # The actual next body paragraph above the figure was excluded by a
        # body heuristic, so it must still veto jumping to the lower block.
        obstacle = replace(
            obstacle,
            text="[2] A. Smith, A reference entry.",
            y0=500,
            y1=530,
            region_kind="plain text",
        )
    assert not detect_page_edges([left, right, caption, obstacle])


def test_section_heading_above_figure_prevents_cross_column_join():
    left, right, caption = figure_page()
    heading = segment(2, 9, "III. RESULTS", column=1, y0=750, y1=763, kind="title")
    assert not detect_page_edges([left, right, caption, heading])
