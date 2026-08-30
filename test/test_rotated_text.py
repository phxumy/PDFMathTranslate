from __future__ import annotations

import re
import unittest

import numpy as np
from pdfminer.layout import LTPage
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager

from pdf2zh.converter import (
    PDFConverterEx,
    TranslateConverter,
    _is_non_horizontal_text_matrix,
)


class FakeFont:
    fontname = "FakeFont"

    @staticmethod
    def to_unichr(cid: int) -> str:
        return chr(cid)

    @staticmethod
    def char_width(cid: int) -> float:
        return 0.5

    @staticmethod
    def char_disp(cid: int) -> int:
        return 0

    @staticmethod
    def is_vertical() -> bool:
        return False

    @staticmethod
    def get_descent() -> float:
        return 0.0


class FakeTranslator:
    lang_out = "zh-cn"


def render_single_protected_char(
    matrix: tuple[float, float, float, float, float, float],
    fontsize: float,
) -> str:
    font = FakeFont()
    converter = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(converter, PDFResourceManager())
    page = LTPage(0, (0.0, 0.0, 200.0, 200.0))
    converter.cur_item = page
    converter.render_char(
        matrix,
        font,
        fontsize,
        1.0,
        0.0,
        ord("T"),
        None,
        PDFGraphicState(),
    )
    converter.layout = {0: np.zeros((200, 200))}
    converter.translator = FakeTranslator()
    converter.vfont = ""
    converter.vchar = ""
    converter.noto_name = "noto"
    converter.noto = None
    converter.fontid = {font: "F1"}
    converter.fontmap = {"F1": font}
    converter._translate_planned_segments = (
        lambda segments, paragraphs, formula_texts, page_width, **kwargs: list(segments)
    )
    return converter.receive_layout(page)


class RotatedTextTests(unittest.TestCase):
    def test_protected_rotated_text_keeps_source_font_state_and_matrix(self) -> None:
        ops = render_single_protected_char(
            (0.0, 9.0, -9.0, 0.0, 50.0, 80.0),
            fontsize=1.0,
        )

        match = re.search(
            r"/F1\s+([-\d.]+)\s+Tf\s+"
            r"([-\d.]+)\s+Tz\s+([-\d.]+)\s+Ts\s+"
            r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+"
            r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm",
            ops,
        )

        self.assertIsNotNone(match)
        values = [float(value) for value in match.groups()]
        self.assertEqual(values[:3], [1.0, 100.0, 0.0])
        self.assertEqual(values[3:], [0.0, 9.0, -9.0, 0.0, 50.0, 80.0])
        self.assertIn("TJ 100 Tz 0 Ts", ops)

    def test_horizontal_protected_text_uses_existing_layout_path(self) -> None:
        ops = render_single_protected_char(
            (1.0, 0.0, 0.0, 1.0, 50.0, 80.0),
            fontsize=9.0,
        )

        self.assertIn("/F1 9.000000 Tf 1 0 0 1 50.000000 80.000000 Tm", ops)
        self.assertNotIn(" Tz ", ops)

    def test_other_rotation_angles_keep_the_complete_source_matrix(self) -> None:
        matrices = [
            (0.0, -9.0, 9.0, 0.0, 80.0, 120.0),
            (8.0, 4.0, -4.0, 8.0, 70.0, 90.0),
        ]

        for matrix in matrices:
            with self.subTest(matrix=matrix):
                ops = render_single_protected_char(matrix, fontsize=1.0)
                serialized_matrix = " ".join(
                    f"{value:f}" for value in matrix
                )
                self.assertIn(f"{serialized_matrix} Tm", ops)
                self.assertIn("/F1 1.000000 Tf", ops)

    def test_non_horizontal_detection_uses_a_relative_tolerance(self) -> None:
        self.assertTrue(
            _is_non_horizontal_text_matrix(
                (1e-10, 9.0, -9.0, -1e-10, 0.0, 0.0)
            )
        )
        self.assertTrue(
            _is_non_horizontal_text_matrix(
                (8.0, 4.0, -4.0, 8.0, 0.0, 0.0)
            )
        )
        self.assertFalse(
            _is_non_horizontal_text_matrix(
                (9.0, 1e-10, 0.0, 9.0, 0.0, 0.0)
            )
        )


if __name__ == "__main__":
    unittest.main()
