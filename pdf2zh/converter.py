import concurrent.futures
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from string import Template
from typing import Dict

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTFigure, LTLine, LTPage
from pdfminer.pdffont import PDFCIDFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.utils import Matrix, apply_matrix_pt, mult_matrix
from pymupdf import Font
from tenacity import retry, wait_fixed, stop_after_attempt

from pdf2zh.line_breaking import (
    CJK_PROHIBITED_LINE_END,
    CJK_PROHIBITED_LINE_START,
    LineBreakUnit,
    iter_line_break_units,
    iter_protected_literals,
    normalize_protected_literals,
)
from pdf2zh.translator import (
    AnythingLLMTranslator,
    ArgosTranslator,
    AzureOpenAITranslator,
    AzureTranslator,
    BaseTranslator,
    BingTranslator,
    CodexTranslator,
    DeepLTranslator,
    DeepLXTranslator,
    DeepseekTranslator,
    DifyTranslator,
    GeminiTranslator,
    GoogleTranslator,
    GrokTranslator,
    GroqTranslator,
    ModelScopeTranslator,
    OllamaTranslator,
    OpenAIlikedTranslator,
    OpenAITranslator,
    QwenMtTranslator,
    SiliconTranslator,
    TencentTranslator,
    XinferenceTranslator,
    ZhipuTranslator,
)
from pdf2zh.translation_policy import (
    ROLE_AFFILIATION,
    ROLE_PRESERVE,
    ROLE_REFERENCE,
    ROLE_TRANSLATE,
    DocumentTranslationPolicy,
    SourceSegment,
    formula_cache_signature,
    restore_affiliation_breaks,
)
from pdf2zh.reading_flow import (
    ContinuationGroup,
    FlowSegment,
    FragmentSpan,
    ReferenceContinuationGroup,
    SegmentRef,
    build_continuation_groups,
    detect_cross_page_edge,
    detect_page_edges,
    detect_reference_continuation,
)

log = logging.getLogger(__name__)


ITALIC_TAG_PREFIX = "[[PDF2ZH_ITALIC_"
ITALIC_TAG_RE = re.compile(
    r"\[\[PDF2ZH_ITALIC_(\d+)_(BEGIN|END)\]\]"
)
ITALIC_SHEAR = math.tan(math.radians(12.0))
_FORMULA_MARKER_RE = re.compile(r"\{\s*v([\d\s]+)\s*\}", re.IGNORECASE)
FLOW_TOKEN_PREFIX = "[[PDF2ZH_FLOW_"
FLOW_TOKEN_RE = re.compile(r"\[\[PDF2ZH_FLOW_(\d+)\]\]")
_MAX_READONLY_FORMULA_GLYPHS = 24
_MAX_READONLY_FORMULA_CODEPOINTS = 48
_PROSE_ITALIC_FONT_RE = re.compile(r"(?:italic|oblique|slanted)", re.IGNORECASE)
_MATH_FONT_RE = re.compile(
    r"(?:^CM(?:I|MI|SY|EX)|RMTMI|MTSY|MTEX|Math|Symbol|Sym$|TeX|Euler|"
    r"AdvP[0-9A-Fa-f]+)",
    re.IGNORECASE,
)
_MATH_WORDS = {
    "arg",
    "cos",
    "cosh",
    "det",
    "diag",
    "dim",
    "exp",
    "gcd",
    "inf",
    "ker",
    "lim",
    "ln",
    "log",
    "max",
    "min",
    "mod",
    "rank",
    "re",
    "im",
    "sgn",
    "sin",
    "sinh",
    "sup",
    "tan",
    "tanh",
    "tr",
}
_NON_TRANSLATABLE_ITALIC_PHRASES = {
    "etal",
    "eg",
    "ie",
    "ibid",
}


def _split_trailing_prose_openers(
    chars: list[LTChar],
) -> tuple[list[LTChar], str]:
    """Detach unmatched opening punctuation from a formula before prose.

    Math fonts often contain the opening parenthesis of a following explanatory
    phrase. Keeping it inside an opaque formula placeholder prevents Chinese
    word order from moving that parenthesis to the translated phrase boundary.
    Only unmatched opening marks at the very end are detached; balanced formula
    parentheses and all closing marks remain untouched.
    """
    pairs = {"(": ")", "[": "]", "（": "）", "［": "］"}
    joined = "".join(char.get_text() for char in chars)
    parenthetical = re.search(
        r"([\(\[（［])(see|cf|where|with|for)$",
        joined,
        re.IGNORECASE,
    )
    if parenthetical is not None and all(
        len(char.get_text()) == 1 for char in chars
    ):
        opener = parenthetical.group(1)
        if joined.count(opener) > joined.count(pairs[opener]):
            split_at = parenthetical.start()
            return chars[:split_at], joined[split_at:]
    split_at = len(chars)
    while split_at:
        marker = chars[split_at - 1].get_text()
        if marker not in pairs:
            break
        candidate = "".join(char.get_text() for char in chars[:split_at])
        if candidate.count(marker) <= candidate.count(pairs[marker]):
            break
        split_at -= 1
    if split_at == len(chars):
        return chars, ""
    return (
        chars[:split_at],
        "".join(char.get_text() for char in chars[split_at:]),
    )


_PROSE_SENTENCE_PUNCTUATION = frozenset(".,;:!?，。；：！？")
_FORMULA_TRAILING_PROSE_WORDS = frozenset(
    {
        "are", "be", "been", "being",
        "called",
        "correspond", "corresponds", "corresponded",
        "denote", "denotes", "denoted",
        "is",
        "represent", "represents", "represented",
        "respectively", "where", "which",
        "was", "were",
    }
)


def _split_trailing_formula_prose_word(
    chars: list[LTChar],
) -> tuple[list[LTChar], str, list[LTChar]]:
    """Detach a high-confidence grammar word swallowed after a math glyph.

    The suffix must be an explicit connective/verb, use only ordinary text fonts,
    and immediately follow a mathematical-font glyph.  This intentionally does
    not classify arbitrary English labels or mathematical function names.
    """
    split_at = len(chars)
    while split_at and chars[split_at - 1].get_text().isalpha():
        split_at -= 1
    if split_at in {0, len(chars)}:
        return chars, "", []
    suffix = chars[split_at:]
    word = "".join(char.get_text() for char in suffix)
    if word.lower() not in _FORMULA_TRAILING_PROSE_WORDS:
        return chars, "", []
    if any(_MATH_FONT_RE.search(_formula_font_name(char)) for char in suffix):
        return chars, "", []
    preceding = chars[split_at - 1]
    preceding_text = preceding.get_text()
    if not (
        _MATH_FONT_RE.search(_formula_font_name(preceding))
        or (preceding_text and unicodedata.category(preceding_text[0]) in {"Lm", "Mn", "Sk", "Sm"})
    ):
        return chars, "", []
    em = max(float(preceding.size), float(suffix[0].size), 1.0)
    gap = float(suffix[0].x0) - float(preceding.x1)
    spacer = " " if gap > 0.12 * em else ""
    return chars[:split_at], spacer + word, suffix


def _split_trailing_prose_punctuation(
    chars: list[LTChar],
) -> tuple[list[LTChar], str]:
    """Detach only terminal sentence punctuation swallowed by a formula run.

    Citation separators and punctuation inside an expression remain part of the
    formula.  Requiring a non-punctuation glyph to remain also avoids turning a
    punctuation-only preserved/display run into prose.
    """
    split_at = len(chars)
    while split_at:
        value = chars[split_at - 1].get_text()
        if len(value) != 1 or value not in _PROSE_SENTENCE_PUNCTUATION:
            break
        split_at -= 1
    if split_at in {0, len(chars)}:
        return chars, ""
    return (
        chars[:split_at],
        "".join(char.get_text() for char in chars[split_at:]),
    )


def _segment_contains_prose(text: str) -> bool:
    """Return whether a segment contains more than formula markers/spacing."""
    return bool(_FORMULA_MARKER_RE.sub("", text).strip())


_INLINE_SCRIPT_REGION_KINDS = frozenset(
    {
        "plain text",
        "title",
        "figure_caption",
        "table_caption",
        "table_footnote",
    }
)


def _should_bridge_cross_class_inline_script(
    previous: LTChar | None,
    candidate: LTChar,
    paragraph_text: str,
    paragraph_size: float,
    previous_class: int,
    candidate_class: int,
    previous_region_kind: str,
    candidate_region_kind: str,
) -> bool:
    """Recognize a script split from its base by overlapping detector boxes."""
    if (
        previous is None
        or previous_class == 0
        or candidate_class == 0
        or previous_class == candidate_class
        or previous_region_kind != candidate_region_kind
        or previous_region_kind not in _INLINE_SCRIPT_REGION_KINDS
        or paragraph_size <= 0
        or re.search(r"(?:^|[^A-Za-z])([A-Za-z])$", paragraph_text.rstrip())
        is None
        or re.fullmatch(r"[A-Za-z0-9]", previous.get_text()) is None
        or re.fullmatch(r"[A-Za-z0-9]", candidate.get_text()) is None
        or float(previous.size) < 0.90 * paragraph_size
        or float(candidate.size) >= 0.79 * paragraph_size
    ):
        return False
    gap = float(candidate.x0) - float(previous.x1)
    if gap < -0.25 * paragraph_size or gap > 0.30 * paragraph_size:
        return False
    previous_center = (float(previous.y0) + float(previous.y1)) / 2
    candidate_center = (float(candidate.y0) + float(candidate.y1)) / 2
    offset = abs(candidate_center - previous_center)
    if offset < 0.10 * paragraph_size or offset > 0.70 * paragraph_size:
        return False
    vertical_overlap = min(float(previous.y1), float(candidate.y1)) - max(
        float(previous.y0),
        float(candidate.y0),
    )
    return vertical_overlap > 0


def _should_continue_cross_class_inline_script(
    chars: list[LTChar],
    previous: LTChar | None,
    candidate: LTChar,
    paragraph_size: float,
    previous_actual_class: int,
    candidate_class: int,
    logical_region_kind: str,
    candidate_region_kind: str,
) -> bool:
    """Continue a cross-class script after its first glyph was bridged."""
    if (
        not chars
        or previous is None
        or previous_actual_class != candidate_class
        or candidate_class == 0
        or logical_region_kind != candidate_region_kind
        or logical_region_kind not in _INLINE_SCRIPT_REGION_KINDS
        or paragraph_size <= 0
        or float(candidate.size) >= 0.79 * paragraph_size
        or re.fullmatch(r"[A-Za-z0-9]", candidate.get_text()) is None
        or not _should_extend_formula_run(chars, candidate)
    ):
        return False
    previous_center = (float(previous.y0) + float(previous.y1)) / 2
    candidate_center = (float(candidate.y0) + float(candidate.y1)) / 2
    return abs(candidate_center - previous_center) <= 0.12 * paragraph_size


def _should_bridge_formula_inline_script(
    chars: list[LTChar],
    previous: LTChar | None,
    candidate: LTChar,
    paragraph_size: float,
    previous_actual_class: int,
    candidate_class: int,
    logical_region_kind: str,
    candidate_region_kind: str,
) -> bool:
    """Keep a detector-split subscript attached to its formula base.

    A layout detector can assign the full-size mathematical base to the prose
    box while assigning its smaller Latin subscript to a neighbouring box.  In
    that case the base has already started ``chars``, but the ordinary-font
    script would otherwise become its own translatable paragraph (for example,
    ``phi`` followed by ``mj``).  Join only an adjacent, vertically displaced,
    reduced-size alphanumeric run in the same semantic region.  The following
    script glyphs are handled by ``_should_continue_cross_class_inline_script``.
    """

    if (
        not chars
        or previous is None
        or chars[-1] is not previous
        or previous_actual_class == 0
        or candidate_class == 0
        or previous_actual_class == candidate_class
        or logical_region_kind != candidate_region_kind
        or logical_region_kind not in _INLINE_SCRIPT_REGION_KINDS
        or paragraph_size <= 0
        or re.fullmatch(r"[A-Za-z0-9]", candidate.get_text()) is None
        or float(previous.size) < 0.90 * paragraph_size
        or float(candidate.size) >= 0.79 * paragraph_size
    ):
        return False

    right_edge = max(float(char.x1) for char in chars)
    gap = float(candidate.x0) - right_edge
    if gap < -0.25 * paragraph_size or gap > 0.30 * paragraph_size:
        return False

    previous_center = (float(previous.y0) + float(previous.y1)) / 2
    candidate_center = (float(candidate.y0) + float(candidate.y1)) / 2
    offset = abs(candidate_center - previous_center)
    if offset < 0.10 * paragraph_size or offset > 0.70 * paragraph_size:
        return False

    vertical_overlap = min(float(previous.y1), float(candidate.y1)) - max(
        float(previous.y0),
        float(candidate.y0),
    )
    return vertical_overlap > 0


def _formula_font_name(char: LTChar) -> str:
    name = char.fontname
    if isinstance(name, bytes):
        try:
            name = name.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(name).split("+")[-1]


