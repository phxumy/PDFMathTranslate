from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


ROLE_TRANSLATE = "translate"
ROLE_PRESERVE = "preserve"
ROLE_REFERENCE = "reference"
ROLE_AFFILIATION = "affiliation"

_TRANSLATABLE_CAPTION_REGION_KINDS = frozenset(
    {"figure_caption", "table_caption", "table_footnote"}
)


@dataclass(frozen=True)
class SourceSegment:
    index: int
    text: str
    x0: float = 0.0
    x1: float = 0.0
    y0: float = 0.0
    y1: float = 0.0
    size: float = 0.0
    page_width: float = 0.0
    break_offsets: tuple[int, ...] = ()
    region_kind: str = ""


@dataclass(frozen=True)
class ReferenceMarker:
    start: int
    end: int
    label: str
    number: int | None
    prefix: str
    kind: str
    raw: str


@dataclass(frozen=True)
class ReferenceRegion:
    start: int
    end: int
    markers: tuple[ReferenceMarker, ...]


@dataclass(frozen=True)
class SegmentPart:
    role: str
    text: str
    break_before: bool = False


@dataclass(frozen=True)
class SegmentPlan:
    source: SourceSegment
    parts: tuple[SegmentPart, ...]


@dataclass(frozen=True)
class ExactReplacement:
    source: str
    translated: str


_FORMULA_RE = re.compile(r"\{\s*v([\d\s]+)\s*\}", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{\s*v[\d\s]+\s*\}", re.IGNORECASE)
_REFERENCE_HEADING_RE = re.compile(
    r"\s*(?:references(?:\s+and\s+notes)?|methods\s+references|"
    r"bibliography|literature\s+cited)\s*[:.]?\s*",
    re.IGNORECASE,
)
_PUBLICATION_BADGE_PREFIX_RE = re.compile(
    r"^(\s*(?:(?:ARTICLE\s+OPEN(?:\s+ACCESS)?|OPEN\s+ACCESS|"
    r"RESEARCH\s+ARTICLE|ORIGINAL\s+ARTICLE|REVIEW\s+ARTICLE|"
    r"BRIEF\s+COMMUNICATION|PERSPECTIVE|EDITORIAL)\s+))(?=\S)",
    re.IGNORECASE,
)
_SECTION_STOP_RE = re.compile(
    r"\s*(?:methods|acknowledg(?:e)?ments?|author\s+contributions?|"
    r"competing\s+interests?|data\s+availability|code\s+availability|"
    r"additional\s+information|publisher[’']s\s+note|"
    r"supplementary\s+(?:information|materials))\b",
    re.IGNORECASE,
)
_INLINE_STOP_RE = re.compile(
    r"(?<=[.!?])\s+(?=(?:methods|acknowledg(?:e)?ments?|"
    r"author\s+contributions?|competing\s+interests?|data\s+availability|"
    r"code\s+availability|additional\s+information|publisher[’']s\s+note|"
    r"supplementary\s+(?:information|materials))\b)",
    re.IGNORECASE,
)
_BIOGRAPHY_RE = re.compile(
    r"\s+(?=[A-Z][A-Za-zÀ-ÖØ-öø-ÿ.'’\- ]{2,100}\breceived\s+the\b)",
    re.IGNORECASE,
)
_AFFILIATION_RE = re.compile(
    r"\b(?:Department|School|Faculty|Institute|Institut|Laboratory|"
    r"Laboratoire|Center|Centre|College|University|Universit[ée]|"
    r"Division|Research\s+Center|Research\s+Centre|National\s+Laboratory|"
    r"Google\s+Research|IBM\s+Quantum|Microsoft\s+Quantum|"
    r"Quantum\s+AI|Academy\s+of\s+Sciences)\b",
    re.IGNORECASE,
)
_AFFILIATION_NOTE_RE = re.compile(
    r"\b(?:These\s+authors\s+contributed\s+equally|Equal\s+contribution|"
    r"Corresponding\s+authors?|Correspondence|Present\s+address|"
    r"Current\s+address|Also\s+at|Electronic\s+mail|E-?mail)\b",
    re.IGNORECASE,
)
_AUTHOR_PROSE_RE = re.compile(
    r"\b(?:we|this|these|that|show|shows|report|reports|demonstrate|"
    r"demonstrates|thank|thanks|conceived|performed|wrote|contributed|"
    r"developed|measured|analysed|analyzed|investigated|received\s+the)\b",
    re.IGNORECASE,
)
_AUTHOR_NAME_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z])(?:[A-Z]\.(?:\s*)?){1,4}"
        r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+"
    ),
    re.compile(
        r"(?<![A-Za-z])[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]{1,}"
        r"\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]{1,}"
    ),
    re.compile(
        r"(?<![A-Za-z])[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]{1,},\s*"
        r"(?:[A-Z]\.(?:\s*)?){1,4}"
    ),
)


