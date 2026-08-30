from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from pdf2zh.translation_policy import (
    looks_like_affiliation,
    looks_like_author_list,
    reference_entry_score,
)


_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_FIRST_ALPHA_RE = re.compile(r"[A-Za-z]")
_SENTENCE_END_RE = re.compile(r"[.!?](?=(?:[\"'\u2019\u201d)\]]|\s|$))")
_TERMINAL_RE = re.compile(
    r"[.!?](?:[\"'\u2019\u201d)\]]|\s|\{\s*v\d+\s*\})*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class SegmentRef:
    page_id: int
    segment_index: int


@dataclass(frozen=True)
class FlowSegment:
    ref: SegmentRef
    text: str
    x0: float
    x1: float
    y0: float
    y1: float
    size: float
    page_width: float
    page_height: float
    region_kind: str = "plain text"

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class ContinuationEdge:
    left: SegmentRef
    right: SegmentRef
    kind: Literal["column", "page"]
    join_mode: Literal["normal", "line_hyphen_candidate"]
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class FragmentSpan:
    ref: SegmentRef
    start: int
    end: int


@dataclass(frozen=True)
class ContinuationGroup:
    edge: ContinuationEdge
    fragments: tuple[FragmentSpan, FragmentSpan]


def _looks_like_body_prose(segment: FlowSegment) -> bool:
    text = segment.text.strip()
    if segment.region_kind != "plain text" or len(text) < 8:
        return False
    if len(_ENGLISH_WORD_RE.findall(text)) < 2:
        return False
    if reference_entry_score(text) >= 3:
        return False
    if looks_like_author_list(text) or looks_like_affiliation(text):
        return False
    return True


def _column_index(segment: FlowSegment) -> int | None:
    if segment.page_width <= 0 or segment.width >= segment.page_width * 0.58:
        return None
    return 0 if segment.center_x < segment.page_width / 2 else 1


def _first_alpha_is_lower(text: str) -> bool:
    match = _FIRST_ALPHA_RE.search(text)
    return bool(match and match.group(0).islower())


def _ends_sentence(text: str) -> bool:
    return bool(_TERMINAL_RE.search(text.rstrip()))


def _compatible_boundary(
    left: FlowSegment,
    right: FlowSegment,
    kind: Literal["column", "page"],
) -> tuple[bool, tuple[str, ...]]:
    if not (_looks_like_body_prose(left) and _looks_like_body_prose(right)):
        return False, ()
    if _ends_sentence(left.text) or not _first_alpha_is_lower(right.text):
        return False, ()
    largest_size = max(abs(left.size), abs(right.size), 0.01)
    if abs(left.size - right.size) / largest_size > 0.15:
        return False, ()
    largest_width = max(left.width, right.width, 0.01)
    if min(left.width, right.width) / largest_width < 0.72:
        return False, ()
    if left.y0 > left.page_height * 0.28:
        return False, ()
    reasons = ["unfinished-left", "lowercase-right", "matching-body-style"]
    if kind == "column":
        if _column_index(left) != 0 or _column_index(right) != 1:
            return False, ()
        if right.y1 < right.page_height * 0.55:
            return False, ()
        reasons.append("column-boundary")
    else:
        reasons.append("physical-page-boundary")
    return True, tuple(reasons)


def _ordered_column_segments(
    segments: Sequence[FlowSegment],
    column: int,
) -> list[FlowSegment]:
    selected = [
        segment
        for segment in segments
        if _looks_like_body_prose(segment) and _column_index(segment) == column
    ]
    return sorted(selected, key=lambda item: (-item.y1, item.x0, item.ref.segment_index))


def _ordered_spanning_segments(
    segments: Sequence[FlowSegment],
) -> list[FlowSegment]:
    selected = [
        segment
        for segment in segments
        if _looks_like_body_prose(segment) and _column_index(segment) is None
    ]
    return sorted(
        selected,
        key=lambda item: (-item.y1, item.x0, item.ref.segment_index),
    )


def _page_boundary_body_segment(
    segments: Sequence[FlowSegment],
    *,
    boundary: Literal["head", "tail"],
) -> FlowSegment | None:
    """Return the first/last body segment in visual reading order.

    Two-column pages normally start in the left column and finish in the right.
    A wide prose block is selected only when it physically precedes the first
    column or follows the last one.  This also covers ordinary single-column
    journal pages without weakening the cross-column detector.
    """
    left = _ordered_column_segments(segments, 0)
    right = _ordered_column_segments(segments, 1)
    spanning = _ordered_spanning_segments(segments)
    if boundary == "head":
        candidate = left[0] if left else (right[0] if right else None)
        wide = spanning[0] if spanning else None
        if candidate is None:
            return wide
        if wide is not None and wide.y1 >= candidate.y1:
            return wide
        return candidate
    candidate = right[-1] if right else (left[-1] if left else None)
    wide = spanning[-1] if spanning else None
    if candidate is None:
        return wide
    if wide is not None and wide.y0 <= candidate.y0:
        return wide
    return candidate


def detect_page_edges(segments: Sequence[FlowSegment]) -> list[ContinuationEdge]:
    """Detect only high-confidence left-column to right-column continuations."""
    left_column = _ordered_column_segments(segments, 0)
    right_column = _ordered_column_segments(segments, 1)
    if not left_column or not right_column:
        return []
    left = left_column[-1]
    right = right_column[0]
    accepted, reasons = _compatible_boundary(left, right, "column")
    if not accepted:
        return []
    hyphenated = left.text.rstrip().endswith("-")
    return [
        ContinuationEdge(
            left.ref,
            right.ref,
            "column",
            "line_hyphen_candidate" if hyphenated else "normal",
            0.96 if hyphenated else 0.93,
            reasons,
        )
    ]


def detect_cross_page_edge(
    previous: Sequence[FlowSegment],
    following: Sequence[FlowSegment],
) -> ContinuationEdge | None:
    """Detect a continuation across consecutive physical pages.

    Floats and captions are absent because only ``plain text`` regions enter the
    candidate columns.  This lets a body paragraph resume below a top-of-page
    figure, as in the Yan regression document.
    """
    if not previous or not following:
        return None
    previous_page = previous[0].ref.page_id
    following_page = following[0].ref.page_id
    if following_page != previous_page + 1:
        return None

    tail = _page_boundary_body_segment(previous, boundary="tail")
    head = _page_boundary_body_segment(following, boundary="head")
    if tail is None or head is None:
        return None
    accepted, reasons = _compatible_boundary(tail, head, "page")
    if not accepted:
        return None
    hyphenated = tail.text.rstrip().endswith("-")
    return ContinuationEdge(
        tail.ref,
        head.ref,
        "page",
        "line_hyphen_candidate" if hyphenated else "normal",
        0.97 if hyphenated else 0.94,
        reasons,
    )


def _tail_span(text: str, maximum_chars: int) -> tuple[int, int] | None:
    matches = list(_SENTENCE_END_RE.finditer(text))
    start = matches[-1].end() if matches else 0
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or len(text) - start > maximum_chars:
        return None
    return start, len(text)


def _head_span(text: str, maximum_chars: int) -> tuple[int, int] | None:
    match = _SENTENCE_END_RE.search(text)
    end = match.end() if match else len(text)
    while end < len(text) and text[end] in "\"'\u2019\u201d)]":
        end += 1
    if end <= 0 or end > maximum_chars:
        return None
    return 0, end


def build_continuation_groups(
    edges: Sequence[ContinuationEdge],
    texts: Mapping[SegmentRef, str],
    *,
    occupied: Sequence[FragmentSpan] = (),
    maximum_fragment_chars: int = 1600,
) -> list[ContinuationGroup]:
    """Turn edges into non-overlapping boundary-sentence translation groups.

    If an unusual document makes two boundary sentences overlap inside one layout
    block, the later edge is deliberately skipped.  This is the safe alternative
    to translating the same source span twice.
    """
    used = list(occupied)
    groups: list[ContinuationGroup] = []
    for edge in edges:
        left_text = texts.get(edge.left)
        right_text = texts.get(edge.right)
        if left_text is None or right_text is None:
            continue
        left_range = _tail_span(left_text, maximum_fragment_chars)
        right_range = _head_span(right_text, maximum_fragment_chars)
        if left_range is None or right_range is None:
            continue
        fragments = (
            FragmentSpan(edge.left, *left_range),
            FragmentSpan(edge.right, *right_range),
        )
        if any(
            candidate.ref == existing.ref
            and candidate.start < existing.end
            and existing.start < candidate.end
            for candidate in fragments
            for existing in used
        ):
            continue
        used.extend(fragments)
        groups.append(ContinuationGroup(edge, fragments))
    return groups