def _should_absorb_trailing_formula_base(
    previous: LTChar | None,
    candidate: LTChar,
    paragraph_text: str,
    paragraph_size: float,
    previous_formula: list[LTChar] | None = None,
) -> bool:
    """Pull a split one-letter base into its following formula glyph run.

    Publisher PDFs sometimes emit an ordinary-font base first and its accent,
    script, or delimiters afterwards in content order.  Keeping the base as prose
    makes the accent render after it instead of over it.  This predicate accepts
    only a trailing standalone Latin base with strongly formula-like geometry.
    """
    if (
        previous is None
        or paragraph_size <= 0
        or re.search(r"(?:^|[^A-Za-z])([A-Za-z])$", paragraph_text.rstrip())
        is None
        or re.fullmatch(r"[A-Za-z]", previous.get_text()) is None
    ):
        return False
    gap = float(candidate.x0) - float(previous.x1)
    vertical_overlap = min(float(previous.y1), float(candidate.y1)) - max(
        float(previous.y0),
        float(candidate.y0),
    )
    if vertical_overlap <= 0:
        return False

    font_name = _formula_font_name(candidate)
    if re.fullmatch(r"AdvP[0-9A-Fa-f]+", font_name, re.IGNORECASE):
        # Vector accents and out-of-order delimiters overlap (or nearly overlap)
        # the ordinary base they visually decorate.
        return (
            gap <= 0.18 * paragraph_size
            and float(candidate.x1) >= float(previous.x0) - 0.10 * paragraph_size
        )

    previous_center = (float(previous.y0) + float(previous.y1)) / 2
    candidate_center = (float(candidate.y0) + float(candidate.y1)) / 2
    if (
        float(candidate.size) < 0.79 * paragraph_size
        and previous_formula
        and re.search(r"\{v\d+\}[ \t]+[A-Za-z]$", paragraph_text) is not None
        and -0.20 * paragraph_size <= gap <= 0.20 * paragraph_size
        and 0.10 * paragraph_size
        <= abs(candidate_center - previous_center)
        <= 0.70 * paragraph_size
    ):
        return True

    # Differential notation is the only full-size ordinary base admitted here.
    # It must bridge an existing formula island directly to a following math glyph.
    if previous.get_text() != "d" or not previous_formula:
        return False
    previous_formula_right = max(float(char.x1) for char in previous_formula)
    candidate_text = candidate.get_text()
    candidate_is_math = (
        _MATH_FONT_RE.search(font_name) is not None
        or (
            bool(candidate_text)
            and (
                unicodedata.category(candidate_text[0])
                in {"Lm", "Mn", "Sk", "Sm"}
                or ord(candidate_text[0]) in range(0x370, 0x400)
            )
        )
    )
    return (
        -0.10 * paragraph_size <= float(previous.x0) - previous_formula_right
        <= 0.45 * paragraph_size
        and -0.10 * paragraph_size <= gap <= 0.12 * paragraph_size
        and candidate_is_math
    )


def _remove_absorbed_formula_base(
    paragraph_text: str,
    base: str,
    previous_formula: list[LTChar] | None,
    base_char: LTChar,
    paragraph_size: float,
    candidate: LTChar | None = None,
) -> str:
    """Remove an absorbed base and only a geometry-confirmed formula-island gap."""
    if not paragraph_text.endswith(base):
        return paragraph_text
    result = paragraph_text[: -len(base)]
    if not previous_formula or paragraph_size <= 0:
        return result
    marker_space = re.search(r"(\{v\d+\})([ \t]+)$", result)
    if marker_space is None:
        return result
    previous_right = max(float(char.x1) for char in previous_formula)
    base_gap = float(base_char.x0) - previous_right
    candidate_gap = (
        float(candidate.x0) - previous_right if candidate is not None else float("inf")
    )
    # An overprint accent/delimiter can start to the left of its ordinary-font
    # base.  In that case the base itself may appear slightly too far from the
    # preceding formula atom even though the current glyph visibly bridges the
    # island.  Accept either measurement, but only after the source text proved
    # this was a direct ``{vN} <single-letter>`` boundary above.
    if (
        -0.20 * paragraph_size <= base_gap <= 0.60 * paragraph_size
        or -0.20 * paragraph_size <= candidate_gap <= 0.60 * paragraph_size
    ):
        return result[: marker_space.start(2)]
    return result


_FORMULA_TRAILING_BOUNDARY = frozenset(",.;，。；")
_FORMULA_OPENING_OR_OPERATOR = frozenset("([{（［{=:+-−×·/∫∑∏√")
_FORMULA_CLOSING = frozenset(")] }）］}".replace(" ", ""))


_SPLIT_MATH_ISLAND_RE = re.compile(
    r"(?P<prefix>[A-Za-z]\s+)"
    r"\{v(?P<a>\d+)\}\s*\{v(?P<b>\d+)\}"
    r"(?P<middle>\s*[,;:]\s*[A-Za-z]\s*)"
    r"\{v(?P<c>\d+)\}"
)

_ENCLOSING_SPLIT_MATH_RE = re.compile(
    r"(?<![A-Za-z])(?P<left>[A-Za-z])\s*"
    r"\{v(?P<a>\d+)\}"
    r"(?P<middle>(?:\s*[,;:]\s*|\s+))"
    r"(?P<right>[A-Za-z])\s*"
    r"\{v(?P<b>\d+)\}"
)


def _merge_overlapping_split_math_islands(
    segments: list[str],
    formulas: list[list[LTChar]],
    formula_paragraphs: list[int],
    paragraph_text_chars: list[list[LTChar]],
) -> None:
    """Reunite a short formula whose enclosing glyphs occur late in PDF order.

    Some publishers emit ``m ∈ {c,q}`` as the membership glyph, ``c``, the
    comma/``q`` text, and finally both braces.  Independent placeholders then
    serialize the braces after their contents.  Merge only when the late group
    geometrically overlaps the preceding variable and encloses the intervening
    one-letter syntax on the same baseline.  Normal prose and merely adjacent
    independent formulas therefore remain untouched.
    """
    for paragraph_id, text in enumerate(segments):
        for match in list(_SPLIT_MATH_ISLAND_RE.finditer(text))[::-1]:
            a, b, c = (int(match.group(name)) for name in ("a", "b", "c"))
            if max(a, b, c) >= len(formulas):
                continue
            if any(formula_paragraphs[index] != paragraph_id for index in (a, b, c)):
                continue
            groups = (formulas[a], formulas[b], formulas[c])
            if any(not group or len(group) > 3 for group in groups):
                continue
            if any("\n" in "".join(ch.get_text() for ch in group) for group in groups):
                continue
            baseline = sum(float(ch.y0) for ch in groups[1]) / len(groups[1])
            size = max(float(ch.size) for group in groups for ch in group)
            if size <= 0 or any(
                abs(float(ch.y0) - baseline) > 0.30 * size
                for group in groups
                for ch in group
            ):
                continue
            a_right = max(float(ch.x1) for ch in groups[0])
            b_left = min(float(ch.x0) for ch in groups[1])
            b_right = max(float(ch.x1) for ch in groups[1])
            c_left = min(float(ch.x0) for ch in groups[2])
            c_right = max(float(ch.x1) for ch in groups[2])
            if not (
                -0.20 * size <= b_left - a_right <= 1.20 * size
                and c_left <= b_right + 0.10 * size
                and c_right > b_right
            ):
                continue
            middle_letters = re.findall(r"[A-Za-z]", match.group("middle"))
            if len(middle_letters) != 1:
                continue
            candidates = [
                ch
                for ch in paragraph_text_chars[paragraph_id]
                if ch.get_text() in ",;:" + middle_letters[0]
                and abs(float(ch.y0) - baseline) <= 0.30 * size
                and b_right - 0.15 * size <= float(ch.x0) <= c_right + 0.15 * size
            ]
            if not any(ch.get_text() == middle_letters[0] for ch in candidates):
                continue
            formulas[a] = groups[0] + groups[1] + candidates + groups[2]
            segments[paragraph_id] = (
                text[: match.start()]
                + match.group("prefix")
                + f"{{v{a}}}"
                + text[match.end() :]
            )

        text = segments[paragraph_id]
        for match in list(_ENCLOSING_SPLIT_MATH_RE.finditer(text))[::-1]:
            a, b = (int(match.group(name)) for name in ("a", "b"))
            if max(a, b) >= len(formulas):
                continue
            if any(formula_paragraphs[index] != paragraph_id for index in (a, b)):
                continue
            operator_group, enclosing_group = formulas[a], formulas[b]
            if (
                not operator_group
                or not enclosing_group
                or len(operator_group) > 3
                or len(enclosing_group) > 8
                or not any(
                    _MATH_FONT_RE.search(_formula_font_name(char))
                    for char in operator_group
                )
            ):
                continue
            size = max(
                float(char.size)
                for group in (operator_group, enclosing_group)
                for char in group
            )
            if size <= 0:
                continue
            baseline = sum(float(char.y0) for char in operator_group) / len(
                operator_group
            )
            if any(
                abs(float(char.y0) - baseline) > 0.30 * size
                for group in (operator_group, enclosing_group)
                for char in group
            ):
                continue
            operator_left = min(float(char.x0) for char in operator_group)
            operator_right = max(float(char.x1) for char in operator_group)
            enclosure_left = min(float(char.x0) for char in enclosing_group)
            enclosure_right = max(float(char.x1) for char in enclosing_group)
            if not (
                enclosure_left < operator_left - 0.10 * size
                and enclosure_right > operator_right + 0.50 * size
            ):
                continue

            text_chars = paragraph_text_chars[paragraph_id]
            left_candidates = [
                char
                for char in text_chars
                if char.get_text() == match.group("left")
                and abs(float(char.y0) - baseline) <= 0.30 * size
                and enclosure_left - 0.10 * size <= float(char.x0)
                and float(char.x1) <= operator_left + 0.20 * size
                and -0.10 * size
                <= float(char.x0) - enclosure_left
                <= 0.80 * size
            ]
            right_candidates = [
                char
                for char in text_chars
                if char.get_text() == match.group("right")
                and abs(float(char.y0) - baseline) <= 0.30 * size
                and operator_right - 0.20 * size <= float(char.x0)
                and float(char.x1) <= enclosure_right + 0.10 * size
                and -0.10 * size
                <= enclosure_right - float(char.x1)
                <= 2.00 * size
            ]
            if not left_candidates or not right_candidates:
                continue
            left_char = max(left_candidates, key=lambda char: float(char.x1))
            right_char = min(right_candidates, key=lambda char: float(char.x0))
            if left_char is right_char:
                continue
            middle_markers = [
                value
                for value in match.group("middle")
                if value in ",;:"
            ]
            middle_chars: list[LTChar] = []
            for marker in middle_markers:
                choices = [
                    char
                    for char in text_chars
                    if char.get_text() == marker
                    and char not in middle_chars
                    and abs(float(char.y0) - baseline) <= 0.30 * size
                    and operator_right - 0.10 * size
                    <= float(char.x0)
                    <= float(right_char.x0) + 0.10 * size
                ]
                if not choices:
                    break
                middle_chars.append(
                    min(
                        choices,
                        key=lambda char: abs(
                            float(char.x0) - operator_right
                        ),
                    )
                )
            if len(middle_chars) != len(middle_markers):
                continue
            if not (
                enclosure_left <= float(left_char.x0) < float(left_char.x1)
                <= operator_left + 0.20 * size
                and operator_right - 0.20 * size <= float(right_char.x0)
                < float(right_char.x1) <= enclosure_right + 0.10 * size
                and any(
                    float(char.x1) <= float(left_char.x0) + 0.10 * size
                    for char in enclosing_group
                )
                and any(
                    float(char.x0) >= float(right_char.x1) - 0.10 * size
                    for char in enclosing_group
                )
            ):
                continue
            formulas[a] = (
                operator_group
                + [left_char]
                + middle_chars
                + [right_char]
                + enclosing_group
            )
            segments[paragraph_id] = (
                text[: match.start()] + f"{{v{a}}}" + text[match.end() :]
            )


def _should_extend_formula_run(chars: list[LTChar], candidate: LTChar) -> bool:
    """Keep visually contiguous ordinary-font glyphs inside an inline formula.

    Scientific PDFs commonly typeset one expression with several fonts.  A Greek
    base glyph may be recognized as mathematics while its parentheses, Latin
    variable, differential, or operator uses the document's ordinary text font.
    Splitting at every font transition exposes the pieces as independently movable
    translation tokens and can scramble the expression.  This predicate extends an
    already-established formula only across the same visual line and a typesetting-
    sized gap.  It deliberately stops before normal prose after terminal punctuation.
    """

    if not chars:
        return False
    value = candidate.get_text()
    if not value or value.isspace():
        return False

    previous = chars[-1]
    previous_value = previous.get_text()
    em = max(float(getattr(candidate, "size", 0.0)), float(previous.size), 1.0)
    right_edge = max(float(char.x1) for char in chars)
    gap = float(candidate.x0) - right_edge
    if gap < -0.15 * em or gap > 0.40 * em:
        return False

    formula_bottom = min(float(char.y0) for char in chars)
    formula_top = max(float(char.y1) for char in chars)
    if (
        float(candidate.y1) < formula_bottom - 0.30 * em
        or float(candidate.y0) > formula_top + 0.30 * em
    ):
        return False

    first = value[0]
    last = previous_value[-1] if previous_value else ""
    formula_text = "".join(char.get_text() for char in chars)
    if (
        first in "([（［"
        and re.fullmatch(r"[\d\s,;\-–—.]+", formula_text) is not None
    ):
        return False
    if last.isalnum() and first in "([（［":
        return gap <= 0.12 * em
    if last in _FORMULA_TRAILING_BOUNDARY and first.isalpha():
        return gap <= 0.12 * em
    if last in _FORMULA_CLOSING and first.isalpha():
        return gap <= 0.20 * em
    if last.isalnum() and first.isalpha():
        return gap <= 0.12 * em
    if last in _FORMULA_OPENING_OR_OPERATOR:
        return True
    return True


