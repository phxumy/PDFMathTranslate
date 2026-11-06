"""Exercise real PDF extraction/rendering with deterministic local translations."""

import io
from types import SimpleNamespace

import numpy as np
import pymupdf as fitz
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from pdf2zh.converter import PDFConverterEx, TranslateConverter
from pdf2zh.pdfinterp import PDFPageInterpreterEx


def render_with_fixture_translation(document, translate):
    page = document[0]
    noto = fitz.Font("cjk")
    page.insert_font(fontname="noto", fontbuffer=noto.buffer)
    page.insert_font(fontname="tiro")
    manager = PDFResourceManager()
    converter = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(converter, manager)
    converter.translator = SimpleNamespace(name="fixture", lang_out="zh")
    converter.vfont = converter.vchar = ""
    converter.noto_name, converter.noto = "noto", noto
    converter.layout = {0: np.full((int(page.rect.height), int(page.rect.width)), 2)}
    converter.layout_region_types = {0: {2: "plain text"}}
    seen = []

    def planned(segments, paragraphs, *args, **kwargs):
        seen.extend(
            (source, paragraph.region_kind)
            for source, paragraph in zip(segments, paragraphs)
        )
        return [translate(source) for source in segments]

    converter._translate_planned_segments = planned
    patches = {}
    interpreter = PDFPageInterpreterEx(manager, converter, patches)
    parsed = PDFDocument(PDFParser(io.BytesIO(document.tobytes())))
    source_page = next(PDFPage.create_pages(parsed))
    source_page.pageno = 0
    source_page.page_xref = document.get_new_xref()
    document.update_object(source_page.page_xref, "<<>>")
    document.update_stream(source_page.page_xref, b"")
    document[0].set_contents(source_page.page_xref)
    interpreter.process_page(source_page)
    for xref, operations in patches.items():
        document.update_stream(xref, operations.encode())
    return seen


def cjk_spans(page):
    return [
        span
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if any("\u3400" <= c <= "\u9fff" for c in span["text"])
    ]


def test_chinese_prose_cannot_reflow_through_an_inline_formula_image():
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=200)
        page.insert_text((20, 80), "the value equals", fontname="tiro", fontsize=10)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 8), False)
        pixmap.clear_with(0)
        image_box = fitz.Rect(90, 70, 150, 85)
        page.insert_image(image_box, pixmap=pixmap, keep_proportion=False)
        page.insert_text(
            (155, 80), "and the reference values follow", fontname="tiro", fontsize=10
        )
        before_image = page.get_image_info(hashes=True)[0]
        seen = render_with_fixture_translation(
            doc,
            lambda text: text.replace("the value equals", "计算所得的数值等于").replace(
                "and the reference values follow", "以及如下参考数值"
            ),
        )
        assert sum(kind == "formula_prose" for _, kind in seen) == 2
        spans = cjk_spans(doc[0])
        assert len(spans) == 2
        assert spans[0]["bbox"][2] <= image_box.x0
        assert spans[1]["bbox"][0] >= image_box.x1
        after_image = doc[0].get_image_info(hashes=True)[0]
        assert after_image["bbox"] == before_image["bbox"]
        assert after_image["digest"] == before_image["digest"]


def test_definition_rows_stay_separate_and_keep_their_original_math_labels():
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=260)
        for index, suffix in enumerate(("eff", "obs", "eof")):
            y = 70 + index * 30
            page.insert_text((20, y), "m", fontname="tiit", fontsize=10)
            page.insert_text((27, y + 1.5), suffix, fontname="tiit", fontsize=6.5)
            page.insert_text(
                (70, y),
                "the measured mobility of the sample",
                fontname="tiro",
                fontsize=10,
            )
        seen = render_with_fixture_translation(
            doc,
            lambda text: text.replace(
                "the measured mobility of the sample", "样品测得的迁移率"
            ),
        )
        spans = cjk_spans(doc[0])
        assert len(spans) == 3
        assert all(span["bbox"][0] >= 69.5 for span in spans)
        assert all(a["bbox"][3] < b["bbox"][1] for a, b in zip(spans, spans[1:]))
        assert (
            sum(
                kind == "formula_definition" and "measured" in source
                for source, kind in seen
            )
            == 3
        )
        output = doc[0].get_text()
        assert "eff" in output and "obs" in output and "eof" in output
