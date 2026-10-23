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

from pdf2zh.converter import PDFConverterEx
from pdf2zh.high_level import _collect_closed_box_strokes
from pdf2zh.pdfinterp import PDFPageInterpreterEx


class StrokeCapture(PDFConverterEx):
    def __init__(self, manager):
        super().__init__(manager)
        self.extracted = []

    def paint_path(self, state, *args):
        self.extracted.append((state, args))

    def receive_layout(self, _):
        return ""


def extract_strokes(document, *, figure=False, layout=None):
    """Use the real interpreter; intentionally do not redraw extracted rules."""
    page = document[0]
    manager = PDFResourceManager()
    converter = StrokeCapture(manager)
    converter.layout = {
        0: (
            layout
            if layout is not None
            else np.zeros((int(page.rect.height), int(page.rect.width)))
        )
    }
    converter.layout_detections = {
        0: (
            [SimpleNamespace(class_name="figure", bounds=tuple(page.rect))]
            if figure
            else []
        )
    }
    converter.source_closed_box_strokes = {0: _collect_closed_box_strokes(page)}
    patches = {}
    interpreter = PDFPageInterpreterEx(manager, converter, patches)
    parsed = PDFDocument(PDFParser(io.BytesIO(document.tobytes())))
    source = next(PDFPage.create_pages(parsed))
    source.pageno = 0
    source.page_xref = document.get_new_xref()
    document.update_object(source.page_xref, "<<>>")
    document.update_stream(source.page_xref, b"")
    page.set_contents(source.page_xref)
    interpreter.process_page(source)
    for xref, operations in patches.items():
        document.update_stream(xref, operations.encode())
    return converter


def square(page, *, missing=False, mismatch=False):
    sides = [
        ((60, 80), (67, 80)),
        ((60, 87), (67, 87)),
        ((60, 80), (60, 87)),
        ((67, 80), (67, 87)),
    ]
    for index, (start, end) in enumerate(sides[:3] if missing else sides):
        page.draw_line(
            start,
            end,
            width=0.4,
            color=(1, 0, 0) if mismatch and index == 3 else (0,),
            stroke_opacity=0.7,
        )


