from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pdf2zh.translation_policy import (
    find_reference_markers,
    looks_like_affiliation,
    looks_like_author_list,
    reference_entry_score,
    split_numbered_reference_region,
)

_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_FIRST_ALPHA_RE = re.compile(r"[A-Za-z]")
_SENTENCE_END_RE = re.compile(r"[.!?](?=(?:[\"'\u2019\u201d)\]]|\s|$))")
_TERMINAL_RE = re.compile(
    r"[.!?](?:[\"'\u2019\u201d)\]]|\s|\{\s*v\d+\s*\})*$",
    re.IGNORECASE,
)
_DANGLING_CONTINUATION_CUE_RE = re.compile(
    r"(?:"
    r"\b(?:a|an|the|and|or|of|to|from|for|with|without|via|into|"
    r"through|using|including)"
    r"|\b(?:according|due|owing|referred)\s+to"
    r"|\b(?:based|dependent)\s+on"
    r"|\b(?:defined|determined|given|obtained|calculated|computed)\s+by"
    r"|\b(?:known|defined|denoted|referred)\s+as"
    r")\s*$",
    re.IGNORECASE,
)
_REFERENCE_INTERNAL_TOKEN_RE = re.compile(
    r"\{\s*v[\d\s]+\s*\}|" r"\[\[PDF2ZH_(?:FLOW|ITALIC|REF)(?:_[^\]]*)?\]\]",
    re.IGNORECASE,
)
_REFERENCE_YEAR_END_RE = re.compile(
    r"(?:\((?:19|20)\d{2}[a-z]?\)|(?:19|20)\d{2}[a-z]?)[.,;:]?\s*$",
    re.IGNORECASE,
)
_REFERENCE_VOLUME_PAGE_END_RE = re.compile(
    r"\b\d{1,4}\s*[,;:]\s*"
    r"(?:[A-Za-z]?\d{2,}(?:\s*[-\u2013\u2014]\s*\d+)?|e\d+)\.?\s*$",
    re.IGNORECASE,
)
_REFERENCE_AUTHOR_NAME_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Z]\.(?:\s*)?){1,4}"
    r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+|"
    r"(?<![A-Za-z])[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]{2,},\s*"
    r"(?:[A-Z]\.(?:\s*)?){1,4}"
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


@dataclass(frozen=True)
class ReferenceContinuationGroup:
    """One bibliography entry split across a physical page boundary.

    ``fragments[0]`` includes the source reference marker so the structured
    reference translator can see the complete entry.  ``left_marker_end`` lets
    the converter preserve that marker outside a later layout override, keeping
    the document-level reference-number state intact.  The right fragment stops
    immediately before the following entry marker.
    """

    reference_number: int
    reference_prefix: str
    fragments: tuple[FragmentSpan, FragmentSpan]
    left_marker_end: int
    next_marker_start: int


@dataclass(frozen=True)
class _ReferenceTail:
    segment: FlowSegment
    span: FragmentSpan
    marker_end: int
    number: int
    prefix: str


@dataclass(frozen=True)
class _ReferenceHead:
    segment: FlowSegment
    span: FragmentSpan
    number: int
    prefix: str


def _looks_like_body_prose(segment: FlowSegment) -> bool:
    text = segment.text.strip()
    if segment.region_kind != "plain text" or len(text) < 8:
        return False
    if len(_ENGLISH_WORD_RE.findall(text)) < 2:
        return False
    if _looks_like_reference_segment(text):
        return False
    if looks_like_author_list(text) or looks_like_affiliation(text):
        return False
    return True


def _looks_like_reference_segment(text: str) -> bool:
    """Reject bibliography prose only when its structure supports the score.

    ``reference_entry_score`` deliberately combines several weak signals.  That
    is useful for bibliography routing, but a score of three is not sufficient
    by itself at a reading-flow boundary.  Ordinary prose can contain both
    several commas and a section reference followed by a capitalized sentence
    (for example, ``Section V-B. When ...``); the latter resembles an author
    initial and surname to the scoring regex.

    Keep the conservative exclusion for a leading numbered reference, a strong
    score, or a scored fragment with a bibliography-style terminal.  This
    preserves the independent reference-continuation path while allowing
    ordinary multi-sentence body text to participate in column/page flow.
    """

    compact = text.strip()
    markers = find_reference_markers(compact)
    if any(not compact[: marker.start].strip() for marker in markers):
        return True
    score = reference_entry_score(compact)
    if score >= 4:
        return True
    return score >= 3 and (
        _has_reference_terminal(compact)
        or _has_incomplete_reference_author_structure(compact)
    )


