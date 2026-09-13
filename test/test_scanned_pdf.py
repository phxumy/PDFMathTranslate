from __future__ import annotations

import io
import html
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest
import requests
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from pdf2zh.converter import PDFConverterEx, TranslateConverter
from pdf2zh.cache import TranslationCache
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.scanned_pdf import (
    detect_scan_background,
    english_scan_sections_to_preserve,
    horizontal_fit_scale,
    prepare_scan_background,
)


def make_source(
    *,
    scanned=True,
    hidden=True,
    colour=False,
    omitted_reference=False,
    reference_ink=True,
    body_text="SOURCE",
    scan_baseline_offset=0,
):
    artwork = pymupdf.open()
    art = artwork.new_page(width=240, height=200)
    art.draw_rect(art.rect, fill=(1, 0.7, 0.7) if colour else (1, 1, 1))
    art.insert_text((20, 25), "HEADER", fontsize=10)
    art.insert_text((20, 80 + scan_baseline_offset), body_text, fontsize=12)
    if scan_baseline_offset:
        art.draw_line((20, 89), (100, 89), width=0.5)
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
    page.insert_text((20, 80), body_text, fontsize=12, render_mode=3 if hidden else 0)
    if omitted_reference:
        page.insert_text((110, 80), "NEXT", fontsize=12, render_mode=3 if hidden else 0)
    else:
        page.insert_text((69, 76), "[1]", fontsize=7, render_mode=3 if hidden else 0)
    page.insert_font("tiro")
    return doc


def rgb(page):
    pix = page.get_pixmap()
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)


def translate_locally(doc, replacement, *, engine=None):
    page = doc[0]
    scan = prepare_scan_background(page, rgb(page))
    manager = PDFResourceManager()
    if engine is None:
        converter = TranslateConverter.__new__(TranslateConverter)
        PDFConverterEx.__init__(converter, manager)
        converter.translator = SimpleNamespace(name="local-test", lang_out="en")
        converter.vfont = ""
        converter.vchar = ""
        converter.noto_name = "noto"
        converter.noto = pymupdf.Font("helv")
    else:
        converter = TranslateConverter(
            manager,
            service=engine,
            lang_in="zh",
            lang_out="en",
            thread=1,
            noto_name="noto",
            noto=pymupdf.Font("helv"),
            ignore_cache=True,
        )
    mask = np.full((200, 240), 2.0)
    mask[165:] = 0
    converter.layout = {0: mask}
    converter.layout_region_types = {0: {2: "plain text"}}
    converter.scan_backgrounds = {0: scan} if scan else {}
    captured = []

    def translate(segments, paragraphs, *args, **kwargs):
        captured.extend(segments)
        return [
            segment.replace("SOURCE", replacement).replace("grouping", replacement)
            for segment in segments
        ]

    if engine is None:
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


