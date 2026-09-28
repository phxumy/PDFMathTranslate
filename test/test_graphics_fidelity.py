from __future__ import annotations

import io
from types import SimpleNamespace
import unittest

import numpy as np
import pymupdf as fitz
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from pdf2zh.converter import PDFConverterEx, TranslateConverter
from pdf2zh.pdfinterp import PDFPageInterpreterEx


def preserve_locally(doc):
    """Run actual extraction and rendering with every text region protected."""
    manager = PDFResourceManager()
    converter = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(converter, manager)
    converter.translator = SimpleNamespace(name="local-test", lang_out="en")
    converter.vfont = converter.vchar = ""
    converter.noto_name = "noto"
    converter.noto = fitz.Font("helv")
    converter.layout = {0: np.zeros((200, 240))}
    converter._translate_planned_segments = lambda segments, *a, **k: list(segments)
    patches = {}
    interpreter = PDFPageInterpreterEx(manager, converter, patches)
    parsed = PDFDocument(PDFParser(io.BytesIO(doc.tobytes())))
    page = next(PDFPage.create_pages(parsed))
    page.pageno = 0
    page.page_xref = doc.get_new_xref()
    doc.update_object(page.page_xref, "<<>>")
    doc.update_stream(page.page_xref, b"")
    doc[0].set_contents(page.page_xref)
    interpreter.process_page(page)
    for xref, operations in patches.items():
        doc.update_stream(xref, operations.encode())
    return patches[page.page_xref]


def text_colors(page):
    return [
        (span["text"], span["color"])
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
    ]


class GraphicsFidelityTests(unittest.TestCase):
    def test_dash_arrays_and_phase_survive_real_pdf_roundtrip(self):
        with fitz.open() as doc:
            page = doc.new_page(width=240, height=200)
            cases = [
                ((1, 0, 0), "[1.0564 1.0564] 0", (50, 30), (50, 80)),
                ((0, 0, 1), "[2.6852 2.6852] 0", (20, 100), (200, 100)),
                ((0, 0, 0), "[2.6852 2.6852] 1.25", (20, 120), (200, 120)),
                ((0, 0, 0), "[0 1.5252] 0", (20, 140), (200, 140)),
                ((1, 0, 0), "[] 0", (20, 160), (200, 160)),
            ]
            for color, dash, start, end in cases:
                page.draw_line(start, end, color=color, dashes=dash, width=1)
            before = page.get_drawings()
            before_pixels = page.get_pixmap().samples
            preserve_locally(doc)
            after = doc[0].get_drawings()
            self.assertEqual(len(after), len(before))
            for original, actual in zip(before, after):
                for field in ("dashes", "color", "rect", "width", "items"):
                    self.assertEqual(actual[field], original[field], field)
            self.assertEqual(doc[0].get_pixmap().samples, before_pixels)

    def test_protected_white_rgb_cmyk_text_keeps_color_without_leaking(self):
        with fitz.open() as doc:
            page = doc.new_page(width=240, height=200)
            page.draw_rect((10, 10, 90, 45), color=None, fill=(0, 0, 0))
            page.insert_text((20, 33), "Meas", color=(1,), fontsize=12)
            page.insert_text((110, 33), "Black", color=(0,), fontsize=12)
            page.insert_text((20, 80), "RGB", color=(0.2, 0.4, 0.8), fontsize=12)
            page.insert_text((20, 110), "CMYK", color=(0, 1, 1, 0), fontsize=12)
            page.insert_text((160, 150), "Rotated", color=(0.7,), fontsize=12, rotate=90)
            before = text_colors(page)
            preserve_locally(doc)
            self.assertEqual(text_colors(doc[0]), before)
            rgb = [(text, color & 0xFFFFFF) for text, color in before]
            self.assertIn(("Meas", 0xFFFFFF), rgb)
            self.assertIn(("Black", 0), rgb)


if __name__ == "__main__":
    unittest.main()
