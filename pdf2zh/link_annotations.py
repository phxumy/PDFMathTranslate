"""Keep link actions without displaying source-layout borders after reflow."""

from __future__ import annotations

from pymupdf import Page


def suppress_reflowed_link_borders(page: Page) -> None:
    """Hide standard link borders on a page whose text has been rebuilt.

    Source rectangles no longer describe the translated text. Keep the action,
    destination and hit area intact; this does not claim to relocate links.
    Walk annotation xrefs rather than resolved links so named destinations and
    unsupported action types are retained. Custom appearance streams are left
    alone because they may contain meaningful artwork, not just a border.
    """
    doc = page.parent
    for xref, *_ in page.annot_xrefs():
        if doc.xref_get_key(xref, "Subtype") != ("name", "/Link"):
            continue
        if doc.xref_get_key(xref, "AP")[0] != "null":
            continue
        doc.xref_set_key(xref, "Border", "[0 0 0]")
        # Replace locally: a shared indirect BS dictionary must not change
        # the appearance of annotations on untranslated pages.
        doc.xref_set_key(xref, "BS", "<< /W 0 >>")