def _is_hidden_glyph_repertoire_probe(
    chars: list[LTChar],
    page_width: float,
) -> bool:
    """Detect a clipped vertical font-glyph probe at the extreme page edge.

    Some publisher PDFs contain a rotated 0-9/punctuation repertoire just outside
    the visible crop.  Re-emitting it with reconstructed text operators can move it
    one point into view.  Requiring every digit, several punctuation characters,
    ASCII-only content, a narrow edge box, and non-horizontal transforms avoids
    suppressing legitimate vertical labels and page numbers.
    """

    if len(chars) < 12 or page_width <= 0:
        return False
    text = "".join(char.get_text() for char in chars)
    if not text or any(ord(char) > 0x7F for char in text):
        return False
    if not set("0123456789").issubset(text):
        return False
    if len(set(text) & set("():,;.!?")) < 3:
        return False
    if any(
        not _is_non_horizontal_text_matrix(
            getattr(
                char,
                "_pdf2zh_source_text_state",
                None,
            ).matrix
            if getattr(char, "_pdf2zh_source_text_state", None) is not None
            else char.matrix
        )
        for char in chars
    ):
        return False
    x0 = min(float(char.x0) for char in chars)
    x1 = max(float(char.x1) for char in chars)
    edge_limit = max(8.0, 0.015 * page_width)
    return x0 <= edge_limit and x1 <= edge_limit and x1 - x0 <= edge_limit


def _formula_horizontal_geometry(chars: list[LTChar]) -> tuple[float, float]:
    """Return the visual left anchor and width of a formula glyph run.

    PDF content order is not guaranteed to start with the leftmost glyph: scripts,
    accents, and diagram labels are often emitted first.  Anchoring reconstruction
    to ``chars[0].x0`` therefore creates negative offsets and can push later glyphs
    into the page margin.  Translatable inline formulas must therefore use their
    visual bounds.  Layout-class-0 runs are preserved page content rather than
    inline formulas, however, and can intentionally contain distant glyphs in PDF
    content order.  Those runs must retain the original first-glyph anchor so their
    existing relative offsets are reproduced instead of shifting the whole group.
    """

    if not chars:
        return 0.0, 0.0
    if any(
        getattr(char, "_pdf2zh_layout_class", None) == 0 for char in chars
    ):
        anchor_x = float(chars[0].x0)
        x1 = max(float(char.x1) for char in chars)
        return anchor_x, max(0.0, x1 - anchor_x)
    x0 = min(float(char.x0) for char in chars)
    x1 = max(float(char.x1) for char in chars)
    return x0, max(0.0, x1 - x0)


def _font_name(value) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(value or "").split("+")[-1]


def _is_prose_italic_font(value) -> bool:
    font = _font_name(value)
    return bool(_PROSE_ITALIC_FONT_RE.search(font)) and not bool(
        _MATH_FONT_RE.search(font)
    )


def _char_baseline(char: LTChar) -> float:
    state = getattr(char, "_pdf2zh_source_text_state", None)
    if state is not None:
        return float(state.matrix[5])
    return float(char.y0)


def _reconstruct_italic_run(chars: list[LTChar]) -> str:
    """Recover omitted word spaces from horizontal PDF glyph geometry."""
    pieces: list[str] = []
    previous: LTChar | None = None
    for char in chars:
        value = char.get_text()
        if not value:
            continue
        if previous is not None and not value.isspace():
            previous_value = previous.get_text()
            if previous_value and not previous_value.isspace():
                gap = float(char.x0) - float(previous.x1)
                em = max(float(char.size), float(previous.size), 1.0)
                if gap > 0.12 * em:
                    pieces.append(" ")
        pieces.append(value)
        previous = char
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def _is_high_confidence_prose_italic(
    chars: list[LTChar],
    paragraph_size: float,
) -> str | None:
    """Return reconstructed prose only when an italic formula run is clearly text."""
    visible = [char for char in chars if char.get_text() and not char.get_text().isspace()]
    if not visible:
        return None
    if any(getattr(char, "_pdf2zh_layout_class", 0) == 0 for char in visible):
        return None
    if any(not _is_prose_italic_font(char.fontname) for char in visible):
        return None
    for char in visible:
        state = getattr(char, "_pdf2zh_source_text_state", None)
        matrix = state.matrix if state is not None else char.matrix
        if _is_non_horizontal_text_matrix(matrix):
            return None

    sizes = [float(char.size) for char in visible]
    typical_size = float(np.median(sizes))
    if typical_size <= 0:
        return None
    if max(sizes) / min(sizes) > 1.05:
        return None
    if paragraph_size > 0 and not (0.90 <= typical_size / paragraph_size <= 1.10):
        return None
    baselines = [_char_baseline(char) for char in visible]
    if max(baselines) - min(baselines) > max(0.8, 0.08 * typical_size):
        return None
    for previous, current in zip(visible, visible[1:]):
        gap = float(current.x0) - float(previous.x1)
        if gap > 0.60 * typical_size:
            return None

    text = _reconstruct_italic_run(chars)
    if not text or any(
        not (char.isalpha() or char.isspace() or char in "-‐‑–'’.")
        for char in text
    ):
        return None
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 4:
        return None
    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    if not words:
        return None

    compact = "".join(letters).casefold()
    if compact in _NON_TRANSLATABLE_ITALIC_PHRASES:
        return None
    if len(words) == 1:
        word = words[0]
        lowered = word.casefold()
        if lowered in _MATH_WORDS or word.isupper():
            return None
        if word != word.lower() and word != word.capitalize():
            return None
    if (
        len(words) == 2
        and words[0][:1].isupper()
        and words[0][1:].islower()
        and words[1].islower()
    ):
        # Conservative handling of binomial taxonomic names.
        return None
    return text


def _has_inline_prose_context(segment: str, formula_id: int) -> bool:
    marker = re.compile(rf"\{{\s*v\s*{formula_id}\s*\}}", re.IGNORECASE)
    match = marker.search(segment)
    if match is None:
        return False
    left = segment[: match.start()].rstrip()
    right = segment[match.end() :].lstrip()
    blocked = "=+−-*/×÷<>[]_"
    if left and left[-1] in blocked:
        return False
    if right and right[0] in blocked:
        return False
    left_is_prose = re.search(r"[A-Za-z]{2,}[\s,;:]*$", left) is not None
    right_is_prose = re.match(r"[\s,;:]*[A-Za-z]{2,}", right) is not None
    return left_is_prose and right_is_prose


def _collect_translatable_italic_runs(
    formula_runs: list[list[LTChar]],
    formula_paragraphs: list[int],
    paragraphs: list["Paragraph"],
    segments: list[str],
) -> dict[int, str]:
    candidates: dict[int, str] = {}
    for formula_id, (chars, paragraph_id) in enumerate(
        zip(formula_runs, formula_paragraphs, strict=True)
    ):
        if not 0 <= paragraph_id < len(paragraphs):
            continue
        text = _is_high_confidence_prose_italic(
            chars,
            paragraphs[paragraph_id].size,
        )
        if text is not None and _has_inline_prose_context(
            segments[paragraph_id], formula_id
        ):
            candidates[formula_id] = text
    return candidates


def _formula_ids_in_text(text: str) -> tuple[int, ...]:
    ids: list[int] = []
    for match in _FORMULA_MARKER_RE.finditer(text):
        try:
            ids.append(int(re.sub(r"\s+", "", match.group(1))))
        except ValueError:
            continue
    return tuple(ids)


def _safe_formula_unicode_char(value: str) -> bool:
    if not value or "\r" in value or "\n" in value or "\ufffd" in value:
        return False
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("C") or category in {"Zl", "Zp"}:
            return False
    return True


def _decode_formula_glyph(char: LTChar) -> str | None:
    """Return a glyph only when the PDF font's Unicode map is fully trustworthy."""
    try:
        decoded = char.font.to_unichr(char.cid)
    except (PDFUnicodeNotDefined, KeyError, TypeError, ValueError):
        return None
    visible = char.get_text()
    if (
        not isinstance(decoded, str)
        or decoded != visible
        or re.search(r"\(cid\s*:", decoded, re.IGNORECASE)
        or not _safe_formula_unicode_char(decoded)
    ):
        return None
    # A few physics PDFs map a visual ASCII tilde to the spacing modifier U+02DC.
    return "~" if decoded == "\u02dc" else decoded


