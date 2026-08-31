from __future__ import annotations

import unittest
from unittest.mock import patch

from pdf2zh.converter import (
    TranslateConverter,
    _MATH_FONT_RE,
    _formula_horizontal_geometry,
    _is_hidden_glyph_repertoire_probe,
    _merge_overlapping_split_math_islands,
    _remove_absorbed_formula_base,
    _segment_contains_prose,
    _should_bridge_cross_class_inline_script,
    _should_bridge_formula_inline_script,
    _should_continue_cross_class_inline_script,
    _should_absorb_trailing_formula_base,
    _should_extend_formula_run,
    _split_trailing_prose_openers,
    _split_trailing_prose_punctuation,
    _split_trailing_formula_prose_word,
)


class FakeChar:
    def __init__(
        self,
        text: str,
        *,
        x0: float = 0.0,
        x1: float = 1.0,
        y0: float = 0.0,
        y1: float = 9.0,
        size: float = 9.0,
        matrix: tuple[float, ...] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        layout_class: int | None = None,
        fontname: str = "AdvOT46dcae81",
    ) -> None:
        self.text = text
        self.x0 = x0
        self.x1 = x1
        self.y0 = y0
        self.y1 = y1
        self.size = size
        self.matrix = matrix
        self.fontname = fontname
        self.cid = 0
        if layout_class is not None:
            self._pdf2zh_layout_class = layout_class

    def get_text(self) -> str:
        return self.text


