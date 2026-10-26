from __future__ import annotations

import unittest

import numpy as np
from pymupdf import Document

from pdf2zh.high_level import (
    _abandon_matches_stronger_text_region,
    _build_layout_mask,
    _paint_layout_region,
)


class _FakeScalar:
    def __init__(self, value: float) -> None:
        self.value = value

    def squeeze(self) -> float:
        return self.value


class _FakeBox:
    def __init__(
        self,
        class_id: int,
        bounds: tuple[int, int, int, int],
        confidence: float = 0.8,
    ) -> None:
        self.cls = class_id
        self.xyxy = np.array([bounds], dtype=np.float32)
        self.conf = _FakeScalar(confidence)


class _FakeLayout:
    def __init__(self, boxes: list[_FakeBox], names: dict[int, str]) -> None:
        self.boxes = boxes
        self.names = names


class LayoutOverlapTests(unittest.TestCase):
    def test_clipped_caption_edges_include_whole_words_and_superscript(self):
        with Document() as document:
            page = document.new_page(width=600, height=800)
            caption = "Table 4. SAR Exploration of the Methylene Linker Substituent"
            page.insert_text((50, 70), caption, fontsize=10)
            right = page.search_for("Substituent")[0].x1
            page.insert_text((right, 66), "a", fontsize=6)
            page.insert_text((50, 98), "WRN ADP-Glo IC50", fontsize=10)
            layout, kinds = _build_layout_mask(
                _FakeLayout(
                    [
                        _FakeBox(0, (63, 59, int(right) - 3, 72)),
                        _FakeBox(1, (45, 74, 550, 500)),
                    ],
                    {0: "figure_caption", 1: "table"},
                ),
                800,
                600,
                page,
            )
            self.assertEqual(kinds[2], "table_caption")
            for word in page.get_text("words"):
                if word[1] < 74:
                    for x in (word[0] + 1, word[2] - 1):
                        y = 800 - (word[1] + word[3]) / 2
                        self.assertEqual(layout[int(y), int(x)], 2, word[4])
            self.assertEqual(layout[705, 80], 0)

    def test_caption_expansion_does_not_absorb_adjacent_body_or_column(self):
        with Document() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text((50, 70), "Figure 2. Inhibitor structures.", fontsize=10)
            page.insert_text((340, 70), "An unrelated body paragraph.", fontsize=10)
            page.insert_text((50, 90), "The subsequent body paragraph.", fontsize=10)
            layout, kinds = _build_layout_mask(
                _FakeLayout([_FakeBox(0, (70, 59, 165, 73))], {0: "figure_caption"}),
                800,
                600,
                page,
            )
            self.assertEqual(layout[735, 52], 2)
            self.assertEqual(layout[735, 345], 1)
            self.assertEqual(layout[715, 55], 1)

    def test_caption_expansion_requires_source_numbered_caption(self):
        with Document() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text(
                (50, 70), "Table values appear in the main text.", fontsize=10
            )
            layout, _ = _build_layout_mask(
                _FakeLayout([_FakeBox(0, (70, 59, 165, 73))], {0: "figure_caption"}),
                800,
                600,
                page,
            )
            self.assertEqual(layout[735, 52], 1)

    def test_multiline_scheme_caption_recovers_each_clipped_line(self):
        with Document() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text(
                (50, 70),
                "Scheme 1. Enantioselective synthesis of the\n"
                "covalent inhibitor and its substituted analogues.",
                fontsize=10,
            )
            layout, kinds = _build_layout_mask(
                _FakeLayout([_FakeBox(0, (70, 59, 225, 89))], {0: "figure_caption"}),
                800,
                600,
                page,
            )
            for word in page.get_text("words"):
                for x in (word[0] + 1, word[2] - 1):
                    y = 800 - (word[1] + word[3]) / 2
                    self.assertEqual(layout[int(y), int(x)], 2, word[4])
            self.assertEqual(kinds[2], "figure_caption")

    def test_marked_statistical_table_note_survives_combined_figure_container(self):
        with Document() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text((50, 119), "a", fontsize=7)
            page.insert_text(
                (54, 123),
                "Data represent the geometric mean of three replicates.",
                fontsize=9,
            )
            layout, kinds = _build_layout_mask(
                _FakeLayout(
                    [
                        _FakeBox(0, (40, 65, 560, 600)),
                        _FakeBox(1, (49, 50, 300, 65)),
                    ],
                    {0: "figure", 1: "table_caption"},
                ),
                800,
                600,
                page,
            )
            self.assertEqual(kinds[int(layout[680, 80])], "table_footnote")
            self.assertEqual(layout[600, 80], 0)

    def test_unmarked_plot_text_is_not_recovered_as_a_table_note(self):
        with Document() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text(
                (50, 123),
                "Data represent the geometric mean of three replicates.",
                fontsize=9,
            )
            layout, kinds = _build_layout_mask(
                _FakeLayout(
                    [
                        _FakeBox(0, (40, 65, 560, 600)),
                        _FakeBox(1, (49, 50, 300, 65)),
                    ],
                    {0: "figure", 1: "table_caption"},
                ),
                800,
                600,
                page,
            )
            self.assertNotIn("table_footnote", kinds.values())
            self.assertEqual(layout[680, 80], 0)

    def test_supplementary_heading_is_recovered_from_abandon(self) -> None:
        with Document() as document:
            page = document.new_page(width=612, height=792)
            page.insert_text((72, 86), "Supplementary Figures", fontsize=14)
            layout, kinds = _build_layout_mask(
                _FakeLayout(
                    [_FakeBox(0, (70, 69, 220, 91), confidence=0.82)],
                    {0: "abandon"},
                ),
                792,
                612,
                page,
            )
            self.assertEqual(kinds[2], "title")
            self.assertEqual(layout[710, 100], 2)

    def test_equation_continuation_is_recovered_with_nearby_formula(self) -> None:
        with Document() as document:
            page = document.new_page(width=595, height=842)
            page.insert_text((48, 77), "spectra are given by", fontsize=10)
            layout, kinds = _build_layout_mask(
                _FakeLayout(
                    [
                        _FakeBox(0, (45, 66, 135, 81), confidence=0.54),
                        _FakeBox(1, (94, 87, 244, 115), confidence=0.92),
                    ],
                    {0: "abandon", 1: "isolate_formula"},
                ),
                842,
                595,
                page,
            )
            self.assertEqual(kinds[2], "plain text")
            self.assertEqual(layout[766, 60], 2)
            self.assertEqual(layout[742, 120], 0)

    def test_abandoned_lead_in_requires_a_nearby_formula(self) -> None:
        with Document() as document:
            page = document.new_page(width=595, height=842)
            page.insert_text((48, 77), "spectra are given by", fontsize=10)
            layout, kinds = _build_layout_mask(
                _FakeLayout(
                    [_FakeBox(0, (45, 66, 135, 81), confidence=0.54)],
                    {0: "abandon"},
                ),
                842,
                595,
                page,
            )
            self.assertEqual(kinds[2], "abandon")
            self.assertEqual(layout[766, 60], 0)

    def test_supplementary_running_header_and_figure_label_stay_protected(self) -> None:
        with Document() as document:
            page = document.new_page(width=612, height=792)
            page.insert_text((72, 25), "Supplementary Figures", fontsize=10)
            page.insert_text((72, 86), "Supplementary Figures", fontsize=14)
            layout, kinds = _build_layout_mask(
                _FakeLayout(
                    [
                        _FakeBox(0, (70, 13, 220, 28), confidence=0.82),
                        _FakeBox(0, (70, 69, 220, 91), confidence=0.82),
                        _FakeBox(1, (65, 65, 250, 200), confidence=0.90),
                    ],
                    {0: "abandon", 1: "figure"},
                ),
                792,
                612,
                page,
            )
            self.assertEqual(kinds[2], "abandon")
            self.assertEqual(kinds[3], "abandon")
            self.assertEqual(layout[770, 100], 0)
            self.assertEqual(layout[710, 100], 0)

    def test_low_confidence_abandon_does_not_erase_plain_text(self) -> None:
        layout = np.ones((6, 6))
        confidence = np.full(layout.shape, -np.inf, dtype=np.float32)
        bounds = (1, 1, 5, 5)

        _paint_layout_region(layout, confidence, bounds, 2, 0.8)
        _paint_layout_region(layout, confidence, bounds, 0, 0.4)

        self.assertTrue(np.all(layout[1:5, 1:5] == 2))
        self.assertTrue(np.allclose(confidence[1:5, 1:5], 0.8))

    def test_stronger_abandon_can_override_plain_text(self) -> None:
        layout = np.ones((6, 6))
        confidence = np.full(layout.shape, -np.inf, dtype=np.float32)
        bounds = (1, 1, 5, 5)

        _paint_layout_region(layout, confidence, bounds, 2, 0.4)
        _paint_layout_region(layout, confidence, bounds, 0, 0.8)

        self.assertTrue(np.all(layout[1:5, 1:5] == 0))

    def test_figure_and_formula_regions_can_still_force_preservation(self) -> None:
        layout = np.ones((6, 6))
        confidence = np.full(layout.shape, -np.inf, dtype=np.float32)
        bounds = (1, 1, 5, 5)

        _paint_layout_region(layout, confidence, bounds, 2, 0.95)
        _paint_layout_region(
            layout,
            confidence,
            bounds,
            0,
            0.2,
            force=True,
        )

        self.assertTrue(np.all(layout[1:5, 1:5] == 0))

    def test_near_duplicate_abandon_defers_to_stronger_text(self) -> None:
        self.assertTrue(
            _abandon_matches_stronger_text_region(
                (10, 20, 110, 220),
                0.4,
                [((10, 20, 110, 220), 0.8)],
            )
        )

    def test_small_nested_abandon_does_not_defer_to_large_text(self) -> None:
        self.assertFalse(
            _abandon_matches_stronger_text_region(
                (10, 20, 30, 40),
                0.4,
                [((0, 0, 200, 200), 0.8)],
            )
        )

    def test_abandon_does_not_defer_to_weaker_text(self) -> None:
        self.assertFalse(
            _abandon_matches_stronger_text_region(
                (10, 20, 110, 220),
                0.8,
                [((10, 20, 110, 220), 0.4)],
            )
        )

    def test_table_caption_is_repainted_over_table(self) -> None:
        layout, region_types = _build_layout_mask(
            _FakeLayout(
                [
                    _FakeBox(0, (1, 1, 9, 9)),
                    _FakeBox(1, (2, 2, 8, 4)),
                ],
                {0: "table", 1: "table_caption"},
            ),
            10,
            10,
        )

        self.assertEqual(region_types, {2: "table", 3: "table_caption"})
        self.assertEqual(layout[7, 3], 3)
        self.assertEqual(layout[3, 3], 0)

    def test_figure_without_caption_remains_fully_preserved(self) -> None:
        layout, _ = _build_layout_mask(
            _FakeLayout(
                [_FakeBox(0, (1, 1, 9, 9))],
                {0: "figure"},
            ),
            10,
            10,
        )

        self.assertTrue(np.all(layout[0:9, 0:9] == 0))

    def test_formula_inside_caption_takes_final_precedence(self) -> None:
        layout, _ = _build_layout_mask(
            _FakeLayout(
                [
                    _FakeBox(0, (1, 1, 9, 9)),
                    _FakeBox(1, (2, 2, 8, 5)),
                    _FakeBox(2, (4, 3, 6, 4)),
                ],
                {
                    0: "figure",
                    1: "figure_caption",
                    2: "isolate_formula",
                },
            ),
            10,
            10,
        )

        self.assertEqual(layout[4, 2], 3)
        self.assertEqual(layout[6, 4], 0)

    def test_weak_duplicate_formula_does_not_erase_stronger_title(self) -> None:
        layout, region_types = _build_layout_mask(
            _FakeLayout(
                [
                    _FakeBox(0, (2, 2, 8, 4), confidence=0.80),
                    _FakeBox(1, (2, 2, 8, 4), confidence=0.26),
                ],
                {0: "title", 1: "isolate_formula"},
            ),
            10,
            10,
        )

        self.assertEqual(region_types, {2: "title", 3: "isolate_formula"})
        self.assertTrue(np.all(layout[6:8, 2:8] == 2))

    def test_stronger_duplicate_formula_still_preserves_content(self) -> None:
        layout, _ = _build_layout_mask(
            _FakeLayout(
                [
                    _FakeBox(0, (2, 2, 8, 4), confidence=0.60),
                    _FakeBox(1, (2, 2, 8, 4), confidence=0.90),
                ],
                {0: "title", 1: "isolate_formula"},
            ),
            10,
            10,
        )

        self.assertTrue(np.all(layout[6:8, 2:8] == 0))


if __name__ == "__main__":
    unittest.main()