def _escape_formula_context_glyph(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _serialize_safe_inline_formula(
    formula_id: int,
    chars: list[LTChar],
    lines: list[LTLine],
    paragraph_id: int,
    paragraphs: list["Paragraph"],
    segments: list[str],
    italic_candidates: dict[int, str] | None = None,
) -> str | None:
    """Build a read-only semantic mirror for a high-confidence inline formula.

    This string is model context only.  Rendering continues to use the original
    PDF CID/font objects stored on each ``LTChar``.
    """
    if (
        formula_id in (italic_candidates or {})
        or lines
        or not 0 <= paragraph_id < len(paragraphs)
        or not 0 <= paragraph_id < len(segments)
        or not 1 <= len(chars) <= _MAX_READONLY_FORMULA_GLYPHS
    ):
        return None

    segment = segments[paragraph_id]
    if _formula_ids_in_text(segment).count(formula_id) != 1:
        return None
    prose = _FORMULA_MARKER_RE.sub(" ", segment)
    if len(re.findall(r"[A-Za-z]{2,}", prose)) < 2:
        return None

    paragraph_size = float(paragraphs[paragraph_id].size)
    if paragraph_size <= 0:
        return None
    visible_chars = [char for char in chars if char.get_text()]
    if not visible_chars:
        return None

    decoded_glyphs: list[str] = []
    for char in visible_chars:
        if getattr(char, "_pdf2zh_layout_class", 0) == 0:
            return None
        state = getattr(char, "_pdf2zh_source_text_state", None)
        matrix = state.matrix if state is not None else char.matrix
        if _is_non_horizontal_text_matrix(matrix):
            return None
        decoded = _decode_formula_glyph(char)
        if decoded is None:
            return None
        decoded_glyphs.append(decoded)

    sizes = [float(char.size) for char in visible_chars]
    if min(sizes) < 0.55 * paragraph_size or max(sizes) > 1.25 * paragraph_size:
        return None
    x0 = min(float(char.x0) for char in visible_chars)
    x1 = max(float(char.x1) for char in visible_chars)
    y0 = min(float(char.y0) for char in visible_chars)
    y1 = max(float(char.y1) for char in visible_chars)
    if x1 - x0 > 12.0 * paragraph_size or y1 - y0 > 1.8 * paragraph_size:
        return None
    baselines = [_char_baseline(char) for char in visible_chars]
    if max(baselines) - min(baselines) > 0.75 * paragraph_size:
        return None

    main_baselines = [
        baseline
        for char, baseline in zip(visible_chars, baselines, strict=True)
        if float(char.size) >= 0.90 * paragraph_size
    ]
    main_baseline = float(np.median(main_baselines or baselines))

    pieces: list[str] = []
    script_kind: str | None = None
    script_pieces: list[str] = []
    previous: LTChar | None = None

    def flush_script() -> None:
        nonlocal script_kind, script_pieces
        if script_kind is not None and script_pieces:
            marker = "^" if script_kind == "sup" else "_"
            pieces.append(f"{marker}{{{''.join(script_pieces)}}}")
        script_kind = None
        script_pieces = []

    for char, glyph, baseline in zip(
        visible_chars,
        decoded_glyphs,
        baselines,
        strict=True,
    ):
        if glyph.isspace():
            flush_script()
            if pieces and pieces[-1] != " ":
                pieces.append(" ")
            previous = char
            continue
        kind: str | None = None
        if float(char.size) <= 0.82 * paragraph_size:
            offset = baseline - main_baseline
            if offset > 0.12 * paragraph_size:
                kind = "sup"
            elif offset < -0.12 * paragraph_size:
                kind = "sub"
        escaped = _escape_formula_context_glyph(glyph)
        if kind is not None:
            if script_kind != kind:
                flush_script()
                script_kind = kind
            script_pieces.append(escaped)
        else:
            flush_script()
            if previous is not None:
                gap = float(char.x0) - float(previous.x1)
                if gap > 0.25 * paragraph_size and pieces and pieces[-1] != " ":
                    pieces.append(" ")
            pieces.append(escaped)
        previous = char
    flush_script()

    serialized = re.sub(r"\s+", " ", "".join(pieces)).strip()
    if (
        not serialized
        or len(serialized) > _MAX_READONLY_FORMULA_CODEPOINTS
        or not any(char.isalpha() or char.isdigit() for char in serialized)
        or not _safe_formula_unicode_char(serialized)
    ):
        return None
    return unicodedata.normalize("NFC", serialized)


def _collect_readonly_formula_contexts(
    formula_runs: list[list[LTChar]],
    formula_lines: list[list[LTLine]],
    formula_paragraphs: list[int],
    paragraphs: list["Paragraph"],
    segments: list[str],
    italic_candidates: dict[int, str] | None = None,
) -> dict[int, str]:
    contexts: dict[int, str] = {}
    for formula_id, (chars, lines, paragraph_id) in enumerate(
        zip(formula_runs, formula_lines, formula_paragraphs, strict=True)
    ):
        serialized = _serialize_safe_inline_formula(
            formula_id,
            chars,
            lines,
            paragraph_id,
            paragraphs,
            segments,
            italic_candidates,
        )
        if serialized is not None and not _looks_like_latin_identifier_fragment(
            formula_id,
            serialized,
            segments[paragraph_id],
        ):
            contexts[formula_id] = serialized
    return contexts


def _looks_like_latin_identifier_fragment(
    formula_id: int,
    serialized: str,
    segment: str,
) -> bool:
    """Reject conservative ASCII identifiers misdetected as formulas.

    A short run such as ``PY`` is ambiguous on its own and can be a genuine
    mathematical identifier.  It is treated as prose only when its placeholder
    is directly joined, without whitespace, to a neighbouring multi-letter
    Latin identifier in the source paragraph (for example ``{v0}EPR``).  The
    layout extractor may also group the complete product/library name into one
    run, as with ``PYEPR``.  An independent 3--12 character all-uppercase ASCII
    alphanumeric identifier is therefore omitted from the semantic formula
    mirror as well.  It must contain a letter, so a page or equation number does
    not take this path.

    The original placeholder remains in the translation stream, so rendering still
    reuses the source PDF glyphs; only the read-only semantic formula mirror is
    omitted.
    """
    if (
        3 <= len(serialized) <= 12
        and serialized.isascii()
        and serialized.isalnum()
        and serialized.upper() == serialized
        and any(char.isalpha() for char in serialized)
    ):
        return True
    if re.fullmatch(r"[A-Za-z]{1,4}", serialized) is None:
        return False
    marker = re.compile(rf"\{{v\s*{formula_id}\s*\}}")
    for match in marker.finditer(segment):
        left = segment[: match.start()]
        right = segment[match.end() :]
        if re.search(r"[A-Za-z]{2,}$", left) or re.match(r"^[A-Za-z]{2,}", right):
            return True
    return False


def _formula_context_for_text(
    text: str,
    contexts_by_id: dict[int, str] | None,
) -> dict[str, str]:
    """Select only mappings for exact placeholders present in one model item."""
    if not contexts_by_id:
        return {}
    selected: dict[str, str] = {}
    for match in _FORMULA_MARKER_RE.finditer(text):
        try:
            formula_id = int(re.sub(r"\s+", "", match.group(1)))
        except ValueError:
            continue
        value = contexts_by_id.get(formula_id)
        if value is not None:
            selected[match.group(0)] = value
    return selected


def _tag_translatable_italic_formulas(
    text: str,
    candidates: dict[int, str],
) -> tuple[str, tuple[int, ...]]:
    if not candidates or ITALIC_TAG_PREFIX in text:
        return text, ()
    used: list[int] = []

    def replace(match: re.Match[str]) -> str:
        try:
            formula_id = int(re.sub(r"\s+", "", match.group(1)))
        except ValueError:
            return match.group(0)
        phrase = candidates.get(formula_id)
        if phrase is None:
            return match.group(0)
        used.append(formula_id)
        return (
            f"[[PDF2ZH_ITALIC_{formula_id}_BEGIN]]"
            f"{phrase}"
            f"[[PDF2ZH_ITALIC_{formula_id}_END]]"
        )

    return _FORMULA_MARKER_RE.sub(replace, text), tuple(used)


def _gen_target_text_op(
    font: str,
    size: float,
    x: float,
    y: float,
    rtxt: str,
    italic: bool = False,
) -> str:
    matrix = f"1 0 {ITALIC_SHEAR:f} 1" if italic else "1 0 0 1"
    return (
        f"/{font} {size:f} Tf {matrix} "
        f"{x:f} {y:f} Tm [<{rtxt}>] TJ "
    )


def _should_pre_wrap_line_break_unit(
    unit: LineBreakUnit,
    *,
    current_x: float,
    unit_extent: float,
    right_limit: float,
    line_capacity: float,
) -> bool:
    """Return whether an atomic unit should move off a non-empty line.

    An over-wide paired unit cannot fit on any line, but leaving its opening
    punctuation in the small remainder of the current line violates Chinese
    line-ending rules.  Move that unit to a fresh line first, then let the
    renderer's ordinary character fallback split its contents there.  Other
    over-wide non-splittable units (notably formulas) retain their existing
    placement behaviour.
    """
    if current_x + unit_extent <= right_limit:
        return False
    first_visible = next(
        (atom for atom in unit.atoms if not atom.zero_width),
        None,
    )
    starts_with_opening_punctuation = (
        first_visible is not None
        and first_visible.kind == "char"
        and first_visible.text in CJK_PROHIBITED_LINE_END
    )
    return (
        unit_extent <= line_capacity
        or unit.fallback_splittable
        or starts_with_opening_punctuation
    )


_SUSPENDED_HYPHEN_CONTINUATION_INITIALS = frozenset("aot")


def _should_insert_reconstructed_line_space(
    preceding_text: str,
    next_character: str,
) -> bool:
    """Decide whether a PDF line wrap needs a reconstructed prose space.

    A line-final ASCII hyphen is already the separator between two extracted
    Latin fragments, so inserting another space creates artifacts such as
    ``Cali- fornia`` and ``NbTi- NbTi``.  Keep the space for the conventional
    suspended-compound continuations ``and``, ``or``, and ``to`` (identified by
    their first character); this fails closed rather than collapsing those
    legitimate constructions.  Same-line hyphens never pass through this
    helper.
    """

    fragment = re.search(r"[A-Za-z][A-Za-z0-9]{1,31}-$", preceding_text)
    if fragment is None or re.fullmatch(r"[A-Za-z0-9]", next_character) is None:
        return True
    if next_character.islower() and next_character in (
        _SUSPENDED_HYPHEN_CONTINUATION_INITIALS
    ):
        return True
    return False


@dataclass(frozen=True, slots=True)
class TargetVerticalFit:
    line_height: float
    render_scale: float
    contained: bool


_MIN_COMPACT_LINE_HEIGHT = 0.95


def _fit_target_vertical_layout(
    *,
    line_count: int,
    font_size: float,
    available_height: float,
    default_line_height: float,
) -> TargetVerticalFit:
    """Choose readable leading, then uniformly shrink an overfull paragraph.

    Historical rendering compacts leading in 0.05 steps down to 0.95.  When
    that is still too tall, reducing leading further would make adjacent CJK
    lines collide.  Instead, keep 0.95 leading and uniformly scale the movable
    text/formula geometry so it remains inside the source paragraph box.
    """
    if (
        line_count <= 0
        or font_size <= 0
        or available_height <= 0
        or default_line_height <= 0
    ):
        return TargetVerticalFit(default_line_height, 1.0, False)

    line_height = default_line_height
    while (
        line_count * font_size * line_height > available_height
        and line_height > _MIN_COMPACT_LINE_HEIGHT
    ):
        line_height = max(
            _MIN_COMPACT_LINE_HEIGHT,
            round(line_height - 0.05, 10),
        )

    occupied_height = line_count * font_size * line_height
    render_scale = min(1.0, available_height / occupied_height)
    return TargetVerticalFit(line_height, render_scale, True)


@dataclass(frozen=True)
class OriginalTextState:
    matrix: Matrix
    fontsize: float
    scaling: float
    rise: float


def _is_non_horizontal_text_matrix(
    matrix: Matrix,
    tolerance: float = 1e-6,
) -> bool:
    """Return whether the text baseline is meaningfully non-horizontal."""
    a, b, _, _, _, _ = matrix
    baseline_scale = max(abs(a), abs(b))
    return baseline_scale > tolerance and abs(b) > baseline_scale * tolerance


def _gen_preserved_text_op(
    font: str,
    state: OriginalTextState,
    rtxt: str,
) -> str:
    """Rebuild protected transformed text without losing its source matrix."""
    a, b, c, d, e, f = state.matrix
    return (
        f"/{font} {state.fontsize:f} Tf "
        f"{state.scaling * 100:f} Tz {state.rise:f} Ts "
        f"{a:f} {b:f} {c:f} {d:f} {e:f} {f:f} Tm "
        f"[<{rtxt}>] TJ 100 Tz 0 Ts "
    )


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
    ) -> None:
        PDFConverter.__init__(self, rsrcmgr, None, "utf-8", 1, None)

    def begin_page(self, page, ctm) -> None:
        # 重载替换 cropbox
        (x0, y0, x1, y1) = page.cropbox
        (x0, y0) = apply_matrix_pt(ctm, (x0, y0))
        (x1, y1) = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0 - x1), abs(y0 - y1))
        self.cur_item = LTPage(page.pageno, mediabox)

    def end_page(self, page):
        # 重载返回指令流
        return self.receive_layout(self.cur_item)

    def begin_figure(self, name, bbox, matrix) -> None:
        # 重载设置 pageid
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        self.cur_item.pageid = self._stack[-1].pageid

    def end_figure(self, _: str) -> None:
        # 重载返回指令流
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure), str(type(self.cur_item))
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return self.receive_layout(fig)

    def render_char(
        self,
        matrix,
        font,
        fontsize: float,
        scaling: float,
        rise: float,
        cid: int,
        ncs,
        graphicstate: PDFGraphicState,
    ) -> float:
        # 重载设置 cid 和 font
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, str), str(type(text))
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            textwidth,
            textdisp,
            ncs,
            graphicstate,
        )
        item._pdf2zh_source_text_state = OriginalTextState(
            matrix=matrix,
            fontsize=float(fontsize),
            scaling=float(scaling),
            rise=float(rise),
        )
        self.cur_item.add(item)
        item.cid = cid  # hack 插入原字符编码
        item.font = font  # hack 插入原字符字体
        return item.adv


class Paragraph:
    def __init__(
        self,
        y,
        x,
        x0,
        x1,
        y0,
        y1,
        size,
        brk,
        *,
        page_id: int = -1,
        layout_class: int = -1,
        region_kind: str = "",
    ):
        self.y: float = y  # 初始纵坐标
        self.x: float = x  # 初始横坐标
        self.x0: float = x0  # 左边界
        self.x1: float = x1  # 右边界
        self.y0: float = y0  # 上边界
        self.y1: float = y1  # 下边界
        self.size: float = size  # 字体大小
        self.brk: bool = brk  # 换行标记
        self.break_offsets: list[int] = []  # 原文段落内换行在 sstk 中的位置
        self.page_id = int(page_id)
        self.layout_class = int(layout_class)
        self.region_kind = region_kind


@dataclass
class FragmentOverride:
    span: FragmentSpan
    translation: str


@dataclass
class PageLayoutDraft:
    ltpage: LTPage
    page_id: int
    page_xref: int
    width: float
    height: float
    sstk: list[str]
    pstk: list[Paragraph]
    var: list[list[LTChar]]
    varl: list[list[LTLine]]
    varf: list[float]
    varp: list[int]
    vlen: list[float]
    lstk: list[LTLine]
    formula_texts: list[str]
    italic_candidates: dict[int, str]
    readonly_formula_contexts: dict[int, str]
    fontid: dict
    fontmap: dict
    overrides: list[FragmentOverride]