class SplitMathIslandTests(unittest.TestCase):
    def test_content_ordered_braces_are_merged_with_short_math_syntax(self) -> None:
        segments = ["mode m {v0} {v1}; q {v2} is excited"]
        formulas = [
            [FakeChar("∈", x0=100, x1=106, y0=50)],
            [FakeChar("c", x0=113, x1=117, y0=50)],
            [FakeChar("{", x0=108, x1=111, y0=50), FakeChar("}", x0=127, x1=130, y0=50)],
        ]
        punctuation = FakeChar(";", x0=118, x1=121, y0=50)
        q = FakeChar("q", x0=122, x1=126, y0=50)

        _merge_overlapping_split_math_islands(
            segments, formulas, [0, 0, 0], [[punctuation, q]]
        )

        self.assertEqual(segments, ["mode m {v0} is excited"])
        self.assertEqual("".join(ch.get_text() for ch in formulas[0]), "∈c;q{}")

    def test_ordinary_short_phrase_is_not_merged(self) -> None:
        segments = ["mode m {v0} {v1} and q {v2} is excited"]
        formulas = [[FakeChar("∈")], [FakeChar("c")], [FakeChar("{}")]]
        original = list(segments)
        _merge_overlapping_split_math_islands(segments, formulas, [0, 0, 0], [[]])
        self.assertEqual(segments, original)

    def test_late_enclosing_glyphs_absorb_split_infix_expression(self) -> None:
        segments = ["agreement, i.e., c {v0} m {v1}"]
        minus = FakeChar(
            "−",
            x0=405.3,
            x1=411.05,
            y0=434.04,
            y1=441.51,
            size=7.47,
            fontname="AdvP4C4E74",
        )
        enclosure = [
            FakeChar("(", x0=397.47, x1=400.35, y0=434.04, size=7.47),
            FakeChar(")", x0=418.73, x1=421.61, y0=434.04, size=7.47),
            FakeChar("/", x0=421.62, x1=425.36, y0=434.04, size=7.47),
            FakeChar("c", x0=425.37, x1=428.47, y0=434.04, size=7.47),
            FakeChar(".", x0=428.60, x1=430.10, y0=434.04, size=7.47),
        ]
        left = FakeChar(
            "c", x0=400.36, x1=403.47, y0=434.04, size=7.47
        )
        right = FakeChar(
            "m", x0=412.72, x1=418.72, y0=434.04, size=7.47
        )
        formulas = [[minus], enclosure]

        _merge_overlapping_split_math_islands(
            segments,
            formulas,
            [0, 0],
            [[left, right]],
        )

        self.assertEqual(segments, ["agreement, i.e., {v0}"])
        self.assertEqual(
            set(formulas[0]),
            {minus, left, right, *enclosure},
        )
        self.assertEqual(
            (min(char.x0 for char in formulas[0]), max(char.x1 for char in formulas[0])),
            (397.47, 430.10),
        )

    def test_late_parentheses_absorb_split_diagonal_matrix_entries(self) -> None:
        segments = [
            "junction energies E{v0} diag E{v1}; E{v2}, which lead"
        ]
        declaration = [
            FakeChar("J", x0=75.57, x1=77.79, y0=621.64, size=6.28),
            FakeChar(":", x0=80.96, x1=83.38, y0=623.0),
            FakeChar("¼", x0=83.40, x1=90.30, y0=623.0),
        ]
        first_entry = [
            FakeChar(
                "1",
                x0=118.66,
                x1=121.86,
                y0=621.64,
                size=6.28,
                fontname="AdvOT46dcae81",
            ),
            FakeChar(
                ";",
                x0=122.34,
                x1=124.76,
                y0=623.0,
                fontname="AdvP4C4E51",
            ),
            FakeChar(
                "¼",
                x0=127.79,
                x1=136.75,
                y0=623.0,
                fontname="AdvTT3f84ef53",
            ),
        ]
        late_enclosure = [
            FakeChar("J", x0=146.72, x1=148.94, y0=621.64, size=6.28),
            FakeChar("ð", x0=110.61, x1=114.06, y0=623.0),
            FakeChar("Þ", x0=149.61, x1=153.07, y0=623.0),
        ]
        left_entry_base = FakeChar(
            "E", x0=114.07, x1=118.40, y0=623.0, fontname="AdvOT46dcae81"
        )
        right_entry_base = FakeChar(
            "E", x0=140.0, x1=146.50, y0=623.0, fontname="AdvOT46dcae81"
        )
        middle_separator = FakeChar(
            ";", x0=138.22, x1=140.64, y0=623.0, fontname="AdvP4C4E51"
        )
        formulas = [declaration, first_entry, late_enclosure]

        _merge_overlapping_split_math_islands(
            segments,
            formulas,
            [0, 0, 0],
            [[left_entry_base, middle_separator, right_entry_base]],
        )

        self.assertEqual(
            segments,
            ["junction energies E{v0} diag {v1}, which lead"],
        )
        self.assertEqual(
            set(formulas[1]),
            {
                left_entry_base,
                middle_separator,
                right_entry_base,
                *first_entry,
                *late_enclosure,
            },
        )
        self.assertEqual(
            (
                min(char.x0 for char in formulas[1]),
                max(char.x1 for char in formulas[1]),
            ),
            (110.61, 153.07),
        )

    def test_separate_infix_formulas_are_not_absorbed_without_enclosure(self) -> None:
        segments = ["compare c {v0} m {v1} in both devices"]
        formulas = [
            [
                FakeChar(
                    "−",
                    x0=105,
                    x1=111,
                    y0=50,
                    fontname="AdvP4C4E74",
                )
            ],
            [FakeChar("/", x0=121, x1=125, y0=50)],
        ]
        left = FakeChar("c", x0=100, x1=104, y0=50)
        right = FakeChar("m", x0=112, x1=120, y0=50)
        original = list(segments)

        _merge_overlapping_split_math_islands(
            segments,
            formulas,
            [0, 0],
            [[left, right]],
        )

        self.assertEqual(segments, original)
        self.assertEqual(len(formulas[0]), 1)