def _compact_digits(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _formula_marker_number(
    match: re.Match[str], formula_texts: Sequence[str]
) -> int | None:
    try:
        formula_index = int(_compact_digits(match.group(1)))
        formula_text = formula_texts[formula_index]
    except (IndexError, TypeError, ValueError):
        return None
    normalized = _compact_digits(formula_text)
    if not normalized.isdecimal():
        return None
    return int(normalized)


def _numbered_marker(
    match: re.Match[str], kind: str, prefix: str = ""
) -> ReferenceMarker:
    number = int(_compact_digits(match.group(1)))
    return ReferenceMarker(
        match.start(),
        match.end(),
        f"{prefix}{number}",
        number,
        prefix,
        kind,
        match.group(0),
    )


def find_reference_markers(
    text: str, formula_texts: Sequence[str] = ()
) -> tuple[ReferenceMarker, ...]:
    """Find common bibliography markers without deciding that text is a bibliography."""
    markers: list[ReferenceMarker] = []

    numbered_patterns = (
        (r"[\[［]\s*(\d(?:\s*\d){0,2})\s*[\]］]", "bracket"),
        (
            r"[\[［]\s*[Ss]\s*(\d(?:\s*\d){0,2})\s*[\]］]",
            "supplement-bracket",
        ),
        (
            r"(?<![\dA-Za-z])[(（]?\s*[Ss]\s*(\d(?:\s*\d){0,2})"
            r"\s*[.．。)）](?=\s)",
            "supplement",
        ),
        (
            r"(?<![\dA-Za-z])[(（]\s*(\d(?:\s*\d){0,2})\s*[)）]"
            r"(?=\s*[A-Z])",
            "parenthetical",
        ),
        (
            r"(?<![\dA-Za-z(（])(\d(?:\s*\d){0,2})\s*[)）]"
            r"(?=\s*[A-Z])",
            "closing",
        ),
        (
            r"(?<!\d)(\d(?:\s*\d){0,2})\s*[.．。](?=\s)",
            "dotted",
        ),
        (
            r"(?<![\dA-Za-z])(\d(?:\s*\d){0,2})"
            r"(?=(?:[A-Z]\.\s*){1,4}[A-Z])",
            "tight-initial",
        ),
        (
            r"(?<![\dA-Za-z])(\d(?:\s*\d){0,2})"
            r"(?=[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]{2,}[,\s])",
            "tight-surname",
        ),
    )
    for pattern, kind in numbered_patterns:
        prefix = "S" if kind.startswith("supplement") else ""
        for match in re.finditer(pattern, text):
            markers.append(_numbered_marker(match, kind, prefix))

    for match in _FORMULA_RE.finditer(text):
        number = _formula_marker_number(match, formula_texts)
        if number is None:
            continue
        # A superscript affiliation marker follows an author name, whereas a
        # numbered bibliography marker starts a new entry.  Rejecting formula
        # placeholders attached to a word prevents author bylines such as
        # ``A. Smith{v1}, B. Jones{v2}`` from being split as references.
        prefix = text[: match.start()]
        if re.search(
            r"[0-9A-Za-zÀ-ÖØ-öø-ÿ'’\-]\s*$",
            prefix,
        ) and _REFERENCE_HEADING_RE.fullmatch(prefix) is None:
            continue
        markers.append(
            ReferenceMarker(
                match.start(),
                match.end(),
                str(number),
                number,
                "",
                "formula",
                match.group(0),
            )
        )

    priority = {
        "bracket": 8,
        "supplement-bracket": 8,
        "supplement": 7,
        "dotted": 6,
        "parenthetical": 5,
        "closing": 4,
        "tight-initial": 3,
        "tight-surname": 2,
        "formula": 1,
    }
    selected: list[ReferenceMarker] = []
    for marker in sorted(
        markers, key=lambda item: (item.start, -priority.get(item.kind, 0), item.end)
    ):
        overlap_index = next(
            (
                index
                for index, existing in enumerate(selected)
                if marker.start < existing.end and existing.start < marker.end
            ),
            None,
        )
        if overlap_index is None:
            selected.append(marker)
            continue
        if priority.get(marker.kind, 0) > priority.get(
            selected[overlap_index].kind, 0
        ):
            selected[overlap_index] = marker
    return tuple(sorted(selected, key=lambda item: (item.start, item.end)))


def reference_entry_score(text: str) -> int:
    """Return a conservative bibliography-likeness score for one source entry."""
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return 0

    score = 0
    author_patterns = (
        r"(?:[A-Z]\.(?:\s*)?){1,4}[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+",
        r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]{2,},\s*(?:[A-Z]\.(?:\s*)?){1,4}",
        r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]{2,}\s+(?:[A-Z]\s*){1,3}"
        r"(?:et\s*(?:\{\s*v\d+\s*\})?\s*al\.?|and\b)",
    )
    if any(re.search(pattern, compact) for pattern in author_patterns):
        score += 2
    if re.search(r"(?:19|20)\d{2}", compact):
        score += 1
    if re.search(
        r"\b(?:doi|arxiv|preprint|vol\.?|no\.?|pp?\.?|"
        r"nature|science|physical\s+review|phys\.?\s*rev|"
        r"ieee|journal|proceedings|proc\.?|springer|wiley|elsevier|"
        r"university\s+press|rev\.?\s*mod|quantum|appl\.?\s*phys)\b",
        compact,
        re.IGNORECASE,
    ):
        score += 1
    if re.search(r"https?://|www\.|10\.\d{4,9}/", compact, re.IGNORECASE):
        score += 1
    if re.search(r"\b\d{1,4}\s*,\s*(?:[A-Za-z]?\d{2,}|\d+[-–]\d+)\b", compact):
        score += 1
    if compact.count(",") >= 2:
        score += 1
    return score