# fmt: off
class TranslateConverter(PDFConverterEx):
    def __init__(
        self,
        rsrcmgr,
        vfont: str = None,
        vchar: str = None,
        thread: int = 0,
        layout={},
        lang_in: str = "",
        lang_out: str = "",
        service: str = "",
        noto_name: str = "",
        noto: Font = None,
        envs: Dict = None,
        prompt: Template = None,
        ignore_cache: bool = False,
    ) -> None:
        super().__init__(rsrcmgr)
        self.vfont = vfont
        self.vchar = vchar
        self.thread = thread
        self.layout = layout
        self.noto_name = noto_name
        self.noto = noto
        self.translator: BaseTranslator = None
        # e.g. "ollama:gemma2:9b" -> ["ollama", "gemma2:9b"]
        param = service.split(":", 1)
        service_name = param[0]
        service_model = param[1] if len(param) > 1 else None
        if not envs:
            envs = {}
        for translator in [GoogleTranslator, BingTranslator, DeepLTranslator, DeepLXTranslator, OllamaTranslator, XinferenceTranslator, AzureOpenAITranslator,
                           OpenAITranslator, ZhipuTranslator, ModelScopeTranslator, SiliconTranslator, GeminiTranslator, AzureTranslator, TencentTranslator, DifyTranslator, AnythingLLMTranslator, ArgosTranslator, GrokTranslator, GroqTranslator, DeepseekTranslator, OpenAIlikedTranslator, CodexTranslator, QwenMtTranslator]:
            if service_name == translator.name:
                self.translator = translator(lang_in, lang_out, service_model, envs=envs, prompt=prompt, ignore_cache=ignore_cache)
        if not self.translator:
            raise ValueError("Unsupported translation service")
        if self.translator.name == "codex":
            self.translator.set_concurrency(self.thread)
        self.translation_policy = DocumentTranslationPolicy()
        self.layout_region_types: dict[int, dict[int, str]] = {}
        self._pending_page: PageLayoutDraft | None = None
        self._flow_token_counter = 0

    @staticmethod
    def _is_passthrough_text(s: str) -> bool:
        return (
            not s.strip()
            or re.fullmatch(r"\{v\d+\}", s.strip()) is not None
            or FLOW_TOKEN_RE.fullmatch(s.strip()) is not None
        )

    def _allocate_flow_token(self) -> str:
        counter = int(getattr(self, "_flow_token_counter", 0))
        self._flow_token_counter = counter + 1
        return f"{FLOW_TOKEN_PREFIX}{counter}]]"

    def _mask_protected_literals(
        self,
        text: str,
    ) -> tuple[str, dict[str, str]]:
        """Normalize and hide URLs, DOIs, and emails from the translator.

        FLOW tokens are already opaque, ordered tokens in the Codex protocol.
        Reusing that protocol prevents a model from inserting spaces, translating
        domain punctuation, or rewriting an identifier while keeping the actual
        literal local to the converter.
        """

        normalized = normalize_protected_literals(text)
        literals = list(iter_protected_literals(normalized))
        if not literals:
            return normalized, {}

        pieces: list[str] = []
        replacements: dict[str, str] = {}
        cursor = 0
        for literal in literals:
            pieces.append(normalized[cursor : literal.start])
            token = self._allocate_flow_token()
            pieces.append(token)
            replacements[token] = literal.text
            cursor = literal.end
        pieces.append(normalized[cursor:])
        return "".join(pieces), replacements

    @staticmethod
    def _restore_protected_literals(
        text: str,
        replacements: dict[str, str],
    ) -> str:
        restored = text
        for token, literal in replacements.items():
            if restored.count(token) != 1:
                raise ValueError(
                    "translation damaged a protected URL, DOI, or email token"
                )
            restored = restored.replace(token, literal, 1)
        return restored

    def _mask_protected_literal_batch(
        self,
        texts: list[str],
    ) -> tuple[list[str], list[dict[str, str]]]:
        masked: list[str] = []
        replacements: list[dict[str, str]] = []
        for text in texts:
            masked_text, item_replacements = self._mask_protected_literals(text)
            masked.append(masked_text)
            replacements.append(item_replacements)
        return masked, replacements

    @staticmethod
    def _restore_protected_literal_batch(
        texts: list[str],
        replacements: list[dict[str, str]],
    ) -> list[str]:
        if len(texts) != len(replacements):
            raise ValueError("protected literal result count mismatch")
        return [
            TranslateConverter._restore_protected_literals(text, mapping)
            for text, mapping in zip(texts, replacements, strict=True)
        ]

    @staticmethod
    def _validate_batch_result(
        translated,
        expected_count: int,
        operation: str,
    ) -> list[str]:
        if isinstance(translated, (str, bytes)):
            raise TypeError(f"{operation} returned a scalar instead of a list")
        try:
            results = list(translated)
        except TypeError as exc:
            raise TypeError(f"{operation} did not return an iterable") from exc
        if len(results) != expected_count:
            raise ValueError(
                f"{operation} returned {len(results)} items for "
                f"{expected_count} source items"
            )
        if not all(isinstance(result, str) for result in results):
            raise TypeError(f"{operation} returned a non-string item")
        return results

    def _translate_text_segments(
        self,
        sstk: list[str],
        formula_contexts: list[dict[str, str]] | None = None,
    ) -> list[str]:
        if not sstk:
            return []
        if formula_contexts is None:
            formula_contexts = [{} for _ in sstk]
        if len(formula_contexts) != len(sstk):
            raise ValueError(
                "formula context count does not match source segment count"
            )
        sstk, literal_replacements = self._mask_protected_literal_batch(sstk)

        @retry(wait=wait_fixed(1))
        def worker(s: str):
            if self._is_passthrough_text(s):
                return s
            try:
                return self.translator.translate(s)
            except BaseException as e:
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.exception(e, exc_info=False)
                raise e

        @retry(wait=wait_fixed(1), stop=stop_after_attempt(3))
        def batch_worker(texts: list[str]):
            try:
                translated = self.translator.translate_batch(texts)
                return self._validate_batch_result(
                    translated,
                    len(texts),
                    "translate_batch",
                )
            except BaseException as e:
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.exception(e, exc_info=False)
                raise e

        @retry(wait=wait_fixed(1), stop=stop_after_attempt(3))
        def contextual_batch_worker(
            texts: list[str],
            contexts: list[dict[str, str]],
        ):
            contextual_translator = getattr(
                self.translator,
                "translate_batch_with_formula_contexts",
                None,
            )
            if not callable(contextual_translator):
                return batch_worker(texts)
            try:
                translated = contextual_translator(texts, contexts)
                return self._validate_batch_result(
                    translated,
                    len(texts),
                    "translate_batch_with_formula_contexts",
                )
            except BaseException as e:
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.exception(e, exc_info=False)
                raise e

        if getattr(self.translator, "name", "") == "codex":
            results = list(sstk)
            ordinary_indices: list[int] = []
            ordinary_texts: list[str] = []
            contextual_indices: list[int] = []
            contextual_texts: list[str] = []
            contextual_contexts: list[dict[str, str]] = []
            for idx, (text, context) in enumerate(
                zip(sstk, formula_contexts, strict=True)
            ):
                if self._is_passthrough_text(text):
                    continue
                if context:
                    contextual_indices.append(idx)
                    contextual_texts.append(text)
                    contextual_contexts.append(context)
                else:
                    ordinary_indices.append(idx)
                    ordinary_texts.append(text)
            if ordinary_texts:
                translated_batch = batch_worker(ordinary_texts)
                for idx, translated in zip(
                    ordinary_indices, translated_batch, strict=True
                ):
                    results[idx] = translated
            if contextual_texts:
                translated_batch = contextual_batch_worker(
                    contextual_texts,
                    contextual_contexts,
                )
                for idx, translated in zip(
                    contextual_indices,
                    translated_batch,
                    strict=True,
                ):
                    results[idx] = translated
            return self._restore_protected_literal_batch(
                results,
                literal_replacements,
            )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.thread
        ) as executor:
            results = list(executor.map(worker, sstk))
        if len(results) != len(sstk):
            raise ValueError(
                f"translate returned {len(results)} items for {len(sstk)} source items"
            )
        return self._restore_protected_literal_batch(
            results,
            literal_replacements,
        )

    def _translate_reference_segments(
        self,
        entries: list[str],
        cache_contexts: list[str],
    ) -> list[str]:
        if not entries:
            return []
        if len(entries) != len(cache_contexts):
            raise ValueError(
                "reference entry/cache-context counts do not match"
            )
        normalized_entries = [
            normalize_protected_literals(entry) for entry in entries
        ]
        reference_translator = getattr(
            self.translator, "translate_reference_entries", None
        )
        if (
            getattr(self.translator, "name", "") != "codex"
            or not callable(reference_translator)
        ):
            return normalized_entries
        masked_entries, literal_replacements = self._mask_protected_literal_batch(
            normalized_entries
        )

        @retry(wait=wait_fixed(1), stop=stop_after_attempt(3))
        def reference_worker(
            texts: list[str], contexts: list[str]
        ) -> list[str]:
            try:
                translated = reference_translator(
                    texts, cache_contexts=contexts
                )
                return self._validate_batch_result(
                    translated,
                    len(texts),
                    "translate_reference_entries",
                )
            except BaseException as e:
                if log.isEnabledFor(logging.DEBUG):
                    log.exception(e)
                else:
                    log.exception(e, exc_info=False)
                raise e

        return self._restore_protected_literal_batch(
            reference_worker(masked_entries, cache_contexts),
            literal_replacements,
        )

    def _translate_styled_segments(
        self,
        texts: list[str],
        formula_contexts: list[dict[str, str]] | None = None,
    ) -> list[str | None]:
        if not texts:
            return []
        if formula_contexts is None:
            formula_contexts = [{} for _ in texts]
        if len(formula_contexts) != len(texts):
            raise ValueError(
                "styled formula context count does not match source segment count"
            )
        texts, literal_replacements = self._mask_protected_literal_batch(texts)
        styled_translator = getattr(
            self.translator,
            "translate_styled_batch",
            None,
        )
        if not callable(styled_translator):
            return [None] * len(texts)
        try:
            translated = styled_translator(
                texts,
                formula_contexts=formula_contexts,
            )
            if isinstance(translated, (str, bytes)):
                raise TypeError(
                    "translate_styled_batch returned a scalar instead of a list"
                )
            results = list(translated)
            if len(results) != len(texts):
                raise ValueError(
                    "translate_styled_batch result count does not match input"
                )
            if any(
                result is not None and not isinstance(result, str)
                for result in results
            ):
                raise TypeError(
                    "translate_styled_batch returned an unsupported item"
                )
            return [
                None
                if result is None
                else self._restore_protected_literals(result, replacements)
                for result, replacements in zip(
                    results,
                    literal_replacements,
                    strict=True,
                )
            ]
        except BaseException as exc:
            if log.isEnabledFor(logging.DEBUG):
                log.exception(exc)
            else:
                log.exception(exc, exc_info=False)
            return [None] * len(texts)

    def _translate_planned_segments(
        self,
        sstk: list[str],
        pstk: list[Paragraph],
        formula_texts: list[str],
        page_width: float,
        italic_candidates: dict[int, str] | None = None,
        readonly_formula_contexts: dict[int, str] | None = None,
    ) -> list[str]:
        if len(sstk) != len(pstk):
            raise ValueError(
                f"paragraph text/property count mismatch: {len(sstk)} != {len(pstk)}"
            )

        plans = []
        for segment_index, (text, paragraph) in enumerate(
            zip(sstk, pstk, strict=True)
        ):
            source = SourceSegment(
                index=segment_index,
                text=text,
                x0=paragraph.x0,
                x1=paragraph.x1,
                y0=paragraph.y0,
                y1=paragraph.y1,
                size=paragraph.size,
                page_width=page_width,
                break_offsets=tuple(paragraph.break_offsets),
                region_kind=paragraph.region_kind,
            )
            plans.append(
                self.translation_policy.plan_segment(
                    source,
                    formula_texts=formula_texts,
                )
            )

        part_results: list[list[str | None]] = [
            [None] * len(plan.parts) for plan in plans
        ]
        ordinary_locations: list[tuple[int, int, str, str]] = []
        ordinary_texts: list[str] = []
        ordinary_formula_contexts: list[dict[str, str]] = []
        styled_locations: list[tuple[int, int, str, str]] = []
        styled_texts: list[str] = []
        styled_formula_contexts: list[dict[str, str]] = []
        reference_locations: list[tuple[int, int]] = []
        reference_texts: list[str] = []
        reference_cache_contexts: list[str] = []

        for plan_index, plan in enumerate(plans):
            for part_index, part in enumerate(plan.parts):
                if part.role == ROLE_PRESERVE:
                    part_results[plan_index][part_index] = (
                        normalize_protected_literals(part.text)
                    )
                elif part.role in {ROLE_TRANSLATE, ROLE_AFFILIATION}:
                    styled_text, style_ids = _tag_translatable_italic_formulas(
                        part.text,
                        italic_candidates or {},
                    )
                    if style_ids:
                        styled_locations.append(
                            (plan_index, part_index, part.role, part.text)
                        )
                        styled_texts.append(styled_text)
                        styled_formula_contexts.append(
                            _formula_context_for_text(
                                styled_text,
                                readonly_formula_contexts,
                            )
                        )
                    else:
                        ordinary_locations.append(
                            (plan_index, part_index, part.role, part.text)
                        )
                        ordinary_texts.append(part.text)
                        ordinary_formula_contexts.append(
                            _formula_context_for_text(
                                part.text,
                                readonly_formula_contexts,
                            )
                        )
                elif part.role == ROLE_REFERENCE:
                    reference_locations.append((plan_index, part_index))
                    reference_texts.append(part.text)
                    reference_cache_contexts.append(
                        formula_cache_signature(part.text, formula_texts)
                    )
                else:
                    raise ValueError(f"Unsupported translation role: {part.role}")

        ordinary_results = self._translate_text_segments(
            ordinary_texts,
            ordinary_formula_contexts,
        )
        if len(ordinary_results) != len(ordinary_locations):
            raise ValueError(
                "ordinary translation result count does not match planned parts"
            )
        for location, translated in zip(
            ordinary_locations, ordinary_results, strict=True
        ):
            plan_index, part_index, role, source_text = location
            if role == ROLE_AFFILIATION:
                translated = restore_affiliation_breaks(
                    source_text,
                    translated,
                    formula_texts=formula_texts,
                )
            part_results[plan_index][part_index] = translated

        styled_results = self._translate_styled_segments(
            styled_texts,
            styled_formula_contexts,
        )
        if len(styled_results) != len(styled_locations):
            raise ValueError(
                "styled translation result count does not match planned parts"
            )
        for location, translated, formula_context in zip(
            styled_locations,
            styled_results,
            styled_formula_contexts,
            strict=True,
        ):
            plan_index, part_index, role, source_text = location
            if translated is None:
                # Fail closed: the original {vN} path keeps the source italic run
                # intact if the model damaged a style or formula marker.
                translated = self._translate_text_segments(
                    [source_text],
                    [formula_context],
                )[0]
            if role == ROLE_AFFILIATION:
                translated = restore_affiliation_breaks(
                    source_text,
                    translated,
                    formula_texts=formula_texts,
                )
            part_results[plan_index][part_index] = translated

        reference_results = self._translate_reference_segments(
            reference_texts,
            reference_cache_contexts,
        )
        if len(reference_results) != len(reference_locations):
            raise ValueError(
                "reference translation result count does not match planned parts"
            )
        for location, translated in zip(
            reference_locations, reference_results, strict=True
        ):
            plan_index, part_index = location
            part_results[plan_index][part_index] = translated

        translated_segments: list[str] = []
        for plan, results in zip(plans, part_results, strict=True):
            pieces: list[str] = []
            for part, translated in zip(plan.parts, results, strict=True):
                if translated is None:
                    raise ValueError("A planned translation part has no result")
                if part.break_before and pieces:
                    preceding = "".join(pieces).rstrip(" \t\u00a0")
                    pieces = [preceding]
                    if preceding and not preceding.endswith(("\n", "\r")):
                        pieces.append("\n")
                pieces.append(translated)
            translated_segments.append("".join(pieces))

        if len(translated_segments) != len(sstk):
            raise ValueError(
                "planned translation result count does not match source paragraphs"
            )
        return translated_segments

    @staticmethod
    def _draft_flow_segments(draft: PageLayoutDraft) -> list[FlowSegment]:
        return [
            FlowSegment(
                ref=SegmentRef(draft.page_id, index),
                text=text,
                x0=paragraph.x0,
                x1=paragraph.x1,
                y0=paragraph.y0,
                y1=paragraph.y1,
                size=paragraph.size,
                page_width=draft.width,
                page_height=draft.height,
                region_kind=paragraph.region_kind,
            )
            for index, (text, paragraph) in enumerate(
                zip(draft.sstk, draft.pstk, strict=True)
            )
        ]

    @staticmethod
    def _draft_by_ref(
        drafts: list[PageLayoutDraft],
    ) -> dict[SegmentRef, PageLayoutDraft]:
        return {
            SegmentRef(draft.page_id, index): draft
            for draft in drafts
            for index in range(len(draft.sstk))
        }

    def _translate_continuation_group(
        self,
        group: ContinuationGroup,
        draft_lookup: dict[SegmentRef, PageLayoutDraft],
    ) -> list[FragmentOverride] | None:
        translate = getattr(
            self.translator,
            "translate_continuation_fragments",
            None,
        )
        if not callable(translate):
            return None
        tagged_sources: list[str] = []
        literal_replacements: list[dict[str, str]] = []
        formula_contexts: list[dict[str, str]] = []
        for span in group.fragments:
            draft = draft_lookup[span.ref]
            source = draft.sstk[span.ref.segment_index][span.start : span.end]
            tagged, _ = _tag_translatable_italic_formulas(
                source,
                draft.italic_candidates,
            )
            tagged, replacements = self._mask_protected_literals(tagged)
            tagged_sources.append(tagged)
            literal_replacements.append(replacements)
            formula_contexts.append(
                _formula_context_for_text(
                    tagged,
                    draft.readonly_formula_contexts,
                )
            )
        try:
            translated = translate(
                tagged_sources,
                formula_contexts,
                join_kind=f"{group.edge.kind}:{group.edge.join_mode}",
            )
        except BaseException as exc:
            if log.isEnabledFor(logging.DEBUG):
                log.exception(exc)
            else:
                log.warning("Continuation translation failed: %s", exc)
            return None
        if translated is None or len(translated) != len(group.fragments):
            return None
        try:
            translated = self._restore_protected_literal_batch(
                list(translated),
                literal_replacements,
            )
        except ValueError as exc:
            log.warning("Continuation translation damaged a protected literal: %s", exc)
            return None
        return [
            FragmentOverride(span, target)
            for span, target in zip(group.fragments, translated, strict=True)
        ]

    def _translate_reference_continuation_group(
        self,
        group: ReferenceContinuationGroup,
        draft_lookup: dict[SegmentRef, PageLayoutDraft],
    ) -> list[FragmentOverride] | None:
        translate = getattr(
            self.translator,
            "translate_reference_continuation_fragments",
            None,
        )
        if not callable(translate):
            return None
        left_span, right_span = group.fragments
        left_draft = draft_lookup[left_span.ref]
        right_draft = draft_lookup[right_span.ref]
        left_entry = left_draft.sstk[left_span.ref.segment_index][
            left_span.start : left_span.end
        ]
        right_prefix = right_draft.sstk[right_span.ref.segment_index][
            right_span.start : right_span.end
        ]
        try:
            replacements = translate(left_entry, right_prefix)
        except BaseException as exc:
            if log.isEnabledFor(logging.DEBUG):
                log.exception(exc)
            else:
                log.warning("Reference continuation translation failed: %s", exc)
            return None
        if replacements is None or len(replacements) != 2:
            return None
        left_replacement, right_replacement = replacements
        if (
            left_entry.count(left_replacement.source) != 1
            or right_prefix.count(right_replacement.source) != 1
        ):
            return None
        left_start = left_span.start + left_entry.index(left_replacement.source)
        right_start = right_span.start + right_prefix.index(right_replacement.source)
        return [
            FragmentOverride(
                FragmentSpan(
                    left_span.ref,
                    left_start,
                    left_start + len(left_replacement.source),
                ),
                left_replacement.translated,
            ),
            FragmentOverride(
                FragmentSpan(
                    right_span.ref,
                    right_start,
                    right_start + len(right_replacement.source),
                ),
                right_replacement.translated,
            ),
        ]

    def _prepare_continuation_overrides(
        self,
        pending: PageLayoutDraft,
        following: PageLayoutDraft | None,
    ) -> None:
        drafts = [pending] + ([following] if following is not None else [])
        flow_by_page = {
            draft.page_id: self._draft_flow_segments(draft) for draft in drafts
        }
        edges = detect_page_edges(flow_by_page[pending.page_id])
        if following is not None:
            cross_page = detect_cross_page_edge(
                flow_by_page[pending.page_id],
                flow_by_page[following.page_id],
            )
            if cross_page is not None:
                edges.append(cross_page)
        texts = {
            SegmentRef(draft.page_id, index): text
            for draft in drafts
            for index, text in enumerate(draft.sstk)
        }
        occupied = [
            override.span
            for draft in drafts
            for override in draft.overrides
        ]
        draft_lookup = self._draft_by_ref(drafts)
        if following is not None:
            reference_group = detect_reference_continuation(
                flow_by_page[pending.page_id],
                flow_by_page[following.page_id],
                occupied=occupied,
            )
            if reference_group is not None:
                reference_overrides = (
                    self._translate_reference_continuation_group(
                        reference_group,
                        draft_lookup,
                    )
                )
                if reference_overrides is not None:
                    for override in reference_overrides:
                        draft_lookup[override.span.ref].overrides.append(override)
                        occupied.append(override.span)
        groups = build_continuation_groups(
            edges,
            texts,
            occupied=occupied,
        )
        if not groups:
            return
        for group in groups:
            overrides = self._translate_continuation_group(group, draft_lookup)
            if overrides is None:
                continue
            for override in overrides:
                draft_lookup[override.span.ref].overrides.append(override)

    def _mask_fragment_overrides(
        self,
        sstk: list[str],
        overrides: list[FragmentOverride],
        page_id: int,
    ) -> tuple[list[str], dict[str, tuple[int, str]]]:
        masked = list(sstk)
        replacements: dict[str, tuple[int, str]] = {}
        by_segment: dict[int, list[FragmentOverride]] = {}
        for override in overrides:
            if override.span.ref.page_id == page_id:
                by_segment.setdefault(
                    override.span.ref.segment_index,
                    [],
                ).append(override)
        for segment_index, segment_overrides in by_segment.items():
            if not 0 <= segment_index < len(masked):
                continue
            previous_start = len(masked[segment_index])
            for override in sorted(
                segment_overrides,
                key=lambda item: item.span.start,
                reverse=True,
            ):
                start, end = override.span.start, override.span.end
                if not (0 <= start < end <= previous_start):
                    log.warning(
                        "Ignoring overlapping or invalid continuation span on page %s",
                        page_id,
                    )
                    continue
                token = self._allocate_flow_token()
                masked[segment_index] = (
                    masked[segment_index][:start]
                    + token
                    + masked[segment_index][end:]
                )
                replacements[token] = (segment_index, override.translation)
                previous_start = start
        return masked, replacements

    @staticmethod
    def _restore_fragment_overrides(
        translated: list[str],
        replacements: dict[str, tuple[int, str]],
    ) -> list[str]:
        restored = list(translated)
        for token, (segment_index, target) in replacements.items():
            if not 0 <= segment_index < len(restored):
                raise ValueError("continuation target segment is out of range")
            if restored[segment_index].count(token) != 1:
                raise ValueError("translation damaged a continuation layout token")
            restored[segment_index] = restored[segment_index].replace(
                token,
                target,
                1,
            )
        if any(FLOW_TOKEN_PREFIX in text for text in restored):
            raise ValueError("an unresolved continuation token reached page rendering")
        return restored

    def _reading_flow_enabled(self) -> bool:
        return (
            getattr(self.translator, "name", "") == "codex"
            and not getattr(self.translator, "prompttext", None)
            and callable(
                getattr(
                    self.translator,
                    "translate_continuation_fragments",
                    None,
                )
            )
        )

    def _finalize_deferred_page(
        self,
        pending: PageLayoutDraft,
        following: PageLayoutDraft | None,
    ) -> str:
        self._prepare_continuation_overrides(pending, following)
        self.fontid = dict(pending.fontid)
        self.fontmap = dict(pending.fontmap)
        return self.receive_layout(
            pending.ltpage,
            page_xref=pending.page_xref,
            overrides=pending.overrides,
        )

    def end_page(self, page):
        if not self._reading_flow_enabled():
            return self.receive_layout(self.cur_item)
        current = self.receive_layout(
            self.cur_item,
            preview_only=True,
            page_xref=int(getattr(page, "page_xref", -1)),
        )
        completed: dict[int, str] = {}
        if self._pending_page is not None:
            completed[self._pending_page.page_xref] = self._finalize_deferred_page(
                self._pending_page,
                current,
            )
        self._pending_page = current
        return completed

    def flush_deferred_pages(self) -> dict[int, str]:
        if self._pending_page is None:
            return {}
        pending = self._pending_page
        self._pending_page = None
        return {
            pending.page_xref: self._finalize_deferred_page(pending, None)
        }

    def receive_layout(
        self,
        ltpage: LTPage,
        *,
        preview_only: bool = False,
        page_xref: int = -1,
        overrides: list[FragmentOverride] | None = None,
    ):
        # 段落
        sstk: list[str] = []            # 段落文字栈
        paragraph_text_chars: list[list[LTChar]] = []
        pstk: list[Paragraph] = []      # 段落属性栈
        vbkt: int = 0                   # 段落公式括号计数
        # 公式组
        vstk: list[LTChar] = []         # 公式符号组
        vlstk: list[LTLine] = []        # 公式线条组
        vfix: float = 0                 # 公式纵向偏移
        # 公式组栈
        var: list[list[LTChar]] = []    # 公式符号组栈
        varl: list[list[LTLine]] = []   # 公式线条组栈
        varf: list[float] = []          # 公式纵向偏移栈
        varp: list[int] = []            # 公式所属段落索引
        vlen: list[float] = []          # 公式宽度栈
        vx0: list[float] = []           # 公式视觉左锚点栈
        # 全局
        lstk: list[LTLine] = []         # 全局线条栈
        xt: LTChar = None               # 上一个字符
        xt_cls: int = -1                # 上一个字符所属段落，保证无论第一个字符属于哪个类别都可以触发新段落
        vmax: float = ltpage.width / 4  # 行内公式最大宽度
        ops: str = ""                   # 渲染结果

        def vflag(font: str, char: str):    # 匹配公式（和角标）字体
            if isinstance(font, bytes):     # 不一定能 decode，直接转 str
                try:
                    font = font.decode('utf-8')  # 尝试使用 UTF-8 解码
                except UnicodeDecodeError:
                    font = ""
            font = font.split("+")[-1]      # 字体名截断
            if re.match(r"\(cid:", char):
                return True
            # 基于字体名规则的判定
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if _MATH_FONT_RE.search(font) or re.match(              # latex 字体
                    r"(CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Ital|.*Sym|.*Math)",
                    font,
                ):
                    return True
            # 基于字符集规则的判定
            if self.vchar:
                if re.match(self.vchar, char):
                    return True
            else:
                if (
                    char
                    and char != " "                                     # 非空格
                    and (
                        unicodedata.category(char[0])
                        in ["Lm", "Mn", "Sk", "Sm", "Zl", "Zp", "Zs"]   # 文字修饰符、数学符号、分隔符号
                        or ord(char[0]) in range(0x370, 0x400)          # 希腊字母
                    )
                ):
                    return True
            return False

        ############################################################
        # A. 原文档解析
        for child in ltpage:
            if isinstance(child, LTChar):
                cur_v = False
                layout = self.layout[ltpage.pageid]
                # ltpage.height 可能是 fig 里面的高度，这里统一用 layout.shape
                h, w = layout.shape
                # 读取当前字符在 layout 中的类别
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                actual_cls = int(cls)
                child._pdf2zh_layout_class = actual_cls
                region_types = getattr(self, "layout_region_types", {}).get(
                    ltpage.pageid,
                    {},
                )
                candidate_region_kind = region_types.get(actual_cls, "")
                previous_actual_cls = int(
                    getattr(xt, "_pdf2zh_layout_class", xt_cls)
                )
                logical_region_kind = region_types.get(int(xt_cls), "")
                previous_region_kind = region_types.get(
                    previous_actual_cls,
                    "",
                )
                bridge_cross_class_script = bool(sstk and pstk) and (
                    _should_bridge_cross_class_inline_script(
                        xt,
                        child,
                        sstk[-1],
                        float(pstk[-1].size),
                        previous_actual_cls,
                        actual_cls,
                        previous_region_kind,
                        candidate_region_kind,
                    )
                    or _should_bridge_formula_inline_script(
                        vstk,
                        xt,
                        child,
                        float(pstk[-1].size),
                        previous_actual_cls,
                        actual_cls,
                        logical_region_kind,
                        candidate_region_kind,
                    )
                    or _should_continue_cross_class_inline_script(
                        vstk,
                        xt,
                        child,
                        float(pstk[-1].size),
                        previous_actual_cls,
                        actual_cls,
                        logical_region_kind,
                        candidate_region_kind,
                    )
                )
                if bridge_cross_class_script:
                    # Preserve ``actual_cls`` on the glyph for later safety
                    # checks, but keep the prior logical class so this script
                    # and the following prose remain in one text segment.
                    cls = xt_cls
                    cur_v = True
                # 锚定文档中 bullet 的位置
                if child.get_text() == "•":
                    cls = 0
                # 判定当前字符是否属于公式
                if (                                                                                        # 判定当前字符是否属于公式
                    cls == 0                                                                                # 1. 类别为保留区域
                    or (cls == xt_cls and len(sstk[-1].strip()) > 1 and child.size < pstk[-1].size * 0.79)  # 2. 角标字体，有 0.76 的角标和 0.799 的大写，这里用 0.79 取中，同时考虑首字母放大的情况
                    or vflag(child.fontname, child.get_text())                                              # 3. 公式字体
                    or (child.matrix[0] == 0 and child.matrix[3] == 0)                                      # 4. 垂直字体
                ):
                    cur_v = True
                if (
                    not cur_v
                    and vstk
                    and cls == xt_cls
                    and not (
                        actual_cls == int(xt_cls)
                        and any(
                            int(
                                getattr(
                                    formula_char,
                                    "_pdf2zh_layout_class",
                                    xt_cls,
                                )
                            )
                            != int(xt_cls)
                            for formula_char in vstk
                        )
                    )
                    and _should_extend_formula_run(vstk, child)
                ):
                    cur_v = True
                previous_formula = (
                    var[-1]
                    if var and varp[-1] == len(sstk) - 1
                    else None
                )
                if (
                    cur_v
                    and not vstk
                    and sstk
                    and pstk
                    and cls == xt_cls
                    and _should_absorb_trailing_formula_base(
                        xt,
                        child,
                        sstk[-1],
                        float(pstk[-1].size),
                        previous_formula,
                    )
                ):
                    base = xt.get_text()
                    sstk[-1] = _remove_absorbed_formula_base(
                        sstk[-1],
                        base,
                        previous_formula,
                        xt,
                        float(pstk[-1].size),
                        child,
                    )
                    vstk.append(xt)
                # 判定括号组是否属于公式
                if not cur_v:
                    if vstk and child.get_text() == "(":
                        cur_v = True
                        vbkt += 1
                    if vbkt and child.get_text() == ")":
                        cur_v = True
                        vbkt -= 1
                if (                                                        # 判定当前公式是否结束
                    not cur_v                                               # 1. 当前字符不属于公式
                    or cls != xt_cls                                        # 2. 当前字符与前一个字符不属于同一段落
                    # or (abs(child.x0 - xt.x0) > vmax and cls != 0)        # 3. 段落内换行，可能是一长串斜体的段落，也可能是段内分式换行，这里设个阈值进行区分
                    # 禁止纯公式（代码）段落换行，直到文字开始再重开文字段落，保证只存在两种情况
                    # A. 纯公式（代码）段落（锚定绝对位置）sstk[-1]=="" -> sstk[-1]=="{v*}"
                    # B. 文字开头段落（排版相对位置）sstk[-1]!=""
                    or (sstk[-1] != "" and abs(child.x0 - xt.x0) > vmax)    # 因为 cls==xt_cls==0 一定有 sstk[-1]==""，所以这里不需要再判定 cls!=0
                ):
                    if vstk:
                        if (                                                # 根据公式右侧的文字修正公式的纵向偏移
                            not cur_v                                       # 1. 当前字符不属于公式
                            and cls == xt_cls                               # 2. 当前字符与前一个字符属于同一段落
                            and child.x0 > max([vch.x0 for vch in vstk])    # 3. 当前字符在公式右侧
                        ):
                            vfix = vstk[0].y0 - child.y0
                        formula_chars = vstk
                        prose_suffix = ""
                        if not cur_v and cls == xt_cls:
                            formula_chars, prose_openers = (
                                _split_trailing_prose_openers(vstk)
                            )
                            prose_suffix = prose_openers
                            if _segment_contains_prose(sstk[-1]):
                                before_punctuation_split = formula_chars
                                formula_chars, punctuation = (
                                    _split_trailing_prose_punctuation(
                                        formula_chars
                                    )
                                )
                                paragraph_text_chars[-1].extend(
                                    before_punctuation_split[len(formula_chars) :]
                                )
                                prose_suffix = punctuation + prose_suffix
                            formula_chars, prose_word, prose_word_chars = (
                                _split_trailing_formula_prose_word(formula_chars)
                            )
                            if prose_word:
                                paragraph_text_chars[-1].extend(prose_word_chars)
                                prose_suffix = prose_word + prose_suffix
                        if sstk[-1] == "" and formula_chars:
                            xt_cls = -1 # 禁止纯公式段落（sstk[-1]=="{v*}"）的后续连接，但是要考虑新字符和后续字符的连接，所以这里修改的是上个字符的类别
                        if formula_chars:
                            sstk[-1] += f"{{v{len(var)}}}"
                            var.append(formula_chars)
                            varl.append(vlstk)
                            varf.append(vfix)
                            varp.append(len(sstk) - 1)
                        sstk[-1] += prose_suffix
                        vstk = []
                        vlstk = []
                        vfix = 0
                # 当前字符不属于公式或当前字符是公式的第一个字符
                if not vstk:
                    if cls == xt_cls:               # 当前字符与前一个字符属于同一段落
                        if child.x0 > xt.x1 + 1:    # 添加行内空格
                            sstk[-1] += " "
                        elif child.x1 < xt.x0:      # 添加换行空格并标记原文段落存在换行
                            break_offset = len(sstk[-1])
                            if (
                                not pstk[-1].break_offsets
                                or pstk[-1].break_offsets[-1] != break_offset
                            ):
                                pstk[-1].break_offsets.append(break_offset)
                            if _should_insert_reconstructed_line_space(
                                sstk[-1],
                                child.get_text(),
                            ):
                                sstk[-1] += " "
                            pstk[-1].brk = True
                    else:                           # 根据当前字符构建一个新的段落
                        sstk.append("")
                        paragraph_text_chars.append([])
                        pstk.append(
                            Paragraph(
                                child.y0,
                                child.x0,
                                child.x0,
                                child.x0,
                                child.y0,
                                child.y1,
                                child.size,
                                False,
                                page_id=ltpage.pageid,
                                layout_class=int(cls),
                                region_kind=getattr(
                                    self,
                                    "layout_region_types",
                                    {},
                                ).get(
                                    ltpage.pageid,
                                    {},
                                ).get(int(cls), ""),
                            )
                        )
                if not cur_v:                                               # 文字入栈
                    if (                                                    # 根据当前字符修正段落属性
                        child.size > pstk[-1].size                          # 1. 当前字符比段落字体大
                        or len(sstk[-1].strip()) == 1                       # 2. 当前字符为段落第二个文字（考虑首字母放大的情况）
                    ) and child.get_text() != " ":                          # 3. 当前字符不是空格
                        pstk[-1].y -= child.size - pstk[-1].size            # 修正段落初始纵坐标，假设两个不同大小字符的上边界对齐
                        pstk[-1].size = child.size
                    sstk[-1] += child.get_text()
                    paragraph_text_chars[-1].append(child)
                else:                                                       # 公式入栈
                    if (                                                    # 根据公式左侧的文字修正公式的纵向偏移
                        not vstk                                            # 1. 当前字符是公式的第一个字符
                        and cls == xt_cls                                   # 2. 当前字符与前一个字符属于同一段落
                        and child.x0 > xt.x0                                # 3. 前一个字符在公式左侧
                    ):
                        vfix = child.y0 - xt.y0
                    vstk.append(child)
                # 更新段落边界，因为段落内换行之后可能是公式开头，所以要在外边处理
                pstk[-1].x0 = min(pstk[-1].x0, child.x0)
                pstk[-1].x1 = max(pstk[-1].x1, child.x1)
                pstk[-1].y0 = min(pstk[-1].y0, child.y0)
                pstk[-1].y1 = max(pstk[-1].y1, child.y1)
                # 更新上一个字符
                xt = child
                xt_cls = cls
            elif isinstance(child, LTFigure):   # 图表
                pass
            elif isinstance(child, LTLine):     # 线条
                layout = self.layout[ltpage.pageid]
                # ltpage.height 可能是 fig 里面的高度，这里统一用 layout.shape
                h, w = layout.shape
                # 读取当前线条在 layout 中的类别
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                if vstk and cls == xt_cls:      # 公式线条
                    vlstk.append(child)
                else:                           # 全局线条
                    lstk.append(child)
            else:
                pass
        # 处理结尾
        if vstk:    # 公式出栈
            formula_chars = vstk
            prose_suffix = ""
            if _segment_contains_prose(sstk[-1]):
                before_punctuation_split = formula_chars
                formula_chars, prose_suffix = (
                    _split_trailing_prose_punctuation(formula_chars)
                )
                paragraph_text_chars[-1].extend(
                    before_punctuation_split[len(formula_chars) :]
                )
            formula_chars, prose_word, prose_word_chars = (
                _split_trailing_formula_prose_word(formula_chars)
            )
            if prose_word:
                paragraph_text_chars[-1].extend(prose_word_chars)
                prose_suffix = prose_word + prose_suffix
            if formula_chars:
                sstk[-1] += f"{{v{len(var)}}}"
                var.append(formula_chars)
                varl.append(vlstk)
                varf.append(vfix)
                varp.append(len(sstk) - 1)
            sstk[-1] += prose_suffix
        _merge_overlapping_split_math_islands(
            sstk,
            var,
            varp,
            paragraph_text_chars,
        )
        log.debug("\n==========[VSTACK]==========\n")
        for id, v in enumerate(var):  # 计算公式宽度
            anchor_x, l = _formula_horizontal_geometry(v)
            log.debug(f'< {l:.1f} {v[0].x0:.1f} {v[0].y0:.1f} {v[0].cid} {v[0].fontname} {len(varl[id])} > v{id} = {"".join([ch.get_text() for ch in v])}')
            vx0.append(anchor_x)
            vlen.append(l)

        formula_texts = [
            "".join(ch.get_text() for ch in value) for value in var
        ]
        hidden_formula_probes = {
            formula_id
            for formula_id, value in enumerate(var)
            if _is_hidden_glyph_repertoire_probe(value, float(ltpage.width))
        }
        italic_candidates = (
            _collect_translatable_italic_runs(var, varp, pstk, sstk)
            if getattr(self.translator, "name", "") == "codex" and not self.vfont
            else {}
        )
        if italic_candidates:
            log.debug("Translatable italic formula runs: %s", italic_candidates)
        readonly_formula_contexts = (
            _collect_readonly_formula_contexts(
                var,
                varl,
                varp,
                pstk,
                sstk,
                italic_candidates,
            )
            if getattr(self.translator, "name", "") == "codex"
            else {}
        )
        if readonly_formula_contexts:
            log.debug(
                "Read-only inline formula contexts selected: %s",
                sorted(readonly_formula_contexts),
            )

        if preview_only:
            return PageLayoutDraft(
                ltpage=ltpage,
                page_id=int(ltpage.pageid),
                page_xref=int(page_xref),
                width=float(ltpage.width),
                height=float(ltpage.height),
                sstk=list(sstk),
                pstk=pstk,
                var=var,
                varl=varl,
                varf=varf,
                varp=varp,
                vlen=vlen,
                lstk=lstk,
                formula_texts=formula_texts,
                italic_candidates=italic_candidates,
                readonly_formula_contexts=readonly_formula_contexts,
                fontid=dict(getattr(self, "fontid", {})),
                fontmap=dict(getattr(self, "fontmap", {})),
                overrides=[],
            )

        ############################################################
        # B. 段落翻译
        log.debug("\n==========[SSTACK]==========\n")
        source_sstk = list(sstk)
        replacement_map: dict[str, tuple[int, str]] = {}
        if overrides:
            sstk, replacement_map = self._mask_fragment_overrides(
                sstk,
                overrides,
                int(ltpage.pageid),
            )
        news = self._translate_planned_segments(
            sstk,
            pstk,
            formula_texts,
            page_width=ltpage.width,
            italic_candidates=italic_candidates,
            readonly_formula_contexts=readonly_formula_contexts,
        )
        if replacement_map:
            news = self._restore_fragment_overrides(news, replacement_map)
        if len(source_sstk) != len(news):
            raise ValueError("continuation translation changed segment count")

        ############################################################
        # C. 新文档排版
        def raw_string(fcur: str, cstk: str):  # 编码字符串
            if fcur == self.noto_name:
                return "".join(["%04x" % self.noto.has_glyph(ord(c)) for c in cstk])
            elif isinstance(self.fontmap[fcur], PDFCIDFont):  # 判断编码长度
                return "".join(["%04x" % ord(c) for c in cstk])
            else:
                return "".join(["%02x" % ord(c) for c in cstk])

        def text_font_and_advance(
            character: str,
            font_size: float,
        ) -> tuple[str, float]:
            font_name = None
            try:
                if self.fontmap["tiro"].to_unichr(ord(character)) == character:
                    font_name = "tiro"
            except Exception:
                pass
            if font_name is None:
                font_name = self.noto_name
            if font_name == self.noto_name:
                advance = self.noto.char_lengths(character, font_size)[0]
            else:
                advance = self.fontmap[font_name].char_width(ord(character)) * font_size
            return font_name, advance

        def formula_advance(marker: str) -> float:
            match = _FORMULA_MARKER_RE.fullmatch(marker)
            if match is None:
                return 0.0
            try:
                formula_id = int(match.group(1).replace(" ", ""))
                if formula_id in hidden_formula_probes:
                    return 0.0
                advance = vlen[formula_id]
                if (
                    var[formula_id][-1].get_text()
                    and unicodedata.category(var[formula_id][-1].get_text()[0])
                    in ["Lm", "Mn", "Sk"]
                ):
                    advance -= var[formula_id][-1].width
                return advance
            except (IndexError, TypeError, ValueError):
                return 0.0

        def measure_line_break_unit(
            unit: LineBreakUnit,
            font_size: float,
            initial_italic: bool,
        ) -> float:
            cursor = 0.0
            visual_extent = 0.0
            measured_italic = initial_italic
            for atom in unit.atoms:
                if atom.kind == "style":
                    style_match = ITALIC_TAG_RE.fullmatch(atom.text)
                    if style_match is not None:
                        measured_italic = style_match.group(2) == "BEGIN"
                    continue
                if atom.kind == "newline":
                    break
                if atom.kind == "formula":
                    cursor += formula_advance(atom.text)
                    visual_extent = max(visual_extent, cursor)
                    continue
                for character in atom.text:
                    _, advance = text_font_and_advance(character, font_size)
                    cursor += advance
                    visual_extent = max(
                        visual_extent,
                        cursor
                        + (ITALIC_SHEAR * font_size if measured_italic else 0.0),
                    )
            return visual_extent

        # 根据目标语言获取默认行距
        LANG_LINEHEIGHT_MAP = {
            "zh-cn": 1.4, "zh-tw": 1.4, "zh-hans": 1.4, "zh-hant": 1.4, "zh": 1.4,
            "ja": 1.1, "ko": 1.2, "en": 1.2, "ar": 1.0, "ru": 0.8, "uk": 0.8, "ta": 0.8
        }
        default_line_height = LANG_LINEHEIGHT_MAP.get(self.translator.lang_out.lower(), 1.1) # 小语种默认1.1
        _x, _y = 0, 0
        ops_list = []

        def gen_op_line(x, y, xlen, ylen, linewidth):
            return f"ET q 1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

        for id, new in enumerate(news):
            x: float = pstk[id].x                       # 段落初始横坐标
            y: float = pstk[id].y                       # 段落初始纵坐标
            x0: float = pstk[id].x0                     # 段落左边界
            x1: float = pstk[id].x1                     # 段落右边界
            height: float = pstk[id].y1 - pstk[id].y0   # 段落高度
            size: float = pstk[id].size                 # 段落字体大小
            brk: bool = pstk[id].brk                    # 段落换行标记
            cstk: str = ""                              # 当前文字栈
            fcur: str = None                            # 当前字体 ID
            lidx = 0                                    # 记录换行次数
            tx = x
            fcur_ = fcur
            ptr = 0
            italic_active = False
            log.debug(f"< {y} {x} {x0} {x1} {size} {brk} > {sstk[id]} | {new}")

            ops_vals: list[dict] = []
            line_break_units = {
                unit.start: unit for unit in iter_line_break_units(new)
            }

            while ptr < len(new):
                break_unit = line_break_units.get(ptr)
                if (
                    brk
                    and break_unit is not None
                    and not any(
                        atom.kind == "newline" for atom in break_unit.atoms
                    )
                    and x > x0 + 0.01
                ):
                    unit_extent = measure_line_break_unit(
                        break_unit,
                        size,
                        italic_active,
                    )
                    line_capacity = x1 - x0 + 0.1 * size
                    if _should_pre_wrap_line_break_unit(
                        break_unit,
                        current_x=x,
                        unit_extent=unit_extent,
                        right_limit=x1 + 0.1 * size,
                        line_capacity=line_capacity,
                    ):
                        if cstk:
                            ops_vals.append({
                                "type": OpType.TEXT,
                                "font": fcur,
                                "size": size,
                                "x": tx,
                                "dy": 0,
                                "rtxt": raw_string(fcur, cstk),
                                "lidx": lidx,
                                "italic": italic_active,
                            })
                            cstk = ""
                        x = x0
                        lidx += 1
                        fcur = None
                italic_tag = ITALIC_TAG_RE.match(new, ptr)
                if italic_tag is not None:
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur,
                            "size": size,
                            "x": tx,
                            "dy": 0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx,
                            "italic": italic_active,
                        })
                        cstk = ""
                    italic_active = italic_tag.group(2) == "BEGIN"
                    ptr = italic_tag.end()
                    fcur = None
                    continue
                if new.startswith(ITALIC_TAG_PREFIX, ptr):
                    # Internal markup must never leak into the rendered PDF.
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur,
                            "size": size,
                            "x": tx,
                            "dy": 0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx,
                            "italic": italic_active,
                        })
                        cstk = ""
                    marker_end = new.find("]]", ptr)
                    ptr = len(new) if marker_end < 0 else marker_end + 2
                    italic_active = False
                    fcur = None
                    continue
                if new[ptr] in "\r\n":
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur,
                            "size": size,
                            "x": tx,
                            "dy": 0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx,
                            "italic": italic_active,
                        })
                        cstk = ""
                    if new[ptr] == "\r" and ptr + 1 < len(new) and new[ptr + 1] == "\n":
                        ptr += 1
                    ptr += 1
                    x = x0
                    lidx += 1
                    fcur = None
                    continue
                vy_regex = re.match(
                    r"\{\s*v([\d\s]+)\}", new[ptr:], re.IGNORECASE
                )  # 匹配 {vn} 公式标记
                mod = 0  # 文字修饰符
                if vy_regex:  # 加载公式
                    ptr += len(vy_regex.group(0))
                    try:
                        vid = int(vy_regex.group(1).replace(" ", ""))
                        if vid in hidden_formula_probes:
                            continue
                        adv = vlen[vid]
                    except Exception:
                        continue  # 翻译器可能会自动补个越界的公式标记
                    if var[vid][-1].get_text() and unicodedata.category(var[vid][-1].get_text()[0]) in ["Lm", "Mn", "Sk"]:  # 文字修饰符
                        mod = var[vid][-1].width
                else:  # 加载文字
                    ch = new[ptr]
                    fcur_, adv = text_font_and_advance(ch, size)
                    ptr += 1
                visual_end = x + adv + (
                    ITALIC_SHEAR * size if italic_active and not vy_regex else 0.0
                )
                right_limit = x1 + (
                    size
                    if not vy_regex and ch in CJK_PROHIBITED_LINE_START
                    else 0.1 * size
                )
                if (                                # 输出文字缓冲区
                    fcur_ != fcur                   # 1. 字体更新
                    or vy_regex                     # 2. 插入公式
                    or visual_end > right_limit     # 3. 到达右边界（含斜体右悬伸）
                ):
                    if cstk:
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": fcur,
                            "size": size,
                            "x": tx,
                            "dy": 0,
                            "rtxt": raw_string(fcur, cstk),
                            "lidx": lidx,
                            "italic": italic_active,
                        })
                        cstk = ""
                if brk and visual_end > right_limit:  # 到达右边界且原文段落存在换行
                    x = x0
                    lidx += 1
                if vy_regex:  # 插入公式
                    # A wrapped inline formula may begin a new target line after
                    # ``fcur`` has been reset.  Its source baseline correction is
                    # still required; otherwise a subscript becomes a detached
                    # baseline glyph merely because translation changed wrapping.
                    fix = varf[vid]
                    for vch in var[vid]:  # 排版公式字符
                        vc = chr(vch.cid)
                        source_state = getattr(
                            vch,
                            "_pdf2zh_source_text_state",
                            None,
                        )
                        preserve_source_transform = (
                            source_state is not None
                            and getattr(vch, "_pdf2zh_layout_class", None) == 0
                            and _is_non_horizontal_text_matrix(source_state.matrix)
                        )
                        if preserve_source_transform:
                            source_font = self.fontid[vch.font]
                            ops_vals.append({
                                "type": OpType.PRESERVED_TEXT,
                                "font": source_font,
                                "state": source_state,
                                "rtxt": raw_string(source_font, vc),
                            })
                            continue
                        ops_vals.append({
                            "type": OpType.TEXT,
                            "font": self.fontid[vch.font],
                            "size": vch.size,
                            "x": x + vch.x0 - vx0[vid],
                            "dy": fix + vch.y0 - var[vid][0].y0,
                            "rtxt": raw_string(self.fontid[vch.font], vc),
                            "lidx": lidx,
                            "italic": False,
                        })
                        if log.isEnabledFor(logging.DEBUG):
                            lstk.append(LTLine(0.1, (_x, _y), (x + vch.x0 - vx0[vid], fix + y + vch.y0 - var[vid][0].y0)))
                            _x, _y = x + vch.x0 - vx0[vid], fix + y + vch.y0 - var[vid][0].y0
                    for l in varl[vid]:  # 排版公式线条
                        if l.linewidth < 5:  # hack 有的文档会用粗线条当图片背景
                            ops_vals.append({
                                "type": OpType.LINE,
                                "x": l.pts[0][0] + x - vx0[vid],
                                "dy": l.pts[0][1] + fix - var[vid][0].y0,
                                "linewidth": l.linewidth,
                                "xlen": l.pts[1][0] - l.pts[0][0],
                                "ylen": l.pts[1][1] - l.pts[0][1],
                                "lidx": lidx
                            })
                else:  # 插入文字缓冲区
                    if not cstk:  # 单行开头
                        tx = x
                        if x == x0 and ch == " ":  # 消除段落换行空格
                            adv = 0
                        else:
                            cstk += ch
                    else:
                        cstk += ch
                adv -= mod # 文字修饰符
                fcur = fcur_
                x += adv
                if log.isEnabledFor(logging.DEBUG):
                    lstk.append(LTLine(0.1, (_x, _y), (x, y)))
                    _x, _y = x, y
            # 处理结尾
            if cstk:
                ops_vals.append({
                    "type": OpType.TEXT,
                    "font": fcur,
                    "size": size,
                    "x": tx,
                    "dy": 0,
                    "rtxt": raw_string(fcur, cstk),
                    "lidx": lidx,
                    "italic": italic_active,
                })

            vertical_fit = _fit_target_vertical_layout(
                line_count=lidx + 1,
                font_size=size,
                available_height=height,
                default_line_height=default_line_height,
            )
            line_height = vertical_fit.line_height
            render_scale = vertical_fit.render_scale
            if not vertical_fit.contained:
                log.warning(
                    "Unable to fit target paragraph %s: invalid geometry "
                    "(lines=%s, size=%s, height=%s)",
                    id,
                    lidx + 1,
                    size,
                    height,
                )
            elif render_scale < 0.65:
                log.warning(
                    "Target paragraph %s required strong uniform scaling: %.3f",
                    id,
                    render_scale,
                )

            for vals in ops_vals:
                if vals["type"] == OpType.TEXT:
                    ops_list.append(_gen_target_text_op(
                        vals["font"],
                        vals["size"] * render_scale,
                        x0 + (vals["x"] - x0) * render_scale,
                        vals["dy"] * render_scale
                        + y
                        - vals["lidx"] * size * render_scale * line_height,
                        vals["rtxt"],
                        vals.get("italic", False),
                    ))
                elif vals["type"] == OpType.PRESERVED_TEXT:
                    ops_list.append(_gen_preserved_text_op(vals["font"], vals["state"], vals["rtxt"]))
                elif vals["type"] == OpType.LINE:
                    ops_list.append(gen_op_line(
                        x0 + (vals["x"] - x0) * render_scale,
                        vals["dy"] * render_scale
                        + y
                        - vals["lidx"] * size * render_scale * line_height,
                        vals["xlen"] * render_scale,
                        vals["ylen"] * render_scale,
                        vals["linewidth"] * render_scale,
                    ))

        for l in lstk:  # 排版全局线条
            if l.linewidth < 5:  # hack 有的文档会用粗线条当图片背景
                ops_list.append(gen_op_line(l.pts[0][0], l.pts[0][1], l.pts[1][0] - l.pts[0][0], l.pts[1][1] - l.pts[0][1], l.linewidth))

        ops = f"BT {''.join(ops_list)}ET "
        return ops


class OpType(Enum):
    TEXT = "text"
    PRESERVED_TEXT = "preserved_text"
    LINE = "line"
