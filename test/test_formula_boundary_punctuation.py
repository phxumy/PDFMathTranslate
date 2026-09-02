from __future__ import annotations

import unittest
from unittest.mock import patch

from pdf2zh.converter import (
    Paragraph,
    TranslateConverter,
    _MATH_FONT_RE,
    _collect_translatable_italic_runs,
    _formula_horizontal_geometry,
    _is_hidden_glyph_repertoire_probe,
    _merge_overlapping_split_math_islands,
    _split_formula_prose_boundaries,
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
    _split_trailing_formula_prose_clause,
    _split_trailing_runin_connector,
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
            [
                FakeChar("{", x0=108, x1=111, y0=50),
                FakeChar("}", x0=127, x1=130, y0=50),
            ],
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
        left = FakeChar("c", x0=400.36, x1=403.47, y0=434.04, size=7.47)
        right = FakeChar("m", x0=412.72, x1=418.72, y0=434.04, size=7.47)
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
            (
                min(char.x0 for char in formulas[0]),
                max(char.x1 for char in formulas[0]),
            ),
            (397.47, 430.10),
        )

    def test_late_parentheses_absorb_split_diagonal_matrix_entries(self) -> None:
        segments = ["junction energies E{v0} diag E{v1}; E{v2}, which lead"]
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
    def test_roman_connector_is_released_from_italic_runin_heading(self) -> None:
        heading = "Simultaneous extraction of multiple SEs:"
        connector = "In"
        run: list[FakeChar] = []
        x = 0.0
        for value in heading:
            if value.isspace():
                x += 3.0
                continue
            run.append(
                FakeChar(
                    value,
                    x0=x,
                    x1=x + 4.0,
                    y0=100.0,
                    y1=110.0,
                    size=10.0,
                    layout_class=2,
                    fontname="Times-Italic",
                )
            )
            x += 4.0
        x += 2.0
        for value in connector:
            run.append(
                FakeChar(
                    value,
                    x0=x,
                    x1=x + 4.0,
                    y0=100.0,
                    y1=110.0,
                    size=10.0,
                    layout_class=2,
                    fontname="Times-Roman",
                )
            )
            x += 4.0

        formula, prose, prose_chars = _split_trailing_runin_connector(
            run,
            paragraph_layout_class=2,
        )

        self.assertEqual(
            "".join(char.get_text() for char in formula), heading.replace(" ", "")
        )
        self.assertEqual(prose, " In")
        self.assertEqual("".join(char.get_text() for char in prose_chars), connector)

    def test_runin_connector_split_rejects_unlisted_or_unsafe_shapes(self) -> None:
        def make_run(connector: str, *, heading_font: str = "Times-Italic"):
            values = "Heading words:"
            run: list[FakeChar] = []
            x = 0.0
            for value in values:
                if value.isspace():
                    x += 3.0
                    continue
                run.append(
                    FakeChar(
                        value,
                        x0=x,
                        x1=x + 4.0,
                        y0=100.0,
                        y1=110.0,
                        size=10.0,
                        layout_class=2,
                        fontname=heading_font,
                    )
                )
                x += 4.0
            x += 2.0
            for value in connector:
                run.append(
                    FakeChar(
                        value,
                        x0=x,
                        x1=x + 4.0,
                        y0=100.0,
                        y1=110.0,
                        size=10.0,
                        layout_class=2,
                        fontname="Times-Roman",
                    )
                )
                x += 4.0
            return run

        for run, layout_class in (
            (make_run("Because"), 2),
            (make_run("The", heading_font="Times-Roman"), 2),
            (make_run("The"), 0),
        ):
            with self.subTest(layout_class=layout_class):
                self.assertEqual(
                    _split_trailing_runin_connector(
                        run,
                        paragraph_layout_class=layout_class,
                    ),
                    (run, "", []),
                )

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

    def test_formula_closer_followed_by_copula_is_returned_to_prose(self) -> None:
        chars = [
            FakeChar("·", x0=100, x1=105, fontname="CMSY10"),
            FakeChar(")", x0=105, x1=108, fontname="CMR10"),
            FakeChar("i", x0=110, x1=113),
            FakeChar("s", x0=113, x1=116),
        ]

        formula, prose, prose_chars = _split_trailing_formula_prose_word(chars)

        self.assertEqual("".join(ch.get_text() for ch in formula), "·)")
        self.assertEqual(prose, " is")
        self.assertEqual("".join(ch.get_text() for ch in prose_chars), "is")

    def test_closed_connectors_and_arrow_legend_are_returned_to_prose(self) -> None:
        cases = [
            (
                [
                    FakeChar("N", x0=100, x1=105, fontname="CMMI10"),
                    FakeChar("=", x0=105, x1=110, fontname="CMR10"),
                    FakeChar("4", x0=110, x1=115, fontname="CMR10"),
                    FakeChar(")", x0=115, x1=118, fontname="CMR10"),
                    FakeChar("i", x0=120, x1=123),
                    FakeChar("n", x0=123, x1=127),
                ],
                "N=4)",
                " in",
            ),
            (
                [
                    FakeChar("x", x0=100, x1=105, fontname="CMMI10"),
                    FakeChar(")", x0=105, x1=108, fontname="CMR10"),
                    FakeChar("a", x0=110, x1=114),
                    FakeChar("n", x0=114, x1=118),
                    FakeChar("d", x0=118, x1=122),
                ],
                "x)",
                " and",
            ),
            (
                [
                    FakeChar("↓", x0=100, x1=105, fontname="CMSY10"),
                    *[
                        FakeChar(
                            letter,
                            x0=108 + index * 4,
                            x1=112 + index * 4,
                        )
                        for index, letter in enumerate("indicates")
                    ],
                ],
                "↓",
                " indicates",
            ),
        ]
        for chars, formula_text, prose_text in cases:
            with self.subTest(source="".join(ch.get_text() for ch in chars)):
                formula, prose, prose_chars = _split_trailing_formula_prose_word(
                    chars,
                    has_prose_context=True,
                )
                self.assertEqual("".join(ch.get_text() for ch in formula), formula_text)
                self.assertEqual(prose, prose_text)
                self.assertEqual(
                    "".join(ch.get_text() for ch in prose_chars),
                    prose_text.strip(),
                )

    def test_trailing_word_split_preserves_class_zero_and_math_words(self) -> None:
        protected = [
            FakeChar("↓", fontname="CMSY10", layout_class=0),
            *[FakeChar(letter, layout_class=0) for letter in "indicates"],
        ]
        formula, prose, prose_chars = _split_trailing_formula_prose_word(protected)
        self.assertEqual(formula, protected)
        self.assertEqual((prose, prose_chars), ("", []))

    def test_short_connectors_require_prose_context_and_safe_geometry(self) -> None:
        formula_only = [
            FakeChar("f", x0=0, x1=4, fontname="CMMI10"),
            FakeChar("(", x0=4, x1=6, fontname="CMR10"),
            FakeChar("x", x0=6, x1=10, fontname="CMMI10"),
            FakeChar(")", x0=10, x1=12, fontname="CMR10"),
            FakeChar("a", x0=14, x1=18),
            FakeChar("n", x0=18, x1=22),
            FakeChar("d", x0=22, x1=26),
        ]
        self.assertEqual(
            _split_trailing_formula_prose_word(formula_only),
            (formula_only, "", []),
        )

        unsafe_cases = []
        for mutation in ("distant", "rotated", "small"):
            chars = [
                FakeChar("x", x0=0, x1=4, fontname="CMMI10"),
                FakeChar(")", x0=4, x1=6, fontname="CMR10"),
                FakeChar("i", x0=8, x1=11),
                FakeChar("n", x0=11, x1=14),
            ]
            if mutation == "distant":
                chars[2].x0, chars[2].x1 = 20, 23
                chars[3].x0, chars[3].x1 = 23, 26
            elif mutation == "rotated":
                chars[2].matrix = (0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
            else:
                chars[2].size = chars[3].size = 6.0
            unsafe_cases.append((mutation, chars))
        for mutation, chars in unsafe_cases:
            with self.subTest(mutation=mutation):
                self.assertEqual(
                    _split_trailing_formula_prose_word(
                        chars,
                        has_prose_context=True,
                    ),
                    (chars, "", []),
                )

        logical_class_zero = [
            FakeChar("x", x0=0, x1=4, fontname="CMMI10"),
            FakeChar(")", x0=4, x1=6, fontname="CMR10"),
            FakeChar("i", x0=8, x1=11),
            FakeChar("n", x0=11, x1=14),
        ]
        self.assertEqual(
            _split_trailing_formula_prose_word(
                logical_class_zero,
                has_prose_context=True,
                paragraph_layout_class=0,
            ),
            (logical_class_zero, "", []),
        )

        math_word = [
            FakeChar("x", fontname="CMMI10"),
            *[FakeChar(letter, fontname="CMMI10") for letter in "and"],
        ]
        formula, prose, prose_chars = _split_trailing_formula_prose_word(math_word)
        self.assertEqual(formula, math_word)
        self.assertEqual((prose, prose_chars), ("", []))

        mixed_font_sin = [
            FakeChar("s", fontname="CMMI10"),
            FakeChar("i", fontname="Times-Roman"),
            FakeChar("n", fontname="Times-Roman"),
        ]
        formula, prose, prose_chars = _split_trailing_formula_prose_word(mixed_font_sin)
        self.assertEqual(formula, mixed_font_sin)
        self.assertEqual((prose, prose_chars), ("", []))

    def test_closed_quoted_clause_is_split_after_italic_word(self) -> None:
        chars = []
        x = 0.0
        for value in "sounds":
            chars.append(FakeChar(value, x0=x, x1=x + 4, fontname="Times-Italic"))
            x += 4
        for value, gap in zip(
            ".“Beam”refers",
            [0, 2, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0],
            strict=True,
        ):
            x += gap
            chars.append(FakeChar(value, x0=x, x1=x + 4, fontname="Times-Roman"))
            x += 4

        formula, prose, prose_chars = _split_trailing_formula_prose_clause(chars)

        self.assertEqual("".join(ch.get_text() for ch in formula), "sounds")
        self.assertEqual(prose, ". “Beam” refers")
        self.assertEqual(
            "".join(ch.get_text() for ch in prose_chars),
            ".“Beam”refers",
        )

        protected = list(chars)
        for char in protected:
            char._pdf2zh_layout_class = 0
        self.assertEqual(
            _split_trailing_formula_prose_clause(protected),
            (protected, "", []),
        )

    def test_closed_quoted_clause_rejects_unsafe_geometry_and_shape(self) -> None:
        def make_clause(
            suffix: str = ".“Beam”refers",
        ) -> list[FakeChar]:
            result: list[FakeChar] = []
            x = 0.0
            for value in "sounds" + suffix:
                fontname = (
                    "Times-Italic" if len(result) < len("sounds") else "Times-Roman"
                )
                result.append(FakeChar(value, x0=x, x1=x + 4, fontname=fontname))
                x += 4
            return result

        unsafe = {
            "rotated": make_clause(),
            "mixed_size": make_clause(),
            "zero_size": make_clause(),
            "unbalanced_quote": make_clause(".“Beam refers"),
            "unknown_verb": make_clause(".“Beam”changes"),
        }
        unsafe["rotated"][-1].matrix = (0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
        unsafe["mixed_size"][-1].size = 6.0
        unsafe["zero_size"][-1].size = 0.0
        for name, chars in unsafe.items():
            with self.subTest(name=name):
                self.assertEqual(
                    _split_trailing_formula_prose_clause(chars),
                    (chars, "", []),
                )

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
        chars = [
            [FakeChar(";", x0=118, x1=121, y0=50), FakeChar("q", x0=122, x1=126, y0=50)]
        ]
        original = list(segments)
        _merge_overlapping_split_math_islands(segments, formulas, [0, 0, 0], chars)
        self.assertEqual(segments, original)


class FormulaProseBoundarySplitTests(unittest.TestCase):
    @staticmethod
    def _paragraph(*, layout_class: int = 4) -> Paragraph:
        return Paragraph(
            100.0,
            0.0,
            0.0,
            300.0,
            90.0,
            110.0,
            9.0,
            False,
            layout_class=layout_class,
            region_kind="plain text",
        )

    @staticmethod
    def _italic_phrase(text: str, *, x0: float = 0.0) -> list[FakeChar]:
        result: list[FakeChar] = []
        x = x0
        for value in text:
            if value == " ":
                x += 2.0
                continue
            result.append(
                FakeChar(
                    value,
                    x0=x,
                    x1=x + 4.0,
                    fontname="Times-Italic",
                    layout_class=4,
                )
            )
            x += 4.0
        return result

    def test_italic_descriptor_and_terminal_math_variable_are_split(self) -> None:
        prefix = self._italic_phrase("of possible SE classes")
        comma_x = prefix[-1].x1
        comma = FakeChar(
            ",",
            x0=comma_x,
            x1=comma_x + 2.0,
            fontname="Times-Roman",
            layout_class=4,
        )
        variable = FakeChar(
            "N",
            x0=comma.x1 + 2.0,
            x1=comma.x1 + 8.0,
            fontname="CMMI10",
            layout_class=4,
        )
        segments = ["The first system uses {v0} [25] in experiments."]
        formulas = [[*prefix, comma, variable]]
        lines = [[]]
        offsets = [0.0]
        formula_paragraphs = [0]
        paragraphs = [self._paragraph()]

        _split_formula_prose_boundaries(
            segments,
            formulas,
            lines,
            offsets,
            formula_paragraphs,
            paragraphs,
        )

        self.assertEqual(
            segments,
            ["The first system uses {v0}, {v1} [25] in experiments."],
        )
        self.assertEqual(
            "".join(ch.get_text() for ch in formulas[0]),
            "ofpossibleSEclasses",
        )
        self.assertEqual("".join(ch.get_text() for ch in formulas[1]), "N")
        self.assertEqual(
            (
                len(lines),
                len(offsets),
                len(formula_paragraphs),
            ),
            (len(formulas),) * 3,
        )
        self.assertEqual(segments[0].count("{v0}"), 1)
        self.assertEqual(segments[0].count("{v1}"), 1)

    def test_math_variable_and_quoted_italic_descriptor_are_split(self) -> None:
        variable = FakeChar("N", x0=0.0, x1=6.0, fontname="CMMI10", layout_class=4)
        suffix = self._italic_phrase("“seen” SE classes", x0=9.0)
        segments = ["limited to the {v0} encountered during training"]
        formulas = [[variable, *suffix]]
        lines = [[]]
        offsets = [0.0]
        formula_paragraphs = [0]
        paragraphs = [self._paragraph()]

        _split_formula_prose_boundaries(
            segments,
            formulas,
            lines,
            offsets,
            formula_paragraphs,
            paragraphs,
        )

        self.assertEqual(
            segments,
            ["limited to the {v0} {v1} encountered during training"],
        )
        self.assertEqual("".join(ch.get_text() for ch in formulas[0]), "N")
        self.assertEqual(
            "".join(ch.get_text() for ch in formulas[1]),
            "“seen”SEclasses",
        )
        self.assertEqual(
            (
                len(lines),
                len(offsets),
                len(formula_paragraphs),
            ),
            (len(formulas),) * 3,
        )
        self.assertEqual(segments[0].count("{v0}"), 1)
        self.assertEqual(segments[0].count("{v1}"), 1)

    def test_italic_word_and_superscript_footnote_are_split_and_styled(self) -> None:
        word = self._italic_phrase("samples")
        footnote = FakeChar(
            "2",
            x0=word[-1].x1 + 0.2,
            x1=word[-1].x1 + 3.4,
            y0=-1.2,
            y1=5.1,
            size=6.3,
            fontname="Times-Roman",
            layout_class=4,
        )
        segments = ["derived from enrollment audio {v0}. With more data"]
        formulas = [[*word, footnote]]
        lines = [[]]
        offsets = [0.0]
        formula_paragraphs = [0]
        paragraphs = [self._paragraph()]

        _split_formula_prose_boundaries(
            segments,
            formulas,
            lines,
            offsets,
            formula_paragraphs,
            paragraphs,
        )

        self.assertEqual(
            segments,
            ["derived from enrollment audio {v0}{v1}. With more data"],
        )
        self.assertEqual("".join(ch.get_text() for ch in formulas[0]), "samples")
        self.assertEqual("".join(ch.get_text() for ch in formulas[1]), "2")
        self.assertAlmostEqual(offsets[1], -1.2)
        self.assertEqual(
            _collect_translatable_italic_runs(
                formulas,
                formula_paragraphs,
                paragraphs,
                segments,
            ),
            {0: "samples"},
        )
        self.assertEqual(
            (len(lines), len(offsets), len(formula_paragraphs)),
            (len(formulas),) * 3,
        )

    def test_superscript_footnote_split_rejects_unsafe_or_mathematical_shapes(
        self,
    ) -> None:
        def make_formula(kind: str) -> list[FakeChar]:
            word_text = "cm" if kind == "short_word" else "samples"
            word = self._italic_phrase(word_text)
            footnote = FakeChar(
                "2",
                x0=word[-1].x1 + 0.2,
                x1=word[-1].x1 + 3.4,
                y0=-1.2,
                y1=5.1,
                size=6.3,
                fontname="Times-Roman",
                layout_class=4,
            )
            if kind == "baseline":
                footnote.y0, footnote.y1 = 0.0, 9.0
            elif kind == "distant":
                footnote.x0 += 10.0
                footnote.x1 += 10.0
            elif kind == "full_size":
                footnote.size = 9.0
            elif kind == "rotated":
                footnote.matrix = (0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
            elif kind == "class_zero":
                footnote._pdf2zh_layout_class = 0
            return [*word, footnote]

        for kind in (
            "short_word",
            "baseline",
            "distant",
            "full_size",
            "rotated",
            "class_zero",
        ):
            with self.subTest(kind=kind):
                formula = make_formula(kind)
                segments = ["derived from {v0}. With more data"]
                formulas = [formula]
                lines = [[]]
                offsets = [0.0]
                formula_paragraphs = [0]
                _split_formula_prose_boundaries(
                    segments,
                    formulas,
                    lines,
                    offsets,
                    formula_paragraphs,
                    [self._paragraph()],
                )
                self.assertEqual(segments, ["derived from {v0}. With more data"])
                self.assertEqual(formulas, [formula])
                self.assertEqual(
                    (len(lines), len(offsets), len(formula_paragraphs)),
                    (1, 1, 1),
                )

    def test_math_variable_dimensional_suffix_is_released_to_prose(self) -> None:
        variable = FakeChar("D", x0=0.0, x1=6.0, fontname="CMMI10", layout_class=4)
        suffix = []
        x = 6.2
        for value in "-dimensional":
            suffix.append(
                FakeChar(
                    value,
                    x0=x,
                    x1=x + 4.0,
                    fontname="Times-Roman",
                    layout_class=4,
                )
            )
            x += 4.0
        segments = ["uses a {v0} target embedding vector"]
        formulas = [[variable, *suffix]]

        _split_formula_prose_boundaries(
            segments,
            formulas,
            [[]],
            [0.0],
            [0],
            [self._paragraph()],
        )

        self.assertEqual(segments, ["uses a {v0}-dimensional target embedding vector"])
        self.assertEqual("".join(ch.get_text() for ch in formulas[0]), "D")

    def test_dimensional_suffix_split_rejects_unsafe_or_unlisted_shapes(self) -> None:
        for variable_text, suffix_text, context, mutation in (
            ("D", "-dimensional", "uses {v0} target embedding", "missing_article"),
            ("D", "-dimensional", "uses a {v0} target embedding", "ordinary_base"),
            ("D", "-dimensional", "uses a {v0} target embedding", "distant"),
            ("D", "-dimensional", "uses a {v0} target embedding", "rotated"),
            ("D", "-dimensional", "uses a {v0} target embedding", "class_zero"),
            ("N", "-body", "uses a {v0} target embedding", "unlisted_suffix"),
            ("x", "-axis", "uses a {v0} target embedding", "unlisted_suffix"),
        ):
            with self.subTest(mutation=mutation, suffix=suffix_text):
                variable = FakeChar(
                    variable_text,
                    x0=0.0,
                    x1=6.0,
                    fontname="CMMI10",
                    layout_class=4,
                )
                suffix = []
                x = 6.2
                for value in suffix_text:
                    suffix.append(
                        FakeChar(
                            value,
                            x0=x,
                            x1=x + 4.0,
                            fontname="Times-Roman",
                            layout_class=4,
                        )
                    )
                    x += 4.0
                if mutation == "ordinary_base":
                    variable.fontname = "Times-Italic"
                elif mutation == "distant":
                    suffix[0].x0 += 10.0
                    suffix[0].x1 += 10.0
                elif mutation == "rotated":
                    suffix[0].matrix = (0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
                elif mutation == "class_zero":
                    suffix[0]._pdf2zh_layout_class = 0
                formula = [variable, *suffix]
                segments = [context]
                formulas = [formula]
                _split_formula_prose_boundaries(
                    segments,
                    formulas,
                    [[]],
                    [0.0],
                    [0],
                    [self._paragraph()],
                )
                self.assertEqual(segments, [context])
                self.assertEqual(formulas, [formula])

    def test_math_variable_ordinal_suffix_is_released_to_prose(self) -> None:
        for variable_text, noun in (("n", "SE class"), ("t", "frame")):
            with self.subTest(variable=variable_text):
                variable = FakeChar(
                    variable_text,
                    x0=0.0,
                    x1=5.0,
                    fontname="CMMI10",
                    layout_class=4,
                )
                suffix = [
                    FakeChar(
                        value,
                        x0=5.1 + index * 4.0,
                        x1=9.1 + index * 4.0,
                        fontname="Times-Roman",
                        layout_class=4,
                    )
                    for index, value in enumerate("-th")
                ]
                segments = [f"source signal from the {{v0}} {noun}"]
                formulas = [[variable, *suffix]]

                _split_formula_prose_boundaries(
                    segments,
                    formulas,
                    [[]],
                    [0.0],
                    [0],
                    [self._paragraph()],
                )

                self.assertEqual(
                    segments,
                    [f"source signal from the {{v0}}-th {noun}"],
                )
                self.assertEqual(
                    "".join(ch.get_text() for ch in formulas[0]),
                    variable_text,
                )

    def test_ordinal_suffix_split_rejects_unsafe_or_nonprose_shapes(self) -> None:
        cases = (
            ("n", "-th", "compare {v0} values", "missing_ordinal_context"),
            ("n", "-axis", "compare the {v0} direction", "unlisted_suffix"),
            ("n", "-th", "compare the {v0} value", "ordinary_base"),
            ("n", "-th", "compare the {v0} value", "distant"),
            ("n", "-th", "compare the {v0} value", "rotated"),
            ("n", "-th", "compare the {v0} value", "class_zero"),
        )
        for variable_text, suffix_text, context, mutation in cases:
            with self.subTest(mutation=mutation):
                variable = FakeChar(
                    variable_text,
                    x0=0.0,
                    x1=5.0,
                    fontname="CMMI10",
                    layout_class=4,
                )
                suffix = [
                    FakeChar(
                        value,
                        x0=5.1 + index * 4.0,
                        x1=9.1 + index * 4.0,
                        fontname="Times-Roman",
                        layout_class=4,
                    )
                    for index, value in enumerate(suffix_text)
                ]
                if mutation == "ordinary_base":
                    variable.fontname = "Times-Roman"
                elif mutation == "distant":
                    suffix[0].x0 += 10.0
                    suffix[0].x1 += 10.0
                elif mutation == "rotated":
                    suffix[0].matrix = (0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
                elif mutation == "class_zero":
                    suffix[0]._pdf2zh_layout_class = 0
                formula = [variable, *suffix]
                segments = [context]
                formulas = [formula]
                _split_formula_prose_boundaries(
                    segments,
                    formulas,
                    [[]],
                    [0.0],
                    [0],
                    [self._paragraph()],
                )
                self.assertEqual(segments, [context])
                self.assertEqual(formulas, [formula])

    def test_terminal_variable_split_rejects_unsafe_math_geometry(self) -> None:
        def make_formula(kind: str) -> tuple[list[FakeChar], list[list[object]]]:
            prefix = self._italic_phrase("of possible SE classes")
            comma_x = prefix[-1].x1
            comma = FakeChar(
                ",",
                x0=comma_x,
                x1=comma_x + 2.0,
                fontname="Times-Roman",
                layout_class=4,
            )
            variable = FakeChar(
                "N",
                x0=comma.x1 + 2.0,
                x1=comma.x1 + 8.0,
                fontname="CMMI10",
                layout_class=4,
            )
            lines: list[list[object]] = [[]]
            if kind == "distant":
                variable.x0 += 10.0
                variable.x1 += 10.0
            elif kind == "superscript":
                variable.y0 += 3.0
                variable.y1 += 3.0
            elif kind == "small":
                variable.size = 6.0
            elif kind == "rotated":
                variable.matrix = (0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
            elif kind == "line_formula":
                lines = [[object()]]
            elif kind == "subscript":
                return [*prefix, comma, variable, FakeChar("i")], lines
            return [*prefix, comma, variable], lines

        for kind in (
            "distant",
            "superscript",
            "small",
            "rotated",
            "line_formula",
            "subscript",
        ):
            with self.subTest(kind=kind):
                formula, lines = make_formula(kind)
                segments = ["ordinary prose {v0} remains"]
                formulas = [formula]
                _split_formula_prose_boundaries(
                    segments,
                    formulas,
                    lines,
                    [0.0],
                    [0],
                    [self._paragraph()],
                )
                self.assertEqual(segments, ["ordinary prose {v0} remains"])
                self.assertEqual(len(formulas), 1)

    def test_quoted_suffix_requires_a_natural_language_tail(self) -> None:
        for suffix_text in ("“seen” X", "“seen” SE"):
            with self.subTest(suffix=suffix_text):
                variable = FakeChar(
                    "N",
                    x0=0.0,
                    x1=6.0,
                    fontname="CMMI10",
                    layout_class=4,
                )
                suffix = self._italic_phrase(suffix_text, x0=9.0)
                segments = ["ordinary prose {v0} remains"]
                formulas = [[variable, *suffix]]
                _split_formula_prose_boundaries(
                    segments,
                    formulas,
                    [[]],
                    [0.0],
                    [0],
                    [self._paragraph()],
                )
                self.assertEqual(segments, ["ordinary prose {v0} remains"])
                self.assertEqual(len(formulas), 1)

    def test_boundary_split_rejects_class_zero_short_and_nonmath_tails(self) -> None:
        cases = [
            (
                [
                    *self._italic_phrase("signal energy"),
                    FakeChar(",", fontname="Times-Roman", layout_class=4),
                    FakeChar("N", fontname="CMMI10", layout_class=4),
                ],
                self._paragraph(),
            ),
            (
                [
                    *self._italic_phrase("possible output signal"),
                    FakeChar(",", fontname="Times-Roman", layout_class=4),
                    FakeChar("N", fontname="Times-Italic", layout_class=4),
                ],
                self._paragraph(),
            ),
            (
                [
                    *self._italic_phrase("possible output signal"),
                    FakeChar(",", fontname="Times-Roman", layout_class=0),
                    FakeChar("N", fontname="CMMI10", layout_class=0),
                ],
                self._paragraph(),
            ),
            (
                [
                    *self._italic_phrase("possible output signal"),
                    FakeChar(",", fontname="Times-Roman", layout_class=4),
                    FakeChar("N", fontname="CMMI10", layout_class=4),
                ],
                self._paragraph(layout_class=0),
            ),
        ]
        for formula, paragraph in cases:
            with self.subTest(source="".join(ch.get_text() for ch in formula)):
                segments = ["ordinary prose {v0} remains"]
                formulas = [formula]
                _split_formula_prose_boundaries(
                    segments, formulas, [[]], [0.0], [0], [paragraph]
                )
                self.assertEqual(segments, ["ordinary prose {v0} remains"])
                self.assertEqual(len(formulas), 1)


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

    def test_subscript_and_differential_bases_are_absorbed_in_formula_island(
        self,
    ) -> None:
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

    def test_formula_script_bridge_rejects_baseline_prose_and_region_change(
        self,
    ) -> None:
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
            FakeChar(
                letter,
                x0=10.0 + 4.0 * index,
                x1=14.0 + 4.0 * index,
                y0=100.0,
                y1=109.0,
                size=9.0,
            )
            for index, letter in enumerate("value")
        ]
        base = FakeChar(
            "φ",
            x0=34.0,
            x1=40.0,
            y0=100.0,
            y1=109.0,
            size=9.0,
            fontname="AdvOT65f8a23b.I+03",
        )
        subscript_m = FakeChar(
            "m",
            x0=39.9,
            x1=44.0,
            y0=97.0,
            y1=103.0,
            size=6.0,
            fontname="AdvOT65f8a23b.I",
        )
        subscript_j = FakeChar(
            "j",
            x0=44.0,
            x1=47.0,
            y0=97.0,
            y1=103.0,
            size=6.0,
            fontname="AdvOT65f8a23b.I",
        )
        period = FakeChar(
            ".",
            x0=47.1,
            x1=49.0,
            y0=100.0,
            y1=109.0,
            size=9.0,
        )
        converter = TranslateConverter.__new__(TranslateConverter)
        converter.layout = {1: FakeLayout()}
        converter.layout_region_types = {1: {4: "plain text", 13: "plain text"}}
        converter.vfont = ""
        converter.vchar = ""
        converter.translator = type("Translator", (), {"name": "google"})()

        with (
            patch("pdf2zh.converter.LTChar", FakeChar),
            patch(
                "pdf2zh.converter._split_formula_prose_boundaries"
            ) as split_boundaries,
        ):
            draft = converter.receive_layout(
                FakePage([*prose, base, subscript_m, subscript_j, period]),
                preview_only=True,
            )

        self.assertEqual(draft.sstk, ["value {v0}."])
        self.assertEqual(draft.formula_texts, ["φmj"])
        self.assertEqual(draft.varp, [0])
        split_boundaries.assert_not_called()

    def test_receive_layout_keeps_runin_heading_and_body_in_one_segment(
        self,
    ) -> None:
        class FakePage(list):
            pageid = 1
            width = 300.0
            height = 200.0

        class FakeLayout:
            shape = (200, 300)

            def __getitem__(self, position: tuple[int, int]) -> int:
                return 4

        values: list[FakeChar] = []
        x = 10.0
        for value in "Extraction of classes:":
            if value.isspace():
                x += 3.0
                continue
            values.append(
                FakeChar(
                    value,
                    x0=x,
                    x1=x + 4.0,
                    y0=100.0,
                    y1=109.0,
                    size=9.0,
                    fontname="Times-Italic",
                )
            )
            x += 4.0
        x += 2.0
        for value in "The":
            values.append(
                FakeChar(
                    value,
                    x0=x,
                    x1=x + 4.0,
                    y0=100.0,
                    y1=109.0,
                    size=9.0,
                    fontname="Times-Roman",
                )
            )
            x += 4.0
        x += 5.0
        for value in "number":
            values.append(
                FakeChar(
                    value,
                    x0=x,
                    x1=x + 4.0,
                    y0=100.0,
                    y1=109.0,
                    size=9.0,
                    fontname="Times-Roman",
                )
            )
            x += 4.0

        converter = TranslateConverter.__new__(TranslateConverter)
        converter.layout = {1: FakeLayout()}
        converter.layout_region_types = {1: {4: "plain text"}}
        converter.vfont = ""
        converter.vchar = ""
        converter.translator = type("Translator", (), {"name": "google"})()

        with patch("pdf2zh.converter.LTChar", FakeChar):
            draft = converter.receive_layout(
                FakePage(values),
                preview_only=True,
            )

        self.assertEqual(draft.sstk, ["{v0} The number"])
        self.assertEqual(draft.formula_texts, ["Extractionofclasses:"])
        self.assertEqual(draft.varp, [0])

    def test_receive_layout_detaches_sentence_period_when_formula_closes_at_class_boundary(
        self,
    ) -> None:
        class FakePage(list):
            pageid = 1
            width = 300.0
            height = 200.0

        class FakeLayout:
            shape = (200, 300)

            def __getitem__(self, position: tuple[int, int]) -> int:
                _y, x = position
                return 5 if x >= 90 else 4

        prose = [
            FakeChar(
                letter,
                x0=10.0 + 4.0 * index,
                x1=14.0 + 4.0 * index,
                y0=100.0,
                y1=109.0,
                size=9.0,
            )
            for index, letter in enumerate("where")
        ]
        variable = FakeChar(
            "x",
            x0=32.0,
            x1=37.0,
            y0=100.0,
            y1=109.0,
            size=9.0,
            fontname="CMMI10",
        )
        period = FakeChar(
            ".",
            x0=37.1,
            x1=39.0,
            y0=100.0,
            y1=109.0,
            size=9.0,
            fontname="Times-Roman",
        )
        next_paragraph = [
            FakeChar(
                letter,
                x0=100.0 + 4.0 * index,
                x1=104.0 + 4.0 * index,
                y0=80.0,
                y1=89.0,
                size=9.0,
            )
            for index, letter in enumerate("Next")
        ]
        converter = TranslateConverter.__new__(TranslateConverter)
        converter.layout = {1: FakeLayout()}
        converter.layout_region_types = {1: {4: "plain text", 5: "plain text"}}
        converter.vfont = ""
        converter.vchar = ""
        converter.translator = type("Translator", (), {"name": "google"})()

        with patch("pdf2zh.converter.LTChar", FakeChar):
            draft = converter.receive_layout(
                FakePage([*prose, variable, period, *next_paragraph]),
                preview_only=True,
            )

        self.assertEqual(draft.sstk, ["where {v0}.", "Next"])
        self.assertEqual(draft.formula_texts, ["x"])
        self.assertEqual(draft.varp, [0])

    def test_receive_layout_keeps_period_in_formula_only_run_at_class_boundary(
        self,
    ) -> None:
        class FakePage(list):
            pageid = 1
            width = 300.0
            height = 200.0

        class FakeLayout:
            shape = (200, 300)

            def __getitem__(self, position: tuple[int, int]) -> int:
                _y, x = position
                return 5 if x >= 90 else 4

        formula = [
            FakeChar(
                "x",
                x0=10.0,
                x1=15.0,
                y0=100.0,
                y1=109.0,
                size=9.0,
                fontname="CMMI10",
            ),
            FakeChar(
                ".",
                x0=15.1,
                x1=17.0,
                y0=100.0,
                y1=109.0,
                size=9.0,
                fontname="Times-Roman",
            ),
        ]
        next_paragraph = FakeChar(
            "N",
            x0=100.0,
            x1=105.0,
            y0=80.0,
            y1=89.0,
            size=9.0,
        )
        converter = TranslateConverter.__new__(TranslateConverter)
        converter.layout = {1: FakeLayout()}
        converter.layout_region_types = {1: {4: "plain text", 5: "plain text"}}
        converter.vfont = ""
        converter.vchar = ""
        converter.translator = type("Translator", (), {"name": "google"})()

        with patch("pdf2zh.converter.LTChar", FakeChar):
            draft = converter.receive_layout(
                FakePage([*formula, next_paragraph]),
                preview_only=True,
            )

        self.assertEqual(draft.sstk, ["{v0}", "N"])
        self.assertEqual(draft.formula_texts, ["x."])

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

        for (
            text,
            previous_class,
            candidate_class,
            previous_kind,
            candidate_kind,
        ) in cases:
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
                formula, suffix = _split_trailing_prose_punctuation(chars(source))
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
        self.assertFalse(_is_hidden_glyph_repertoire_probe(probe[:3], 600.0))
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