def test_native_short_label_retains_its_font_size_when_translation_fits_page():
    doc = make_source(scanned=False, hidden=False)
    translate_locally(doc, "Translated label")
    translated_spans = [
        span
        for block in doc[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if "Translated" in span["text"]
    ]
    assert translated_spans
    assert translated_spans[0]["size"] == pytest.approx(12)
    assert translated_spans[0]["bbox"][2] < doc[0].rect.width


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


@pytest.mark.parametrize("engine", ["google", "bing"])
def test_scan_cleanup_uses_the_same_real_translation_pipeline_for_http_engines(
    monkeypatch, engine
):
    """Exercise policy, batching, engine parsing and rendering; stub only HTTP."""
    requests_seen = []

    def offline_request(_session, method, url, **kwargs):
        requests_seen.append((method, url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response.url = url
        if url.endswith("/translator"):
            payload = (
                '{"ig":"offline-ig"} <div data-iid="offline-iid">'
                'params_AbusePreventionHelper = [12345,"offline-token",'
            )
        else:
            source = (
                kwargs["params"]["q"] if engine == "google" else kwargs["data"]["text"]
            )
            target = source.replace("SOURCE", "A much longer translated phrase")
            payload = (
                f'<div class="result-container">{html.escape(target)}</div>'
                if engine == "google"
                else json.dumps([{"translations": [{"text": target}]}])
            )
        response._content = payload.encode("utf-8")
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "request", offline_request)
    monkeypatch.setattr(TranslationCache, "set", lambda *_args: None)
    doc = make_source()
    before = rgb(doc[0]).copy()
    operations, _ = translate_locally(doc, "unused", engine=engine)
    after = rgb(doc[0])

    assert requests_seen
    assert any("SOURCE" in str(kwargs) for _, _, kwargs in requests_seen)
    assert all("HEADER" not in str(kwargs) for _, _, kwargs in requests_seen)
    assert "q 1 g" in operations
    assert "re W n" in operations  # opaque reference uses the original scan
    assert "A much longer translated phrase" in doc[0].get_text()
    assert "SOURCE" not in doc[0].get_text()
    assert np.array_equal(before[:35], after[:35])  # protected OCR stays hidden
    assert np.array_equal(before[105:170, 140:220], after[105:170, 140:220])
    assert after[71:75, 20:65].mean() > 250  # source scan ink was erased


def test_scan_ink_below_inaccurate_ocr_baseline_is_removed_without_erasing_a_rule():
    doc = make_source(body_text="grouping", scan_baseline_offset=2)
    before = rgb(doc[0]).copy()
    translate_locally(doc, "A translated sentence with more words")
    after = rgb(doc[0])
    assert before[83:86, 20:60].mean() < 252
    assert after[83:86, 20:60].mean() > 254
    assert np.array_equal(before[88:91, 20:100], after[88:91, 20:100])


@pytest.mark.parametrize("multiply", [False, True])
def test_scan_fragment_white_background_does_not_erase_existing_target_content(
    multiply,
):
    doc = make_source()
    page = doc[0]
    scan = prepare_scan_background(page, rgb(page))
    assert scan is not None
    if not multiply:
        scan = replace(scan, blend_state=None)
    page.draw_rect((135, 100, 195, 125), color=(1, 0, 0), fill=(1, 0, 0))
    old = b" ".join(doc.xref_stream(xref) for xref in page.get_contents())
    operations = scan.fragment_ops((20, 116, 64, 132), 140, 80, 1.0)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, old + b" BT " + operations.encode() + b" ET")
    page.set_contents(xref)
    pixel = rgb(page)[118, 141]
    if multiply:
        assert tuple(pixel) == (255, 0, 0)
    else:
        assert tuple(pixel) == (255, 255, 255)


ENGLISH_ABSTRACT = (
    "Abstract: We examine the history of capitalism and its development in China."
)
ENGLISH_KEYWORDS = "Key words: modernization; national capitalism; industrialization"


@pytest.mark.parametrize(
    "texts, scanned, source_language, target_language, expected",
    [
        ([ENGLISH_ABSTRACT, ENGLISH_KEYWORDS], True, "zh-CN", "en", {0, 1}),
        ([ENGLISH_ABSTRACT, ENGLISH_KEYWORDS], False, "zh", "en", set()),
        ([ENGLISH_ABSTRACT, ENGLISH_KEYWORDS], True, "en", "zh", set()),
        ([ENGLISH_ABSTRACT, ENGLISH_KEYWORDS], True, "fr", "en", set()),
        (
            [ENGLISH_ABSTRACT.removeprefix("Abstract: "), ENGLISH_KEYWORDS],
            True,
            "zh",
            "en",
            set(),
        ),
        (
            [
                "Abstract: Cette recherche examine les relations entre le capitalisme et la modernisation.",
                ENGLISH_KEYWORDS,
            ],
            True,
            "zh",
            "en",
            set(),
        ),
        (
            [
                "Abstract: We examine the history of capitalism and its development in 中国。",
                ENGLISH_KEYWORDS,
            ],
            True,
            "zh",
            "en",
            set(),
        ),
        ([ENGLISH_KEYWORDS], True, "zh", "en", set()),
        (
            [ENGLISH_ABSTRACT, "Key words: modernization; 民族资本主义"],
            True,
            "zh",
            "en",
            {0},
        ),
    ],
)
def test_existing_english_scan_sections_are_preserved_only_for_explicit_bilingual_blocks(
    texts, scanned, source_language, target_language, expected
):
    assert (
        english_scan_sections_to_preserve(
            texts,
            scanned=scanned,
            source_language=source_language,
            target_language=target_language,
        )
        == expected
    )


def test_real_scan_pipeline_keeps_existing_english_abstract_pixels_without_http(
    monkeypatch,
):
    requests_seen = []

    def offline_request(*args, **kwargs):
        requests_seen.append((args, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = (
            b'<div class="result-container">Unexpected translation</div>'
        )
        return response

    monkeypatch.setattr(requests.Session, "request", offline_request)
    monkeypatch.setattr(TranslationCache, "set", lambda *_args: None)
    doc = make_source(body_text=ENGLISH_ABSTRACT)
    before = rgb(doc[0]).copy()
    operations, _ = translate_locally(doc, "unused", engine="google")
    assert not requests_seen
    assert "q 1 g" not in operations
    assert np.array_equal(before, rgb(doc[0]))