class FormulaTrailingProseTests(unittest.TestCase):
    def test_vector_followed_by_denotes_is_returned_to_prose(self) -> None:
        chars = [
            FakeChar("r", x0=100, x1=105),
            FakeChar("!", x0=99, x1=106, fontname="AdvP4C4E74"),
            *[
                FakeChar(letter, x0=110 + index * 4, x1=114 + index * 4)
                for index, letter in enumerate("denotes")
            ],
        ]
        formula, prose, prose_chars = _split_trailing_formula_prose_word(chars)
        self.assertEqual("".join(ch.get_text() for ch in formula), "r!")
        self.assertEqual(prose, " denotes")
        self.assertEqual("".join(ch.get_text() for ch in prose_chars), "denotes")

    def test_formula_symbol_followed_by_copula_is_returned_to_prose(self) -> None:
        chars = [
            FakeChar("E", x0=100, x1=105),
            FakeChar("!", x0=99, x1=106, fontname="AdvP4C4E74"),
            FakeChar("i", x0=107, x1=110),
            FakeChar("s", x0=110, x1=113),
        ]

        formula, prose, prose_chars = _split_trailing_formula_prose_word(chars)

        self.assertEqual("".join(ch.get_text() for ch in formula), "E!")
        self.assertEqual(prose, "is")
        self.assertEqual("".join(ch.get_text() for ch in prose_chars), "is")

    def test_math_function_and_arbitrary_formula_label_are_not_split(self) -> None:
        prefix = [FakeChar("∫", fontname="AdvP4C4E74")]
        for word in ("max", "energy"):
            chars = prefix + [FakeChar(letter) for letter in word]
            formula, prose, prose_chars = _split_trailing_formula_prose_word(chars)
            self.assertEqual(formula, chars)
            self.assertEqual(prose, "")
            self.assertEqual(prose_chars, [])

    def test_adjacent_independent_formulas_are_not_merged_without_overlap(self) -> None:
        segments = ["mode m {v0} {v1}; q {v2} is excited"]
        formulas = [
            [FakeChar("∈", x0=100, x1=106, y0=50)],
            [FakeChar("c", x0=113, x1=117, y0=50)],
            [FakeChar("x", x0=140, x1=145, y0=50)],
        ]
        chars = [[FakeChar(";", x0=118, x1=121, y0=50), FakeChar("q", x0=122, x1=126, y0=50)]]
        original = list(segments)
        _merge_overlapping_split_math_islands(segments, formulas, [0, 0, 0], chars)
        self.assertEqual(segments, original)


def chars(text: str) -> list[FakeChar]:
    return [FakeChar(value) for value in text]


