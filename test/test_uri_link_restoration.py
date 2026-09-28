from io import BytesIO
from unittest.mock import patch

import pymupdf as fitz
import pytest
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import PDFObjRef, dict_value, resolve1
from pdfminer.psparser import literal_name

from pdf2zh import high_level
from pdf2zh.link_annotations import restore_imported_uri_links


def uri_document(*, rotated=False):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Relative publisher links and a local file.")
    doc.new_page()
    page = doc[0]
    # Identical rectangles exercise pairing in annotation order. A real local
    # file action must not be mistaken for the relative URI following it.
    for kind, target, rect in [
        (fitz.LINK_LAUNCH, "local.pdf", fitz.Rect(80, 100, 180, 120)),
        (
            fitz.LINK_URI,
            "/article/10.1234/example?x=1#part",
            fitz.Rect(80, 100, 180, 120),
        ),
        (fitz.LINK_URI, "../relative/paper", fitz.Rect(80, 140, 180, 160)),
        (fitz.LINK_URI, "https://example.org/paper", fitz.Rect(80, 180, 180, 200)),
    ]:
        page.insert_link(
            {
                "kind": kind,
                "from": rect,
                "uri" if kind == fitz.LINK_URI else "file": target,
            }
        )
    page.insert_link(
        {"kind": fitz.LINK_GOTO, "page": 1, "from": fitz.Rect(80, 220, 180, 240)}
    )
    page.add_text_annot((220, 110), "Keep this annotation.")
    if rotated:
        page.set_cropbox(fitz.Rect(20, 40, 570, 800))
        page.set_rotation(90)
    return fitz.open(stream=doc.tobytes(), filetype="pdf")


def parsed_annotation(doc, xref):
    parsed = PDFDocument(PDFParser(BytesIO(doc.tobytes())))
    return dict_value(parsed.getobj(xref))


def uri_actions(doc, page):
    return [
        (link["from"], resolve1(parsed_annotation(doc, link["xref"])["A"]))
        for link in doc[page].get_links()
        if doc.xref_get_key(link["xref"], "A/S")[1] == "/URI"
    ]


@pytest.mark.parametrize("rotated", [False, True])
def test_relative_uri_restored_without_changing_other_actions_or_geometry(rotated):
    source = uri_document(rotated=rotated)
    destination = fitz.open(stream=source.tobytes(), filetype="pdf")
    destination.insert_pdf(source)
    before = destination[2].get_links()
    assert destination.xref_get_key(before[1]["xref"], "A/S")[1] == "/Launch"
    untouched = {
        before[index]["xref"]: destination.xref_object(before[index]["xref"])
        for index in (0, 4)
    }
    first_side = [
        destination.xref_object(link["xref"]) for link in destination[0].get_links()
    ]
    note = next(destination[2].annots())
    untouched[note.xref] = destination.xref_object(note.xref)

    restore_imported_uri_links(source, destination, start_at=2)
    actual = uri_actions(destination, 2)
    expected = uri_actions(source, 0)
    assert len(actual) == len(expected) == 3
    for (actual_rect, actual_action), (expected_rect, expected_action) in zip(
        actual, expected
    ):
        assert actual_rect == expected_rect
        assert actual_action == expected_action
    assert [
        destination.xref_object(link["xref"]) for link in destination[0].get_links()
    ] == first_side
    assert all(
        destination.xref_object(xref) == value for xref, value in untouched.items()
    )
    assert destination[2].rotation == source[0].rotation
    assert destination[2].cropbox == source[0].cropbox
    assert destination[2].get_pixmap().samples == source[0].get_pixmap().samples

    counts = [len(page.get_links()) for page in destination]
    restore_imported_uri_links(source, destination, start_at=2)
    assert [len(page.get_links()) for page in destination] == counts
    assert uri_actions(destination, 2) == actual


