"""Tokenize translated text into safe PDF line-breaking units.

The renderer needs to measure text before it knows where a line will end.  This
module deliberately contains no font or PDF dependencies: it only identifies
tokens that should normally stay together.  A caller may still split a
``latin`` atom character-by-character when a single token is wider than the
available line.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

AtomKind = Literal[
    "char",
    "formula",
    "latin",
    "literal",
    "newline",
    "style",
]


# Closing punctuation must not begin a Chinese line.
CJK_PROHIBITED_LINE_START = frozenset(
    "，。、；：！？,.?!;:%‰）)]｝}〉》」』】〕〗〙〛’”»…—"
)

# Opening punctuation must not be left at the end of a Chinese line.
CJK_PROHIBITED_LINE_END = frozenset("（([｛{〈《「『【〔〖〘〚‘“«")
_CJK_BOUND_PUNCTUATION = CJK_PROHIBITED_LINE_START | CJK_PROHIBITED_LINE_END


_STYLE_TOKEN_RE = re.compile(r"\[\[PDF2ZH_ITALIC_\d+_(?:BEGIN|END)\]\]")
_FORMULA_TOKEN_RE = re.compile(
    r"(?:\{\{\s*[vV]\s*[\d\s]*\d[\d\s]*\}\}|" r"\{\s*[vV]\s*[\d\s]*\d[\d\s]*\})"
)
_URL_PREFIX_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)",
    re.IGNORECASE,
)
_BROKEN_URL_PREFIX_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*\s*:\s*/\s*/\s*|www\s*\.\s*)",
    re.IGNORECASE,
)
_BROKEN_DOI_PREFIX_RE = re.compile(
    r"(?:doi\s*:\s*|(?<![A-Za-z0-9])10\s*\.\s*\d{4,9}\s*/\s*)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"[A-Za-z0-9._%+\-]+\s*@\s*[A-Za-z0-9\-]+"
    r"(?:\s*\.\s*[A-Za-z0-9\-]+)+"
)
_DOI_RE = re.compile(
    r"(?:(?:doi):)?10\.\d{4,9}/[-._;()/:A-Za-z0-9]+",
    re.IGNORECASE,
)

_WORD_SEPARATORS = frozenset("-'’_./:")
_URL_CHARACTERS = frozenset("-._~:/?#[]@!$&'()*+,;=%")
_TRAILING_URL_PUNCTUATION = frozenset(".,;:!?)]}，。；：！？）】》")
_PAIRED_PUNCTUATION = {
    "（": "）",
    "(": ")",
    "［": "］",
    "[": "]",
    "｛": "｝",
    "{": "}",
    "〈": "〉",
    "《": "》",
    "「": "」",
    "『": "』",
    "【": "】",
    "〔": "〕",
    "〖": "〗",
    "〘": "〙",
    "〚": "〛",
    "‘": "’",
    "“": "”",
    "«": "»",
}
_MAX_PAIRED_CONTENT_LENGTH = 24
_FORMULA_CLUSTER_CHARACTERS = frozenset(
    " \t()[]{}（［］）,:;.=+-−–—×·/|!^_<>≤≥≈≃≡∫∑∏√ðÞ¼，。；"
)
_FORMULA_CLUSTER_TERMINATORS = frozenset(",.;，。；")


@dataclass(frozen=True, slots=True)
class RenderAtom:
    """A source span that the renderer should normally treat as one atom."""

    start: int
    end: int
    kind: AtomKind
    text: str

    @property
    def zero_width(self) -> bool:
        """Whether the atom changes style but consumes no horizontal space."""

        return self.kind == "style"

    @property
    def fallback_splittable(self) -> bool:
        """Whether an over-wide atom may safely fall back to character layout."""

        return self.kind == "latin"


@dataclass(frozen=True, slots=True)
class ProtectedLiteral:
    """A URL, DOI, or email span whose characters must survive translation."""

    start: int
    end: int
    kind: Literal["url", "doi", "email"]
    text: str


@dataclass(frozen=True, slots=True)
class LineBreakUnit:
    """Atoms joined by a Chinese line-start or line-end prohibition."""

    atoms: tuple[RenderAtom, ...]

    @property
    def start(self) -> int:
        return self.atoms[0].start

    @property
    def end(self) -> int:
        return self.atoms[-1].end

    @property
    def text(self) -> str:
        return "".join(atom.text for atom in self.atoms)

    @property
    def fallback_splittable(self) -> bool:
        """Whether this unit contains one Latin token that callers may split.

        Bound opening/closing punctuation does not prevent fallback.  The
        caller can split the Latin atom while keeping those punctuation atoms
        attached to the appropriate end.
        """

        content = tuple(
            atom
            for atom in self.atoms
            if not atom.zero_width
            and not (atom.kind == "char" and atom.text in _CJK_BOUND_PUNCTUATION)
        )
        return len(content) == 1 and content[0].fallback_splittable


def _is_latin_or_digit(character: str) -> bool:
    if character.isascii():
        return character.isalnum()
    if character.isdigit():
        return True
    if not character.isalpha():
        return False
    return "LATIN" in unicodedata.name(character, "")


def _segment_length(text: str, start: int, end: int) -> int:
    cursor = end - 1
    while cursor >= start and _is_latin_or_digit(text[cursor]):
        cursor -= 1
    return end - cursor - 1


def _can_join_word_separator(
    text: str,
    token_start: int,
    separator_index: int,
) -> bool:
    next_index = separator_index + 1
    if next_index >= len(text):
        return False

    separator = text[separator_index]
    left = text[separator_index - 1]
    right = text[next_index]
    if not (_is_latin_or_digit(left) and _is_latin_or_digit(right)):
        return False

    # A full stop or colon followed by a capital normally ends prose rather
    # than joining two words (``sentence.Next``).  Single-letter initials such
    # as ``A.B`` remain atomic.
    if separator in ".:" and right.isupper():
        return _segment_length(text, token_start, separator_index) == 1
    return True


def _scan_url(text: str, start: int) -> int | None:
    prefix = _URL_PREFIX_RE.match(text, start)
    if prefix is None:
        return None

    cursor = prefix.end()
    while cursor < len(text):
        character = text[cursor]
        if _is_latin_or_digit(character) or character in _URL_CHARACTERS:
            cursor += 1
            continue
        break

    while cursor > prefix.end() and text[cursor - 1] in _TRAILING_URL_PUNCTUATION:
        cursor -= 1
    return cursor


def _is_structural_literal_gap(
    compact: str,
    text: str,
    next_index: int,
) -> bool:
    """Return whether whitespace is an extraction break inside a literal.

    PDF line reconstruction commonly inserts a space next to ``://``, ``/``,
    ``-``, or a domain dot.  Only those structural boundaries are joined; an
    ordinary space after a complete URL remains a prose boundary.
    """

    if not compact or next_index >= len(text):
        return False
    previous = compact[-1]
    following = text[next_index]
    structural = "/:_?#@&=%-"
    if previous in structural or following in structural + ".":
        return True
    if previous != ".":
        return False

    # A dot is structural only while scanning an authority (or the fixed
    # ``10.`` DOI prefix).  Once a URL has entered its path, a trailing full
    # stop followed by another number is prose/metadata, not a broken domain.
    # This prevents ``.../2006.04130. 2006.04130`` from being collapsed into
    # one visibly corrupted URL.
    folded = compact.casefold()
    if re.fullmatch(r"(?:doi:)?10\.", folded) is None:
        if "://" in compact:
            authority = compact.split("://", 1)[1]
        elif folded.startswith("www."):
            authority = compact
        else:
            return False
        if "/" in authority:
            return False

    # A dot followed by a domain label and then another URL delimiter is a
    # broken host name (``creativecommons. org/licenses``), not sentence prose.
    label = re.match(r"[A-Za-z0-9-]{2,63}", text[next_index:])
    if label is None:
        return False
    after_label = next_index + label.end()
    return after_label == len(text) or text[after_label] in "/.?#[]"


def _scan_broken_literal_tail(
    text: str,
    start: int,
    prefix_end: int,
    canonical_prefix: str,
) -> tuple[int, str]:
    """Scan one URL/DOI candidate, removing only structural PDF whitespace."""

    compact = canonical_prefix
    cursor = prefix_end
    while cursor < len(text):
        character = text[cursor]
        if _is_latin_or_digit(character) or character in _URL_CHARACTERS:
            compact += character
            cursor += 1
            continue
        if character.isspace():
            next_index = cursor + 1
            while next_index < len(text) and text[next_index].isspace():
                next_index += 1
            if _is_structural_literal_gap(compact, text, next_index):
                cursor = next_index
                continue
        break

    while compact and compact[-1] in _TRAILING_URL_PUNCTUATION:
        compact = compact[:-1]
        cursor -= 1
    return cursor, compact


def _scan_broken_url(text: str, start: int) -> tuple[int, str] | None:
    prefix = _BROKEN_URL_PREFIX_RE.match(text, start)
    if prefix is None:
        return None
    raw_prefix = re.sub(r"\s+", "", prefix.group(0))
    end, compact = _scan_broken_literal_tail(
        text,
        start,
        prefix.end(),
        raw_prefix,
    )
    if _scan_url(compact, 0) != len(compact):
        return None
    return end, compact


def _scan_broken_doi(text: str, start: int) -> tuple[int, str] | None:
    prefix = _BROKEN_DOI_PREFIX_RE.match(text, start)
    if prefix is None:
        return None
    raw_prefix = re.sub(r"\s+", "", prefix.group(0))
    end, compact = _scan_broken_literal_tail(
        text,
        start,
        prefix.end(),
        raw_prefix,
    )
    if _DOI_RE.fullmatch(compact) is None:
        return None
    return end, compact


def _next_broken_literal(
    text: str,
    cursor: int,
) -> tuple[int, int, str] | None:
    """Find the next URL, DOI, or email, accepting structural PDF spaces."""

    candidates: list[tuple[int, int, str]] = []
    url_prefix = _BROKEN_URL_PREFIX_RE.search(text, cursor)
    if url_prefix is not None:
        scanned = _scan_broken_url(text, url_prefix.start())
        if scanned is not None:
            end, compact = scanned
            candidates.append((url_prefix.start(), end, compact))

    doi_prefix = _BROKEN_DOI_PREFIX_RE.search(text, cursor)
    if doi_prefix is not None:
        scanned = _scan_broken_doi(text, doi_prefix.start())
        if scanned is not None:
            end, compact = scanned
            candidates.append((doi_prefix.start(), end, compact))

    email = _EMAIL_RE.search(text, cursor)
    if email is not None:
        candidates.append(
            (
                email.start(),
                email.end(),
                re.sub(r"\s+", "", email.group(0)),
            )
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], -item[1]))


def normalize_protected_literals(text: str) -> str:
    """Repair structural whitespace inside URLs, DOIs, and email addresses.

    The operation is intentionally narrow: whitespace is removed only beside
    URL syntax, so ordinary prose and hyphenated words are left untouched.
    """

    pieces: list[str] = []
    cursor = 0
    while cursor < len(text):
        candidate = _next_broken_literal(text, cursor)
        if candidate is None:
            pieces.append(text[cursor:])
            break
        start, end, compact = candidate
        pieces.append(text[cursor:start])
        pieces.append(compact)
        cursor = end
    return "".join(pieces)


def protected_literal_at(text: str, start: int) -> ProtectedLiteral | None:
    """Return a canonical protected literal beginning at *start*, if any."""

    email = _EMAIL_RE.match(text, start)
    if email is not None and not re.search(r"\s", email.group(0)):
        return ProtectedLiteral(start, email.end(), "email", email.group(0))

    doi = _DOI_RE.match(text, start)
    if doi is not None:
        end = doi.end()
        while end > start and text[end - 1] in _TRAILING_URL_PUNCTUATION:
            end -= 1
        return ProtectedLiteral(start, end, "doi", text[start:end])

    url_end = _scan_url(text, start)
    if url_end is not None:
        return ProtectedLiteral(start, url_end, "url", text[start:url_end])
    return None


def iter_protected_literals(text: str) -> Iterator[ProtectedLiteral]:
    """Yield canonical URL, DOI, and email spans from *text*."""

    cursor = 0
    while cursor < len(text):
        literal = protected_literal_at(text, cursor)
        if literal is not None:
            yield literal
            cursor = literal.end
            continue
        cursor += 1


def _scan_latin_word(text: str, start: int) -> int:
    url_end = _scan_url(text, start)
    if url_end is not None:
        return url_end

    cursor = start + 1
    while cursor < len(text):
        character = text[cursor]
        if _is_latin_or_digit(character):
            cursor += 1
            continue
        if character in _WORD_SEPARATORS and _can_join_word_separator(
            text,
            start,
            cursor,
        ):
            cursor += 1
            continue
        break
    return cursor


def iter_render_atoms(text: str) -> Iterator[RenderAtom]:
    """Yield formulas, style markers, words, newlines, and single characters."""

    cursor = 0
    while cursor < len(text):
        if text[cursor] in "\r\n":
            end = cursor + 1
            if text[cursor] == "\r" and end < len(text) and text[end] == "\n":
                end += 1
            yield RenderAtom(cursor, end, "newline", text[cursor:end])
            cursor = end
            continue

        style = _STYLE_TOKEN_RE.match(text, cursor)
        if style is not None:
            end = style.end()
            yield RenderAtom(cursor, end, "style", text[cursor:end])
            cursor = end
            continue

        formula = _FORMULA_TOKEN_RE.match(text, cursor)
        if formula is not None:
            end = formula.end()
            yield RenderAtom(cursor, end, "formula", text[cursor:end])
            cursor = end
            continue

        literal = protected_literal_at(text, cursor)
        if literal is not None:
            yield RenderAtom(
                literal.start,
                literal.end,
                "literal",
                literal.text,
            )
            cursor = literal.end
            continue

        if _is_latin_or_digit(text[cursor]):
            end = _scan_latin_word(text, cursor)
            yield RenderAtom(cursor, end, "latin", text[cursor:end])
            cursor = end
            continue

        yield RenderAtom(cursor, cursor + 1, "char", text[cursor])
        cursor += 1


def _unit_ends_with_newline(unit: list[RenderAtom]) -> bool:
    return bool(unit) and unit[-1].kind == "newline"


def _paired_atom_length(atom: RenderAtom) -> int:
    if atom.kind == "style":
        return 0
    if atom.kind == "formula":
        return 2
    return len(atom.text)


def _short_paired_spans(atoms: list[RenderAtom]) -> dict[int, int]:
    """Map short, balanced opening atoms to their matching closing atoms."""

    spans: dict[int, int] = {}
    stack: list[tuple[int, str]] = []
    for index, atom in enumerate(atoms):
        if atom.kind == "newline":
            stack.clear()
            continue
        if atom.kind != "char":
            continue

        closing = _PAIRED_PUNCTUATION.get(atom.text)
        if closing is not None:
            stack.append((index, closing))
            continue
        if not stack or atom.text != stack[-1][1]:
            continue

        opening_index, _ = stack.pop()
        content_length = sum(
            _paired_atom_length(content_atom)
            for content_atom in atoms[opening_index + 1 : index]
        )
        if content_length <= _MAX_PAIRED_CONTENT_LENGTH:
            spans[opening_index] = index
    return spans


def group_line_break_units(
    atoms: Iterable[RenderAtom],
) -> Iterator[LineBreakUnit]:
    """Apply Chinese punctuation rules and protect short bracketed phrases."""

    atom_list = list(atoms)
    paired_spans = _short_paired_spans(atom_list)
    units: list[list[RenderAtom]] = []
    pending: list[RenderAtom] = []

    index = 0
    while index < len(atom_list):
        atom = atom_list[index]
        paired_end = paired_spans.get(index)
        if paired_end is not None:
            units.append([*pending, *atom_list[index : paired_end + 1]])
            pending = []
            index = paired_end + 1
            continue

        if atom.kind == "newline":
            if pending:
                units.append(pending)
                pending = []
            units.append([atom])
            index += 1
            continue

        if atom.kind == "style":
            if (
                atom.text.endswith("_BEGIN]]")
                or not units
                or _unit_ends_with_newline(units[-1])
            ):
                pending.append(atom)
            else:
                units[-1].append(atom)
            index += 1
            continue

        if atom.kind == "char" and atom.text in CJK_PROHIBITED_LINE_END:
            pending.append(atom)
            index += 1
            continue

        if atom.kind == "char" and atom.text in CJK_PROHIBITED_LINE_START:
            if pending:
                if units and not _unit_ends_with_newline(units[-1]):
                    units[-1].extend(pending)
                    units[-1].append(atom)
                else:
                    units.append([*pending, atom])
                pending = []
            elif units and not _unit_ends_with_newline(units[-1]):
                units[-1].append(atom)
            else:
                units.append([atom])
            index += 1
            continue

        units.append([*pending, atom])
        pending = []
        index += 1

    if pending:
        units.append(pending)

    yield from _join_formula_line_break_units(
        [LineBreakUnit(tuple(unit)) for unit in units]
    )


def _unit_has_formula(unit: LineBreakUnit) -> bool:
    return any(atom.kind == "formula" for atom in unit.atoms)


def _unit_is_formula_syntax(unit: LineBreakUnit) -> bool:
    """Whether *unit* can be part of one visually atomic inline expression."""

    for atom in unit.atoms:
        if atom.kind in {"formula", "style"}:
            continue
        if atom.kind == "newline":
            return False
        if atom.kind == "latin":
            if len(atom.text) > 2:
                return False
            continue
        if atom.kind == "char":
            if atom.text in _FORMULA_CLUSTER_CHARACTERS:
                continue
            if atom.text and (
                unicodedata.category(atom.text[0]).startswith("S")
                or "GREEK" in unicodedata.name(atom.text[0], "")
            ):
                continue
        return False
    return True


def _unit_is_space(unit: LineBreakUnit) -> bool:
    visible = "".join(atom.text for atom in unit.atoms if not atom.zero_width)
    return bool(visible) and visible.isspace()


def _unit_ends_formula_terminator(unit: LineBreakUnit) -> bool:
    visible = "".join(atom.text for atom in unit.atoms if not atom.zero_width)
    return bool(visible) and visible[-1] in _FORMULA_CLUSTER_TERMINATORS


def _join_formula_line_break_units(
    units: list[LineBreakUnit],
) -> Iterator[LineBreakUnit]:
    """Keep split-font inline formulas together when choosing target line breaks.

    Extraction may represent ``p_m`` as a normal-font ``p`` followed by ``{v7}``,
    or a longer expression as several placeholders separated by ordinary-font
    parentheses and one-letter variables.  The translation validator protects token
    order, but allowing a line break between those pieces still produces detached
    subscripts and vertical formula fragments.  Join only short mathematical syntax
    around an existing formula token; ordinary words remain independent.
    """

    merged: list[LineBreakUnit] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if not _unit_has_formula(unit):
            merged.append(unit)
            index += 1
            continue

        atoms = list(unit.atoms)
        if (
            merged
            and _unit_is_formula_syntax(merged[-1])
            and not _unit_is_space(merged[-1])
            and merged[-1].end == unit.start
        ):
            atoms = [*merged.pop().atoms, *atoms]

        index += 1
        while index < len(units):
            following = units[index]
            if not _unit_is_formula_syntax(following):
                break
            if _unit_ends_formula_terminator(
                LineBreakUnit(tuple(atoms))
            ) and _unit_is_space(following):
                break
            if following.start != atoms[-1].end:
                break
            if _unit_is_space(following):
                if index + 1 >= len(units) or not _unit_is_formula_syntax(
                    units[index + 1]
                ):
                    break
                if (
                    not _unit_has_formula(units[index + 1])
                    and len(units[index + 1].text.strip()) > 2
                ):
                    break
            atoms.extend(following.atoms)
            index += 1

        merged.append(LineBreakUnit(tuple(atoms)))

    yield from merged


def iter_line_break_units(text: str) -> Iterator[LineBreakUnit]:
    """Tokenize *text* and yield units that are safe line-break boundaries."""

    yield from group_line_break_units(iter_render_atoms(text))
