from __future__ import annotations

import unittest

from pdf2zh.converter import (
    _fit_target_vertical_layout,
    _rebalance_cjk_title_orphan,
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


class CjkTitleOrphanTests(unittest.TestCase):
    def test_moves_suffix_from_penultimate_line(self) -> None:
        source = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰"

        result = _rebalance_cjk_title_orphan(
            source,
            line_capacity=7.0,
            character_advance=lambda _character: 1.0,
        )

        self.assertEqual(result, "甲乙丙丁戊己庚辛壬癸子\n丑寅卯辰")
        self.assertEqual(result.replace("\n", ""), source)

    def test_does_not_touch_formula_style_or_explicit_breaks(self) -> None:
        cases = (
            "量子{v0}电路标题甲乙丙丁",
            "[[PDF2ZH_ITALIC_1_BEGIN]]斜体标题[[PDF2ZH_ITALIC_1_END]]",
            "已有\n换行",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(
                    _rebalance_cjk_title_orphan(
                        source,
                        line_capacity=4.0,
                        character_advance=lambda _character: 1.0,
                    ),
                    source,
                )


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

    def test_formula_wrapped_line_final_hyphen_uses_real_formula_text(self) -> None:
        self.assertFalse(
            _should_insert_reconstructed_line_space(
                "introduce {v7}",
                "B",
                "Sound-",
            )
        )
        self.assertTrue(
            _should_insert_reconstructed_line_space(
                "introduce {v7}",
                "a",
                "pre-",
            )
        )
        self.assertTrue(
            _should_insert_reconstructed_line_space(
                "introduce {v7}",
                "B",
                "x-",
            )
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