def _sequence_from(
    markers: Sequence[ReferenceMarker], start_index: int
) -> tuple[ReferenceMarker, ...]:
    first = markers[start_index]
    if first.number is None:
        return ()
    sequence = [first]
    expected = first.number + 1
    for marker in markers[start_index + 1 :]:
        if marker.prefix != first.prefix or marker.number is None:
            continue
        if marker.number == expected:
            sequence.append(marker)
            expected += 1
    return tuple(sequence)


def _entry_texts(
    text: str, markers: Sequence[ReferenceMarker], end: int
) -> tuple[str, ...]:
    entries = []
    for index, marker in enumerate(markers):
        next_start = markers[index + 1].start if index + 1 < len(markers) else end
        entries.append(text[marker.start:next_start])
    return tuple(entries)


def _reference_sequence_is_confident(
    text: str,
    markers: Sequence[ReferenceMarker],
    heading_hint: bool,
    expected_number: int | None,
    expected_prefix: str,
    end: int,
) -> bool:
    if not markers:
        return False
    entries = _entry_texts(text, markers, end)
    scores = [reference_entry_score(entry) for entry in entries]
    strong_entries = sum(score >= 3 for score in scores)
    first = markers[0]
    expected_match = (
        expected_number is not None
        and first.number == expected_number
        and first.prefix == expected_prefix
    )
    starts_entry = not text[: first.start].strip()

    if first.kind in {"parenthetical", "closing"}:
        if len(markers) >= 3:
            return strong_entries >= 2
        if len(markers) == 2:
            return strong_entries == 2 and (heading_hint or expected_match)
        return scores[0] >= 5 and (heading_hint or expected_match)
    if first.kind == "formula":
        if len(markers) >= 3:
            return strong_entries >= 2
        if len(markers) == 2:
            return strong_entries == 2
        return scores[0] >= 5 and (
            heading_hint or expected_match or starts_entry
        )
    if len(markers) >= 3 and strong_entries >= 2:
        return True
    if len(markers) == 2 and strong_entries == 2:
        return True
    if len(markers) == 1:
        return scores[0] >= 4 and (heading_hint or expected_match or starts_entry)
    return heading_hint and strong_entries >= 1


def _reference_region_end(text: str, last_marker: ReferenceMarker) -> int:
    candidates = []
    for pattern in (_INLINE_STOP_RE, _BIOGRAPHY_RE):
        match = pattern.search(text, last_marker.end)
        if match:
            candidates.append(match.start())
    return min(candidates) if candidates else len(text)


