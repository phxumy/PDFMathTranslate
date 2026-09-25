"""Recover wrapped proper names only from unambiguous document-local evidence.

An ASCII hyphen at a line ending is not enough to distinguish a discretionary
hyphen from a real part of a name. Require an unbroken occurrence in an author
list with geometrically attached affiliation numbers and the same preceding
name component. Veto recovery if an unbroken hyphenated spelling also occurs.
Names without that evidence deliberately keep their source text.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
import unicodedata

from pymupdf import TEXTFLAGS_DICT, TEXT_PRESERVE_IMAGES

_LETTER = r"[^\W\d_]"
_HYPHENS = "-\u2010\u2011"
_WORD_EDGE = rf"[\w'’{re.escape(_HYPHENS)}]"
_WORD = re.compile(
    rf"(?<!{_WORD_EDGE}){_LETTER}+(?:[{re.escape(_HYPHENS)}]{_LETTER}+)*"
    rf"(?!{_WORD_EDGE})"
)
_WRAP_LEFT = re.compile(
    rf"(?<!{_WORD_EDGE})(?P<context>{_LETTER}{{2,}})[ \t]+"
    rf"(?P<left>{_LETTER}{{2,}})-$"
)
_WRAP_RIGHT = re.compile(rf"[ \t]*(?P<right>{_LETTER}{{2,}})(?!{_WORD_EDGE}|[{{\[])")
_AFFILIATION = "\x01"
_GRAMMAR_WORDS = frozenset(
    "a an and are as at be been being but by for from has have in into is it its "
    "nor of on or our than that the their them then there these they this those "
    "to was we were which who with without you your".split()
)


def _touches_combining_mark(value: str, start: int, end: int) -> bool:
    return any(
        unicodedata.category(value[position]).startswith("M")
        for position in (start - 1, end)
        if 0 <= position < len(value)
    )


def _name_components(value: str) -> tuple[str, ...]:
    components = tuple(value.split())
    if not 2 <= len(components) <= 4:
        return ()
    for component in components:
        if component.casefold() in _GRAMMAR_WORDS:
            return ()
        parts = re.split(rf"['’{re.escape(_HYPHENS)}]", component)
        if not all(_proper_component(part) for part in parts):
            return ()
    return components


def _proper_component(value: str) -> bool:
    # This is a spelling safeguard, not a general named-entity recognizer.
    # Keeping lowercase prose out avoids semantic pairs such as re-cover/recover.
    return len(value) >= 2 and value.isalpha() and value[0].isupper()


def _canonical_hyphenated(value: str) -> str:
    return (
        unicodedata.normalize("NFC", value)
        .casefold()
        .translate(str.maketrans({"\u2010": "-", "\u2011": "-"}))
    )


@dataclass(frozen=True, slots=True)
class SourceSpellingIndex:
    contexts: frozenset[tuple[str, str]] = frozenset()
    hyphenated_words: frozenset[str] = frozenset()

    @classmethod
    def from_lines(
        cls, lines: Iterable[str], *, affiliation_names: Iterable[str] = ()
    ) -> SourceSpellingIndex:
        """Index veto spellings; names need separate byline geometry evidence."""

        contexts = set()
        hyphenated_words = set()
        for line in lines:
            line = unicodedata.normalize("NFC", line)
            for word in _WORD.finditer(line):
                value = word.group()
                if not _touches_combining_mark(line, word.start(), word.end()) and any(
                    hyphen in value for hyphen in _HYPHENS
                ):
                    hyphenated_words.add(_canonical_hyphenated(value))
        for name in affiliation_names:
            components = _name_components(unicodedata.normalize("NFC", name))
            for previous, value in zip(components, components[1:]):
                if _proper_component(previous) and _proper_component(value):
                    contexts.add((previous.casefold(), value.casefold()))
        return cls(frozenset(contexts), frozenset(hyphenated_words))


def _document_blocks(document) -> Iterable[tuple[str, ...]]:
    for page in document:
        # The index needs text only; decoding embedded images here can add a
        # substantial memory and time cost when indexing scanned documents.
        flags = TEXTFLAGS_DICT & ~TEXT_PRESERVE_IMAGES
        for block in page.get_text("dict", flags=flags).get("blocks", ()):
            block_lines = []
            for line in block.get("lines", ()):
                if line.get("wmode", 0) or tuple(line.get("dir", (1, 0))) != (1, 0):
                    # Unsupported orientations can still veto a spelling, but
                    # must not contribute inferred author-list geometry.
                    block_lines.append(
                        "\x00".join(
                            span.get("text", "") for span in line.get("spans", ())
                        )
                    )
                    continue
                pieces = []
                previous = None
                for span in line.get("spans", ()):
                    value = span.get("text", "")
                    # Superscript affiliations end a word, but do not turn an
                    # ordinary identifier such as TANCREDI1 into spelling proof.
                    if span.get("flags", 0) & 1 and value.strip().isdigit():
                        attached = (
                            previous is not None
                            and span["size"] <= 0.85 * previous["size"]
                            and 0.15 * previous["size"]
                            <= previous["origin"][1] - span["origin"][1]
                            <= 0.8 * previous["size"]
                            and -0.3 * previous["size"]
                            <= span["bbox"][0] - previous["bbox"][2]
                            <= 0.8 * previous["size"]
                        )
                        pieces.append(_AFFILIATION if attached else "\x00")
                        previous = None
                        continue
                    if previous is not None:
                        size = min(previous["size"], span["size"])
                        gap = span["bbox"][0] - previous["bbox"][2]
                        baseline = abs(span["origin"][1] - previous["origin"][1])
                        if gap > 1.2 * size or baseline > 0.25 * size:
                            pieces.append("\x00")
                        elif gap > 0.2 * size:
                            pieces.append(" ")
                    pieces.append(value)
                    previous = span
                block_lines.append("".join(pieces))
            yield tuple(block_lines)


def _affiliation_names(line: str) -> tuple[str, ...]:
    """Accept independent comma-separated bylines ending in attached numbers.

    A sentence ending in a superscript citation is not a byline. Require every
    nonempty segment on the line to have name structure, and collect evidence
    only for the name immediately before an attached affiliation number.
    """

    line = unicodedata.normalize("NFC", line)
    if _AFFILIATION not in line or "\x00" in line:
        return ()
    if line.rsplit(_AFFILIATION, 1)[1].strip(" ,\t"):
        return ()
    for segment in line.replace(_AFFILIATION, ",").split(","):
        if segment.strip() and not _name_components(segment):
            return ()
    return tuple(
        prefix.rsplit(",", 1)[-1].strip()
        for prefix in line.split(_AFFILIATION)[:-1]
        if _name_components(prefix.rsplit(",", 1)[-1])
    )


def collect_source_spelling(document) -> SourceSpellingIndex:
    """Read the unchanged source PDF before page streams are replaced."""

    lines = []
    names = []
    for block in _document_blocks(document):
        lines.extend(block)
        byline_rows = [(line, _affiliation_names(line)) for line in block]
        candidates = [name for _, row_names in byline_rows for name in row_names]
        # Repeated name/affiliation structure and list punctuation distinguish
        # author lists from isolated titles or sentences with footnote numbers.
        # Evidence must be local to one text block, never pooled across pages
        # or unrelated headings. Single-author bylines remain unchanged.
        if len({name.casefold() for name in candidates}) < 2:
            continue
        if not any("," in line and row_names for line, row_names in byline_rows):
            continue
        names.extend(candidates)
    return SourceSpellingIndex.from_lines(lines, affiliation_names=names)


def normalize_source_wraps(
    text: str,
    break_offsets: Iterable[int],
    index: SourceSpellingIndex | None,
) -> tuple[str, tuple[int, ...]]:
    """Remove only evidenced name hyphens at known source line boundaries.

    Call immediately after character reconstruction, before any transformation
    that changes string offsets.  Formula/style tokens remain opaque, same-line
    hyphens are untouched, and all offsets are shifted with their source text.
    """

    offsets = tuple(break_offsets)
    if index is None or not index.contexts:
        return text, offsets
    removals = []
    for offset in sorted(set(offsets)):
        if not 0 < offset < len(text) or text[offset - 1] != "-":
            continue
        left = _WRAP_LEFT.search(text[:offset])
        right = _WRAP_RIGHT.match(text[offset:])
        if left is None or right is None:
            continue
        if _touches_combining_mark(text, left.start(), offset) or (
            _touches_combining_mark(
                text, offset + right.start("right"), offset + right.end("right")
            )
        ):
            continue
        context = left.group("context")
        prefix = left.group("left")
        suffix = right.group("right")
        if not _proper_component(context) or not _proper_component(prefix):
            continue
        # A second capitalized component is more likely a true compound surname.
        if not suffix.islower():
            continue
        combined = (prefix + suffix).casefold()
        if (context.casefold(), combined) not in index.contexts:
            continue
        if f"{prefix}-{suffix}".casefold() in index.hyphenated_words:
            continue
        removals.append((offset - 1, offset + right.start("right")))
    if not removals:
        return text, offsets
    result = text
    for start, end in reversed(removals):
        result = result[:start] + result[end:]
    shifted = tuple(
        offset - sum(max(0, min(offset, end) - start) for start, end in removals)
        for offset in offsets
    )
    return result, shifted