class FormulaBoundaryPunctuationTests(unittest.TestCase):
    def test_vector_accent_absorbs_single_letter_base(self) -> None:
        base = FakeChar("E", x0=100.0, x1=106.0, y0=100.0, y1=109.0)
        accent = FakeChar(
            "!",
            x0=103.0,
            x1=108.0,
            y0=105.0,
            y1=111.0,
            size=6.0,
            fontname="AdvP4C4E74",
        )

        self.assertTrue(
            _should_absorb_trailing_formula_base(
                base,
                accent,
                "fields are E",
                9.0,
            )
        )

    def test_formula_base_absorption_rejects_ordinary_prose(self) -> None:
        base = FakeChar("E", x0=100.0, x1=106.0, y0=100.0, y1=109.0)
        ordinary = FakeChar(
            "i",
            x0=108.0,
            x1=111.0,
            y0=100.0,
            y1=109.0,
        )

        self.assertFalse(
            _should_absorb_trailing_formula_base(
                base,
                ordinary,
                "energy E",
                9.0,
            )
        )
        self.assertFalse(
            _should_absorb_trailing_formula_base(
                base,
                FakeChar(
                    "!",
                    x0=103.0,
                    x1=108.0,
                    y0=105.0,
                    y1=111.0,
                    size=6.0,
                    fontname="AdvP4C4E74",
                ),
                "ordinary prose",
                9.0,
            )
        )

    def test_subscript_and_differential_bases_are_absorbed_in_formula_island(self) -> None:
        base_v = FakeChar("v", x0=100.0, x1=105.0, y0=100.0, y1=109.0)
        subscript = FakeChar(
            "J",
            x0=105.2,
            x1=108.0,
            y0=98.5,
            y1=104.5,
            size=6.0,
            fontname="AdvOT65f8a23b.I",
        )
        self.assertTrue(
            _should_absorb_trailing_formula_base(
                base_v,
                subscript,
                "{v3} v",
                9.0,
                [FakeChar(")", x0=90.0, x1=98.0)],
            )
        )

        previous_formula = [FakeChar(")", x0=90.0, x1=99.0)]
        base_d = FakeChar("d", x0=100.0, x1=105.0, y0=100.0, y1=109.0)
        tau = FakeChar(
            "τ",
            x0=105.0,
            x1=110.0,
            y0=100.0,
            y1=109.0,
            fontname="AdvOT65f8a23b.I+03",
        )
        self.assertTrue(
            _should_absorb_trailing_formula_base(
                base_d,
                tau,
                "{v4} d",
                9.0,
                previous_formula,
            )
        )

    def test_formula_island_gap_is_removed_but_prose_spacing_is_kept(self) -> None:
        previous_formula = [FakeChar("φ", x0=90.0, x1=100.0)]
        base = FakeChar("t", x0=103.0, x1=108.0)

        self.assertEqual(
            _remove_absorbed_formula_base(
                "value {v2} t",
                "t",
                previous_formula,
                base,
                9.0,
            ),
            "value {v2}",
        )
        self.assertEqual(
            _remove_absorbed_formula_base(
                "value, E",
                "E",
                previous_formula,
                base,
                9.0,
            ),
            "value, ",
        )

        bridged_base = FakeChar("r", x0=107.0, x1=112.0)
        overprint = FakeChar("!", x0=104.0, x1=109.0, fontname="AdvP4C4E74")
        self.assertEqual(
            _remove_absorbed_formula_base(
                "field {v71} r",
                "r",
                previous_formula,
                bridged_base,
                9.0,
                overprint,
            ),
            "field {v71}",
        )

    def test_publisher_pi_font_is_math_but_ordinary_text_font_is_not(self) -> None:
        self.assertIsNotNone(_MATH_FONT_RE.search("AdvP4C4E74"))
        self.assertIsNotNone(_MATH_FONT_RE.search("ABCDEF+AdvP4C4E46"))
        self.assertIsNone(_MATH_FONT_RE.search("AdvOT46dcae81"))

    def test_cross_class_small_script_is_joined_to_one_letter_base(self) -> None:
        base = FakeChar("p", x0=100.0, x1=105.0, y0=100.0, y1=109.0)
        script = FakeChar(
            "m",
            x0=105.0,
            x1=109.0,
            y0=98.8,
            y1=104.8,
            size=6.0,
        )

        self.assertTrue(
            _should_bridge_cross_class_inline_script(
                base,
                script,
                "the EPR p",
                9.0,
                3,
                2,
                "plain text",
                "plain text",
            )
        )

    def test_cross_class_formula_subscript_is_kept_in_formula_island(self) -> None:
        base = FakeChar(
            "φ",
            x0=244.347,
            x1=249.763,
            y0=128.523,
            y1=137.489,
            size=8.966,
            layout_class=13,
            fontname="AdvOT65f8a23b.I+03",
        )
        first_script = FakeChar(
            "m",
            x0=249.732,
            x1=255.125,
            y0=125.743,
            y1=131.720,
            size=5.977,
            layout_class=4,
            fontname="AdvOT65f8a23b.I",
        )
        second_script = FakeChar(
            "j",
            x0=255.125,
            x1=256.591,
            y0=125.743,
            y1=131.720,
            size=5.977,
            layout_class=4,
            fontname="AdvOT65f8a23b.I",
        )

        self.assertTrue(
            _should_bridge_formula_inline_script(
                [base],
                base,
                first_script,
                8.966,
                13,
                4,
                "plain text",
                "plain text",
            )
        )
        self.assertTrue(
            _should_continue_cross_class_inline_script(
                [base, first_script],
                first_script,
                second_script,
                8.966,
                4,
                4,
                "plain text",
                "plain text",
            )
        )

    def test_formula_script_bridge_rejects_baseline_prose_and_region_change(self) -> None:
        base = FakeChar(
            "φ",
            x0=100.0,
            x1=106.0,
            y0=100.0,
            y1=109.0,
            size=9.0,
        )
        baseline_letter = FakeChar(
            "m",
            x0=106.0,
            x1=111.0,
            y0=100.0,
            y1=109.0,
            size=6.0,
        )
        subscript = FakeChar(
            "m",
            x0=106.0,
            x1=111.0,
            y0=97.0,
            y1=103.0,
            size=6.0,
        )

        self.assertFalse(
            _should_bridge_formula_inline_script(
                [base],
                base,
                baseline_letter,
                9.0,
                13,
                4,
                "plain text",
                "plain text",
            )
        )
        self.assertFalse(
            _should_bridge_formula_inline_script(
                [base],
                base,
                subscript,
                9.0,
                13,
                4,
                "plain text",
                "table_footnote",
            )
        )

    def test_receive_layout_keeps_cross_class_formula_subscript_atomic(self) -> None:
        class FakePage(list):
            pageid = 1
            width = 300.0
            height = 200.0

        class FakeLayout:
            shape = (200, 300)

            def __getitem__(self, position: tuple[int, int]) -> int:
                _y, x = position
                return 4 if 39 <= x < 47 else 13

        prose = [
            FakeChar(letter, x0=10.0 + 4.0 * index, x1=14.0 + 4.0 * index,
                     y0=100.0, y1=109.0, size=9.0)
            for index, letter in enumerate("value")
        ]
        base = FakeChar(
            "φ", x0=34.0, x1=40.0, y0=100.0, y1=109.0, size=9.0,
            fontname="AdvOT65f8a23b.I+03",
        )
        subscript_m = FakeChar(
            "m", x0=39.9, x1=44.0, y0=97.0, y1=103.0, size=6.0,
            fontname="AdvOT65f8a23b.I",
        )
        subscript_j = FakeChar(
            "j", x0=44.0, x1=47.0, y0=97.0, y1=103.0, size=6.0,
            fontname="AdvOT65f8a23b.I",
        )
        period = FakeChar(
            ".", x0=47.1, x1=49.0, y0=100.0, y1=109.0, size=9.0,
        )
        converter = TranslateConverter.__new__(TranslateConverter)
        converter.layout = {1: FakeLayout()}
        converter.layout_region_types = {
            1: {4: "plain text", 13: "plain text"}
        }
        converter.vfont = ""
        converter.vchar = ""
        converter.translator = type("Translator", (), {"name": "google"})()

        with patch("pdf2zh.converter.LTChar", FakeChar):
            draft = converter.receive_layout(
                FakePage([*prose, base, subscript_m, subscript_j, period]),
                preview_only=True,
            )

        self.assertEqual(draft.sstk, ["value {v0}."])
        self.assertEqual(draft.formula_texts, ["φmj"])
        self.assertEqual(draft.varp, [0])

    def test_cross_class_script_bridge_rejects_footnote_and_new_paragraph(self) -> None:
        base = FakeChar("p", x0=100.0, x1=105.0, y0=100.0, y1=109.0)
        script = FakeChar(
            "1",
            x0=105.0,
            x1=109.0,
            y0=98.8,
            y1=104.8,
            size=6.0,
        )
        cases = [
            ("the response", 3, 2, "plain text", "plain text"),
            ("the EPR p", 3, 2, "plain text", "table_footnote"),
            ("the EPR p", 3, 0, "plain text", "plain text"),
        ]

        for text, previous_class, candidate_class, previous_kind, candidate_kind in cases:
            with self.subTest(text=text, candidate_kind=candidate_kind):
                self.assertFalse(
                    _should_bridge_cross_class_inline_script(
                        base,
                        script,
                        text,
                        9.0,
                        previous_class,
                        candidate_class,
                        previous_kind,
                        candidate_kind,
                    )
                )

    def test_cross_class_script_continuation_accepts_only_same_small_run(self) -> None:
        first = FakeChar(
            "m",
            x0=105.0,
            x1=109.0,
            y0=98.8,
            y1=104.8,
            size=6.0,
        )
        second = FakeChar(
            "l",
            x0=109.0,
            x1=112.0,
            y0=98.8,
            y1=104.8,
            size=6.0,
        )

        self.assertTrue(
            _should_continue_cross_class_inline_script(
                [first],
                first,
                second,
                9.0,
                2,
                2,
                "plain text",
                "plain text",
            )
        )
        self.assertFalse(
            _should_continue_cross_class_inline_script(
                [first],
                first,
                FakeChar("l", x0=125.0, x1=128.0, size=6.0),
                9.0,
                2,
                2,
                "plain text",
                "plain text",
            )
        )

    def test_terminal_sentence_punctuation_is_returned_to_prose(self) -> None:
        for source, expected_formula, expected_suffix in [
            ("49.", "49", "."),
            ("τ=ϕ₀,", "τ=ϕ₀", ","),
            ("x；", "x", "；"),
            ("x?!", "x", "?!"),
        ]:
            with self.subTest(source=source):
                formula, suffix = _split_trailing_prose_punctuation(
                    chars(source)
                )
                self.assertEqual(
                    "".join(item.get_text() for item in formula),
                    expected_formula,
                )
                self.assertEqual(suffix, expected_suffix)

    def test_internal_citation_separator_is_kept(self) -> None:
        formula, suffix = _split_trailing_prose_punctuation(chars("47,48,"))

        self.assertEqual(
            "".join(item.get_text() for item in formula),
            "47,48",
        )
        self.assertEqual(suffix, ",")

    def test_punctuation_only_display_run_is_not_detached(self) -> None:
        source = chars(".,")

        formula, suffix = _split_trailing_prose_punctuation(source)

        self.assertIs(formula, source)
        self.assertEqual(suffix, "")

    def test_formula_only_segment_is_not_treated_as_prose(self) -> None:
        self.assertFalse(_segment_contains_prose(" {v0} {v1} "))
        self.assertTrue(_segment_contains_prose("rate {v0}"))

    def test_unmatched_opening_parenthesis_is_returned_to_following_prose(self) -> None:
        formula, suffix = _split_trailing_prose_openers(chars("|010⟩("))

        self.assertEqual("".join(item.get_text() for item in formula), "|010⟩")
        self.assertEqual(suffix, "(")

    def test_balanced_formula_parentheses_are_not_detached(self) -> None:
        source = chars("sin(x)")

        formula, suffix = _split_trailing_prose_openers(source)

        self.assertIs(formula, source)
        self.assertEqual(suffix, "")

    def test_closing_formula_punctuation_is_not_detached(self) -> None:
        source = chars("|010⟩)")

        formula, suffix = _split_trailing_prose_openers(source)

        self.assertIs(formula, source)
        self.assertEqual(suffix, "")

    def test_parenthetical_prose_opener_is_not_hidden_in_formula(self) -> None:
        formula, suffix = _split_trailing_prose_openers(chars("H_lin(see"))

        self.assertEqual("".join(item.get_text() for item in formula), "H_lin")
        self.assertEqual(suffix, "(see")

    def test_mathematical_function_argument_is_not_detached(self) -> None:
        source = chars("sin(x")

        formula, suffix = _split_trailing_prose_openers(source)

        self.assertIs(formula, source)
        self.assertEqual(suffix, "")

    def test_contiguous_ordinary_font_glyph_extends_formula(self) -> None:
        formula = [FakeChar("φ", x0=45.0, x1=50.5)]
        opening = FakeChar("(", x0=50.8, x1=53.0)
        variable = FakeChar("t", x0=53.1, x1=56.0)

        self.assertTrue(_should_extend_formula_run(formula, opening))
        self.assertTrue(_should_extend_formula_run(formula + [opening], variable))

    def test_formula_extension_stops_before_following_prose(self) -> None:
        formula = [FakeChar(",", x0=130.0, x1=131.5)]
        following_word = FakeChar("w", x0=134.0, x1=139.0)

        self.assertFalse(_should_extend_formula_run(formula, following_word))

        citation = [FakeChar("47,48", x0=100.0, x1=120.0)]
        self.assertFalse(
            _should_extend_formula_run(
                citation,
                FakeChar("(", x0=120.5, x1=123.0),
            )
        )
        subscript = [FakeChar("lin", x0=100.0, x1=115.0)]
        self.assertFalse(
            _should_extend_formula_run(
                subscript,
                FakeChar("(", x0=117.0, x1=119.5),
            )
        )

    def test_formula_extension_rejects_new_line_or_large_gap(self) -> None:
        formula = [FakeChar("φ", x0=45.0, x1=50.5, y0=100.0, y1=109.0)]

        self.assertFalse(
            _should_extend_formula_run(
                formula,
                FakeChar("t", x0=60.0, x1=63.0, y0=100.0, y1=109.0),
            )
        )
        self.assertFalse(
            _should_extend_formula_run(
                formula,
                FakeChar("t", x0=50.6, x1=53.0, y0=80.0, y1=89.0),
            )
        )

    def test_hidden_vertical_glyph_repertoire_probe_is_suppressed(self) -> None:
        probe = [
            FakeChar(
                value,
                x0=1.0,
                x1=6.0,
                y0=float(index),
                y1=float(index + 1),
                matrix=(0.0, 5.0, -5.0, 0.0, 6.0, float(index)),
            )
            for index, value in enumerate("1234567890():,;")
        ]

        self.assertTrue(_is_hidden_glyph_repertoire_probe(probe, 600.0))
        self.assertFalse(
            _is_hidden_glyph_repertoire_probe(probe[:3], 600.0)
        )
        visible = list(probe)
        visible[0] = FakeChar(
            "1",
            x0=50.0,
            x1=55.0,
            matrix=(0.0, 5.0, -5.0, 0.0, 50.0, 0.0),
        )
        self.assertFalse(_is_hidden_glyph_repertoire_probe(visible, 600.0))

    def test_formula_geometry_uses_leftmost_glyph_not_content_order(self) -> None:
        # PDF drawing order emits the accent/script glyph first even though it is
        # visually to the right of the formula base.
        formula = [
            FakeChar("^", x0=120.0, x1=124.0),
            FakeChar("H", x0=100.0, x1=112.0),
            FakeChar("q", x0=113.0, x1=118.0),
        ]

        self.assertEqual(_formula_horizontal_geometry(formula), (100.0, 24.0))

    def test_preserved_geometry_keeps_first_glyph_anchor(self) -> None:
        # A preserved run may combine items that are distant on the page and whose
        # PDF content order is meaningful.  Keep the original anchor and negative
        # relative offset instead of moving the entire run to its leftmost glyph.
        preserved = [
            FakeChar("H", x0=301.0, x1=330.0, layout_class=0),
            FakeChar("3", x0=35.0, x1=40.0, layout_class=0),
        ]

        self.assertEqual(_formula_horizontal_geometry(preserved), (301.0, 29.0))


if __name__ == "__main__":
    unittest.main()