def test_uri_action_preserves_indirect_bytes_flags_and_followup_page_reference():
    source = uri_document()
    link_xref = source[0].get_links()[1]["xref"]
    uri_xref = source.get_new_xref()
    uri_bytes = b"../paper(edition)\\v1?raw=\xff&escaped=%28#fragment"
    source.update_object(uri_xref, f"<{uri_bytes.hex()}>")
    next_xref = source.get_new_xref()
    source.update_object(
        next_xref, f"<< /S /GoTo /D [{source.page_xref(1)} 0 R /XYZ null 610 1.25] >>"
    )
    action_xref = source.get_new_xref()
    source.update_object(
        action_xref,
        f"<< /S /URI /URI {uri_xref} 0 R /IsMap true /Next {next_xref} 0 R >>",
    )
    source.xref_set_key(link_xref, "A", f"{action_xref} 0 R")
    source.xref_set_key(link_xref, "QuadPoints", "[80 742 180 742 80 722 180 722]")
    source = fitz.open(stream=source.tobytes(), filetype="pdf")
    destination = fitz.open(stream=source.tobytes(), filetype="pdf")
    destination.insert_pdf(source)

    restore_imported_uri_links(source, destination, start_at=2)
    # Moving pages after importing must not invalidate the indirect GoTo.
    destination.move_page(2, 1)
    destination = fitz.open(stream=destination.tobytes(), filetype="pdf")
    actual_xref = destination[1].get_links()[1]["xref"]
    annotation = parsed_annotation(destination, actual_xref)
    action = resolve1(annotation["A"])
    assert literal_name(action["S"]) == "URI"
    assert resolve1(action["URI"]) == uri_bytes
    assert action["IsMap"] is True
    followup = resolve1(action["Next"])
    assert literal_name(followup["S"]) == "GoTo"
    assert isinstance(followup["D"][0], PDFObjRef)
    assert followup["D"][0].objid == destination.page_xref(3)
    assert literal_name(followup["D"][1]) == "XYZ"
    assert followup["D"][2:] == [None, 610, 1.25]
    assert annotation["QuadPoints"] == [80, 742, 180, 742, 80, 722, 180, 722]


def test_missing_imported_uri_is_restored_and_invalid_ranges_are_rejected():
    source = uri_document()
    destination = fitz.open(stream=source.tobytes(), filetype="pdf")
    destination.insert_pdf(source)
    page = destination[2]
    page.delete_link(page.get_links()[2])
    restore_imported_uri_links(source, destination, start_at=2)
    # Reopen after annotation insertion, as required when a caller retains an
    # older loaded Page instance in MuPDF.
    destination = fitz.open(stream=destination.tobytes(), filetype="pdf")
    assert sorted(
        (tuple(rect), action["URI"]) for rect, action in uri_actions(destination, 2)
    ) == sorted((tuple(rect), action["URI"]) for rect, action in uri_actions(source, 0))
    for start_at in (-1, 3):
        with pytest.raises(ValueError, match="outside"):
            restore_imported_uri_links(source, destination, start_at=start_at)


@pytest.mark.parametrize("skip_subset", [False, True])
def test_translation_pipeline_keeps_raw_uri_action_on_both_language_sides(
    tmp_path, skip_subset
):
    source = uri_document()
    font = tmp_path / "font.ttf"
    font.write_bytes(fitz.Font("tiro").buffer)
    with (
        patch.object(high_level, "download_remote_fonts", return_value=str(font)),
        patch.object(
            high_level, "prepare_pdf_text_font", side_effect=lambda path: path
        ),
        patch.object(high_level, "translate_patch", return_value={}),
    ):
        mono_bytes, dual_bytes = high_level.translate_stream(
            source.tobytes(), lang_out="en", skip_subset_fonts=skip_subset
        )
    mono = fitz.open(stream=mono_bytes, filetype="pdf")
    dual = fitz.open(stream=dual_bytes, filetype="pdf")
    for side in (0, 1):
        assert uri_actions(dual, side) == uri_actions(mono, 0)
        assert len(dual[side].get_links()) == len(source[0].get_links())