def split_numbered_reference_region(
    text: str,
    formula_texts: Sequence[str] = (),
    expected_number: int | None = None,
    heading_hint: bool = False,
    expected_prefix: str = "",
) -> ReferenceRegion | None:
    markers = find_reference_markers(text, formula_texts)
    if not markers:
        return None

    best: tuple[ReferenceMarker, ...] = ()
    best_end = len(text)
    best_score: tuple[int, int, int, int] = (-1, -1, -1, -1)
    for index in range(len(markers)):
        sequence = _sequence_from(markers, index)
        if not sequence:
            continue
        end = _reference_region_end(text, sequence[-1])
        entries = _entry_texts(text, sequence, end)
        entry_scores = [reference_entry_score(entry) for entry in entries]
        citation_score = sum(entry_scores)
        strong_entries = sum(score >= 3 for score in entry_scores)
        expected_bonus = int(
            expected_number is not None
            and sequence[0].number == expected_number
            and sequence[0].prefix == expected_prefix
        )
        if not _reference_sequence_is_confident(
            text,
            sequence,
            heading_hint=heading_hint,
            expected_number=expected_number,
            expected_prefix=expected_prefix,
            end=end,
        ):
            continue
        # Prefer the expected continuation, then actual citation evidence.  A
        # longer but weak numbered Methods list must not hide a shorter real
        # bibliography sequence later in the same layout block.
        score = (expected_bonus, strong_entries, len(sequence), citation_score)
        if score > best_score:
            best = sequence
            best_end = end
            best_score = score

    if not best:
        return None
    return ReferenceRegion(best[0].start, best_end, best)


def _count_author_names(text: str) -> int:
    spans: set[tuple[int, int]] = set()
    for pattern in _AUTHOR_NAME_PATTERNS:
        for match in pattern.finditer(text):
            spans.add((match.start(), match.end()))
    return len(spans)


def _author_name_coverage(text: str) -> float:
    """Return the fraction of alphabetic text covered by author-name patterns.

    Capitalized prose pairs such as ``The Hamiltonian`` or ``Supplementary
    Section`` also resemble names.  Requiring names to occupy most of a candidate
    byline keeps long captions and continued body paragraphs out of the front-
    matter preservation path without imposing an arbitrary maximum author count.
    """

    spans = [
        (match.start(), match.end())
        for pattern in _AUTHOR_NAME_PATTERNS
        for match in pattern.finditer(text)
    ]
    alphabetic = {index for index, char in enumerate(text) if char.isalpha()}
    if not alphabetic or not spans:
        return 0.0
    covered = {
        index
        for start, end in spans
        for index in range(start, end)
        if index in alphabetic
    }
    return len(covered) / len(alphabetic)


def looks_like_author_list(text: str) -> bool:
    compact = re.sub(r"\{\s*v[\d\s]+\s*\}", "", text, flags=re.IGNORECASE)
    compact = re.sub(r"\s+", " ", compact).strip()
    if len(compact) < 8 or _AUTHOR_PROSE_RE.search(compact):
        return False
    if re.search(r"(?:19|20)\d{2}|https?://|\bdoi\b", compact, re.IGNORECASE):
        return False
    return (
        _count_author_names(compact) >= 2
        and _author_name_coverage(compact) >= 0.55
        and bool(re.search(r",|\band\b|&", compact))
    )


def _affiliation_boundary(text: str, keyword_start: int) -> int:
    prefix = text[:keyword_start]
    marker = re.search(
        r"(?:\{\s*v[\d\s]+\s*\}|(?<!\w)\d{1,2}|(?<!\w)[a-z]\))\s*$",
        prefix,
        re.IGNORECASE,
    )
    return marker.start() if marker else keyword_start


def split_author_affiliation(text: str) -> tuple[str, str] | None:
    for match in _AFFILIATION_RE.finditer(text):
        boundary = _affiliation_boundary(text, match.start())
        author_text = text[:boundary]
        affiliation_text = text[boundary:]
        if looks_like_author_list(author_text) and affiliation_text:
            return author_text, affiliation_text
    return None


def split_named_affiliation_sentence(text: str) -> tuple[str, str] | None:
    match = re.match(r"^(?P<names>.+?)(?P<rest>\s+(?:is|are)\s+with\b.+)$", text)
    if not match:
        return None
    names = match.group("names")
    if _count_author_names(names) < 1:
        return None
    return names, match.group("rest")


def looks_like_affiliation(text: str) -> bool:
    return bool(_AFFILIATION_RE.search(text)) and not _AUTHOR_PROSE_RE.search(text)