class SourceStrokeFidelityTests(unittest.TestCase):
    def test_one_pixel_fraction_stencil_moves_only_inside_text_regions(self):
        for mode in ("fraction", "blank", "figure", "thick", "white"):
            with self.subTest(mode=mode), fitz.open() as doc:
                page = doc.new_page(width=240, height=200)
                page.draw_line((30, 40), (50, 40))
                image = doc.get_new_xref()
                doc.update_object(
                    image,
                    "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                    "/ImageMask true /BitsPerComponent 1 >>",
                )
                doc.update_stream(image, b"\x80" if mode == "blank" else b"\x00")
                resources = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])
                doc.xref_set_key(resources, "XObject", f"<< /Rule {image} 0 R >>")
                height = 5 if mode == "thick" else 0.5
                color = "1 g " if mode == "white" else ""
                doc.update_stream(
                    page.get_contents()[0],
                    (f"{color}q 10 0 0 -{height} 40 120 cm /Rule Do Q").encode(),
                )
                converter = extract_strokes(
                    doc, layout=np.full((200, 240), 0 if mode == "figure" else 2)
                )
                self.assertEqual(len(converter.extracted), int(mode == "fraction"))
                if mode == "fraction":
                    self.assertEqual(converter.extracted[0][0].linewidth, 0.5)

    def test_black_scn_fraction_rules_are_extracted_but_white_cmyk_is_not(self):
        for colorspace, components, expected in (
            (None, "", 1),
            ("DeviceRGB", "0 0 0", 1),
            ("DeviceGray", "0", 1),
            ("DeviceCMYK", "0 0 0 1", 1),
            ("DeviceCMYK", "0 0 0 0", 0),
            ("DeviceRGB", "0.1 0.3 0.7", 0),
        ):
            with (
                self.subTest(colorspace=colorspace, components=components),
                fitz.open() as doc,
            ):
                page = doc.new_page(width=240, height=200)
                page.draw_line((30, 40), (50, 40))
                doc.update_stream(
                    page.get_contents()[0],
                    (
                        (f"/{colorspace} CS {components} SCN " if colorspace else "")
                        + "0.5 w "
                        "30 160 m 50 160 l S"
                    ).encode(),
                )
                converter = extract_strokes(doc)
                self.assertEqual(len(converter.extracted), expected)
                if expected:
                    self.assertEqual(doc[0].get_drawings(), [])

    def test_four_separate_proof_box_sides_remain_pixel_identical(self):
        with fitz.open() as doc:
            square(doc.new_page(width=240, height=200))
            before = doc[0].get_pixmap(matrix=fitz.Matrix(5, 5)).samples
            converter = extract_strokes(doc)
            self.assertEqual(len(converter.source_closed_box_strokes[0]), 2)
            self.assertEqual(converter.extracted, [])
            self.assertEqual(
                doc[0].get_pixmap(matrix=fitz.Matrix(5, 5)).samples, before
            )

    def test_two_fraction_bars_or_incomplete_box_are_still_extracted(self):
        with fitz.open() as doc:
            square(doc.new_page(width=240, height=200), missing=True)
            converter = extract_strokes(doc)
            self.assertEqual(converter.source_closed_box_strokes[0], ())
            self.assertEqual(len(converter.extracted), 2)

    def test_box_needs_matching_style_on_all_four_sides(self):
        with fitz.open() as doc:
            square(doc.new_page(width=240, height=200), mismatch=True)
            self.assertEqual(_collect_closed_box_strokes(doc[0]), ())

    def test_rotated_and_cropped_box_uses_interpreter_coordinates(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation), fitz.open() as doc:
                square(doc.new_page(width=240, height=200))
                doc[0].set_cropbox(fitz.Rect(20, 30, 210, 185))
                doc[0].set_rotation(rotation)
                before = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4)).samples
                converter = extract_strokes(doc)
                self.assertEqual(len(converter.source_closed_box_strokes[0]), 2)
                self.assertEqual(converter.extracted, [])
                self.assertEqual(
                    doc[0].get_pixmap(matrix=fitz.Matrix(4, 4)).samples, before
                )

    def test_figure_retains_opacity_color_dashes_and_clipping(self):
        with fitz.open() as doc:
            page = doc.new_page(width=240, height=200)
            page.draw_line(
                (20, 40), (220, 40), width=0.8, color=(0,), stroke_opacity=0.1
            )
            page.draw_line((20, 70), (220, 70), width=0.8, color=(0,), dashes="[2 3] 1")
            page.draw_line((20, 100), (220, 100), width=0.8, color=(0.2, 0.5, 0.7))
            # Clipping is part of the source graphics state, not an LTLine.
            content = page.get_contents()[0]
            doc.update_stream(
                content, b"q 45 20 120 170 re W n " + doc.xref_stream(content) + b" Q"
            )
            before = page.get_pixmap(matrix=fitz.Matrix(4, 4)).samples
            before_drawings = page.get_drawings()
            converter = extract_strokes(doc, figure=True)
            self.assertEqual(converter.extracted, [])
            self.assertEqual(
                doc[0].get_pixmap(matrix=fitz.Matrix(4, 4)).samples, before
            )
            for old, new in zip(before_drawings, doc[0].get_drawings()):
                for key in (
                    "items",
                    "color",
                    "dashes",
                    "stroke_opacity",
                    "lineCap",
                    "width",
                ):
                    self.assertEqual(old[key], new[key], key)

    def test_formula_override_inside_figure_can_still_reflow(self):
        with fitz.open() as doc:
            doc.new_page(width=240, height=200).draw_line(
                (40, 70), (80, 70), color=(0,)
            )
            layout = np.zeros((200, 240))
            layout[125:136, 35:85] = 2
            converter = extract_strokes(doc, figure=True, layout=layout)
            self.assertEqual(len(converter.extracted), 1)

    def test_figure_stroke_inside_nested_form_keeps_original_graphics_state(self):
        with fitz.open() as source, fitz.open() as target:
            source.new_page(width=100, height=100).draw_line(
                (10, 40), (90, 40), width=0.4, color=(0,), stroke_opacity=0.1
            )
            target.new_page(width=240, height=200).show_pdf_page(
                fitz.Rect(20, 20, 220, 180), source, 0
            )
            before = target[0].get_pixmap(matrix=fitz.Matrix(4, 4)).samples
            converter = extract_strokes(target, figure=True)
            self.assertEqual(converter.extracted, [])
            self.assertEqual(
                target[0].get_pixmap(matrix=fitz.Matrix(4, 4)).samples, before
            )


if __name__ == "__main__":
    unittest.main()
