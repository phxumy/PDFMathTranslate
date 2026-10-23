"""A failed request inside a real PDF Form must stop later page translations."""

import io
from asyncio import CancelledError

import pymupdf
import pytest
from pdfminer.layout import LTChar, LTFigure
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from pdf2zh.codex_errors import (
    CodexPermanentError,
    CodexTranslationValidationError,
    CodexExecutionError,
)
from pdf2zh.converter import PDFConverterEx
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.translator import TranslationAbortedError


def document_with_masthead_form():
    with pymupdf.open() as form, pymupdf.open() as document:
        form.new_page(width=300, height=60).insert_text((20, 30), "nature physics")
        page = document.new_page(width=300, height=300)
        page.show_pdf_page(pymupdf.Rect(0, 0, 300, 60), form, 0)
        page.insert_text((20, 100), "Later page body")
        return document.tobytes()


def process_document(data, translate):
    def text(node):
        if isinstance(node, LTChar):
            return node.get_text()
        return "".join(text(child) for child in getattr(node, "_objs", ()))

    class Device(PDFConverterEx):
        def receive_layout(self, layout):
            # The leaf Form and the page body are distinct translation batches.
            if isinstance(layout, LTFigure):
                if any(isinstance(child, LTChar) for child in layout):
                    translate(text(layout))
            else:
                translate("Later page body")
            return ""

    manager = PDFResourceManager()
    device = Device(manager)
    interpreter = PDFPageInterpreterEx(manager, device, {})
    parsed = PDFDocument(PDFParser(io.BytesIO(data)))
    page = next(PDFPage.create_pages(parsed))
    page.pageno = 0
    page.page_xref = 999999
    interpreter.process_page(page)


@pytest.mark.parametrize(
    "error",
    [
        TranslationAbortedError("Original ChatGPT answer is still pending"),
        CodexPermanentError("Authentication required"),
        CodexTranslationValidationError("Incomplete translated paragraph"),
        CodexExecutionError("Provider request failed"),
        CancelledError("Translation cancelled"),
    ],
)
def test_form_failure_preserves_original_error_and_stops_page(error):
    calls = []

    def translate(source):
        calls.append(source)
        if source == "nature physics":
            raise error
        raise AssertionError("A later batch ran after the Form failed")

    with pytest.raises(type(error)) as caught:
        process_document(document_with_masthead_form(), translate)

    assert caught.value is error
    assert calls == ["nature physics"]


def test_resuming_page_visits_pending_form_before_later_batch():
    data = document_with_masthead_form()
    calls = []
    pending = None

    def translate(source):
        nonlocal pending
        if pending is not None:
            assert source == pending, "Recovery bypassed the original Form request"
            calls.append(("recover", source))
            pending = None
            return
        calls.append(("translate", source))
        if len(calls) == 1:
            pending = source
            raise TranslationAbortedError("Waiting for the original answer")

    with pytest.raises(TranslationAbortedError, match="original answer"):
        process_document(data, translate)
    assert calls == [("translate", "nature physics")]

    process_document(data, translate)
    assert calls == [
        ("translate", "nature physics"),
        ("recover", "nature physics"),
        ("translate", "Later page body"),
    ]