def _affiliation_markers(
    text: str, formula_texts: Sequence[str]
) -> tuple[tuple[int, int, str], ...]:
    markers: list[tuple[int, int, str]] = []
    for match in _FORMULA_RE.finditer(text):
        number = _formula_marker_number(match, formula_texts)
        if number is None:
            continue
        following = text[match.end() : match.end() + 80]
        if _AFFILIATION_RE.search(following) or _AFFILIATION_NOTE_RE.search(
            following
        ):
            markers.append((match.start(), match.end(), match.group(0)))
    plain_pattern = re.compile(
        r"(?<![\w.])(\d{1,2}|[a-z]\))(?=\s*(?:Department|School|Faculty|"
        r"Institute|Institut|Laboratory|Laboratoire|Center|Centre|College|"
        r"University|Universit[ée]|Division|Google\s+Research|"
        r"These\s+authors\s+contributed\s+equally|Equal\s+contribution|"
        r"Corresponding\s+authors?|Correspondence|Present\s+address|"
        r"Current\s+address|Also\s+at|Electronic\s+mail|E-?mail))",
        re.IGNORECASE,
    )
    for match in plain_pattern.finditer(text):
        markers.append((match.start(), match.end(), match.group(0)))
    markers.sort(key=lambda item: item[0])
    return tuple(markers)


def restore_affiliation_breaks(
    source_text: str, translated_text: str, formula_texts: Sequence[str] = ()
) -> str:
    source_markers = _affiliation_markers(source_text, formula_texts)
    if len(source_markers) < 2:
        return translated_text
    positions: list[int] = []
    search_from = 0
    for _, _, raw in source_markers:
        position = translated_text.find(raw, search_from)
        if position < 0:
            return translated_text
        positions.append(position)
        search_from = position + len(raw)
    result = translated_text
    for position in reversed(positions[1:]):
        before = result[:position].rstrip(" \t\u00a0")
        after = result[position:].lstrip(" \t\u00a0")
        result = before + "\n" + after
    return result


def formula_cache_signature(text: str, formula_texts: Sequence[str]) -> str:
    values = []
    for match in _FORMULA_RE.finditer(text):
        try:
            index = int(_compact_digits(match.group(1)))
            value = formula_texts[index]
        except (IndexError, TypeError, ValueError):
            value = "<missing>"
        values.append(f"{match.group(0)}={value}")
    return "|".join(values)


def apply_exact_replacements(
    source: str, replacements: Sequence[ExactReplacement]
) -> str | None:
    """Apply verified, non-overlapping exact replacements or fail safely."""
    located: list[tuple[int, int, str]] = []
    for replacement in replacements:
        old = replacement.source
        new = replacement.translated
        if not old.strip() or not new.strip() or source.count(old) != 1:
            return None
        if not re.search(r"[A-Za-z]", old):
            return None
        if re.search(r"https?://|\bdoi\b|10\.\d{4,9}/", old, re.IGNORECASE):
            return None
        if sorted(_PLACEHOLDER_RE.findall(old)) != sorted(
            _PLACEHOLDER_RE.findall(new)
        ):
            return None
        start = source.index(old)
        located.append((start, start + len(old), new))
    located.sort(key=lambda item: item[0])
    if any(left[1] > right[0] for left, right in zip(located, located[1:])):
        return None
    result = source
    for start, end, translated in reversed(located):
        result = result[:start] + translated + result[end:]
    return result


