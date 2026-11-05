import zlib

import pymupdf as fitz
import pytest

from test_graphics_fidelity import preserve_locally


@pytest.mark.parametrize("filtered", [False, True])
def test_inline_rgb_strips_keep_pixels_transform_and_original_filter_chain(filtered):
    with fitz.open() as doc:
        page = doc.new_page(width=120, height=100)
        page.draw_rect((1, 1, 2, 2))
        pixels = b"\xff\x00\x00\x00\xff\x00"
        data = zlib.compress(pixels) if filtered else pixels
        filters = b" /F /Fl" if filtered else b""
        stream = (
            b"q 30 0 0 3 17 44 cm BI /W 2 /H 1 /BPC 8 /CS /RGB"
            + filters
            + b" ID "
            + data
            + b" EI Q"
        )
        doc.update_stream(page.get_contents()[0], stream)
        before = page.get_pixmap(matrix=fitz.Matrix(2, 2)).samples
        before_info = [
            (x["bbox"], x["width"], x["height"]) for x in page.get_image_info()
        ]
        operations = preserve_locally(doc)
        assert "ASCIIHexDecode" in operations
        assert doc[0].get_pixmap(matrix=fitz.Matrix(2, 2)).samples == before
        assert [
            (x["bbox"], x["width"], x["height"]) for x in doc[0].get_image_info()
        ] == before_info


def test_inline_stencil_boolean_and_decode_array_preserve_current_color():
    with fitz.open() as doc:
        page = doc.new_page(width=120, height=100)
        page.draw_rect((1, 1, 2, 2))
        doc.update_stream(
            page.get_contents()[0],
            b"q 0.2 0.4 0.6 rg 25 0 0 4 31 28 cm BI /W 1 /H 1 /BPC 1 /IM true /D [0 1] ID \x00 EI Q",
        )
        before = page.get_pixmap(matrix=fitz.Matrix(2, 2)).samples
        preserve_locally(doc)
        assert doc[0].get_pixmap(matrix=fitz.Matrix(2, 2)).samples == before
        assert len(doc[0].get_image_info()) == 1


def test_inline_and_external_xref_images_are_both_preserved():
    with fitz.open() as doc:
        page = doc.new_page(width=120, height=100)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
        pixmap.clear_with(90)
        page.insert_image((5, 5, 35, 35), pixmap=pixmap)
        existing = page.get_contents()[0]
        external = doc.xref_stream(existing)
        doc.update_stream(
            existing,
            external
            + b" q 20 0 0 2 50 50 cm BI /W 1 /H 1 /BPC 8 /CS /RGB ID \xff\x00\x00 EI Q",
        )
        before = page.get_pixmap(matrix=fitz.Matrix(2, 2)).samples
        assert len(page.get_image_info()) == 2
        preserve_locally(doc)
        assert len(doc[0].get_image_info()) == 2
        assert doc[0].get_pixmap(matrix=fitz.Matrix(2, 2)).samples == before
