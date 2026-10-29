from unittest.mock import patch

import pymupdf
import pytest

from pdf2zh import high_level
from pdf2zh.link_annotations import restore_imported_transparency_groups


def source_document(*, indirect_color=False):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((30, 40), "Text and a transparent overlay", fontsize=10)
    page.draw_rect((30, 60, 180, 180), color=None, fill=(1, 0, 0), fill_opacity=0.6)
    page.draw_rect((90, 100, 220, 220), color=None, fill=(0, 0, 1), fill_opacity=0.5)
    color = "/DeviceCMYK"
    if indirect_color:
        xref = doc.get_new_xref()
        doc.update_object(
            xref, "[/CalRGB << /WhitePoint [0.9505 1 1.089] /Gamma [2.2 2.2 2.2] >>]"
        )
        color = f"{xref} 0 R"
    group = doc.get_new_xref()
    doc.update_object(group, f"<< /S /Transparency /CS {color} /I false /K false >>")
    doc.xref_set_key(page.xref, "Group", f"{group} 0 R")
    return doc


@pytest.mark.parametrize("indirect_color", [False, True])
def test_imported_blending_group_copies_references_without_changing_source(
    indirect_color,
):
    with (
        source_document(indirect_color=indirect_color) as source,
        pymupdf.open() as target,
    ):
        source_before = source.tobytes(no_new_id=True)
        target.new_page()
        target.insert_pdf(source)
        restore_imported_transparency_groups(source, target, start_at=1)
        with pymupdf.open(stream=target.tobytes()) as reopened:
            assert source[0].get_pixmap().samples == reopened[1].get_pixmap().samples
            assert (
                reopened.xref_get_key(reopened[1].xref, "Group/S")[1] == "/Transparency"
            )
            assert reopened.xref_get_key(reopened[1].xref, "Group/I")[1] == "false"
        assert source.tobytes(no_new_id=True) == source_before


@pytest.mark.parametrize("skip_subset", [False, True])
def test_bilingual_pipeline_preserves_translated_page_compositing(
    tmp_path, skip_subset
):
    with source_document() as source:
        font = tmp_path / "font.ttf"
        font.write_bytes(pymupdf.Font("tiro").buffer)
        with (
            patch.object(high_level, "download_remote_fonts", return_value=str(font)),
            patch.object(high_level, "prepare_pdf_text_font", side_effect=lambda p: p),
            patch.object(high_level, "translate_patch", return_value={}),
        ):
            mono, dual = high_level.translate_stream(
                source.tobytes(), lang_out="en", skip_subset_fonts=skip_subset
            )
        with pymupdf.open(stream=mono) as m, pymupdf.open(stream=dual) as d:
            assert m[0].get_pixmap().samples == d[1].get_pixmap().samples
            assert source[0].get_pixmap().samples == d[0].get_pixmap().samples