class DocumentTranslationPolicy:
    """Plan translation roles without treating a whole page as references."""

    def __init__(self) -> None:
        self.last_reference_number: int | None = None
        self.last_reference_prefix = ""
        self.pending_reference_heading = 0
        self.pending_named_prose_segments = 0

    @property
    def expected_reference_number(self) -> int | None:
        if self.last_reference_number is None:
            return None
        return self.last_reference_number + 1

    def plan_segment(
        self,
        segment: SourceSegment,
        formula_texts: Sequence[str] = (),
        protect_authors: bool = True,
    ) -> SegmentPlan:
        text = segment.text
        stripped = text.strip()
        if not stripped:
            return SegmentPlan(segment, (SegmentPart(ROLE_PRESERVE, text),))

        # A detector-confirmed caption or table note is semantic prose, not a
        # byline or bibliography block.  Route it directly to translation so
        # incidental Title Case phrases cannot trigger author-name heuristics.
        if segment.region_kind in _TRANSLATABLE_CAPTION_REGION_KINDS:
            return SegmentPlan(segment, (SegmentPart(ROLE_TRANSLATE, text),))

        if segment.region_kind == "title":
            badge = _PUBLICATION_BADGE_PREFIX_RE.match(text)
            badge_text_end = (
                len(badge.group(0).rstrip()) if badge is not None else -1
            )
            badge_has_source_break = badge_text_end in segment.break_offsets or (
                0 <= badge_text_end < len(text)
                and text[badge_text_end] in "\r\n"
            )
            if (
                badge is not None
                and badge.end() < len(text)
                and badge_has_source_break
            ):
                return SegmentPlan(
                    segment,
                    (
                        SegmentPart(ROLE_PRESERVE, badge.group(0)),
                        SegmentPart(
                            ROLE_TRANSLATE,
                            text[badge.end() :],
                            break_before=True,
                        ),
                    ),
                )

        heading = _REFERENCE_HEADING_RE.fullmatch(stripped)
        if heading:
            self.pending_reference_heading = 3
            return SegmentPlan(segment, (SegmentPart(ROLE_TRANSLATE, text),))

        heading_prefix = _REFERENCE_HEADING_RE.match(text)
        heading_hint = self.pending_reference_heading > 0 or heading_prefix is not None

        in_named_prose_section = self.pending_named_prose_segments > 0
        if _SECTION_STOP_RE.match(stripped):
            self.pending_reference_heading = 0
            self.pending_named_prose_segments = 4
            in_named_prose_section = True
        elif self.pending_named_prose_segments:
            self.pending_named_prose_segments -= 1

        region = split_numbered_reference_region(
            text,
            formula_texts=formula_texts,
            expected_number=self.expected_reference_number,
            expected_prefix=self.last_reference_prefix,
            heading_hint=heading_hint,
        )
        if region is not None:
            self.pending_reference_heading = 0
            numbers = [marker.number for marker in region.markers if marker.number is not None]
            if numbers:
                maximum = max(numbers)
                prefix = region.markers[0].prefix
                starts_new_numbering = (
                    heading_hint
                    and self.last_reference_number is not None
                    and numbers[0] <= self.last_reference_number
                )
                if prefix == self.last_reference_prefix and not starts_new_numbering:
                    self.last_reference_number = max(
                        maximum, self.last_reference_number or maximum
                    )
                else:
                    self.last_reference_prefix = prefix
                    self.last_reference_number = maximum
            parts: list[SegmentPart] = []
            prefix = text[: region.start]
            if prefix:
                prefix_role = (
                    ROLE_PRESERVE
                    if reference_entry_score(prefix) >= 3
                    else ROLE_TRANSLATE
                )
                parts.append(SegmentPart(prefix_role, prefix))
            for index, marker in enumerate(region.markers):
                next_start = (
                    region.markers[index + 1].start
                    if index + 1 < len(region.markers)
                    else region.end
                )
                entry = text[marker.start:next_start]
                parts.append(
                    SegmentPart(
                        ROLE_REFERENCE,
                        entry,
                        break_before=index > 0 or bool(prefix.strip()),
                    )
                )
            suffix = text[region.end :]
            if suffix:
                parts.append(SegmentPart(ROLE_TRANSLATE, suffix, break_before=True))
            return SegmentPlan(segment, tuple(parts))

        if self.pending_reference_heading:
            self.pending_reference_heading -= 1

        if protect_authors and not in_named_prose_section:
            combined = split_author_affiliation(text)
            if combined:
                author_text, affiliation_text = combined
                return SegmentPlan(
                    segment,
                    (
                        SegmentPart(ROLE_PRESERVE, author_text),
                        SegmentPart(
                            ROLE_AFFILIATION, affiliation_text, break_before=True
                        ),
                    ),
                )
            named_affiliation = split_named_affiliation_sentence(text)
            if named_affiliation:
                names, affiliation_sentence = named_affiliation
                return SegmentPlan(
                    segment,
                    (
                        SegmentPart(ROLE_PRESERVE, names),
                        SegmentPart(
                            ROLE_TRANSLATE,
                            affiliation_sentence,
                            break_before=True,
                        ),
                    ),
                )
            if looks_like_author_list(text):
                return SegmentPlan(segment, (SegmentPart(ROLE_PRESERVE, text),))
            if looks_like_affiliation(text):
                return SegmentPlan(segment, (SegmentPart(ROLE_AFFILIATION, text),))
        return SegmentPlan(segment, (SegmentPart(ROLE_TRANSLATE, text),))
