from __future__ import annotations

import unittest

from pdf2zh.reading_flow import (
    FlowSegment,
    FragmentSpan,
    SegmentRef,
    detect_reference_continuation,
)

LEFT_ENTRY = (
    "17. Solgun, F., DiVincenzo, D. P. & Gambetta, J. M. Simple impedance "
    "response formulas for the dispersive interaction rates in the effective "
    "Hamiltonians of low"
)
RIGHT_PREFIX = (
    "anharmonicity superconducting qubits. IEEE Trans. Microwave Theory Tech. "
    "67, 928–948 (2019). "
)
NEXT_ENTRY = (
    "18. Petrescu, A., Malekakhlagh, M. & Türeci, H. E. Lifetime "
    "renormalization of driven weakly anharmonic superconducting qubits: II. "
    "The readout problem. Phys. Rev. B 101, 134510 (2019)."
)


def segment(
    page_id: int,
    segment_index: int,
    text: str,
    *,
    column: int | None,
    y0: float,
    y1: float,
    size: float = 8.0,
    width: float | None = None,
    kind: str = "plain text",
) -> FlowSegment:
    page_width = 595.0
    if column == 0:
        x0, x1 = 40.0, 290.0
    elif column == 1:
        x0, x1 = 305.0, 555.0
    else:
        x0, x1 = 40.0, 555.0
    if width is not None:
        x1 = x0 + width
    return FlowSegment(
        SegmentRef(page_id, segment_index),
        text,
        x0,
        x1,
        y0,
        y1,
        size,
        page_width,
        791.0,
        kind,
    )


def reference_pages(
    *,
    left_text: str = LEFT_ENTRY,
    right_text: str = RIGHT_PREFIX + NEXT_ENTRY,
    previous_page: int = 8,
    following_page: int = 9,
    left_column: int | None = 1,
    right_column: int | None = 0,
    left_y0: float = 36.0,
    right_y1: float = 752.0,
    left_size: float = 8.0,
    right_size: float = 8.0,
    left_width: float | None = None,
    right_width: float | None = None,
    left_kind: str = "plain text",
    right_kind: str = "plain text",
) -> tuple[list[FlowSegment], list[FlowSegment]]:
    return (
        [
            segment(
                previous_page,
                4,
                left_text,
                column=left_column,
                y0=left_y0,
                y1=390.0,
                size=left_size,
                width=left_width,
                kind=left_kind,
            )
        ],
        [
            segment(
                following_page,
                0,
                right_text,
                column=right_column,
                y0=390.0,
                y1=right_y1,
                size=right_size,
                width=right_width,
                kind=right_kind,
            )
        ],
    )


class ReferenceContinuationTests(unittest.TestCase):
    def test_reference_17_is_detected_without_swallowing_reference_18(self) -> None:
        previous, following = reference_pages()

        group = detect_reference_continuation(previous, following)

        self.assertIsNotNone(group)
        assert group is not None
        left, right = group.fragments
        self.assertEqual(group.reference_number, 17)
        self.assertEqual(group.reference_prefix, "")
        self.assertEqual(previous[0].text[left.start : left.end], LEFT_ENTRY)
        self.assertEqual(following[0].text[right.start : right.end], RIGHT_PREFIX)
        self.assertEqual(group.left_marker_end, len("17."))
        self.assertEqual(group.next_marker_start, len(RIGHT_PREFIX))
        self.assertNotIn("18.", following[0].text[right.start : right.end])

    def test_single_column_pages_are_supported(self) -> None:
        previous, following = reference_pages(
            left_column=None,
            right_column=None,
        )

        self.assertIsNotNone(detect_reference_continuation(previous, following))

    def test_complete_year_or_volume_page_tail_is_not_joined(self) -> None:
        complete_tails = (
            LEFT_ENTRY + " anharmonicity superconducting qubits (2019).",
            (
                "17. Solgun, F. et al. Simple impedance response formulas. "
                "Phys. Rev. B 101, 134509."
            ),
        )
        for complete in complete_tails:
            with self.subTest(complete=complete):
                previous, following = reference_pages(left_text=complete)
                self.assertIsNone(detect_reference_continuation(previous, following))

    def test_wrong_next_number_or_nonconsecutive_page_is_rejected(self) -> None:
        previous, following = reference_pages(
            right_text=RIGHT_PREFIX + NEXT_ENTRY.replace("18.", "19.", 1)
        )
        self.assertIsNone(detect_reference_continuation(previous, following))

        previous, following = reference_pages(following_page=10)
        self.assertIsNone(detect_reference_continuation(previous, following))

    def test_wrong_columns_or_page_boundary_geometry_is_rejected(self) -> None:
        cases = (
            {"left_column": 0},
            {"right_column": 1},
            {"left_y0": 260.0},
            {"right_y1": 520.0},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                previous, following = reference_pages(**kwargs)
                self.assertIsNone(detect_reference_continuation(previous, following))

    def test_mismatched_font_size_or_column_width_is_rejected(self) -> None:
        previous, following = reference_pages(right_size=10.0)
        self.assertIsNone(detect_reference_continuation(previous, following))

        previous, following = reference_pages(right_width=150.0)
        self.assertIsNone(detect_reference_continuation(previous, following))

    def test_formula_and_internal_tokens_fail_closed(self) -> None:
        unsafe_values = (
            " {v0}",
            " [[PDF2ZH_FLOW_0]]",
            " [[PDF2ZH_ITALIC_0_BEGIN]]",
            " [[PDF2ZH_REF_BOUNDARY_0]]",
        )
        for unsafe in unsafe_values:
            with self.subTest(unsafe=unsafe, side="left"):
                previous, following = reference_pages(left_text=LEFT_ENTRY + unsafe)
                self.assertIsNone(detect_reference_continuation(previous, following))
            with self.subTest(unsafe=unsafe, side="right"):
                previous, following = reference_pages(
                    right_text=RIGHT_PREFIX + unsafe + NEXT_ENTRY
                )
                self.assertIsNone(detect_reference_continuation(previous, following))

    def test_non_plain_text_regions_are_rejected(self) -> None:
        previous, following = reference_pages(left_kind="figure_caption")
        self.assertIsNone(detect_reference_continuation(previous, following))

        previous, following = reference_pages(right_kind="table_caption")
        self.assertIsNone(detect_reference_continuation(previous, following))

    def test_occupied_fragment_prevents_a_second_override(self) -> None:
        previous, following = reference_pages()
        first = detect_reference_continuation(previous, following)
        self.assertIsNotNone(first)
        assert first is not None
        occupied = [
            FragmentSpan(
                first.fragments[0].ref,
                first.fragments[0].start + 1,
                first.fragments[0].end,
            )
        ]

        self.assertIsNone(
            detect_reference_continuation(
                previous,
                following,
                occupied=occupied,
            )
        )


if __name__ == "__main__":
    unittest.main()
