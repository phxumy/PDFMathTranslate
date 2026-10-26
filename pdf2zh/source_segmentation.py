"""Keep scientific reference units intact while chunking source prose."""

from __future__ import annotations

import re

_FORMULA = r"(?:\{\{v\d+\}\}|\{v\d+\})"
_REFERENCE_IDENTIFIER = (
    rf"(?:{_FORMULA}|"
    r"[A-Za-z]\.\d+(?:\.\d+)*[A-Za-z]?|"
    r"[A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?|(?-i:[IVXLCDM]+))"
    # A split-font suffix and a subfigure letter belong to the same reference.
    rf"(?:{_FORMULA})*(?:\([A-Za-z]\))?"
)
_ABBREVIATED_REFERENCE = re.compile(
    r"\b(?:figs?|eqs?|secs?|refs?|tbls?)\.\s*"
    rf"(?:\[\s*\d+[^\]\r\n]{{0,80}}\]|［\s*\d+[^］\r\n]{{0,80}}］|"
    rf"(?:[（(]\s*)?{_REFERENCE_IDENTIFIER}(?![A-Za-z0-9])(?:[）)])?)",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?;:](?:\s+|$)")
_INTERNAL_TOKEN = re.compile(r"\[\[PDF2ZH_(?:FLOW_\d+|ITALIC_\d+_(?:BEGIN|END))\]\]")
_CONTINUING_ABBREVIATION = re.compile(
    r"\b(?:w\.\s*r\.\s*t\.|i\.\s*e\.|e\.\s*g\.|[ad]\.\s*c\.|viz\.|cf\.)"
    r"(?=\s*[,;:]?\s+\S)",
    re.IGNORECASE,
)
_CONTINUING_STATISTICAL_ABBREVIATION = re.compile(
    r"\b(?:w\.\s*h\.\s*p\.|i\.\s*i\.\s*d\.)"
    rf"(?=\s*,?\s+(?:{_FORMULA}|[a-z]|[A-Z]{{2,}}\s*{_FORMULA}))",
)
_DEFINED_TABLE_ABBREVIATION = re.compile(
    r"\b(?:n\.d\.|n\.a\.)(?=\s*[-–—:=]\s*"
    r"(?:not\s+(?:determined|detected|available|applicable)|no\s+data)\b)",
    re.IGNORECASE,
)


def scientific_reference_spans(text: str) -> tuple[tuple[int, int], ...]:
    return tuple(match.span() for match in _ABBREVIATED_REFERENCE.finditer(text))


def statistical_abbreviation_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Keep a statistical qualifier attached to prose or an acronym operand."""
    return tuple(
        match.span() for match in _CONTINUING_STATISTICAL_ABBREVIATION.finditer(text)
    )


def scientific_abbreviation_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Dependent prose abbreviations must stay with their following clause.

    Unlike terminal abbreviations such as ``etc.``, these introduce a relation
    or explanation. Internal spacing in PDF extraction must not create a false
    sentence boundary either (``w. r. t.``).
    """
    return (
        tuple(match.span() for match in _CONTINUING_ABBREVIATION.finditer(text))
        + statistical_abbreviation_spans(text)
        + tuple(match.span() for match in _DEFINED_TABLE_ABBREVIATION.finditer(text))
    )


def scientific_sentence_parts(text: str) -> list[str]:
    references = scientific_reference_spans(text) + scientific_abbreviation_spans(text)
    cursor = 0
    parts = []
    for match in _SENTENCE_END.finditer(text):
        if any(start <= match.start() < end for start, end in references):
            continue
        parts.append(text[cursor : match.end()])
        cursor = match.end()
    if cursor < len(text):
        parts.append(text[cursor:])
    return parts


def safe_chunk_boundary(
    text: str,
    limit: int,
    formula_atoms: tuple[tuple[int, int, bool], ...],
) -> int:
    """Keep source-reference units and opaque tokens intact at a size boundary.

    A single indivisible unit longer than the limit is returned whole. All other
    chunks fit the limit; no characters or source whitespace are changed.
    """
    spans = [*scientific_reference_spans(text), *scientific_abbreviation_spans(text)]
    spans.extend((start, end) for start, end, _ in formula_atoms)
    spans.extend(match.span() for match in _INTERNAL_TOKEN.finditer(text))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    for match in reversed(tuple(re.finditer(r"\s+", text[:limit]))):
        cut = match.start()
        if cut > 0 and not any(start < cut < end for start, end in merged):
            return cut
    cut = limit
    for start, end in merged:
        if start < cut < end:
            return start if start > 0 else end
    return cut