def _has_incomplete_reference_author_structure(text: str) -> bool:
    """Recognize an unnumbered citation tail without mistaking body prose.

    A reference marker can be emitted as its own PDF segment, leaving the
    following author/title fragment apparently unnumbered and unfinished.  Two
    author-name shapes are strong evidence; one is accepted only at the start
    of a comma-rich fragment.  The latter deliberately does not match the
    ``Section V-B. When ...`` body case that motivated the score-three
    relaxation because its lone initial/surname-like token is embedded later.
    """

    matches = list(_REFERENCE_AUTHOR_NAME_RE.finditer(text))
    if len(matches) >= 2:
        return True
    return bool(
        matches and not text[: matches[0].start()].strip() and text.count(",") >= 2
    )


def _column_index(segment: FlowSegment) -> int | None:
    if segment.page_width <= 0 or segment.width >= segment.page_width * 0.58:
        return None
    return 0 if segment.center_x < segment.page_width / 2 else 1


def _has_reference_terminal(text: str) -> bool:
    compact = text.rstrip()
    return bool(
        _REFERENCE_YEAR_END_RE.search(compact)
        or _REFERENCE_VOLUME_PAGE_END_RE.search(compact)
    )


def _has_unsafe_reference_token(text: str) -> bool:
    return _REFERENCE_INTERNAL_TOKEN_RE.search(text) is not None


def _reference_tail(segment: FlowSegment) -> _ReferenceTail | None:
    if segment.region_kind != "plain text":
        return None
    region = split_numbered_reference_region(segment.text)
    if region is None or segment.text[region.end :].strip():
        return None
    marker = region.markers[-1]
    if marker.number is None:
        return None
    span = FragmentSpan(segment.ref, marker.start, region.end)
    candidate = segment.text[span.start : span.end]
    if _has_unsafe_reference_token(candidate) or _has_reference_terminal(candidate):
        return None
    return _ReferenceTail(
        segment=segment,
        span=span,
        marker_end=marker.end,
        number=marker.number,
        prefix=marker.prefix,
    )


def _reference_head(segment: FlowSegment) -> _ReferenceHead | None:
    if segment.region_kind != "plain text":
        return None
    markers = find_reference_markers(segment.text)
    if not markers:
        return None
    marker = markers[0]
    if marker.number is None or marker.start <= 0:
        return None
    span = FragmentSpan(segment.ref, 0, marker.start)
    candidate = segment.text[span.start : span.end]
    if (
        not candidate.strip()
        or _has_unsafe_reference_token(candidate)
        or not _has_reference_terminal(candidate)
    ):
        return None
    return _ReferenceHead(
        segment=segment,
        span=span,
        number=marker.number,
        prefix=marker.prefix,
    )


def _reference_geometry_is_compatible(
    left: FlowSegment,
    right: FlowSegment,
) -> bool:
    left_column = _column_index(left)
    right_column = _column_index(right)
    if left_column not in {1, None} or right_column not in {0, None}:
        return False
    largest_size = max(abs(left.size), abs(right.size), 0.01)
    if abs(left.size - right.size) / largest_size > 0.15:
        return False
    largest_width = max(left.width, right.width, 0.01)
    if min(left.width, right.width) / largest_width < 0.72:
        return False
    if left.y0 > left.page_height * 0.28:
        return False
    return right.y1 >= right.page_height * 0.72


def _span_overlaps(
    candidate: FragmentSpan,
    occupied: Sequence[FragmentSpan],
) -> bool:
    return any(
        candidate.ref == existing.ref
        and candidate.start < existing.end
        and existing.start < candidate.end
        for existing in occupied
    )


def _first_alpha_is_lower(text: str) -> bool:
    match = _FIRST_ALPHA_RE.search(text)
    return bool(match and match.group(0).islower())


def _after_leading_balanced_parenthetical(text: str) -> str | None:
    """Return prose following one complete leading parenthetical.

    A physical page may begin with a parenthetical qualifier whose first word is
    a proper name or model identifier, while the sentence itself resumes with a
    lower-case word after the closing delimiter.  Treat only a balanced leading
    ``(...)``/``[...]`` group as skippable; an unmatched delimiter must keep the
    conservative cross-boundary rejection behaviour.
    """

    compact = text.lstrip()
    pairs = {"(": ")", "[": "]", "（": "）", "［": "］"}
    expected = pairs.get(compact[:1])
    if expected is None:
        return None
    stack = [expected]
    for index, character in enumerate(compact[1:], start=1):
        nested = pairs.get(character)
        if nested is not None:
            stack.append(nested)
            continue
        if character != stack[-1]:
            continue
        stack.pop()
        if not stack:
            return compact[index + 1 :]
    return None


