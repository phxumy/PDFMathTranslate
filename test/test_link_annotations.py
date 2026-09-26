from unittest.mock import patch

import pymupdf as fitz
import pytest

from pdf2zh import high_level
from pdf2zh.link_annotations import (
    restore_imported_named_links,
    suppress_reflowed_link_borders,
)


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


@pytest.mark.parametrize("skip_subset", [False, True])
def test_pipeline_changes_only_rebuilt_pages_and_dual_translation_sides(
    tmp_path, skip_subset
):
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
            doc.tobytes(), pages=[0], lang_out="en", skip_subset_fonts=skip_subset
        )
    for stream, expected in [(mono, [True, False]), (dual, [False, True, False, True])]:
        reopened = fitz.open(stream=stream, filetype="pdf")
        assert len(reopened) == len(expected)
        for page, hidden in zip(reopened, expected):
            links = page.get_links()
            assert len(links) == 1
            assert links[0]["uri"] == "https://example.org/paper"
            border = reopened.xref_get_key(links[0]["xref"], "Border")[1]
            width = reopened.xref_get_key(links[0]["xref"], "BS/W")[1]
            # MuPDF recreates imported URI links with BS/W=0 when assembling
            # the dual, while original-side annotations retain their style.
            assert (border == "[0 0 0]" or width == "0") is hidden
    mono_doc = fitz.open(stream=mono, filetype="pdf")
    dual_doc = fitz.open(stream=dual, filetype="pdf")
    assert "Translated text." in mono_doc[0].get_text()
    assert mono_doc[1].get_text() == doc[1].get_text()
    assert dual_doc[0].get_text() == doc[0].get_text()
    assert dual_doc[1].get_text() == mono_doc[0].get_text()


def named_link_document():
    doc = fitz.open()
    for index in range(4):
        doc.new_page().insert_text((72, 72), f"Original page {index + 1}.")
    page_xrefs = [doc.page_xref(index) for index in range(4)]
    # Exercise both PDF destination containers and both string/name operands.
    doc.xref_set_key(
        doc.pdf_catalog(),
        "Names",
        "<< /Dests << /Names ["
        f"(backward) [{page_xrefs[0]} 0 R /XYZ 80 680 1.25] "
        f"(forward) [{page_xrefs[2]} 0 R /XYZ 90 610 0]"
        "] >> >>",
    )
    doc.xref_set_key(
        doc.pdf_catalog(), "Dests", f"<< /wide [{page_xrefs[3]} 0 R /FitH 660] >>"
    )
    for page_number, name in [(0, "(forward)"), (1, "/wide"), (2, "(backward)")]:
        page = doc[page_number]
        page.insert_link(
            dict(kind=fitz.LINK_GOTO, page=0, **{"from": fitz.Rect(72, 90, 135, 110)})
        )
        xref = page.annot_xrefs()[-1][0]
        doc.xref_set_key(xref, "A", "null")
        doc.xref_set_key(xref, "Dest", name)
    for page_number, target in [(0, 3), (3, 1)]:
        doc[page_number].insert_link(
            dict(
                kind=fitz.LINK_GOTO,
                page=target,
                to=fitz.Point(75, 125),
                **{"from": fitz.Rect(72, 130, 135, 150)},
            )
        )
    doc[0].insert_link(
        dict(
            kind=fitz.LINK_URI,
            uri="https://example.org/paper#section-two",
            **{"from": fitz.Rect(72, 170, 200, 190)},
        )
    )
    doc[0].add_text_annot((210, 100), "Keep the review note.")
    doc.set_toc([[1, "First section", 1], [1, "Later section", 3]])
    return fitz.open(stream=doc.tobytes(), filetype="pdf")


