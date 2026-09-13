"""Preserve the source geometry of compact author identifier lists."""

from __future__ import annotations

import re

from pdfminer.layout import LTChar, LTPage

from pdf2zh.toc_layout import _lines


_ORCID = re.compile(r"(?:https?://)?orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]", re.I)


def orcid_list_chars(page: LTPage) -> set[LTChar]:
    """Keep author names, accent glyphs and identifier URLs with their icons.

    Require at least two short author/URL rows. An ORCID link mentioned in an
    ordinary sentence must remain translatable. Glyph positions are retained,
    rather than reflowing the text while publisher artwork stays in place.
    """
    rows = []
    for line in _lines([item for item in page if isinstance(item, LTChar)]):
        pieces = []
        for index, char in enumerate(line):
            if index and char.x0 - line[index - 1].x1 > char.size * 0.18:
                pieces.append(" ")
            pieces.append(char.get_text())
        text = "".join(pieces).strip()
        match = _ORCID.search(text)
        if match is None:
            continue
        prefix, suffix = text[:match.start()].strip(), text[match.end():].strip()
        if len(prefix) > 75 or suffix or re.search(r"[,;:!?]", prefix):
            continue
        if len(prefix.split()) > 7:
            continue
        rows.append(line)
    if len(rows) < 2:
        return set()
    return {char for row in rows for char in row}
