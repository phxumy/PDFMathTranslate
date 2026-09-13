"""Recover table-of-contents rows before ordinary paragraph reconstruction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from pdfminer.layout import LTChar, LTPage

_LEADERS = re.compile(
    r"[.\u2026\u00b7\u2024]{3,}\s*([0-9]{1,4}|[ivxlcdmIVXLCDM]{1,8})\s*$"
)
_PAGE_NUMBER = re.compile(r"(?:[0-9]{1,4}|(?<![A-Za-z])[ivxlcdmIVXLCDM]{1,8})\s*$")
_LEADER_ONLY = re.compile(r"[.\u2026\u00b7\u2024]{3,}\s*$")
_HEADING = re.compile(
    r"^(?:table\s*of\s*contents|contents|list\s*of\s*(?:figures|tables)|"
    r"目录|目次|插图目录|图目录|表目录)$",
    re.IGNORECASE,
)
_ENTRY_START = re.compile(
    r"^(?:\d+(?:\.\d+)*(?=\D)|(?:fig(?:ure)?\.?|table)\s*\d)", re.IGNORECASE
)


@dataclass
class TocEntry:
    chars: list[LTChar]
    leaders: list[LTChar]
    page_number: list[LTChar]
    x0: float
    x1: float
    y0: float
    y1: float
    page_right: float


@dataclass
class TocLayout:
    entries: list[TocEntry]
    # Glyph identities are used rather than broad rectangles so adjacent
    # formulas, indentation and page numbers keep their exact ownership.
    entry_by_char: dict[int, int]
    omitted_chars: set[int]
    page_number_chars: set[int]


def _text(chars: list[LTChar]) -> str:
    return "".join(char.get_text() for char in chars)


def _lines(chars: list[LTChar]) -> list[list[LTChar]]:
    if not chars:
        return []
    size = median(char.size for char in chars)
    body = [char for char in chars if char.size >= size * 0.8]
    scripts = [char for char in chars if char.size < size * 0.8]
    lines: list[list[LTChar]] = []
    for char in sorted(body, key=lambda item: (-item.y0, item.x0)):
        if (
            lines
            and abs(char.y0 - median(item.y0 for item in lines[-1])) <= size * 0.35
        ):
            lines[-1].append(char)
        else:
            lines.append([char])
    for char in scripts:
        target = min(
            lines,
            key=lambda line: abs(
                (char.y0 + char.y1) / 2
                - median((item.y0 + item.y1) / 2 for item in line)
            ),
        )
        target.append(char)
    return [sorted(line, key=lambda item: item.x0) for line in lines]


def detect_toc_layout(ltpage: LTPage) -> TocLayout:
    """Require repeated aligned dotted leaders, then recover whole entries.

    A single dotted sentence/equation is deliberately insufficient.  Multiple
    wrapped lines belong to one entry until its right-aligned destination page;
    list-of-figures continuation pages do not require a repeated heading.
    """
    empty = TocLayout([], {}, set(), set())
    lines = _lines([item for item in ltpage if isinstance(item, LTChar)])
    if not lines:
        return empty
    texts = [_text(line).strip() for line in lines]
    matches = [(i, _LEADERS.search(_text(line))) for i, line in enumerate(lines)]
    matches = [(i, match) for i, match in matches if match is not None]
    if len(matches) < 3:
        return empty
    edge = median(lines[i][-1].x1 for i, _ in matches)
    matches = [(i, match) for i, match in matches if abs(lines[i][-1].x1 - edge) <= 12]
    if len(matches) < 3 or edge < ltpage.width * 0.65:
        return empty

    # Restrict detection to the consecutive list surrounding the leaders.
    first = matches[0][0]
    while first > 0:
        previous = lines[first - 1]
        if _HEADING.fullmatch(texts[first - 1]):
            break
        gap = min(char.y0 for char in previous) - max(char.y1 for char in lines[first])
        if gap > median(char.size for char in lines[first]) * 1.5:
            break
        first -= 1
    wrapped = {
        index + 1: index
        for index in range(first, len(lines) - 1)
        if _LEADER_ONLY.search(texts[index])
        and _PAGE_NUMBER.fullmatch(texts[index + 1])
        and min(char.y0 for char in lines[index])
        - max(char.y1 for char in lines[index + 1])
        < 12
    }
    last = max([matches[-1][0], *wrapped])
    endings = {i: match for i, match in matches}
    entries: list[TocEntry] = []
    pending: list[LTChar] = []
    for index in range(first, last + 1):
        line = lines[index]
        text = _text(line)
        match = endings.get(index)
        # A long label may leave no room for dots, but its page still shares
        # the column established by at least three unambiguous dotted rows.
        number_match = _PAGE_NUMBER.search(text)
        no_leader_end = (
            match is None
            and number_match is not None
            and abs(line[-1].x1 - edge) <= 12
            and (pending or _ENTRY_START.match(text.lstrip()))
        )
        wrapped_number = index in wrapped
        if wrapped_number:
            no_leader_end = True
        if match is None and not no_leader_end:
            pending.extend(line)
            continue
        start = match.start() if match is not None else number_match.start()
        number_start = match.start(1) if match is not None else number_match.start()
        # LTChar is normally one Unicode character; fail closed for ligature
        # expansion rather than misassigning a source glyph to a page number.
        offsets = []
        cursor = 0
        for char in line:
            offsets.append(cursor)
            cursor += len(char.get_text())
        label = pending + [
            char for char, offset in zip(line, offsets) if offset < start
        ]
        leaders = [
            char
            for char, offset in zip(line, offsets)
            if start <= offset < number_start
        ]
        number = [char for char, offset in zip(line, offsets) if offset >= number_start]
        if wrapped_number:
            while label and (
                label[-1].get_text().isspace()
                or label[-1].get_text() in ".\u2026\u00b7\u2024"
            ):
                leaders.insert(0, label.pop())
        pending = []
        if not label or not number:
            return empty
        size = median(char.size for char in label)
        entries.append(
            TocEntry(
                label,
                leaders,
                number,
                min(char.x0 for char in label),
                edge
                - (max(char.x1 for char in number) - min(char.x0 for char in number))
                - size * 0.7,
                min(char.y0 for char in label + number),
                max(char.y1 for char in label + number),
                edge,
            )
        )
    # Trailing unfinished entries on a following page stay in ordinary text;
    # complete entries above them still retain their own rows.
    return TocLayout(
        entries,
        {
            id(char): index
            for index, entry in enumerate(entries)
            for char in entry.chars
        },
        {id(char) for entry in entries for char in entry.leaders},
        {id(char) for entry in entries for char in entry.page_number},
    )


def toc_leader_op(
    entry: TocEntry, text_end: float, last_baseline: float, size: float
) -> str:
    """Fill unused row width with dots, keeping the destination column fixed."""
    if not entry.leaders:
        return ""
    width = max(char.x1 for char in entry.page_number) - min(
        char.x0 for char in entry.page_number
    )
    right = entry.page_right - width - size * 0.25
    start = text_end + size * 0.25
    # In a wrapped source entry the original number is on its final source
    # baseline.  Only draw a horizontal leader when the translated last line
    # actually shares that baseline; otherwise aligned numbers remain clear.
    number_baseline = median(char.y0 for char in entry.page_number)
    if right - start < size or abs(last_baseline - number_baseline) > size * 0.4:
        return ""
    return (
        f"ET q 0 G 1 J {max(0.4, size * 0.06):f} w "
        f"[0 {size * 0.25:f}] 0 d {start:f} {last_baseline + size * 0.12:f} m "
        f"{right:f} {last_baseline + size * 0.12:f} l S Q BT "
    )