@pytest.mark.parametrize("skip_subset", [False, True])
def test_dual_keeps_named_and_direct_links_on_corresponding_language_pages(
    tmp_path, skip_subset
):
    source = named_link_document()
    font = tmp_path / "font.ttf"
    font.write_bytes(fitz.Font("tiro").buffer)

    def rebuild_pages(_fp, **kwargs):
        target = kwargs["doc_zh"]
        patches = {}
        for number, page in enumerate(target):
            xref = target.get_new_xref()
            target.update_object(xref, "<<>>")
            target.update_stream(xref, b"")
            page.set_contents(xref)
            patches[xref] = (
                f"BT /tiro 12 Tf 72 700 Td (Translated page {number + 1}.) Tj ET"
            )
        return patches

    with (
        patch.object(high_level, "download_remote_fonts", return_value=str(font)),
        patch.object(
            high_level, "prepare_pdf_text_font", side_effect=lambda path: path
        ),
        patch.object(high_level, "translate_patch", side_effect=rebuild_pages),
    ):
        mono_bytes, dual_bytes = high_level.translate_stream(
            source.tobytes(), lang_out="en", skip_subset_fonts=skip_subset
        )
    mono = fitz.open(stream=mono_bytes, filetype="pdf")
    dual = fitz.open(stream=dual_bytes, filetype="pdf")
    for original_page, source_page in enumerate(source):
        expected_links = source_page.get_links()
        for language_side in (0, 1):
            copied = dual[2 * original_page + language_side]
            copied_links = copied.get_links()
            assert len(copied_links) == len(expected_links)
            for expected in expected_links:
                actual = next(
                    link for link in copied_links if link["from"] == expected["from"]
                )
                if expected["kind"] == fitz.LINK_URI:
                    assert actual["kind"] == fitz.LINK_URI
                    assert actual["uri"] == expected["uri"]
                elif "dest" in expected:
                    if language_side:
                        target_xref = dual.page_xref(2 * expected["page"] + 1)
                        raw = dual.xref_get_key(actual["xref"], "A/D")[1]
                        assert "".join(raw.split()) == (
                            f"[{target_xref}0R{expected['dest']}]".replace(" ", "")
                        )
                    else:
                        assert actual["page"] == 2 * expected["page"]
                        assert actual["dest"] == expected["dest"]
                else:
                    assert actual["page"] == 2 * expected["page"] + language_side
                    if language_side and expected["kind"] == fitz.LINK_NAMED:
                        expected_point = (
                            expected["to"]
                            * source[expected["page"]].transformation_matrix
                        )
                        assert actual["to"] == expected_point
                        raw = dual.xref_get_key(actual["xref"], "A/D")[1]
                        assert float(raw.rstrip("]").split()[-1]) == expected["zoom"]
                    else:
                        assert actual["to"] == expected["to"]
            expected_text = (mono if language_side else source)[
                original_page
            ].get_text()
            assert copied.get_text() == expected_text
    for page_number in (0, 1):
        notes = list(dual[page_number].annots())
        assert len(notes) == 1
        assert notes[0].info["content"] == "Keep the review note."
    assert dual.get_toc() == [[1, "First section", 1], [1, "Later section", 5]]
    fit_link = dual[3].get_links()[0]
    assert "/FitH 660" in dual.xref_get_key(fit_link["xref"], "A/D")[1]


def test_named_link_restoration_is_idempotent_and_checks_import_range():
    source = named_link_document()
    destination = fitz.open(stream=source.tobytes(), filetype="pdf")
    destination.insert_pdf(source)
    restore_imported_named_links(source, destination, start_at=len(source))
    counts = [len(page.get_links()) for page in destination]
    restore_imported_named_links(source, destination, start_at=len(source))
    assert [len(page.get_links()) for page in destination] == counts
    with pytest.raises(ValueError, match="outside"):
        restore_imported_named_links(source, destination, start_at=len(destination))


def test_named_link_preserves_null_operands_and_rotated_target():
    source = named_link_document()
    source[2].set_cropbox(fitz.Rect(20, 40, 570, 800))
    source[2].set_rotation(90)
    source.xref_set_key(
        source.pdf_catalog(),
        "Names/Dests/Names",
        f"[(forward) [{source.page_xref(2)} 0 R /XYZ null 610 null]]",
    )
    source = fitz.open(stream=source.tobytes(), filetype="pdf")
    destination = fitz.open(stream=source.tobytes(), filetype="pdf")
    destination.insert_pdf(source)
    # The fixture's backward name is now unresolved. Leave it unresolved and
    # preserve the valid forward destination without inventing coordinates.
    restore_imported_named_links(source, destination, start_at=len(source))
    restored = next(
        link
        for link in destination[4].get_links()
        if link["from"] == source[0].get_links()[0]["from"]
    )
    raw = destination.xref_get_key(restored["xref"], "A/D")[1]
    target_xref = destination.page_xref(6)
    assert "".join(raw.split()) == f"[{target_xref}0R/XYZnull610null]"
    assert destination[6].rotation == 90
    assert destination[6].cropbox == source[2].cropbox
