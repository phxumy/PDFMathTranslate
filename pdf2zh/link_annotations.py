"""Keep link actions without displaying source-layout borders after reflow."""

from __future__ import annotations

from io import BytesIO

from pdfminer.pdfdocument import PDFDestinationNotFound, PDFDocument
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import PDFObjRef, dict_value, resolve1
from pdfminer.psparser import PSLiteral, literal_name
from pymupdf import Document, LINK_GOTO, LINK_NAMED, Page, get_pdf_str


def _internal_destination(document: PDFDocument, xref: int):
    """Read destination operands without normalizing coordinates or nulls."""
    annotation = dict_value(document.getobj(xref))
    target = resolve1(annotation.get("Dest"))
    if target is None:
        action = dict_value(annotation.get("A", {}))
        if literal_name(action.get("S")) != "GoTo":
            return None
        target = resolve1(action.get("D"))
    if isinstance(target, PSLiteral):
        target = literal_name(target)
    if isinstance(target, (str, bytes)):
        try:
            target = resolve1(document.get_dest(target))
        except (KeyError, TypeError, PDFDestinationNotFound):
            alternate = (
                target.decode("latin-1")
                if isinstance(target, bytes)
                else target.encode("utf-8")
            )
            try:
                target = resolve1(document.get_dest(alternate))
            except (KeyError, TypeError, PDFDestinationNotFound):
                return None
    if isinstance(target, dict):
        target = resolve1(target.get("D"))
    if not isinstance(target, list) or len(target) < 2:
        return None
    page, view, *operands = target
    if not isinstance(page, PDFObjRef) or not isinstance(view, PSLiteral):
        return None
    if literal_name(view) not in (
        "XYZ",
        "Fit",
        "FitH",
        "FitV",
        "FitR",
        "FitB",
        "FitBH",
        "FitBV",
    ):
        return None
    operands = [resolve1(value) for value in operands]
    if any(
        value is not None and not isinstance(value, (int, float)) for value in operands
    ):
        return None
    tail = " ".join("null" if value is None else str(value) for value in operands)
    return page.objid, f"/{literal_name(view)} {tail}".rstrip()


def restore_imported_named_links(
    source: Document, destination: Document, *, start_at: int
) -> None:
    """Restore named internal links omitted when MuPDF imports PDF pages.

    The imported pages do not bring their document-level destination name tree.
    Resolve names in the source and use explicit page references in the copy.
    Doing this before interleaving pages lets PDF page references follow their
    corresponding translated pages, including forward and backward targets.
    """
    if start_at < 0 or start_at + len(source) > len(destination):
        raise ValueError("imported page range is outside the destination document")
    parsed = None
    page_numbers = {source.page_xref(index): index for index in range(len(source))}
    for page_number, source_page in enumerate(source):
        copied_page = destination[start_at + page_number]
        existing = copied_page.get_links()
        for link in source_page.get_links():
            if link["kind"] != LINK_NAMED:
                continue
            if parsed is None:
                parsed = PDFDocument(PDFParser(BytesIO(source.tobytes())))
            resolved = _internal_destination(parsed, link["xref"])
            if resolved is None or resolved[0] not in page_numbers:
                # Some source PDFs contain names with no destination. Retain
                # those references without guessing a page for a broken link.
                name = link.get("nameddest")
                if not name:
                    continue
                imported_target = -1
                explicit_destination = get_pdf_str(name)
            else:
                target_page = page_numbers[resolved[0]]
                imported_target = start_at + target_page
                page_xref = destination.page_xref(imported_target)
                explicit_destination = f"[{page_xref} 0 R {resolved[1]}]"
            # Avoid duplicating links when called again or when a newer MuPDF
            # already imports a named destination as a resolved direct link.
            matching = next(
                (
                    old
                    for old in existing
                    if old["from"] == link["from"]
                    and (
                        (
                            old["kind"] == LINK_NAMED
                            and link.get("nameddest") is not None
                            and old.get("nameddest") == link.get("nameddest")
                        )
                        or (
                            "".join(
                                destination.xref_get_key(old["xref"], "A/D")[1].split()
                            )
                            == "".join(explicit_destination.split())
                        )
                    )
                ),
                None,
            )
            replacement = {
                "kind": LINK_GOTO,
                "from": link["from"],
                "page": imported_target,
            }
            if imported_target < 0:
                replacement["to"] = link["nameddest"]
            if matching is None:
                previous_xrefs = {xref for xref, *_ in copied_page.annot_xrefs()}
                copied_page.insert_link(replacement)
                new_xref = next(
                    xref
                    for xref, *_ in copied_page.annot_xrefs()
                    if xref not in previous_xrefs
                )
            else:
                existing.remove(matching)
                new_xref = matching["xref"]
                if matching["kind"] == LINK_NAMED:
                    copied_page.update_link(
                        {**replacement, "xref": new_xref, "id": matching.get("id", "")}
                    )
            # Named and direct link dictionaries expose different coordinate
            # conventions in MuPDF. Raw operands preserve view, zoom and nulls.
            destination.xref_set_key(new_xref, "A/D", explicit_destination)
            copied_page = destination.reload_page(copied_page)


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
