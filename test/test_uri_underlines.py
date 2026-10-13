import io
from types import SimpleNamespace

import numpy as np
import pymupdf as fitz
import pytest
from pdfminer.layout import LTFigure, LTPage
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

from pdf2zh.converter import PDFConverterEx, TranslateConverter
from pdf2zh.high_level import _collect_source_uri_links
from pdf2zh.pdfinterp import PDFPageInterpreterEx


def converter_for_line():
    converter = PDFConverterEx(PDFResourceManager())
    converter.cur_item = LTPage(0, (0, 0, 240, 200))
    converter.source_uri_links = {0: [("/relative", (20, 100, 90, 107))]}
    return converter


def state(**values):
    result = PDFGraphicState()
    result.linewidth = 0.75
    result.scolor = (0, 0.4, 0.7)
    result.dash = ([], 0)
    for key, value in values.items():
        setattr(result, key, value)
    return result


@pytest.mark.parametrize(
    "start,end,style",
    [
        ((20, 100.6), (89, 100.6), state()),  # wrong endpoint
        ((20, 103), (90, 103), state()),  # not an underline
        ((20, 100.6), (90, 101), state()),  # sloped scientific line
        ((20, 100.6), (90, 100.6), state(dash=([2, 2], 0))),
        ((20, 100.6), (90, 100.6), state(linewidth=2)),
        ((20, 100.6), (90, 100.6), state(scolor=(0, 0, 0))),
    ],
)
def test_nonmatching_scientific_strokes_are_never_marked(start, end, style):
    assert converter_for_line().mark_uri_underline(start, end, style) == ""


def test_matching_marker_is_resolved_only_for_reflowed_prose_and_always_cleared():
    converter = converter_for_line()
    marker = converter.mark_uri_underline((20, 100.6), (90, 100.6), state())
    operations = "20 100.6 m 90 100.6 l " + marker + "\n S 20 80 m 90 80 l S"
    kept = converter.resolve_uri_underline_markers(operations)
    assert "PDF2ZH" not in kept and kept.count(" S") == 2
    converter._reflowed_uri_prose = [(0, (19, 99, 91, 108))]
    removed = converter.resolve_uri_underline_markers(operations)
    assert "PDF2ZH" not in removed and removed.count(" S") == 1
    assert " l n " in removed


@pytest.mark.parametrize(
    "case,removed",
    [
        ({}, True),
        ({"unchanged": True}, False),
        ({"brk": False}, False),
        ({"layout_class": 0}, False),
        ({"region_kind": "figure"}, False),
        ({"region_kind": "table"}, False),
    ],
)
def test_form_xobject_prose_reflow_protects_unchanged_and_graphics(case, removed):
    converter = converter_for_line()
    converter.cur_item = LTFigure("cover", (0, 0, 240, 200), (1, 0, 0, 1, 0, 0))
    converter.cur_item.pageid = 0
    marker = converter.mark_uri_underline((20, 100.6), (90, 100.6), state())
    assert marker
    values = dict(
        brk=True, layout_class=4, region_kind="plain text", x0=19, y0=99, x1=91, y1=108
    )
    unchanged = case.get("unchanged", False)
    values.update({key: value for key, value in case.items() if key != "unchanged"})
    converter._record_reflowed_uri_prose(
        0, ["source"], ["source" if unchanged else "译文"], [SimpleNamespace(**values)]
    )
    operations = "20 100.6 m 90 100.6 l " + marker + "\n S"
    result = converter.resolve_uri_underline_markers(operations)
    assert "PDF2ZH" not in result
    assert result.endswith("n" if removed else "S")


def test_raw_relative_uri_is_supported_without_treating_launch_as_uri():
    with fitz.open() as doc:
        page = doc.new_page(width=240, height=200)
        page.insert_link(
            {
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(20, 93, 90, 100),
                "uri": "/relative",
            }
        )
        page.insert_link(
            {
                "kind": fitz.LINK_LAUNCH,
                "from": fitz.Rect(20, 120, 90, 127),
                "file": "program.exe",
            }
        )
        page = doc.reload_page(page)
        assert _collect_source_uri_links(doc) == {
            0: [("/relative", (20, 100, 90, 107))]
        }


@pytest.mark.parametrize("reflowed", [False, True])
def test_real_stroke_termination_and_following_scientific_line_survive(reflowed):
    with fitz.open() as doc:
        page = doc.new_page(width=240, height=200)
        page.draw_line((20, 99.4), (90, 99.4), color=(0, 0.4, 0.7), width=0.75)
        page.draw_line(
            (20, 130), (90, 130), color=(1, 0, 0), width=0.75, dashes="[2 2] 0"
        )
        before = page.get_drawings()
        manager = PDFResourceManager()
        converter = TranslateConverter.__new__(TranslateConverter)
        PDFConverterEx.__init__(converter, manager)
        converter.translator = SimpleNamespace(name="local-test", lang_out="en")
        converter.vfont = converter.vchar = ""
        converter.noto_name = "noto"
        converter.noto = fitz.Font("helv")
        converter.layout = {0: np.zeros((200, 240))}
        converter.source_uri_links = {0: [("/relative", (20, 100, 90, 107))]}
        converter._translate_planned_segments = lambda segments, *a, **k: list(segments)
        patches = {}
        interpreter = PDFPageInterpreterEx(manager, converter, patches)
        parsed = PDFDocument(PDFParser(io.BytesIO(doc.tobytes())))
        source_page = next(PDFPage.create_pages(parsed))
        source_page.pageno = 0
        source_page.page_xref = doc.get_new_xref()
        doc.update_object(source_page.page_xref, "<<>>")
        doc.update_stream(source_page.page_xref, b"")
        page.set_contents(source_page.page_xref)
        interpreter.process_page(source_page)
        if reflowed:
            converter._reflowed_uri_prose = [(0, (19, 99, 91, 108))]
        for xref, operations in patches.items():
            resolved = converter.resolve_uri_underline_markers(operations)
            assert "PDF2ZH_URI_UNDERLINE" not in resolved
            doc.update_stream(xref, resolved.encode())
        after = doc[0].get_drawings()
        assert len(after) == (1 if reflowed else 2)
        expected = before[-1]
        for key in ("items", "rect", "color", "width", "dashes"):
            assert after[-1][key] == expected[key]
