from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from pdf2zh.converter import PDFConverterEx, TranslateConverter
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.scanned_pdf import detect_scan_background, horizontal_fit_scale


def make_source(
    *,
    scanned=True,
    hidden=True,
    colour=False,
    omitted_reference=False,
    reference_ink=True,
):
    artwork = pymupdf.open()
    art = artwork.new_page(width=240, height=200)
    art.draw_rect(art.rect, fill=(1, 0.7, 0.7) if colour else (1, 1, 1))
    art.insert_text((20, 25), "HEADER", fontsize=10)
    art.insert_text((20, 80), "SOURCE", fontsize=12)
    if omitted_reference:
        if reference_ink:
            art.insert_text((78, 73), "184-218", fontsize=5)
        art.insert_text((110, 80), "NEXT", fontsize=12)
    else:
        art.insert_text((69, 76), "[1]", fontsize=7)
    art.draw_rect((145, 110, 215, 165), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    doc = pymupdf.open()
    page = doc.new_page(width=240, height=200)
    if scanned:
        page.insert_image(
            page.rect, stream=art.get_pixmap(matrix=pymupdf.Matrix(3, 3)).tobytes("png")
        )
    page.insert_text((20, 25), "HEADER", fontsize=10, render_mode=3 if hidden else 0)
    page.insert_text((20, 80), "SOURCE", fontsize=12, render_mode=3 if hidden else 0)
    if omitted_reference:
        page.insert_text((110, 80), "NEXT", fontsize=12, render_mode=3 if hidden else 0)
    else:
        page.insert_text((69, 76), "[1]", fontsize=7, render_mode=3 if hidden else 0)
    page.insert_font("tiro")
    return doc


def rgb(page):
    pix = page.get_pixmap()
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)


def translate_locally(doc, replacement):
    page = doc[0]
    scan = detect_scan_background(page, rgb(page))
    manager = PDFResourceManager()
    converter = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(converter, manager)
    converter.translator = SimpleNamespace(name="local-test", lang_out="en")
    converter.vfont = ""
    converter.vchar = ""
    converter.noto_name = "noto"
    converter.noto = pymupdf.Font("helv")
    mask = np.full((200, 240), 2.0)
    mask[165:] = 0
    converter.layout = {0: mask}
    converter.layout_region_types = {0: {2: "plain text"}}
    converter.scan_backgrounds = {0: scan} if scan else {}
    captured = []

    def translate(segments, paragraphs, *args, **kwargs):
        captured.extend(segments)
        return [segment.replace("SOURCE", replacement) for segment in segments]

    converter._translate_planned_segments = translate
    patches = {}
    interpreter = PDFPageInterpreterEx(manager, converter, patches)
    parser = PDFParser(io.BytesIO(doc.tobytes()))
    source_page = next(PDFPage.create_pages(PDFDocument(parser)))
    source_page.pageno = 0
    source_page.page_xref = doc.get_new_xref()
    doc.update_object(source_page.page_xref, "<<>>")
    doc.update_stream(source_page.page_xref, b"")
    page.set_contents(source_page.page_xref)
    interpreter.process_page(source_page)
    for xref, operations in patches.items():
        doc.update_stream(xref, operations.encode())
    return patches[source_page.page_xref], captured


def test_scan_text_is_replaced_but_header_and_picture_pixels_are_unchanged():
    doc = make_source()
    before = rgb(doc[0]).copy()
    operations, _ = translate_locally(doc, "A much longer translated phrase")
    after = rgb(doc[0])

    assert "q 1 g" in operations
    assert "re W n" in operations  # source reference is moved as an image clip
    assert np.array_equal(before[:35], after[:35])
    assert np.array_equal(before[105:170, 140:220], after[105:170, 140:220])
    # Translation is scaled to the original single-line width, leaving no scan
    # ink at the upper part of the old letters.
    assert after[71:75, 20:65].mean() > 250
    visible = [t for t in doc[0].get_texttrace() if t["type"] != 3]
    assert visible
    assert max(t["bbox"][2] for t in visible) <= 81


def test_unchanged_hidden_ocr_is_not_redrawn_or_masked():
    doc = make_source()
    before = rgb(doc[0]).copy()
    operations, _ = translate_locally(doc, "SOURCE")
    assert "q 1 g" not in operations
    assert np.array_equal(before, rgb(doc[0]))


@pytest.mark.parametrize(
    "kwargs", [{"scanned": False}, {"hidden": False}, {"colour": True}]
)
def test_scan_cleanup_is_not_enabled_for_other_pdf_kinds(kwargs):
    doc = make_source(**kwargs)
    assert detect_scan_background(doc[0], rgb(doc[0])) is None


def test_long_single_line_translation_gets_uniform_width_fit():
    assert (
        horizontal_fit_scale(source_left=20, source_right=120, target_right=270) == 0.4
    )
    assert (
        horizontal_fit_scale(source_left=20, source_right=120, target_right=100) == 1.0
    )


@pytest.mark.parametrize("reference_ink", [False, True])
def test_only_real_missing_superscript_ink_becomes_a_movable_formula(reference_ink):
    doc = make_source(omitted_reference=True, reference_ink=reference_ink)
    operations, captured = translate_locally(
        doc, "Translated paragraph with a source citation"
    )
    translated_source = next(text for text in captured if "SOURCE" in text)
    assert ("{v1}" in translated_source) is reference_ink
    assert ("re W n" in operations) is reference_ink
    if reference_ink:
        # The old citation is removed from the scan and replayed at its new
        # inline position, rather than disappearing or remaining underneath.
        assert rgb(doc[0])[68:74, 78:94].mean() > 250
