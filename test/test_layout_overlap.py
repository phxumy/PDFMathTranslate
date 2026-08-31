from __future__ import annotations

import unittest

import numpy as np

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


if __name__ == "__main__":
    unittest.main()
