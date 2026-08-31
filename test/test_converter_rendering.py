from __future__ import annotations

import unittest

from pdf2zh.converter import (
    _fit_target_vertical_layout,
    _should_insert_reconstructed_line_space,
    _should_pre_wrap_line_break_unit,
)
from pdf2zh.line_breaking import iter_line_break_units


class TargetVerticalFitTests(unittest.TestCase):
    def test_overfull_paragraph_scales_font_instead_of_collapsing_leading(self) -> None:
        # The historical loop stopped at 0.95 here, occupying 38 pt in a
        # 30 pt source box.  Keep readable leading and scale the entire block.
        fit = _fit_target_vertical_layout(
            line_count=4,
            font_size=10.0,
            available_height=30.0,
            default_line_height=1.4,
        )

        self.assertAlmostEqual(0.95, fit.line_height)
        self.assertAlmostEqual(30.0 / 38.0, fit.render_scale)
        self.assertLessEqual(
            4 * 10.0 * fit.render_scale * fit.line_height,
            30.0,
        )
        self.assertTrue(fit.contained)

    def test_existing_stepwise_compaction_is_preserved_when_it_fits(self) -> None:
        fit = _fit_target_vertical_layout(
            line_count=2,
            font_size=10.0,
            available_height=22.0,
            default_line_height=1.4,
        )

        self.assertAlmostEqual(1.1, fit.line_height)
        self.assertEqual(1.0, fit.render_scale)

    def test_comfortable_paragraph_keeps_default_geometry(self) -> None:
        fit = _fit_target_vertical_layout(
            line_count=2,
            font_size=10.0,
            available_height=40.0,
            default_line_height=1.4,
        )

        self.assertEqual(1.4, fit.line_height)
        self.assertEqual(1.0, fit.render_scale)

    def test_invalid_geometry_is_reported_without_zero_sized_text(self) -> None:
        fit = _fit_target_vertical_layout(
            line_count=2,
            font_size=10.0,
            available_height=0.0,
            default_line_height=1.4,
        )

        self.assertFalse(fit.contained)
        self.assertEqual(1.0, fit.render_scale)


class OpeningPunctuationPreWrapTests(unittest.TestCase):
    def test_overwide_parenthesized_unit_moves_off_nonempty_line(self) -> None:
        unit = next(iter(iter_line_break_units("（见补充材料中的完整推导）")))

        self.assertGreater(len(unit.text), 2)
        self.assertTrue(
            _should_pre_wrap_line_break_unit(
                unit,
                current_x=92.0,
                unit_extent=120.0,
                right_limit=100.0,
                line_capacity=100.0,
            )
        )

    def test_overwide_formula_unit_keeps_existing_fallback(self) -> None:
        unit = next(iter(iter_line_break_units("{v0}")))

        self.assertFalse(
            _should_pre_wrap_line_break_unit(
                unit,
                current_x=92.0,
                unit_extent=120.0,
                right_limit=100.0,
                line_capacity=100.0,
            )
        )


class ReconstructedLineSpaceTests(unittest.TestCase):
    def test_line_final_hyphen_already_joins_latin_fragments(self) -> None:
        cases = (
            ("PhD thesis, Cali-", "f"),
            ("SC-086/50-NbTi-", "N"),
            ("without a sus-", "p"),
        )
        for preceding, following in cases:
            with self.subTest(preceding=preceding, following=following):
                self.assertFalse(
                    _should_insert_reconstructed_line_space(preceding, following)
                )

    def test_legal_or_ambiguous_hyphen_boundaries_keep_a_space(self) -> None:
        cases = (
            ("pre-", "a"),  # pre- and post-processing
            ("two-", "o"),  # two- or three-dimensional
            ("mid-", "t"),  # mid- to late-century
            ("range -", "b"),  # spaced dash, not a split word
            ("-", "x"),  # no preceding Latin fragment
            ("well-", "中"),  # not a Latin continuation
        )
        for preceding, following in cases:
            with self.subTest(preceding=preceding, following=following):
                self.assertTrue(
                    _should_insert_reconstructed_line_space(preceding, following)
                )

    def test_unit_that_fits_remaining_space_does_not_pre_wrap(self) -> None:
        unit = next(iter(iter_line_break_units("（见图1）")))

        self.assertFalse(
            _should_pre_wrap_line_break_unit(
                unit,
                current_x=40.0,
                unit_extent=30.0,
                right_limit=100.0,
                line_capacity=100.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