def _continuation_start_reason(text: str) -> str | None:
    remainder = _after_leading_balanced_parenthetical(text)
    if remainder is not None:
        if _first_alpha_is_lower(remainder):
            return "lowercase-after-leading-parenthetical"
        return None
    if _first_alpha_is_lower(text):
        return "lowercase-right"
    return None


def _left_has_dangling_continuation_cue(text: str) -> bool:
    """Return whether a page-end fragment grammatically requires a complement.

    Lower-case continuation is normally the safest cross-page signal.  It is
    insufficient when the next fragment starts with an eponym or other proper
    name (for example, ``from the`` + ``Ambegaokar--Baratoff relation``).  A
    narrowly scoped dangling determiner/preposition/verb phrase is independent
    evidence that the following capitalized phrase belongs to the same sentence.
    """

    compact = text.rstrip()
    return bool(compact and _DANGLING_CONTINUATION_CUE_RE.search(compact))


def _ends_sentence(text: str) -> bool:
    return bool(_TERMINAL_RE.search(text.rstrip()))


def _compatible_boundary(
    left: FlowSegment,
    right: FlowSegment,
    kind: Literal["column", "page"],
) -> tuple[bool, tuple[str, ...]]:
    if not (_looks_like_body_prose(left) and _looks_like_body_prose(right)):
        return False, ()
    continuation_start = _continuation_start_reason(right.text)
    if continuation_start is None and _left_has_dangling_continuation_cue(left.text):
        continuation_start = "capitalized-after-dangling-cue"
    if _ends_sentence(left.text) or continuation_start is None:
        return False, ()
    largest_size = max(abs(left.size), abs(right.size), 0.01)
    if abs(left.size - right.size) / largest_size > 0.15:
        return False, ()
    largest_width = max(left.width, right.width, 0.01)
    if min(left.width, right.width) / largest_width < 0.72:
        return False, ()
    if left.y0 > left.page_height * 0.28:
        return False, ()
    reasons = ["unfinished-left", continuation_start, "matching-body-style"]
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
    return sorted(
        selected, key=lambda item: (-item.y1, item.x0, item.ref.segment_index)
    )


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


def detect_reference_continuation(
    previous: Sequence[FlowSegment],
    following: Sequence[FlowSegment],
    *,
    occupied: Sequence[FragmentSpan] = (),
) -> ReferenceContinuationGroup | None:
    """Detect a high-confidence bibliography entry split across two pages.

    This detector is intentionally separate from body-prose continuation.  It
    requires both a consecutive-number signal and bibliography evidence from
    the recombined entry, and fails closed for page-local formula placeholders
    or internal layout tokens.
    """

    if not previous or not following:
        return None
    previous_pages = {segment.ref.page_id for segment in previous}
    following_pages = {segment.ref.page_id for segment in following}
    if len(previous_pages) != 1 or len(following_pages) != 1:
        return None
    previous_page = next(iter(previous_pages))
    following_page = next(iter(following_pages))
    if following_page != previous_page + 1:
        return None

    tails = [
        candidate for segment in previous if (candidate := _reference_tail(segment))
    ]
    heads = [
        candidate for segment in following if (candidate := _reference_head(segment))
    ]
    tails.sort(
        key=lambda item: (
            item.segment.y0,
            0 if _column_index(item.segment) == 1 else 1,
            item.segment.ref.segment_index,
        )
    )
    heads.sort(
        key=lambda item: (
            -item.segment.y1,
            0 if _column_index(item.segment) == 0 else 1,
            item.segment.ref.segment_index,
        )
    )

    for tail in tails:
        left_text = tail.segment.text[tail.span.start : tail.span.end]
        left_score = reference_entry_score(left_text)
        for head in heads:
            if head.prefix != tail.prefix or head.number != tail.number + 1:
                continue
            if not _reference_geometry_is_compatible(tail.segment, head.segment):
                continue
            if _span_overlaps(tail.span, occupied) or _span_overlaps(
                head.span, occupied
            ):
                continue
            right_text = head.segment.text[head.span.start : head.span.end]
            combined_score = reference_entry_score(
                f"{left_text.rstrip()} {right_text.lstrip()}"
            )
            if combined_score < 5 or combined_score <= left_score:
                continue
            return ReferenceContinuationGroup(
                reference_number=tail.number,
                reference_prefix=tail.prefix,
                fragments=(tail.span, head.span),
                left_marker_end=tail.marker_end,
                next_marker_start=head.span.end,
            )
    return None


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
