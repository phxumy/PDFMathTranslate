from __future__ import annotations

import unittest

from pdf2zh.reading_flow import (
    FlowSegment,
    SegmentRef,
    build_continuation_groups,
    detect_cross_page_edge,
    detect_page_edges,
)


def segment(
    page: int,
    index: int,
    text: str,
    *,
    column: int,
    y0: float,
    y1: float,
    kind: str = "plain text",
) -> FlowSegment:
    x0, x1 = (52.0, 297.0) if column == 0 else (315.0, 560.0)
    return FlowSegment(
        SegmentRef(page, index),
        text,
        x0,
        x1,
        y0,
        y1,
        9.5,
        612.0,
        792.0,
        kind,
    )


class ReadingFlowDetectionTests(unittest.TestCase):
    def test_yan_page_three_to_four_is_a_continuation(self) -> None:
        previous = [
            segment(
                2,
                4,
                "The four terms in the square brackets represent, respectively, "
                "the coupling strength of (1) the virtual",
                column=1,
                y0=74.0,
                y1=111.0,
            )
        ]
        following = [
            segment(
                3,
                0,
                "exchange interaction via the state {v0} (indirect qubit-qubit "
                "coupling); (2) the virtual exchange interaction.",
                column=0,
                y0=488.0,
                y1=742.0,
            )
        ]

        edge = detect_cross_page_edge(previous, following)

        self.assertIsNotNone(edge)
        self.assertEqual(edge.kind, "page")
        groups = build_continuation_groups(
            [edge],
            {item.ref: item.text for item in previous + following},
        )
        self.assertEqual(len(groups), 1)
        left, right = groups[0].fragments
        self.assertTrue(previous[0].text[left.start : left.end].endswith("virtual"))
        self.assertTrue(
            following[0].text[right.start : right.end].startswith("exchange")
        )

    def test_yan_page_four_column_change_is_a_continuation(self) -> None:
        blocks = [
            segment(
                3,
                1,
                "We find that the contributions are independent from each other, "
                "so that we can discuss their individual contribution",
                column=0,
                y0=74.0,
                y1=147.0,
            ),
            segment(
                3,
                2,
                "separately. First, the reduction of fidelity is negligible.",
                column=1,
                y0=306.0,
                y1=742.0,
            ),
        ]

        edges = detect_page_edges(blocks)

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].kind, "column")

    def test_body_with_section_abbreviation_continues_across_columns(
        self,
    ) -> None:
        """A section label followed by prose must not look like an author."""

        left = segment(
            11,
            10,
            "The results confirm that models trained without IS cannot detect "
            "inactive classes well. In particular, the enrollment-based models "
            "select the closest sound in the mixture, producing 0 dB. When using "
            "IS, which adds 10 % of inactive SE classes to the",
            column=0,
            y0=49.6,
            y1=143.2,
        )
        caption = segment(
            11,
            11,
            "Fig. 5. ROC curves for inactive event detection using TSE models.",
            column=1,
            y0=514.4,
            y1=531.4,
            kind="figure_caption",
        )
        right = segment(
            11,
            12,
            "training data as discussed in Section V-B, all models detect "
            "inactive classes better with {v12} values below -20 dB.",
            column=1,
            y0=451.8,
            y1=485.6,
        )

        edges = detect_page_edges([left, caption, right])

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].left, left.ref)
        self.assertEqual(edges[0].right, right.ref)
        self.assertEqual(edges[0].kind, "column")
        groups = build_continuation_groups(
            edges,
            {item.ref: item.text for item in (left, caption, right)},
        )
        self.assertEqual(len(groups), 1)
        left_span, right_span = groups[0].fragments
        self.assertTrue(left.text[left_span.start : left_span.end].endswith("to the"))
        self.assertEqual(
            right.text[right_span.start : right_span.end],
            right.text,
        )
        self.assertIn("{v12}", right.text[right_span.start : right_span.end])

    def test_numbered_reference_is_not_joined_as_body_across_columns(
        self,
    ) -> None:
        reference_tail = segment(
            11,
            20,
            '[17] A. Author and B. Writer, "An unfinished article title',
            column=0,
            y0=49.6,
            y1=143.2,
        )
        reference_head = segment(
            11,
            21,
            'continued in another column," Journal Name, 2020.',
            column=1,
            y0=451.8,
            y1=485.6,
        )

        self.assertEqual(
            detect_page_edges([reference_tail, reference_head]),
            [],
        )

    def test_unnumbered_incomplete_reference_tail_is_not_joined_as_body(
        self,
    ) -> None:
        reference_tail = segment(
            11,
            20,
            'A. Author, B. Writer, "An unfinished article title',
            column=0,
            y0=49.6,
            y1=143.2,
        )
        reference_head = segment(
            11,
            21,
            'continued in another column," Journal Name, 2020.',
            column=1,
            y0=451.8,
            y1=485.6,
        )

        self.assertEqual(
            detect_page_edges([reference_tail, reference_head]),
            [],
        )

    def test_next_page_float_caption_is_skipped_for_body_continuation(self) -> None:
        previous = [
            segment(
                3,
                6,
                "By checking the final state at the end of the gate operation, we",
                column=1,
                y0=74.0,
                y1=219.0,
            )
        ]
        following = [
            segment(
                4,
                0,
                "FIG. 3. Relation between error per gate and gate length.",
                column=0,
                y0=428.0,
                y1=580.0,
                kind="figure_caption",
            ),
            segment(
                4,
                1,
                "find that the remaining errors are largely due to leakage to the "
                "excited states of the coupler.",
                column=0,
                y0=247.0,
                y1=404.0,
            ),
        ]

        edge = detect_cross_page_edge(previous, following)

        self.assertIsNotNone(edge)
        self.assertEqual(edge.right, SegmentRef(4, 1))

    def test_single_column_pages_can_continue_across_a_page_boundary(self) -> None:
        previous = [
            FlowSegment(
                SegmentRef(7, 0),
                "The measured response is therefore governed by the effective",
                48.0,
                564.0,
                62.0,
                210.0,
                10.0,
                612.0,
                792.0,
                "plain text",
            )
        ]
        following = [
            FlowSegment(
                SegmentRef(8, 0),
                "coupling between the two dressed modes in this limit.",
                48.0,
                564.0,
                570.0,
                742.0,
                10.0,
                612.0,
                792.0,
                "plain text",
            )
        ]

        edge = detect_cross_page_edge(previous, following)

        self.assertIsNotNone(edge)
        self.assertEqual(edge.left, SegmentRef(7, 0))
        self.assertEqual(edge.right, SegmentRef(8, 0))

    def test_leading_parenthetical_qualifier_can_resume_a_cross_page_sentence(
        self,
    ) -> None:
        previous = [
            segment(
                4,
                8,
                "Fine features were defined by a high-voltage pattern generator",
                column=1,
                y0=74.0,
                y1=120.0,
            )
        ]
        following = [
            segment(
                5,
                0,
                "(Model EBPG 5000+) in one step on a resist bilayer.",
                column=0,
                y0=650.0,
                y1=742.0,
            )
        ]

        edge = detect_cross_page_edge(previous, following)

        self.assertIsNotNone(edge)
        self.assertIn("lowercase-after-leading-parenthetical", edge.reasons)
        groups = build_continuation_groups(
            [edge],
            {item.ref: item.text for item in previous + following},
        )
        self.assertEqual(len(groups), 1)
        left, right = groups[0].fragments
        self.assertEqual(
            previous[0].text[left.start : left.end],
            previous[0].text,
        )
        self.assertEqual(
            following[0].text[right.start : right.end],
            following[0].text,
        )

    def test_leading_parenthetical_does_not_hide_a_new_uppercase_sentence(
        self,
    ) -> None:
        previous = [
            segment(
                4,
                8,
                "The preceding page ends with an unfinished prose fragment",
                column=1,
                y0=74.0,
                y1=120.0,
            )
        ]
        following = [
            segment(
                5,
                0,
                "(a) Measurements begin with a separate procedure.",
                column=0,
                y0=650.0,
                y1=742.0,
            )
        ]

        self.assertIsNone(detect_cross_page_edge(previous, following))

    def test_capitalized_eponym_can_follow_a_dangling_page_end_cue(self) -> None:
        previous = [
            segment(
                0,
                8,
                "The Josephson energy E{v36} can be computed from the",
                column=1,
                y0=74.0,
                y1=142.0,
            ),
            segment(
                0,
                9,
                "Z. K. Minev et al. 1",
                column=1,
                y0=32.0,
                y1=41.0,
                kind="footer",
            ),
        ]
        following = [
            segment(
                1,
                0,
                "Fig. 1 Conceptual overview of the device.",
                column=0,
                y0=520.0,
                y1=700.0,
                kind="figure_caption",
            ),
            segment(
                1,
                1,
                "Ambegaokar–Baratoff formula adapted to the measured "
                "room-temperature resistance of the junction{v0}.",
                column=0,
                y0=205.0,
                y1=335.0,
            ),
        ]

        edge = detect_cross_page_edge(previous, following)

        self.assertIsNotNone(edge)
        self.assertEqual(edge.left, SegmentRef(0, 8))
        self.assertEqual(edge.right, SegmentRef(1, 1))
        self.assertIn("capitalized-after-dangling-cue", edge.reasons)
        groups = build_continuation_groups(
            [edge],
            {item.ref: item.text for item in previous + following},
        )
        self.assertEqual(len(groups), 1)
        left, right = groups[0].fragments
        self.assertEqual(
            previous[0].text[left.start : left.end],
            "The Josephson energy E{v36} can be computed from the",
        )
        self.assertEqual(
            following[1].text[right.start : right.end],
            "Ambegaokar–Baratoff formula adapted to the measured "
            "room-temperature resistance of the junction{v0}.",
        )

    def test_capitalized_new_paragraph_is_not_joined_without_dangling_cue(
        self,
    ) -> None:
        previous = [
            segment(
                6,
                4,
                "The measured response remains stable over time",
                column=1,
                y0=74.0,
                y1=121.0,
            )
        ]
        following = [
            segment(
                7,
                0,
                "Methods describe a separate calibration procedure.",
                column=0,
                y0=610.0,
                y1=742.0,
            )
        ]

        self.assertIsNone(detect_cross_page_edge(previous, following))

    def test_finished_sentence_and_heading_do_not_join(self) -> None:
        left = segment(
            0,
            0,
            "This paragraph is complete.",
            column=0,
            y0=70.0,
            y1=140.0,
        )
        right = segment(
            0,
            1,
            "new prose begins here and continues normally.",
            column=1,
            y0=600.0,
            y1=742.0,
        )
        heading = segment(
            1,
            0,
            "IV. CONCLUSION",
            column=0,
            y0=600.0,
            y1=650.0,
            kind="title",
        )

        self.assertEqual(detect_page_edges([left, right]), [])
        self.assertIsNone(detect_cross_page_edge([right], [heading]))

    def test_overlapping_boundary_sentence_is_not_translated_twice(self) -> None:
        first = segment(
            0,
            0,
            "a single unfinished sentence",
            column=0,
            y0=70.0,
            y1=140.0,
        )
        middle = segment(
            0,
            1,
            "continues through an entire short column",
            column=1,
            y0=70.0,
            y1=742.0,
        )
        last = segment(
            1,
            0,
            "and finally ends here.",
            column=0,
            y0=600.0,
            y1=742.0,
        )
        internal = detect_page_edges([first, middle])[0]
        cross = detect_cross_page_edge([middle], [last])

        groups = build_continuation_groups(
            [internal, cross],
            {item.ref: item.text for item in [first, middle, last]},
        )

        self.assertEqual(len(groups), 1)


if __name__ == "__main__":
    unittest.main()
