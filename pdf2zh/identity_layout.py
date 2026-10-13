"""Preserve the source geometry of compact author identifier lists."""

from __future__ import annotations

import re
import unicodedata
from statistics import median

from pdfminer.layout import LTChar, LTPage

from pdf2zh.toc_layout import _lines
from pdf2zh.translation_policy import (
    _STRICT_BYLINE_NAME_TOKEN_RE,
    looks_like_affiliation,
    looks_like_author_list,
)

_ORCID = re.compile(r"(?:https?://)?orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]", re.I)


def _spaced_text(line: list[LTChar]) -> str:
    pieces = []
    for index, char in enumerate(line):
        if index and char.x0 - line[index - 1].x1 > char.size * 0.18:
            pieces.append(" ")
        pieces.append(char.get_text())
    return "".join(pieces).strip()


def _byline_classification_text(text: str) -> str:
    # Classify a copy: PDF overprinted accents and ligatures must keep their
    # original glyphs and positions in the selected source rows.
    text = text.translate(str.maketrans("", "", "˘ˇ˙˚´`¨ˆ˜"))
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def author_byline_chars(page: LTPage) -> set[LTChar]:
    """Anchor a strict first-page byline beside publisher identity artwork.

    A preserved author string can still reflow while ORCID icons stay fixed.
    Recover whole physical name rows immediately above an affiliation, including
    their affiliation markers, and let the existing source-transform path emit
    them. Institutions remain translatable. Initials or raised author markers
    distinguish short Title Case headings from an unambiguous name list.
    """
    if page.pageid != 0:
        return set()
    lines = _lines([item for item in page if isinstance(item, LTChar)])
    selected: set[LTChar] = set()
    for index, affiliation in enumerate(lines):
        affiliation_text = re.sub(
            r"^\s*\d{1,2}(?=[A-Z])", "", _spaced_text(affiliation)
        )
        if not looks_like_affiliation(affiliation_text):
            continue
        rows = []
        following = affiliation
        for line in reversed(lines[max(0, index - 4) : index]):
            size = median(char.size for char in line)
            if (
                min(char.y0 for char in line) < page.height * 0.48
                or size > page.height * 0.019
                or not -0.3 * size
                <= min(char.y0 for char in line) - max(char.y1 for char in following)
                <= 1.8 * size
                or (
                    rows
                    and abs(size - median(ch.size for ch in rows[-1])) > size * 0.12
                )
            ):
                break
            text = _byline_classification_text(_spaced_text(line))
            names = re.sub(r"[\d*†‡§#]+", "", text)
            entries = [
                entry.strip()
                for entry in re.split(r"\s*(?:,|\band\b|&)\s*", names, flags=re.I)
                if entry.strip()
            ]
            if not entries or any(
                not 2 <= len(entry.split()) <= 5
                or any(
                    _STRICT_BYLINE_NAME_TOKEN_RE.fullmatch(word) is None
                    for word in entry.split()
                )
                for entry in entries
            ):
                break
            rows.append(line)
            following = line
        chars = [char for row in reversed(rows) for char in row]
        text = " ".join(
            _byline_classification_text(_spaced_text(row)) for row in reversed(rows)
        )
        if (
            chars
            and looks_like_author_list(text)
            and (
                re.search(r"\b[A-Z]\.", text)
                or any(
                    char.get_text().strip("0123456789*†‡§#,") == ""
                    and char.size < median(ch.size for ch in chars) * 0.83
                    for char in chars
                )
            )
        ):
            selected.update(chars)
    return selected


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
        prefix, suffix = text[: match.start()].strip(), text[match.end() :].strip()
        if len(prefix) > 75 or suffix or re.search(r"[,;:!?]", prefix):
            continue
        if len(prefix.split()) > 7:
            continue
        rows.append(line)
    if len(rows) < 2:
        return set()
    return {char for row in rows for char in row}
