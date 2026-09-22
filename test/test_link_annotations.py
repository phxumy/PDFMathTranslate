from unittest.mock import patch

import pymupdf as fitz

from pdf2zh import high_level
from pdf2zh.link_annotations import suppress_reflowed_link_borders


def source_document():
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), "See section 2 and the online paper.")
    for page in doc:
        page.insert_link(
            dict(
                kind=fitz.LINK_URI,
                uri="https://example.org/paper",
                **{"from": fitz.Rect(72, 60, 110, 75)},
            )
        )
    doc = fitz.open(stream=doc.tobytes(), filetype="pdf")
    for page in doc:
        xref = page.annot_xrefs()[0][0]
        doc.xref_set_key(xref, "Border", "[0 0 1 [2 1]]")
        doc.xref_set_key(xref, "BS", "null")
        doc.xref_set_key(xref, "C", "[1 0 0]")
    return doc


def test_named_links_shared_border_and_non_link_annotations_are_preserved():
    doc = source_document()
    first, second = [page.annot_xrefs()[0][0] for page in doc]
    style = doc.get_new_xref()
    doc.update_object(style, "<< /W 2 /S /D /D [2 1] >>")
    for xref in (first, second):
        doc.xref_set_key(xref, "BS", f"{style} 0 R")
    doc.xref_set_key(first, "A", "<< /S /GoTo /D (section.2) >>")
    before = {key: doc.xref_get_key(first, key) for key in ("A", "Rect", "C")}
    page = doc[0]
    note = page.add_rect_annot(fitz.Rect(150, 60, 200, 90))
    note_before = doc.xref_object(note.xref)

    suppress_reflowed_link_borders(page)

    assert {key: doc.xref_get_key(first, key) for key in before} == before
    assert doc.xref_get_key(first, "Border")[1] == "[0 0 0]"
    assert doc.xref_get_key(first, "BS/W")[1] == "0"
    assert doc.xref_get_key(style, "W")[1] == "2"
    assert doc.xref_get_key(second, "Border")[1] == "[0 0 1[2 1]]"
    assert doc.xref_object(note.xref) == note_before


def test_custom_link_appearance_is_untouched():
    doc = source_document()
    xref = doc[0].annot_xrefs()[0][0]
    appearance = doc.get_new_xref()
    doc.update_object(
        appearance, "<< /Type /XObject /Subtype /Form /BBox [0 0 40 15] >>"
    )
    doc.update_stream(appearance, b"0 0 40 15 re S")
    doc.xref_set_key(xref, "AP", f"<< /N {appearance} 0 R >>")
    before = doc.xref_object(xref)
    suppress_reflowed_link_borders(doc[0])
    assert doc.xref_object(xref) == before


def test_pipeline_changes_only_rebuilt_pages_and_dual_translation_sides(tmp_path):
    doc = source_document()
    font = tmp_path / "font.ttf"
    font.write_bytes(fitz.Font("tiro").buffer)

    def rebuild_one_page(_fp, **kwargs):
        target = kwargs["doc_zh"]
        xref = target.get_new_xref()
        target.update_object(xref, "<<>>")
        target.update_stream(xref, b"")
        target[0].set_contents(xref)
        return {xref: "BT /tiro 12 Tf 72 700 Td (Translated text.) Tj ET"}

    with (
        patch.object(high_level, "download_remote_fonts", return_value=str(font)),
        patch.object(
            high_level, "prepare_pdf_text_font", side_effect=lambda path: path
        ),
        patch.object(high_level, "translate_patch", side_effect=rebuild_one_page),
    ):
        mono, dual = high_level.translate_stream(
            doc.tobytes(), pages=[0], lang_out="en", skip_subset_fonts=True
        )
    for stream, expected in [(mono, [True, False]), (dual, [False, True, False, True])]:
        reopened = fitz.open(stream=stream, filetype="pdf")
        for page, hidden in zip(reopened, expected):
            links = page.get_links()
            assert len(links) == 1
            assert links[0]["uri"] == "https://example.org/paper"
            border = reopened.xref_get_key(links[0]["xref"], "Border")[1]
            width = reopened.xref_get_key(links[0]["xref"], "BS/W")[1]
            # MuPDF recreates imported URI links with BS/W=0 when assembling
            # the dual, while original-side annotations retain their style.
            assert (border == "[0 0 0]" or width == "0") is hidden
