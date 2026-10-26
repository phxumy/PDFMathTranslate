from types import SimpleNamespace
import pymupdf as fitz

from pdf2zh.high_level import _collect_orcid_badge_bounds
from pdf2zh.converter import PDFConverterEx


def test_only_orcid_link_badge_artwork_is_identified():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        for x in (60, 160):
            page.draw_circle((x, 70), 3, color=None, fill=(0.651, 0.808, 0.224))
        page.insert_link(
            {
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(55, 65, 120, 76),
                "uri": "https://orcid.org/0000-0000-0000-0000",
            }
        )
        page = document.reload_page(page)
        badges = _collect_orcid_badge_bounds(page)
        assert len(badges) == 1
        assert badges[0][0] == 57


def test_badge_is_removed_only_when_its_author_address_was_reflowed():
    device = PDFConverterEx.__new__(PDFConverterEx)
    device.cur_item = SimpleNamespace(pageid=16)
    device.source_orcid_badges = {16: [(57, 727, 63, 733)]}
    marker = device.mark_orcid_badge_path((58, 728, 62, 732))
    assert marker
    assert device.mark_orcid_badge_path((50, 720, 80, 740)) == ""
    assert device.resolve_uri_underline_markers(marker + "\nf* ") == "f* "
    device._reflowed_author_addresses = [(16, (50, 700, 290, 750))]
    assert device.resolve_uri_underline_markers(marker + "\nf* ") == "n "
    device._reflowed_author_addresses = [(15, (50, 700, 290, 750))]
    assert device.resolve_uri_underline_markers(marker + "\nf ") == "f "
