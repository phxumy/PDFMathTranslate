from __future__ import annotations

import re
import unittest

import numpy as np
from pdfminer.layout import LTPage
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager

from pdf2zh.code_blocks import code_block_chars
from pdf2zh.converter import PDFConverterEx, TranslateConverter
from pdf2zh.translation_policy import DocumentTranslationPolicy


class Font:
    def __init__(self, name="Consolas"):
        self.fontname = name

    def to_unichr(self, cid):
        return chr(cid)

    def char_width(self, cid):
        return 0.5

    def char_disp(self, cid):
        return 0

    def is_vertical(self):
        return False

    def get_descent(self):
        return 0.0


def page_with_lines(lines, fontname="Consolas"):
    """Build actual LTChars, including the source state used by PDF rendering."""
    converter = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(converter, PDFResourceManager())
    page = LTPage(0, (0.0, 0.0, 600.0, 800.0))
    converter.cur_item = page
    font = Font(fontname)
    for index, text in enumerate(lines):
        for position, character in enumerate(text):
            converter.render_char(
                (1.0, 0.0, 0.0, 1.0, 50 + position * 4.5, 740 - index * 16),
                font,
                9.0,
                1.0,
                0.0,
                ord(character),
                None,
                PDFGraphicState(),
            )
    converter.layout = {0: np.ones((800, 600))}
    converter.translator = type("Translator", (), {"lang_out": "zh-cn"})()
    converter.vfont = ""
    converter.vchar = ""
    converter.noto_name = "noto"
    converter.noto = None
    converter.fontid = {font: "F1"}
    converter.fontmap = {"F1": font, "tiro": font}
    converter.thread = 4
    converter.translation_policy = DocumentTranslationPolicy()
    return converter, page


def selected_text(page):
    selected = code_block_chars(page)
    return "".join(char.get_text() for char in page if char in selected)


class CodeBlockTests(unittest.TestCase):
    def test_google_and_codex_never_receive_code_but_do_translate_prose(self):
        class Google:
            name = "google"
            lang_out = "zh-cn"

            def __init__(self):
                self.calls = []

            def translate(self, text):
                self.calls.append(text)
                return text.replace("Overview", "Introduction").replace(
                    "Discussion", "Conclusion"
                )

        class Codex(Google):
            name = "codex"

            def translate_batch(self, texts):
                return [super(Codex, self).translate(text) for text in texts]

            def translate(self, text):
                raise AssertionError("Codex must use its batch interface")

        for translator in (Google(), Codex()):
            with self.subTest(engine=translator.name):
                converter, page = page_with_lines(
                    [
                        "Overview of the extraction procedure.",
                        "# Retain this comment as well as variable names.",
                        "for idx in range(num_drop):",
                        "    data = pd.read_csv(csv_path)",
                        "    R = data.iloc[:, 2:]",
                        "Discussion of the extraction results.",
                    ]
                )
                converter.translator = translator
                ops = converter.receive_layout(page)
                self.assertEqual(
                    sorted(translator.calls),
                    sorted(
                        [
                            "Overview of the extraction procedure.",
                            "Discussion of the extraction results.",
                        ]
                    ),
                )
                self.assertIn("Introduction".encode().hex(), ops)
                self.assertIn("Conclusion".encode().hex(), ops)

    def test_protects_listing_with_wrapped_comments_and_indentation(self):
        lines = [
            "Overview of the extraction procedure.",
            "#### input ####",
            "# csv_path: Data path to the raw input CSV file.",
            "The columns contain the reflectance spectra.",
            "for idx in range(num_drop):",
            "    data = pd.read_csv(csv_path)",
            "    R = data.iloc[:, 2:]  # reflectance array",
            "    bandgaps = []",
            "Discussion of the extraction results.",
        ]
        _, page = page_with_lines(lines)
        self.assertEqual(selected_text(page), "".join(lines[1:-1]))

    def test_code_continuation_page_needs_no_heading(self):
        lines = [
            "    break",
            "upper = k",
            "for i in range(0, k):",
            "    Delta.append(d)",
            "Delta = np.array(Delta, dtype=float)",
        ]
        _, page = page_with_lines(lines)
        self.assertEqual(selected_text(page), "".join(lines))

    def test_font_name_and_equations_alone_do_not_protect_text(self):
        for lines in [
            ["x = y + z", "y = sin(x)", "z = cos(x)", "a = f(x)"],
            [
                "[1] Smith, A. A title about code.",
                "[2] Jones, B. References in monospaced fonts.",
                "[3] Brown, C. IEEE Transactions 1 (2024).",
            ],
            [
                "The code calls data.copy() before fitting.",
                "We obtain the coefficient from fit(x).",
                "Values follow the equation x = y + z.",
            ],
            ["data = pd.read_csv(path)"],
        ]:
            with self.subTest(lines=lines):
                _, page = page_with_lines(lines)
                self.assertFalse(code_block_chars(page))

    def test_separated_inline_examples_are_not_one_block(self):
        _, page = page_with_lines(
            [
                "data = pd.read_csv(path)",
                "",
                "",
                "",
                "",
                "copy = data.copy()",
                "",
                "",
                "",
                "",
                "output = np.array(copy)",
            ]
        )
        self.assertFalse(code_block_chars(page))

    def test_proportional_text_remains_outside_conservative_detector(self):
        _, page = page_with_lines(
            [
                "for idx in range(num_drop):",
                "    data = pd.read_csv(csv_path)",
                "    R = data.iloc[:, 2:]",
            ],
            fontname="TimesNewRoman",
        )
        self.assertFalse(code_block_chars(page))

    def test_converter_keeps_every_code_matrix_and_translates_surrounding_text(self):
        lines = [
            "Overview of the extraction procedure.",
            "for idx in range(num_drop):",
            "    data = pd.read_csv(csv_path)",
            "    R = data.iloc[:, 2:]  # reflectance array",
            "    bandgaps = []",
            "Discussion of the extraction results.",
        ]
        converter, page = page_with_lines(lines)
        selected = code_block_chars(page)
        calls = []

        def translate(segments, paragraphs, formula_texts, **kwargs):
            calls.extend(segments)
            return [
                text.replace("Overview", "Introduction").replace(
                    "Discussion", "Conclusion"
                )
                for text in segments
            ]

        converter._translate_planned_segments = translate
        ops = converter.receive_layout(page)
        self.assertTrue(any("Overview" in text for text in calls))
        self.assertTrue(any("Discussion" in text for text in calls))
        self.assertFalse(
            any("read_csv" in text or "bandgaps" in text for text in calls)
        )
        matrices = re.findall(
            r"/F1 9\.000000 Tf 100\.000000 Tz 0\.000000 Ts "
            r"([\d. -]+) Tm \[<([0-9a-f]+)>\] TJ",
            ops,
        )
        expected = [
            (
                " ".join(
                    f"{value:f}" for value in char._pdf2zh_source_text_state.matrix
                ),
                f"{char.cid:02x}",
            )
            for char in page
            if char in selected
        ]
        self.assertEqual(matrices, expected)


if __name__ == "__main__":
    unittest.main()
