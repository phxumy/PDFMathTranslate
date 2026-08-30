from __future__ import annotations

import unittest

import numpy as np

from pdf2zh.high_level import (
    _abandon_matches_stronger_text_region,
    _paint_layout_region,
)


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


if __name__ == "__main__":
    unittest.main()
