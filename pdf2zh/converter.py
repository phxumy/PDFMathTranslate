import concurrent.futures
from contextvars import copy_context
import logging
import math
import re
import unicodedata
from asyncio import CancelledError
from builtins import id as object_id
from dataclasses import dataclass
from enum import Enum
from string import Template
from typing import Callable, Dict

import numpy as np
from pdfminer.converter import PDFConverter
from pdfminer.layout import LTChar, LTFigure, LTLine, LTPage
from pdfminer.pdffont import PDFCIDFont, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.utils import Matrix, apply_matrix_pt, mult_matrix
from pymupdf import Font
from tenacity import retry, retry_if_exception, wait_fixed, stop_after_attempt

from pdf2zh.toc_layout import detect_toc_layout, toc_leader_op
from pdf2zh.code_blocks import code_block_chars
from pdf2zh.identity_layout import author_byline_chars, orcid_list_chars
from pdf2zh.codex_errors import CodexPermanentError, CodexTranslationValidationError
from pdf2zh.source_spelling import normalize_source_wraps
from pdf2zh.line_breaking import (
    CJK_PROHIBITED_LINE_END,
    CJK_PROHIBITED_LINE_START,
    LineBreakUnit,
    iter_line_break_units,
    iter_protected_literals,
    normalize_protected_literals,
)
from pdf2zh.scanned_pdf import (
    english_scan_sections_to_preserve,
    horizontal_fit_scale,
    is_hidden_ocr,
    recover_scan_gap_fragments,
    scan_line_mask_ops,
    scan_text_bounds,
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
    TranslationAbortedError,
    XinferenceTranslator,
    ZhipuTranslator,
)
from pdf2zh.translation_policy import (
    ROLE_AFFILIATION,
    ROLE_PRESERVE,
    ROLE_REFERENCE,
    ROLE_TRANSLATE,
    RUNNING_HEADER_REGION_KIND,
    DocumentTranslationPolicy,
    SourceSegment,
    SegmentPart,
    SegmentPlan,
    formula_cache_signature,
    find_reference_markers,
    is_running_header_segment,
    looks_like_affiliation,
    looks_like_reference_author_block,
    looks_like_reference_title_prefix,
    reference_entry_score,
    restore_affiliation_breaks,
    split_numbered_reference_region,
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
    detect_reference_note_continuations,
)

log = logging.getLogger(__name__)


def _retryable_translation_error(error: BaseException) -> bool:
    return isinstance(error, Exception) and not isinstance(
        error,
        (CodexPermanentError, CodexTranslationValidationError, TranslationAbortedError),
    )


def _supports_scientific_translation(translator: BaseTranslator) -> bool:
    """Use backend capabilities, retaining compatibility with older Codex adapters."""
    return bool(
        getattr(
            translator,
            "scientific_translation",
            getattr(translator, "name", "") == "codex",
        )
    )


ITALIC_TAG_PREFIX = "[[PDF2ZH_ITALIC_"
ITALIC_TAG_RE = re.compile(r"\[\[PDF2ZH_ITALIC_(\d+)_(BEGIN|END)\]\]")
ITALIC_SHEAR = math.tan(math.radians(12.0))
_FORMULA_MARKER_RE = re.compile(r"\{\s*v([\d\s]+)\s*\}", re.IGNORECASE)
FLOW_TOKEN_PREFIX = "[[PDF2ZH_FLOW_"
FLOW_TOKEN_RE = re.compile(r"\[\[PDF2ZH_FLOW_(\d+)\]\]")
_MAX_READONLY_FORMULA_GLYPHS = 24
_MAX_READONLY_FORMULA_CODEPOINTS = 48
_FORCED_STYLED_PROSE_ATTR = "_pdf2zh_forced_styled_prose"
_PROSE_ITALIC_FONT_RE = re.compile(
    r"(?:italic|oblique|slanted|ital$|^CM(?:TI|SL|ITT)\d)",
    re.IGNORECASE,
)
_PROSE_BOLD_FONT_RE = re.compile(r"(?:bold|demi|semibold)", re.IGNORECASE)
_MATH_FONT_RE = re.compile(
    r"(?:^CM(?:I(?!TT)|MI|SY|EX)|RMTMI|MTSY|MTEX|Math|Symbol|Sym$|TeX(?!t)|Euler|"
    r"AdvP[0-9A-Fa-f]+)",
    re.IGNORECASE,
)
_MATH_WORDS = {
    "arg",
    "argmin",
    "argmax",
    "arccos",
    "arcsin",
    "arctan",
    "atan2",
    "cot",
    "coth",
    "csc",
    "sec",
    "sech",
    "dist",
    "sign",
    "supp",
    "prox",
    "proj",
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
_PROTECTED_ITALIC_MEMBERSHIP_RE = re.compile(
    r"(?:(?:graduate)?student|associate|senior|life|fellow)?memberieee|"
    r"ieee(?:fellow|member)",
    re.IGNORECASE,
)
_STRUCTURAL_ITALIC_LABEL_RE = re.compile(
    r"^(?:abstract|index\s*terms?)\b",
    re.IGNORECASE,
)
_SUBSECTION_ITALIC_LABEL_RE = re.compile(
    r"^(?:[A-Z]|\d+(?:\.\d+){0,4})\.\s*[A-Z][A-Za-z-]*(?:\s+[A-Za-z][A-Za-z-]*){0,15}$"
)
_RUNIN_ITALIC_HEADING_RE = re.compile(
    r"^(?:\d+\)\s*)?[A-Z][A-Za-z-]*" r"(?:\s+[A-Za-z][A-Za-z-]*){1,16}:$"
)
_RUNIN_CONNECTOR_RE = re.compile(r"^(?:Both|Finally|For|In|Table|The|We),?$")
_PARENTHETICAL_ITALIC_CONNECTOR_RE = re.compile(
    r"^[\(\[（［]\s*(?:and|or|versus|vs\.?)\s*$",
    re.IGNORECASE,
)
_QUOTED_ITALIC_PROSE_RE = re.compile(
    r"^[“\"][A-Za-z][A-Za-z'-]*[”\"]" r"(?:\s+[A-Za-z][A-Za-z'-]*){1,5}$"
)


def _is_structural_styled_text(text: str) -> bool:
    return bool(
        _STRUCTURAL_ITALIC_LABEL_RE.match(text)
        or _SUBSECTION_ITALIC_LABEL_RE.fullmatch(text)
        or _RUNIN_ITALIC_HEADING_RE.fullmatch(text)
    )


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
        r"([\(\[（［])(see|cf|where|with|for|agreeing)$",
        joined,
        re.IGNORECASE,
    )
    if parenthetical is not None and all(len(char.get_text()) == 1 for char in chars):
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


def _release_chemical_direction_qualifiers(
    texts: list[str],
    formulas: list[list[LTChar]],
    formula_paragraphs: list[int],
    paragraph_text_chars: list[list[LTChar]],
) -> None:
    """Return a chemical formula's spaced ``(upper left, ...)`` to prose.

    The generic bracket continuation can swallow the first ordinary word after
    a chemical subscript.  Release only a known positional phrase proven by the
    surrounding paragraph, a subscripted chemical-shaped prefix, and a visible
    word-space before its ordinary-font parenthesis.  Mathematical function
    arguments, adjacent parentheses and protected diagram labels stay opaque.
    """
    pattern = re.compile(
        r"^(?P<formula>\d*(?:[A-Z][a-z]?\d*){2,})"
        r"(?P<suffix>\((?:upper|lower|top|bottom))$"
    )
    for index, chars in enumerate(formulas):
        match = pattern.fullmatch("".join(char.get_text() for char in chars))
        if match is None or any(len(char.get_text()) != 1 for char in chars):
            continue
        paragraph_id = formula_paragraphs[index]
        marker = f"{{v{index}}}"
        source = texts[paragraph_id]
        if source.count(marker) != 1:
            continue
        marker_end = source.index(marker) + len(marker)
        following = re.sub(
            r"\{v(\d+)\}",
            lambda item: (
                "".join(char.get_text() for char in formulas[int(item.group(1))])
                if int(item.group(1)) < len(formulas)
                else item.group(0)
            ),
            source[marker_end:],
        )
        if re.match(r"\s+(?:left|right)\b[^()\r\n]{0,160}\)", following) is None:
            continue
        split_at = match.start("suffix")
        prefix, suffix = chars[:split_at], chars[split_at:]
        em = float(suffix[0].size)
        if em <= 0 or not any(float(char.size) < em * 0.8 for char in prefix):
            continue
        if any(
            getattr(char, "_pdf2zh_layout_class", None) == 0
            or _is_non_horizontal_text_matrix(char.matrix)
            for char in chars
        ):
            continue
        if any(
            _MATH_FONT_RE.search(_formula_font_name(char))
            or _PROSE_ITALIC_FONT_RE.search(_formula_font_name(char))
            or abs(float(char.size) - em) > em * 0.1
            for char in suffix
        ):
            continue
        baseline_chars = [char for char in prefix if float(char.size) >= em * 0.9]
        if not baseline_chars:
            continue
        baseline = float(np.median([_char_baseline(char) for char in baseline_chars]))
        if any(abs(_char_baseline(char) - baseline) > em * 0.15 for char in suffix):
            continue
        gap = float(suffix[0].x0) - max(float(char.x1) for char in prefix)
        if not em * 0.12 < gap <= em * 0.5:
            continue
        formulas[index] = prefix
        texts[paragraph_id] = source.replace(
            marker, marker + " " + match.group("suffix"), 1
        )
        paragraph_text_chars[paragraph_id].extend(suffix)


def _release_parenthetical_prose_articles(
    texts: list[str],
    formulas: list[list[LTChar]],
    formula_paragraphs: list[int],
    paragraph_text_chars: list[list[LTChar]],
) -> None:
    """Release a spaced ordinary-font ``(a relative offset ...)`` explanation.

    A single-letter article can follow a symbol so closely that it enters the
    formula atom. Require the complete descriptive noun-phrase framing in the
    surrounding prose, ordinary glyphs, an unmatched opener, and a word gap.
    Function arguments, italic variables, and phrases such as ``a relative to``
    remain protected.
    """
    phrase = re.compile(
        r"\s+(?:(?:proxy|measure|estimate|indicator)\s+(?:of|for)\s+|"
        r"(?:relative|absolute|small|large|positive|negative|constant|"
        r"uniform|random|finite|infinitesimal|additional|effective|average)"
        r"\s+[a-z]{3,}\b)[^()\r\n=+<>]{0,180}\)"
    )
    for index, chars in enumerate(formulas):
        joined = "".join(char.get_text() for char in chars)
        match = re.search(r"\(an?$", joined)
        if (
            match is None
            or match.start() == 0
            or joined.count("(") <= joined.count(")")
            or any(len(char.get_text()) != 1 for char in chars)
        ):
            continue
        paragraph_id = formula_paragraphs[index]
        source = texts[paragraph_id]
        marker = f"{{v{index}}}"
        if source.count(marker) != 1:
            continue
        following = source[source.index(marker) + len(marker) :]
        expanded_following = _FORMULA_MARKER_RE.sub(
            lambda m: (
                "".join(ch.get_text() for ch in formulas[int(m[1])])
                if int(m[1]) < len(formulas)
                else m[0]
            ),
            following,
        )
        if phrase.match(expanded_following) is None:
            continue
        prefix, suffix = chars[: match.start()], chars[match.start() :]
        em = float(suffix[0].size)
        if em <= 0 or any(
            getattr(char, "_pdf2zh_layout_class", None) == 0
            or _is_non_horizontal_text_matrix(char.matrix)
            for char in chars
        ):
            continue
        if any(
            not _is_non_math_text_font(char.fontname)
            or _PROSE_ITALIC_FONT_RE.search(_formula_font_name(char))
            or abs(float(char.size) - em) > em * 0.1
            for char in suffix
        ):
            continue
        if any(
            abs(
                _char_baseline(char)
                - _char_baseline(max(prefix, key=lambda ch: ch.size))
            )
            > em * 0.15
            for char in suffix
        ):
            continue
        gap = float(suffix[0].x0) - max(float(char.x1) for char in prefix)
        if not em * 0.12 < gap <= em * 0.8:
            continue
        formulas[index] = prefix
        texts[paragraph_id] = source.replace(marker, marker + " " + match.group(), 1)
        paragraph_text_chars[paragraph_id].extend(suffix)


_PROSE_SENTENCE_PUNCTUATION = frozenset(".,;:!?，。；：！？")


def _release_formula_prose_suffixes(texts, formulas, owners, paragraphs, text_chars):
    """Keep axis labels and explicit parenthetical cross-references in prose."""
    for index, chars in enumerate(formulas):
        pid = owners[index]
        if paragraphs[pid].region_kind not in _INLINE_SCRIPT_REGION_KINDS:
            continue
        if any(len(ch.get_text()) != 1 for ch in chars):
            continue
        value = "".join(ch.get_text() for ch in chars)
        marker = f"{{v{index}}}"
        if texts[pid].count(marker) != 1:
            continue
        before, after = texts[pid].split(marker)
        if (
            re.fullmatch(r"[A-Z][A-Za-z]{2,}-", value)
            and re.match(r"(?:design|type|architecture)\b", after)
            and all(
                _is_non_math_text_font(ch.fontname)
                and not _is_non_horizontal_text_matrix(ch.matrix)
                for ch in chars
            )
        ):
            # A named circuit design is ordinary prose, not a mathematical
            # atom whose order must be locked relative to an author citation.
            texts[pid] = before + value + after
            text_chars[pid].extend(chars)
            continue
        quantity = re.fullmatch(
            r"(?P<prefix>instantaneous|average|mean)?(?P<symbol>[A-Z][a-z]?\d)(?P<suffix>gain|time|rate)",
            value,
        )
        if quantity:
            start, end = quantity.span("symbol")
            noun = chars[end:]
            symbol = chars[start:end]
            em = max(ch.size for ch in symbol)
            gap = noun[0].x0 - max(ch.x1 for ch in symbol)
            if not (0.12 * em < gap < 0.8 * em) or any(
                not _is_non_math_text_font(ch.fontname)
                or _is_non_horizontal_text_matrix(ch.matrix)
                for ch in chars[:start] + noun
            ):
                continue
            prefix = quantity["prefix"] or ""
            # An emphasized adjective can end the previous physical line,
            # leaving two adjacent placeholders for one ordinary noun phrase.
            previous = re.search(r"\{v(\d+)\}\s*$", before)
            if not prefix and previous:
                previous_id = int(previous[1])
                previous_chars = formulas[previous_id]
                previous_text = "".join(ch.get_text() for ch in previous_chars)
                if (
                    owners[previous_id] == pid
                    and previous_text in {"instantaneous", "average", "mean"}
                    and all(
                        _is_non_math_text_font(ch.fontname) for ch in previous_chars
                    )
                ):
                    before = before[: previous.start()] + previous_text + " "
                    text_chars[pid].extend(previous_chars)
            formulas[index] = chars[start:end]
            texts[pid] = (
                before
                + (prefix + " " if prefix else "")
                + marker
                + " "
                + quantity["suffix"]
                + after
            )
            text_chars[pid].extend(chars[:start] + chars[end:])
            continue
        expanded_after = _FORMULA_MARKER_RE.sub(
            lambda m: "".join(ch.get_text() for ch in formulas[int(m[1])]), after
        )
        axis = re.search(r"\([xyz]-$", value)
        reference = re.search(r"\((?:Eq|Fig|Ref|Sec)\.?\d+[a-z]?[),]?$", value)
        match = axis if axis and re.match(r"axis\)", expanded_after) else reference
        if match is None or match.start() == 0:
            continue
        prefix, suffix = chars[: match.start()], chars[match.start() :]
        if any(_is_non_horizontal_text_matrix(ch.matrix) for ch in chars):
            continue
        if reference and any(not _is_non_math_text_font(ch.fontname) for ch in suffix):
            continue
        prose = _reconstruct_italic_run(suffix)
        if reference:
            prose = re.sub(r"(Eq|Fig|Ref|Sec)\.?(?=\d)", r"\1. ", prose)
        formulas[index] = prefix
        texts[pid] = before + marker + " " + prose + after
        text_chars[pid].extend(suffix)


def _release_inline_prose_closers(texts, formulas, owners, paragraphs, text_chars):
    """Return a prose explanation's closing mark from an opaque math atom.

    Track literal and formula-owned openers separately. Only a visible opener
    followed by several prose words proves that an unmatched closing glyph
    belongs to the sentence. Balanced functions and cross-atom math stay intact.
    """
    for index, chars in enumerate(formulas):
        pid = owners[index]
        if (
            paragraphs[pid].region_kind not in _INLINE_SCRIPT_REGION_KINDS
            or len(chars) < 2
        ):
            continue
        value = "".join(ch.get_text() for ch in chars)
        if not value.endswith(")") or value.count(")") <= value.count("("):
            continue
        closer = chars[-1]
        if not _is_non_math_text_font(
            closer.fontname
        ) or _is_non_horizontal_text_matrix(closer.matrix):
            continue
        marker = f"{{v{index}}}"
        source = texts[pid]
        if source.count(marker) != 1:
            continue
        before, after = source.split(marker)
        stack = []
        cursor = 0

        def consume(text, offset=None):
            for pos, char in enumerate(text):
                if char == "(":
                    stack.append(None if offset is None else offset + pos)
                elif char == ")" and stack:
                    stack.pop()

        for match in _FORMULA_MARKER_RE.finditer(before):
            consume(before[cursor : match.start()], cursor)
            consume("".join(ch.get_text() for ch in formulas[int(match[1])]))
            cursor = match.end()
        consume(before[cursor:], cursor)
        if not stack or stack[-1] is None:
            continue
        explanation = _FORMULA_MARKER_RE.sub(" ", before[stack[-1] + 1 :])
        percentage_qualifier = (
            not explanation.strip()
            and re.search(r"\b[A-Za-z]{2,}\s+\($", before)
            and re.fullmatch(r"[≈~<>≤≥]?\s*\d+(?:\.\d+)?\s*%\)", value)
        )
        if (
            len(re.findall(r"[A-Za-z]{2,}", explanation)) < 2
            and not percentage_qualifier
        ):
            continue
        formulas[index] = chars[:-1]
        texts[pid] = before + marker + ")" + after
        text_chars[pid].append(closer)


def _merge_encoded_formula_gaps(texts, formulas, owners, paragraphs, text_chars, lines):
    """Preserve source glyphs for a misencoded delimiter between math atoms."""
    pattern = re.compile(r"\{v(\d+)\}(?P<gap>\s*ð\d+\s*)\{v(\d+)\}")
    for pid, source in enumerate(texts):
        for match in reversed(list(pattern.finditer(source))):
            a, b = int(match[1]), int(match[3])
            if (
                owners[a] != pid
                or owners[b] != pid
                or not formulas[a]
                or not formulas[b]
            ):
                continue
            left, right = formulas[a], formulas[b]
            if not any(ch.get_text() == "=" for ch in left) or not "".join(
                ch.get_text() for ch in right
            ).startswith("+expð"):
                continue
            em = paragraphs[pid].size
            baseline = _char_baseline(max(left, key=lambda ch: ch.size))
            x0, x1 = max(ch.x1 for ch in left), min(ch.x0 for ch in right)
            gap_chars = sorted(
                [
                    ch
                    for ch in text_chars[pid]
                    if x0 - 0.1 * em <= ch.x0
                    and ch.x1 <= x1 + 0.1 * em
                    and abs(_char_baseline(ch) - baseline) <= 0.15 * em
                    and not _is_non_horizontal_text_matrix(ch.matrix)
                ],
                key=lambda ch: ch.x0,
            )
            if (
                "".join(ch.get_text() for ch in gap_chars).replace(" ", "")
                != match["gap"].strip()
            ):
                continue
            if x1 - x0 > 3 * em or not all(
                abs(_char_baseline(ch) - baseline) < em for ch in right
            ):
                continue
            formulas[a] = left + gap_chars + right
            lines[a].extend(lines[b])
            texts[pid] = (
                texts[pid][: match.start()] + f"{{v{a}}}" + texts[pid][match.end() :]
            )


_FORMULA_TRAILING_PROSE_WORDS = frozenset(
    {
        "and",
        "are",
        "be",
        "been",
        "being",
        "called",
        "correspond",
        "corresponds",
        "corresponded",
        "denote",
        "denotes",
        "denoted",
        "in",
        "indicates",
        "is",
        "represent",
        "represents",
        "represented",
        "respectively",
        "to",
        "where",
        "which",
        "was",
        "were",
    }
)


def _split_trailing_formula_prose_word(
    chars: list[LTChar],
    *,
    has_prose_context: bool = False,
    paragraph_layout_class: int | None = None,
) -> tuple[list[LTChar], str, list[LTChar]]:
    """Detach a high-confidence grammar word swallowed after a math glyph.

    The suffix must be an explicit connective/verb, use only ordinary text fonts,
    and immediately follow a mathematical-font glyph.  This intentionally does
    not classify arbitrary English labels or mathematical function names.
    """
    if paragraph_layout_class == 0 or any(
        getattr(char, "_pdf2zh_layout_class", None) == 0 for char in chars
    ):
        return chars, "", []
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
    # A publisher can leave a grammar connector glued to the closing ket,
    # e.g. ``|01>—to realize``. The em dash and connector belong to prose;
    # keep only the closed mathematical expression immutable.
    prose_dash = (
        has_prose_context
        and preceding_text in {"—", "–"}
        and split_at >= 2
        and chars[split_at - 2].get_text() in _FORMULA_CLOSING | {"⟩", "〉"}
        and not _MATH_FONT_RE.search(_formula_font_name(preceding))
    )
    mathematical_preceding = chars[split_at - 2] if prose_dash else preceding
    mathematical_split_at = split_at - 1 if prose_dash else split_at
    preceding_is_formula_closer = (
        mathematical_preceding.get_text() in _FORMULA_CLOSING | {"⟩", "〉"}
        and mathematical_split_at >= 2
        and any(
            _MATH_FONT_RE.search(_formula_font_name(char))
            or (
                char.get_text()
                and unicodedata.category(char.get_text()[0]) in {"Lm", "Mn", "Sk", "Sm"}
            )
            for char in chars[
                max(0, mathematical_split_at - 4) : mathematical_split_at - 1
            ]
        )
    )
    lowered_word = word.lower()
    if lowered_word in {"and", "in", "to"} and not (
        has_prose_context and preceding_is_formula_closer
    ):
        # Do not turn a mixed-font mathematical function such as ``sin`` into
        # the formula ``s`` plus the English preposition ``in``.  These short
        # connectives are admitted only after a proven formula-closing mark.
        return chars, "", []
    boundary_chars = (preceding, suffix[0])
    if any(
        _is_non_horizontal_text_matrix(
            getattr(char, "_pdf2zh_source_text_state", None).matrix
            if getattr(char, "_pdf2zh_source_text_state", None) is not None
            else char.matrix
        )
        for char in boundary_chars
    ):
        return chars, "", []
    preceding_size = float(preceding.size)
    suffix_size = float(suffix[0].size)
    em = max(preceding_size, suffix_size, 1.0)
    if (
        min(preceding_size, suffix_size) <= 0
        or max(preceding_size, suffix_size) / min(preceding_size, suffix_size) > 1.10
        or abs(_char_baseline(preceding) - _char_baseline(suffix[0])) > 0.15 * em
    ):
        return chars, "", []
    gap = float(suffix[0].x0) - float(preceding.x1)
    if not -0.10 * em <= gap <= 0.45 * em:
        return chars, "", []
    if lowered_word == "indicates" and not (
        preceding_text
        and not preceding_text.isalnum()
        and (
            _MATH_FONT_RE.search(_formula_font_name(preceding))
            or unicodedata.category(preceding_text[0]) in {"Lm", "Mn", "Sk", "Sm"}
        )
    ):
        # Legend arrows are symbols followed by ordinary-font ``indicates``;
        # an alphabetic formula identifier with that suffix is not this shape.
        return chars, "", []
    if not (
        _MATH_FONT_RE.search(_formula_font_name(preceding))
        or (
            preceding_text
            and unicodedata.category(preceding_text[0]) in {"Lm", "Mn", "Sk", "Sm"}
        )
        or preceding_is_formula_closer
    ):
        return chars, "", []
    spacer = " " if gap > 0.12 * em else ""
    if prose_dash:
        return (
            chars[: split_at - 1],
            preceding_text + spacer + word,
            chars[split_at - 1 :],
        )
    return chars[:split_at], spacer + word, suffix


def _split_trailing_formula_prose_clause(
    chars: list[LTChar],
    *,
    paragraph_layout_class: int | None = None,
) -> tuple[list[LTChar], str, list[LTChar]]:
    """Detach one closed-form sentence tail swallowed by an italic run.

    IEEE sometimes emits the italic noun immediately before a sentence boundary
    and the following ordinary-font explanatory clause as one formula run.  Only
    the narrow ``word. “Term” refers`` shape is released here.  The leading
    italic word remains a separate style candidate; the sentence punctuation and
    ordinary-font clause return to normal prose.  Mathematical fonts, protected
    layout class 0, unbalanced quotes, and arbitrary clauses fail closed.
    """

    if (
        len(chars) < 8
        or paragraph_layout_class == 0
        or any(getattr(char, "_pdf2zh_layout_class", None) == 0 for char in chars)
    ):
        return chars, "", []
    for split_at, char in enumerate(chars):
        if char.get_text() not in ".!?":
            continue
        prefix = chars[:split_at]
        suffix = chars[split_at:]
        prefix_text = _reconstruct_italic_run(prefix)
        suffix_text = _reconstruct_italic_run(suffix)
        if (
            re.fullmatch(r"[a-z][a-z'-]{3,}", prefix_text) is None
            or not all(_is_prose_italic_font(item.fontname) for item in prefix)
            or any(_MATH_FONT_RE.search(_formula_font_name(item)) for item in suffix)
            or re.fullmatch(
                r"[.!?]\s*[“\"][A-Z][A-Za-z'-]{2,}[”\"]\s+" r"(?:refers|means|denotes)",
                suffix_text,
            )
            is None
        ):
            continue
        visible = [item for item in chars if item.get_text()]
        if any(
            _is_non_horizontal_text_matrix(
                getattr(item, "_pdf2zh_source_text_state", None).matrix
                if getattr(item, "_pdf2zh_source_text_state", None) is not None
                else item.matrix
            )
            for item in visible
        ):
            continue
        sizes = [float(item.size) for item in visible]
        typical_size = float(np.median(sizes))
        baselines = [_char_baseline(item) for item in visible]
        boundary_gap = float(suffix[0].x0) - float(prefix[-1].x1)
        if (
            typical_size <= 0
            or min(sizes) <= 0
            or max(sizes) / min(sizes) > 1.10
            or max(baselines) - min(baselines) > 0.15 * typical_size
            or not -0.10 * typical_size <= boundary_gap <= 0.45 * typical_size
        ):
            continue
        return prefix, suffix_text, suffix
    return chars, "", []


def _split_trailing_runin_connector(
    chars: list[LTChar],
    *,
    paragraph_layout_class: int | None = None,
) -> tuple[list[LTChar], str, list[LTChar]]:
    """Return a Roman connector swallowed by an italic run-in heading.

    IEEE content streams often encode ``Heading: The`` as one formula-like run:
    the heading is italic while the first prose word is Roman.  Leaving the
    connector inside the styled atom creates a separate anchored paragraph and
    can duplicate translated prepositions.  Only a small grammatical connector
    set after a complete italic heading is released, with conservative font and
    geometry checks.
    """

    if (
        len(chars) < 6
        or paragraph_layout_class == 0
        or any(getattr(char, "_pdf2zh_layout_class", None) == 0 for char in chars)
    ):
        return chars, "", []
    for split_at in range(1, len(chars)):
        heading_chars = chars[:split_at]
        connector_chars = chars[split_at:]
        heading = _reconstruct_italic_run(heading_chars)
        connector = _reconstruct_italic_run(connector_chars)
        if (
            _RUNIN_ITALIC_HEADING_RE.fullmatch(heading) is None
            or _RUNIN_CONNECTOR_RE.fullmatch(connector) is None
            or not all(_is_prose_italic_font(char.fontname) for char in heading_chars)
            or any(
                _is_prose_italic_font(char.fontname)
                or not _is_non_math_text_font(char.fontname)
                for char in connector_chars
            )
        ):
            continue
        visible = [char for char in chars if char.get_text()]
        if any(
            _is_non_horizontal_text_matrix(
                getattr(char, "_pdf2zh_source_text_state", None).matrix
                if getattr(char, "_pdf2zh_source_text_state", None) is not None
                else char.matrix
            )
            for char in visible
        ):
            continue
        sizes = [float(char.size) for char in visible]
        typical_size = float(np.median(sizes))
        baselines = [_char_baseline(char) for char in visible]
        gap = float(connector_chars[0].x0) - float(heading_chars[-1].x1)
        if (
            typical_size <= 0
            or min(sizes) <= 0
            or max(sizes) / min(sizes) > 1.10
            or max(baselines) - min(baselines) > 0.15 * typical_size
            or not -0.10 * typical_size <= gap <= 0.45 * typical_size
        ):
            continue
        separator = " " if gap > 0.12 * typical_size else ""
        return heading_chars, separator + connector, connector_chars
    return chars, "", []


def _release_tex_accented_prose_words(
    segments, formulas, formula_paragraphs, paragraphs, paragraph_text_chars
):
    """Expose a complete text word split by an overprinted TeX accent.

    A text accent can look like a control sequence and start a formula island
    halfway through a surname. Require a touching ordinary-font word prefix,
    an overprinted accent/base pair, and at least two following letters. Math
    accents, isolated variables, code and preserved areas stay as source glyphs.
    """
    literal_accents = {
        "´": "\u0301",
        "`": "\u0300",
        "¨": "\u0308",
        "ˆ": "\u0302",
        "˜": "\u0303",
        "¯": "\u0304",
        "ˇ": "\u030c",
        "˘": "\u0306",
        "˚": "\u030a",
        "˙": "\u0307",
        "¸": "\u0327",
    }
    for formula_id, chars in enumerate(formulas):
        if len(chars) != 1 or chars[0].get_text() not in literal_accents:
            continue
        accent = chars[0]
        paragraph_id = formula_paragraphs[formula_id]
        paragraph = paragraphs[paragraph_id]
        marker = f"{{v{formula_id}}}"
        if paragraph.layout_class <= 1 or segments[paragraph_id].count(marker) != 1:
            continue
        left, right = segments[paragraph_id].split(marker)
        prefix = re.search(r"([A-Za-z]{2,})\s*$", left)
        suffix = re.match(r"([A-Za-z]+)", right)
        if not prefix or not suffix or len(prefix[1]) + len(suffix[1]) < 4:
            continue
        em = float(paragraph.size)
        if not _is_non_math_text_font(
            accent.fontname
        ) or _is_non_horizontal_text_matrix(accent.matrix):
            continue
        bases = [
            char
            for char in paragraph_text_chars[paragraph_id]
            if char.get_text() == suffix[1][0]
            and char.fontname == accent.fontname
            and abs(char.x0 - accent.x0) <= 0.35 * em
            and char.x0 < accent.x1 - 0.15 * em
            and abs(_char_baseline(char) - _char_baseline(accent)) <= 0.20 * em
            and 0.9 * em <= char.size <= 1.1 * em
        ]
        if len(bases) != 1:
            continue
        previous = bases[0]
        matched = []
        for letter in reversed(prefix[1]):
            choices = [
                char
                for char in paragraph_text_chars[paragraph_id]
                if char.get_text() == letter
                and char.fontname == previous.fontname
                and abs(_char_baseline(char) - _char_baseline(previous)) <= 0.15 * em
                and -0.12 * em <= previous.x0 - char.x1 <= 0.16 * em
            ]
            if len(choices) != 1:
                break
            previous = choices[0]
            matched.append(previous)
        if len(matched) != len(prefix[1]):
            continue
        combined = unicodedata.normalize(
            "NFC", suffix[1][0] + literal_accents[accent.get_text()]
        )
        segments[paragraph_id] = (
            left[: prefix.start()] + prefix[1] + combined + right[1:]
        )
        paragraph_text_chars[paragraph_id].append(accent)
    accents = {
        "`": "\u0300",
        "'": "\u0301",
        '"': "\u0308",
        "^": "\u0302",
        "~": "\u0303",
        "=": "\u0304",
    }
    for formula_id, chars in enumerate(formulas):
        if not chars or chars[0].get_text() not in {"\\" + key for key in accents}:
            continue
        paragraph_id = formula_paragraphs[formula_id]
        paragraph = paragraphs[paragraph_id]
        if (
            paragraph.layout_class <= 1
            or paragraph.region_kind not in _INLINE_SCRIPT_REGION_KINDS
        ):
            continue
        marker = f"{{v{formula_id}}}"
        source = segments[paragraph_id]
        if source.count(marker) != 1:
            continue
        left, right = source.split(marker)
        prefix = re.search(r"(?<![A-Za-z])([A-Z][a-z]*|[a-z]{2,})$", left)
        if prefix is None or re.match(r"[A-Za-z]", right):
            continue
        em = float(paragraph.size)
        # CM small caps can encode dotless i as one multi-character ToUnicode
        # glyph in prose, leaving only the overprinted accent in the island.
        # Recover it only with the same touching source-glyph evidence.
        dotless = re.match(r"\{\\i\}([a-z]{2,})(?![A-Za-z])", right)
        if prefix.group(1).islower() and (
            len(chars) != 1
            or dotless is None
            or len(prefix.group(1)) + 1 + len(dotless.group(1)) < 5
        ):
            continue
        if len(chars) == 1 and dotless is not None:
            accent = chars[0]
            candidates = [
                char
                for char in paragraph_text_chars[paragraph_id]
                if (
                    char.get_text() == r"{\i}"
                    and abs(char.x0 - accent.x0) <= 0.25 * em
                    and abs(_char_baseline(char) - _char_baseline(accent)) <= 0.15 * em
                )
            ]
            if len(candidates) != 1:
                continue
            expanded = [accent, candidates[0]]
            for value in dotless.group(1):
                previous = expanded[-1]
                candidates = [
                    char
                    for char in paragraph_text_chars[paragraph_id]
                    if (
                        char.get_text() == value
                        and -0.12 * em <= char.x0 - previous.x1 <= 0.16 * em
                        and abs(_char_baseline(char) - _char_baseline(previous))
                        <= 0.15 * em
                    )
                ]
                if len(candidates) != 1:
                    break
                expanded.append(candidates[0])
            if len(expanded) != len(dotless.group(1)) + 2:
                continue
            chars = expanded
            right = right[dotless.end() :]
        if len(chars) < 3:
            continue
        tail = "".join(
            "i" if char.get_text() == r"{\i}" else char.get_text() for char in chars[1:]
        )
        if re.fullmatch(r"[a-z]{2,}", tail) is None:
            continue
        accent, base = chars[:2]
        if em <= 0 or any(
            getattr(char, "_pdf2zh_layout_class", None) == 0
            or re.match(r"^CM(?:R|TI|SL|BX|CSC)\d", _font_name(char.fontname)) is None
            or char.fontname != base.fontname
            or not 0.9 * em <= float(char.size) <= 1.1 * em
            or _is_non_horizontal_text_matrix(char.matrix)
            or abs(_char_baseline(char) - _char_baseline(base)) > 0.15 * em
            for char in chars
        ):
            continue
        if abs(accent.x0 - base.x0) > 0.25 * em or base.x0 >= accent.x1 - 0.2 * em:
            continue
        if any(
            not -0.12 * em <= b.x0 - a.x1 <= 0.16 * em
            for a, b in zip(chars[1:], chars[2:])
        ):
            continue
        previous = base
        matched = []
        for value in reversed(prefix.group(1)):
            candidates = [
                char
                for char in paragraph_text_chars[paragraph_id]
                if (
                    char.get_text() == value
                    and char.fontname == base.fontname
                    and not _is_non_horizontal_text_matrix(char.matrix)
                    and 0.9 * em <= char.size <= 1.1 * em
                    and abs(_char_baseline(char) - _char_baseline(base)) <= 0.15 * em
                    and -0.12 * em <= previous.x0 - char.x1 <= 0.16 * em
                )
            ]
            if len(candidates) != 1:
                break
            previous = candidates[0]
            matched.append(previous)
        if len(matched) != len(prefix.group(1)):
            continue
        decoded = unicodedata.normalize("NFC", tail[0] + accents[accent.get_text()[1]])
        if len(decoded) != 1 or not decoded.isalpha():
            continue
        segments[paragraph_id] = left + decoded + tail[1:] + right
        known = {id(char) for char in paragraph_text_chars[paragraph_id]}
        paragraph_text_chars[paragraph_id].extend(
            char for char in chars if id(char) not in known
        )


def _release_reference_number_labels(
    segments, formulas, lines, formula_paragraphs, paragraphs, text_chars
):
    """Restore citation labels and subtitle articles hidden as inline math."""
    author = re.compile(
        r"^\s*[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+,\s*[A-Z](?:\.|[.\-][A-Z])"
    )

    def expand(value):
        return unicodedata.normalize(
            "NFC",
            _FORMULA_MARKER_RE.sub(
                lambda m: "".join(c.get_text() for c in formulas[int(m.group(1))]),
                value,
            ),
        )

    expanded = [expand(s) for s in segments]
    for index, chars in enumerate(formulas):
        label = "".join(ch.get_text() for ch in chars)
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        marker = f"{{v{index}}}"
        # Superscript labels can absorb the first author's initial, or attach
        # to the preceding italic venue/year across a physical line break.
        # Require bibliography evidence and a small numeral at the column's
        # left edge; ordinary years, volumes, powers and bylines do not qualify.
        leading = re.fullmatch(r"(?P<number>\d{1,3})[A-Z][A-Za-z]{0,2}", label)
        trailing = re.search(r"(?<=[.,])(?P<number>\d{1,3})$", label)
        candidate = leading or trailing
        if (
            candidate
            and not lines[index]
            and paragraph.region_kind == "plain text"
            and paragraph.layout_class > 1
            and segments[paragraph_id].count(marker) == 1
            and reference_entry_score(expanded[paragraph_id]) >= 5
            and all(len(ch.get_text()) == 1 for ch in chars)
        ):
            start, end = candidate.span("number")
            digits = chars[start:end]
            em = float(paragraph.size)
            left, right = segments[paragraph_id].split(marker)
            if (
                digits
                and all(0.5 * em <= ch.size <= 0.85 * em for ch in digits)
                and paragraph.x0 - 0.5 * em <= digits[0].x0 <= paragraph.x0 + 1.2 * em
                and all(
                    _is_non_math_text_font(ch.fontname)
                    and not _is_non_horizontal_text_matrix(ch.matrix)
                    for ch in digits
                )
            ):
                if leading and re.match(r"\s*(?:[.,]\s*)?[A-ZÀ-ÖØ-Þ]", right):
                    initial = chars[end:]
                    if all(
                        0.9 * em <= ch.size <= 1.1 * em
                        and 0.15 * em
                        <= _char_baseline(digits[0]) - _char_baseline(ch)
                        <= 0.7 * em
                        and _is_non_math_text_font(ch.fontname)
                        for ch in initial
                    ):
                        segments[paragraph_id] = left + label + right
                        text_chars[paragraph_id].extend(chars)
                        continue
                if (
                    trailing
                    and re.search(
                        r"(?:19|20)\d{2}\)?\.\s*$", expand(left) + label[:start]
                    )
                    and re.match(r"\s*[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+\b", right)
                    and digits[0].x0 < chars[start - 1].x0 - em
                    and _char_baseline(digits[0]) < _char_baseline(chars[start - 1])
                ):
                    formulas[index] = chars[:start]
                    segments[paragraph_id] = (
                        left + marker + " " + candidate["number"] + right.lstrip()
                    )
                    text_chars[paragraph_id].extend(digits)
                    continue
        article = re.fullmatch(r"\):\s*(A|An|The)", label)
        if article and not lines[index]:
            paragraph_id = formula_paragraphs[index]
            paragraph = paragraphs[paragraph_id]
            marker = f"{{v{index}}}"
            if (
                paragraph.region_kind == "plain text"
                and paragraph.layout_class > 1
                and segments[paragraph_id].count(marker) == 1
                and reference_entry_score(expanded[paragraph_id]) >= 5
                and all(
                    _is_non_math_text_font(char.fontname)
                    for char in chars[-len(article[1]) :]
                )
            ):
                left, right = segments[paragraph_id].split(marker)
                if re.match(r"\s+[A-Z][A-Za-z-]{2,}\b", right):
                    formulas[index] = chars[: -len(article[1])]
                    segments[paragraph_id] = (
                        left + marker + article[1] + " " + right.lstrip()
                    )
                    text_chars[paragraph_id].extend(chars[-len(article[1]) :])
        if chars and re.search(r"\d[.,;]\s*\($", label) and not lines[index]:
            paragraph_id = formula_paragraphs[index]
            paragraph = paragraphs[paragraph_id]
            marker = f"{{v{index}}}"
            if (
                paragraph.region_kind == "plain text"
                and paragraph.layout_class > 1
                and segments[paragraph_id].count(marker) == 1
                and _is_non_math_text_font(chars[-1].fontname)
                and reference_entry_score(expanded[paragraph_id]) >= 5
            ):
                left, right = segments[paragraph_id].split(marker)
                if re.match(r"\s*\d{1,3}\)\s+[A-Z]", right):
                    # The preceding venue/page glyph island swallowed only the
                    # next label's opener, e.g. "),6393.(" + "26) ...".
                    formulas[index] = chars[:-1]
                    segments[paragraph_id] = left + marker + " (" + right.lstrip()
                    text_chars[paragraph_id].append(chars[-1])
        if not re.fullmatch(r"\(\d{1,3}\)(?:\([a-z]\))?", label) or lines[index]:
            continue
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        marker = f"{{v{index}}}"
        if (
            paragraph.region_kind != "plain text"
            or paragraph.layout_class <= 1
            or segments[paragraph_id].count(marker) != 1
        ):
            continue
        if any(
            not _is_non_math_text_font(ch.fontname)
            or _is_non_horizontal_text_matrix(ch.matrix)
            for ch in chars
        ):
            continue
        left, right = segments[paragraph_id].split(marker)
        if not left.strip() and not right.strip() and paragraph_id + 1 < len(segments):
            next_id = paragraph_id + 1
            following = paragraphs[next_id]
            if (
                following.region_kind == "plain text"
                and author.match(expand(segments[next_id]))
                and abs(following.y - paragraph.y) <= 0.25 * paragraph.size
                and -0.1 * paragraph.size
                <= following.x - paragraph.x1
                <= 1.5 * paragraph.size
            ):
                segments[next_id] = label + " " + segments[next_id]
                segments[paragraph_id] = ""
                following.x = paragraph.x
                following.x0 = min(following.x0, paragraph.x0)
                text_chars[next_id].extend(chars)
            continue
        if (
            author.match(expand(right))
            and reference_entry_score(expanded[paragraph_id]) >= 5
        ):
            separator = " " if left and not left[-1].isspace() else ""
            segments[paragraph_id] = left + separator + label + " " + right.lstrip()
            text_chars[paragraph_id].extend(chars)


def _merge_adjacent_reference_tail_lines(
    segments, formulas, formula_paragraphs, paragraphs, text_chars
):
    """Rejoin a bibliography entry split by a detector into adjacent lines."""
    from pdf2zh.reading_flow import _has_reference_terminal
    from pdf2zh.translation_policy import find_reference_markers

    expanded = lambda text: _FORMULA_MARKER_RE.sub(
        lambda m: _reconstruct_italic_run(formulas[int(m[1])]), text
    )
    for pid, source in enumerate(segments):
        before = paragraphs[pid]
        if (
            before.region_kind != "plain text"
            or reference_entry_score(expanded(source)) < 3
            or _has_reference_terminal(expanded(source))
        ):
            continue
        nxt = next(
            (j for j in range(pid + 1, len(segments)) if segments[j].strip()), None
        )
        if nxt is None:
            continue
        following = segments[nxt]
        after = paragraphs[nxt]
        markers = find_reference_markers(following)
        prefix = following[: markers[0].start] if markers else following
        if (
            after.region_kind != "plain text"
            or re.match(r"\s*(?:\[\d+\]|\(\d+\)|\d+[.)])", following)
            or not _has_reference_terminal(expanded(prefix))
            or len(prefix) > 700
            or abs(before.size - after.size) > 0.1 * before.size
            or not -0.2 * before.size <= before.y0 - after.y1 <= 2 * before.size
            or not -before.size <= after.x0 - before.x0 <= 3 * before.size
        ):
            continue
        segments[pid] = source.rstrip() + " " + following.lstrip()
        segments[nxt] = ""
        before.y0 = min(before.y0, after.y0)
        before.x1 = max(before.x1, after.x1)
        before.break_offsets.extend(len(source) + 1 + o for o in after.break_offsets)
        text_chars[pid].extend(text_chars[nxt])
        text_chars[nxt] = []
        for i, owner in enumerate(formula_paragraphs):
            if owner == nxt:
                formula_paragraphs[i] = pid
        return True
    return False


def _release_bold_chemical_names(
    segments, formulas, formula_paragraphs, paragraphs, text_chars
):
    """Recover systematic names in bold compound headings, not spectral data.

    Chemistry publishers style the whole IUPAC name in a bold text font. A
    closing parenthesis can start a protected run and swallow its suffix. Only
    the name before its compound-number label (or an entire bold name line)
    qualifies; mixed-font NMR units and actual equations remain protected.
    """
    stems = re.compile(
        r"amino|methyl|ethyl|chloro|bromo|fluoro|hydroxy|pyrrolo|pyrimidi|pyrazino|carboxylate|thiazol|phenyl|butyl|benz",
        re.I,
    )
    allowed = re.compile(r"[A-Za-z0-9\s(),.\[\]:/'’′″−–\-]+")
    for pid, source in enumerate(segments):
        if paragraphs[pid].region_kind not in {"plain text", "title", ""}:
            continue
        candidates = {}
        for match in _FORMULA_MARKER_RE.finditer(source):
            index = int(match[1])
            chars = formulas[index]
            if not chars or formula_paragraphs[index] != pid:
                continue
            if any(
                not _PROSE_BOLD_FONT_RE.search(_font_name(c.fontname))
                or _is_non_horizontal_text_matrix(c.matrix)
                for c in chars
            ):
                continue
            value = _reconstruct_italic_run(chars)
            if allowed.fullmatch(value):
                candidates[index] = value
        if not candidates:
            continue
        expanded = _FORMULA_MARKER_RE.sub(
            lambda m: candidates.get(int(m[1]), m[0]), source
        )
        label = re.search(r"\(\d{1,3}[a-z]?\)\.", expanded)
        name = expanded[: label.end()] if label else expanded
        if (
            len(stems.findall(name)) < 2
            or not re.search(r"\d", name)
            or not allowed.fullmatch(name)
            or (label is None and not _FORMULA_MARKER_RE.fullmatch(source.strip()))
        ):
            continue
        cursor = 0
        pieces = []
        for match in _FORMULA_MARKER_RE.finditer(source):
            index = int(match[1])
            before = _FORMULA_MARKER_RE.sub(
                lambda m: candidates.get(int(m[1]), m[0]), source[: match.start()]
            )
            value = candidates.get(index)
            pieces.append(source[cursor : match.start()])
            if value is not None and (label is None or len(before) < label.end()):
                pieces.append(value)
                text_chars[pid].extend(formulas[index])
            else:
                pieces.append(match[0])
            cursor = match.end()
        pieces.append(source[cursor:])
        segments[pid] = "".join(pieces)
    for pid in range(len(segments) - 1):
        first, second = segments[pid], segments[pid + 1]
        before, after = paragraphs[pid], paragraphs[pid + 1]
        if (
            not first.rstrip().endswith("-")
            or len(stems.findall(first)) < 2
            or not allowed.fullmatch(first)
            or not re.match(
                r"(?:pyrrolo|pyrimidi|pyrazino|thiazol|phenyl|benz)", second, re.I
            )
            or not re.search(r"\(\d{1,3}[a-z]?\)\.", second[:300])
            or before.page_id != after.page_id
            or before.layout_class != after.layout_class
            or abs(before.size - after.size) > 0.1 * before.size
            or not -0.2 * before.size <= before.y0 - after.y1 <= before.size
            or min(before.x1, after.x1) - max(before.x0, after.x0)
            < 0.8 * min(before.x1 - before.x0, after.x1 - after.x0)
        ):
            continue
        segments[pid + 1] = first.rstrip() + second.lstrip()
        segments[pid] = ""
        after.x, after.y = before.x, before.y
        after.x0 = min(before.x0, after.x0)
        after.x1 = max(before.x1, after.x1)
        after.y1 = max(before.y1, after.y1)
        after.break_offsets = [len(first) + o for o in after.break_offsets]
        text_chars[pid + 1].extend(text_chars[pid])
        text_chars[pid] = []
        for index, owner in enumerate(formula_paragraphs):
            if owner == pid:
                formula_paragraphs[index] = pid + 1


def _recover_author_information_affiliations(
    segments, formulas, formula_paragraphs, paragraphs
):
    """Translate publisher contact affiliations while keeping each author name.

    These are address lists, proved by institution vocabulary plus postal or
    contact metadata. No general italic prose or mathematical run is released.
    """
    affiliation_rows = [p for p in paragraphs if p.region_kind == "source_affiliation"]
    for pid, paragraph in enumerate(paragraphs):
        if paragraph.region_kind != "source_affiliation":
            continue
        paragraph.x1 = max(
            p.x1 for p in affiliation_rows if abs(p.x0 - paragraph.x0) <= paragraph.size
        )
        source = segments[pid]
        runs = [formulas[int(m[1])] for m in _FORMULA_MARKER_RE.finditer(source)]
        expanded = _FORMULA_MARKER_RE.sub(
            lambda m: _reconstruct_italic_run(formulas[int(m[1])]), source
        )
        if not looks_like_affiliation(expanded):
            continue
        segments[pid] = expanded
        paragraph.region_kind = "plain text"
        paragraph.affiliation_parts = (SegmentPart(ROLE_AFFILIATION, expanded),)
        letters = [char for run in runs for char in run if char.get_text().isalpha()]
        if letters:
            paragraph.size = float(np.median([char.size for char in letters]))
            paragraph.y = float(np.median([char.y0 for char in letters]))
        paragraph.brk = True
    name_pattern = re.compile(
        r"(?P<name>[A-Z][A-Za-z.'’\-]+(?:\s+[A-Z][A-Za-z.'’\-]+){1,4})"
        r"\s*[−—–]\s*(?=(?:Department|Institute|State|International|Laboratory|"
        r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]*){0,2}\s+"
        r"(?:Center|Therapeutics|University|GmbH|Ltd|Inc|Limited))\b)"
    )
    for paragraph_id, source in enumerate(segments):
        paragraph = paragraphs[paragraph_id]
        if paragraph.region_kind != "plain text" or paragraph.layout_class <= 1:
            continue
        expanded = source
        expanded_ranges = []
        expanded_breaks = list(paragraph.break_offsets)
        for match in reversed(list(_FORMULA_MARKER_RE.finditer(source))):
            index = int(match.group(1))
            chars = formulas[index]
            if formula_paragraphs[index] != paragraph_id or not chars:
                continue
            value = _reconstruct_italic_run(chars)
            if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9\s,.;:&()/'’@_+−—–\-]+", value):
                continue
            if any(_is_non_horizontal_text_matrix(ch.matrix) for ch in chars):
                continue
            delta = len(value) - (match.end() - match.start())
            expanded_ranges = [
                (a + delta, b + delta) if a >= match.end() else (a, b)
                for a, b in expanded_ranges
            ]
            expanded_ranges.append((match.start(), match.start() + len(value)))
            expanded_breaks = [
                offset + delta if offset >= match.end() else offset
                for offset in expanded_breaks
            ]
            expanded = expanded[: match.start()] + value + expanded[match.end() :]
        if (
            _FORMULA_MARKER_RE.search(expanded)
            or len(
                re.findall(
                    r"\b(?:Department|University|Institute|Laboratory|Hospital|Center|Therapeutics|GmbH|Ltd|Inc)\b",
                    expanded,
                )
            )
            < 1
            or (
                not re.search(r"\b\d{5,6}\b|\bEmail:|orcid\.org/", expanded)
                and not (
                    name_pattern.match(expanded)
                    and len(
                        re.findall(
                            r"\b(?:Institute|Laboratory|University|Academy)\b", expanded
                        )
                    )
                    >= 3
                )
            )
        ):
            continue
        names = []
        for match in name_pattern.finditer(expanded):
            start = match.start()
            # Country/address words expanded from an italic source run are not
            # part of the following Roman author name ("Germany Florian ...").
            for a, b in expanded_ranges:
                if a <= start < b <= match.end("name"):
                    start = b
                    break
            # Present-address tails can be Roman too. Only an actual source
            # line break after a country label establishes the new name.
            for boundary in expanded_breaks:
                if start < boundary < match.end("name") and re.fullmatch(
                    r"\s*(?:UK|U\.K\.|United States|United Kingdom|Germany|China)\s*",
                    expanded[start:boundary],
                ):
                    start = boundary
            country = re.match(r"(?:UK|U\.K\.)\s+", expanded[start:])
            if country and re.search(
                r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2},\s*$",
                expanded[max(0, start - 30) : start],
            ):
                start += country.end()
            while start < match.end("name") and (
                expanded[start].isspace() or expanded[start] in ",.;"
            ):
                start += 1
            precise = name_pattern.match(expanded, start)
            if precise and precise.end() == match.end():
                names.append(precise)
        if not names:
            if (
                re.search(r"\b\d{5,6}\b", expanded)
                and "orcid.org/" in expanded
                and re.search(r"\bEmail:\s*\S+@", expanded)
            ):
                segments[paragraph_id] = expanded
                paragraph.affiliation_parts = (SegmentPart(ROLE_AFFILIATION, expanded),)
            continue
        if expanded[: names[0].start()].strip():
            continue
        parts = []
        cursor = 0
        for match in names:
            if match.start() > cursor:
                parts.append(
                    SegmentPart(ROLE_AFFILIATION, expanded[cursor : match.start()])
                )
            parts.append(
                SegmentPart(
                    ROLE_PRESERVE,
                    expanded[match.start() : match.end()],
                    break_before=bool(parts),
                )
            )
            cursor = match.end()
        if expanded[cursor:]:
            parts.append(SegmentPart(ROLE_AFFILIATION, expanded[cursor:]))
        segments[paragraph_id] = expanded
        paragraph.break_offsets = expanded_breaks
        paragraph.affiliation_parts = tuple(parts)


def _release_reference_prose_fragments(
    segments, formulas, formula_paragraphs, paragraphs, text_chars
):
    """Expose ordinary italic title words accidentally treated as math macros.

    Require a certified numbered bibliography, unchanged text-font geometry,
    and a complete prose compound. Operators such as rank(A), code fonts and
    actual mathematical symbols remain opaque.
    """
    regions = {
        i: split_numbered_reference_region(source) for i, source in enumerate(segments)
    }
    for index, chars in enumerate(formulas):
        if not chars:
            continue
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        region = regions[paragraph_id]
        source = segments[paragraph_id]
        marker = f"{{v{index}}}"
        if (
            region is None
            or paragraph.layout_class <= 1
            or paragraph.region_kind != "plain text"
            or source.count(marker) != 1
        ):
            continue
        position = source.index(marker)
        if not region.start <= position < region.end:
            continue
        em = float(paragraph.size)
        if any(
            getattr(char, "_pdf2zh_layout_class", None) == 0
            or not _is_prose_italic_font(char.fontname)
            or char.fontname != chars[0].fontname
            or not 0.9 * em <= char.size <= 1.1 * em
            or _is_non_horizontal_text_matrix(char.matrix)
            or abs(_char_baseline(char) - _char_baseline(chars[0])) > 0.15 * em
            for char in chars
        ):
            continue
        word = "".join(char.get_text() for char in chars)
        left, right = source.split(marker)
        replacement = None
        consumed = 0
        if word == "rank" and (
            re.search(r"\b(?:low|minimum)-$", left, re.I)
            or re.match(r"(?:-|\s+)one\b", right)
        ):
            replacement = word
        elif (
            word == "up"
            and re.search(r"\bspeeding\s+$", left, re.I)
            and re.match(r"\s+first[- ]", right)
        ):
            replacement = word
        elif (
            word == "mod-"
            and re.search(r"\bsemi-algebraic\s+$", left, re.I)
            and re.match(r"els\b", right)
        ):
            replacement, consumed = "models", 3
        else:
            accent = re.fullmatch(r"([A-Z])\\([\"\'`])([a-z])", word)
            suffix = re.match(r"([a-z]{3,})(?![A-Za-z])", right)
            if accent and suffix and len(chars) == 3:
                combining = {'"': "\u0308", "'": "\u0301", "`": "\u0300"}[accent[2]]
                if abs(chars[1].x0 - chars[2].x0) <= 0.25 * em:
                    replacement = (
                        accent[1]
                        + unicodedata.normalize("NFC", accent[3] + combining)
                        + suffix[1]
                    )
                    consumed = len(suffix[1])
        if replacement is None:
            continue
        # All ordinary letters in the island must touch, allowing italic
        # overhang and the accent overprint checked separately above.
        baseline_chars = [
            char for char in chars if not char.get_text().startswith("\\")
        ]
        if any(
            not -0.25 * em <= b.x0 - a.x1 <= 0.20 * em
            for a, b in zip(baseline_chars, baseline_chars[1:])
        ):
            continue
        if consumed:
            previous = chars[-1]
            suffix_chars = []
            for offset, value in enumerate(right[:consumed]):
                following = [
                    char
                    for char in text_chars[paragraph_id]
                    if (
                        char.get_text() == value
                        and char.fontname == previous.fontname
                        and 0.9 * em <= char.size <= 1.1 * em
                        and not _is_non_horizontal_text_matrix(char.matrix)
                        and (
                            (
                                abs(_char_baseline(char) - _char_baseline(previous))
                                <= 0.15 * em
                                and -0.2 * em <= char.x0 - previous.x1 <= 0.2 * em
                            )
                            or (
                                word == "mod-"
                                and offset == 0
                                and 0.8 * em
                                <= _char_baseline(previous) - _char_baseline(char)
                                <= 1.8 * em
                                and paragraph.x0 <= char.x0 < paragraph.x0 + 5 * em
                            )
                        )
                    )
                ]
                if len(following) != 1:
                    break
                previous = following[0]
                suffix_chars.append(previous)
            if len(suffix_chars) != consumed:
                continue
        segments[paragraph_id] = left + replacement + right[consumed:]
        known = {id(char) for char in text_chars[paragraph_id]}
        text_chars[paragraph_id].extend(char for char in chars if id(char) not in known)


def _release_formula_case_explanations(
    segments, formulas, formula_paragraphs, paragraphs, text_chars
):
    """Return an ordinary-font '(in the ... case)' suffix to translated prose."""
    for index, chars in enumerate(formulas):
        if len(chars) < 4 or "".join(ch.get_text() for ch in chars[-3:]) != "(in":
            continue
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        if (
            paragraph.layout_class <= 1
            or paragraph.region_kind not in _INLINE_SCRIPT_REGION_KINDS
        ):
            continue
        source, marker = segments[paragraph_id], f"{{v{index}}}"
        if source.count(marker) != 1:
            continue
        following = source[source.index(marker) + len(marker) :]
        phrase = re.match(
            r"\s+the\s+(?:[A-Za-z-]+\s+){0,4}(?:case|setting|regime|limit)(?:\)|\{v(?P<closer>\d+)\})",
            following,
        )
        if phrase is None:
            continue
        prefix, suffix = chars[:-3], chars[-3:]
        joined = "".join(ch.get_text() for ch in chars)
        em = float(paragraph.size)
        if joined.count("(") <= joined.count(")") or em <= 0:
            continue
        if any(
            getattr(ch, "_pdf2zh_layout_class", None) == 0
            or not _is_non_math_text_font(ch.fontname)
            or re.search(r"(?:Mono|Code|^CMTT|^CMITT)", _font_name(ch.fontname), re.I)
            or not 0.9 * em <= ch.size <= 1.1 * em
            or _is_non_horizontal_text_matrix(ch.matrix)
            or abs(_char_baseline(ch) - _char_baseline(suffix[0])) > 0.15 * em
            for ch in suffix
        ):
            continue
        base = [ch for ch in prefix if 0.9 * em <= ch.size <= 1.1 * em]
        if not base or not any(
            abs(_char_baseline(ch) - _char_baseline(suffix[0])) < 0.15 * em
            for ch in base
        ):
            continue
        gap = suffix[0].x0 - max(ch.x1 for ch in prefix)
        if not 0.12 * em < gap <= 0.7 * em:
            continue
        if any(
            not -0.1 * em <= b.x0 - a.x1 <= 0.2 * em for a, b in zip(suffix, suffix[1:])
        ):
            continue
        closer_id = int(phrase["closer"]) if phrase["closer"] is not None else None
        if closer_id is not None:
            if (
                closer_id >= len(formulas)
                or formula_paragraphs[closer_id] != paragraph_id
            ):
                continue
            closer = formulas[closer_id]
            if (
                len(closer) != 1
                or closer[0].get_text() != ")"
                or closer[0].fontname != suffix[0].fontname
                or abs(closer[0].size - em) > 0.1 * em
                or _is_non_horizontal_text_matrix(closer[0].matrix)
                or source.count(f"{{v{closer_id}}}") != 1
            ):
                continue
            source = source.replace(f"{{v{closer_id}}}", ")", 1)
            text_chars[paragraph_id].extend(closer)
        formulas[index] = prefix
        segments[paragraph_id] = source.replace(marker, marker + " (in", 1)
        text_chars[paragraph_id].extend(suffix)


def _restore_drawn_uri_underscores(segments, paragraphs, text_chars, lines, links):
    """Recover vector-drawn URI underscores using the source link and glyphs.

    Only a complete same-line URI whose visible characters exactly match the
    annotation is eligible. Each absent underscore also needs its own short
    source line between the adjacent glyphs. The consumed lines are removed
    because the reconstructed URI will draw the underscores with the text.
    """
    for uri, bounds in links:
        if "_" not in uri or re.fullmatch(r"https?://[^\s<>]+", uri) is None:
            continue
        x0, y0, x1, y1 = bounds
        for index, paragraph in enumerate(paragraphs):
            if (
                paragraph.layout_class <= 1
                or paragraph.region_kind not in _INLINE_SCRIPT_REGION_KINDS
            ):
                continue
            if not (
                paragraph.x0 <= x0 + 2
                and paragraph.x1 >= x1 - 2
                and paragraph.y0 <= y0 + 2
                and paragraph.y1 >= y1 - 2
            ):
                continue
            chars = [
                char
                for char in text_chars[index]
                if (
                    x0 <= (char.x0 + char.x1) / 2 <= x1
                    and y0 <= (char.y0 + char.y1) / 2 <= y1
                    and not char.get_text().isspace()
                )
            ]
            if not chars or "".join(char.get_text() for char in chars) != uri.replace(
                "_", ""
            ):
                continue
            em = float(paragraph.size)
            if any(
                len(char.get_text()) != 1 or _is_non_horizontal_text_matrix(char.matrix)
                for char in chars
            ):
                continue
            if (
                max(_char_baseline(char) for char in chars)
                - min(_char_baseline(char) for char in chars)
                > 0.15 * em
            ):
                continue
            used_lines = []
            offset = 0
            for value in uri:
                if value != "_":
                    offset += 1
                    continue
                if not 0 < offset < len(chars):
                    break
                before, after = chars[offset - 1 : offset + 1]
                candidates = [
                    line
                    for line in lines
                    if (
                        len(line.pts) == 2
                        and abs(line.pts[0][1] - line.pts[1][1]) < 0.05 * em
                        and 0.1 * em
                        <= abs(line.pts[1][0] - line.pts[0][0])
                        <= 0.75 * em
                        and abs(min(line.pts[0][0], line.pts[1][0]) - before.x1)
                        <= 0.15 * em
                        and abs(max(line.pts[0][0], line.pts[1][0]) - after.x0)
                        <= 0.15 * em
                        and abs(line.pts[0][1] - _char_baseline(before)) <= 0.25 * em
                    )
                ]
                if len(candidates) != 1 or candidates[0] in used_lines:
                    break
                used_lines.append(candidates[0])
            if len(used_lines) != uri.count("_"):
                continue
            pattern = re.compile(
                r"(?<![A-Za-z0-9])"
                + re.escape(uri).replace("_", r"\s+")
                + r"(?![A-Za-z0-9_/])"
            )
            matches = list(pattern.finditer(segments[index]))
            if len(matches) != 1:
                continue
            match = matches[0]
            segments[index] = (
                segments[index][: match.start()] + uri + segments[index][match.end() :]
            )
            for line in used_lines:
                lines.remove(line)


def _restore_wrapped_source_uris(segments, paragraphs, text_chars, links):
    """Remove extraction spaces only at annotation-proven URI line wraps.

    Every linked row must have contiguous source glyphs, and concatenating the
    rows must exactly equal their common URI action. No visible character or
    actual whitespace glyph is removed, and the original actions stay intact.
    """
    by_uri = {}
    for uri, bounds in links:
        if re.fullmatch(r"https?://[^\s<>]+", uri):
            by_uri.setdefault(uri, []).append(bounds)
    for uri, rectangles in by_uri.items():
        if len(rectangles) < 2:
            continue
        for index, paragraph in enumerate(paragraphs):
            if (
                paragraph.layout_class <= 1
                or paragraph.region_kind not in _INLINE_SCRIPT_REGION_KINDS
            ):
                continue
            em = float(paragraph.size)
            rows = []
            for x0, y0, x1, y1 in rectangles:
                chars = sorted(
                    (
                        char
                        for char in text_chars[index]
                        if (
                            x0 <= (char.x0 + char.x1) / 2 <= x1
                            and y0 <= (char.y0 + char.y1) / 2 <= y1
                        )
                    ),
                    key=lambda char: char.x0,
                )
                if not chars or any(
                    len(char.get_text()) != 1
                    or char.get_text().isspace()
                    or getattr(char, "_pdf2zh_layout_class", None) == 0
                    or _is_non_horizontal_text_matrix(char.matrix)
                    or not 0.9 * em <= char.size <= 1.1 * em
                    for char in chars
                ):
                    break
                baseline = _char_baseline(chars[0])
                if (
                    any(
                        abs(_char_baseline(char) - baseline) > 0.15 * em
                        for char in chars
                    )
                    or any(
                        not -0.15 * em <= b.x0 - a.x1 <= 0.2 * em
                        for a, b in zip(chars, chars[1:])
                    )
                    or abs(chars[0].x0 - x0) > 0.3 * em
                    or abs(chars[-1].x1 - x1) > 0.3 * em
                ):
                    break
                rows.append((baseline, "".join(char.get_text() for char in chars)))
            if len(rows) != len(rectangles):
                continue
            rows.sort(reverse=True)
            if (
                any(
                    not 0.7 * em <= a[0] - b[0] <= 2 * em
                    for a, b in zip(rows, rows[1:])
                )
                or "".join(row[1] for row in rows) != uri
            ):
                continue
            pattern = re.compile(
                r"(?<![A-Za-z0-9_/])"
                + r"\s+".join(re.escape(row[1]) for row in rows)
                + r"(?![A-Za-z0-9_/%?#=&+\-]|\.[A-Za-z0-9])"
            )
            matches = list(pattern.finditer(segments[index]))
            if len(matches) != 1:
                continue
            match = matches[0]
            removed = [
                match.start() + offset
                for offset, value in enumerate(match[0])
                if value.isspace()
            ]
            if hasattr(paragraph, "break_offsets"):
                paragraph.break_offsets = [
                    offset - sum(position < offset for position in removed)
                    for offset in paragraph.break_offsets
                ]
            segments[index] = (
                segments[index][: match.start()] + uri + segments[index][match.end() :]
            )


def _release_standalone_prose_periods(
    segments, formulas, formula_paragraphs, paragraphs, paragraph_text_chars
):
    """Expose a text-font sentence period that was classified as mathematics.

    Text-italic captions sometimes switch to roman digits for a citation and
    back to italic for the period. Require established prose, a touching text
    glyph on the left, and a sentence end or a new capitalized word on the right.
    Decimal points, abbreviations, code and mathematical punctuation stay opaque.
    """
    for index, chars in enumerate(formulas):
        if len(chars) != 1 or chars[0].get_text() != ".":
            continue
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        if (
            paragraph.layout_class <= 1
            or paragraph.region_kind not in _INLINE_SCRIPT_REGION_KINDS
        ):
            continue
        char = chars[0]
        em = float(paragraph.size)
        if (
            em <= 0
            or getattr(char, "_pdf2zh_layout_class", None) == 0
            or not _is_non_math_text_font(char.fontname)
            or re.search(r"(?:Mono|Code|^CMTT|^CMITT)", _font_name(char.fontname), re.I)
            or not 0.9 * em <= char.size <= 1.1 * em
            or _is_non_horizontal_text_matrix(char.matrix)
        ):
            continue
        marker = f"{{v{index}}}"
        source = segments[paragraph_id]
        if source.count(marker) != 1:
            continue
        left, right = source.split(marker)
        if right.strip() and re.match(r"\s+[A-Z][a-z]{1,}\b", right) is None:
            continue
        if re.search(
            r"\b(?:e\.g|i\.e|w\.r\.t|w\.h\.p|i\.i\.d|cf|viz|vs|Dr|Prof|Mr|Mrs)$",
            left,
            re.I,
        ):
            continue
        prose = re.sub(r"\{v\d+\}", " ", source)
        if len(re.findall(r"\b[A-Za-z]{3,}\b", prose)) < 3:
            continue
        neighbors = [
            previous
            for previous in paragraph_text_chars[paragraph_id]
            if (
                previous.get_text()
                and previous.get_text()[-1]
                in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789)]"
                and getattr(previous, "_pdf2zh_layout_class", None) != 0
                and _is_non_math_text_font(previous.fontname)
                and not re.search(
                    r"(?:Mono|Code|^CMTT|^CMITT)", _font_name(previous.fontname), re.I
                )
                and 0.9 * em <= previous.size <= 1.1 * em
                and not _is_non_horizontal_text_matrix(previous.matrix)
                and abs(_char_baseline(previous) - _char_baseline(char)) <= 0.15 * em
                and -0.20 * em <= char.x0 - previous.x1 <= 0.35 * em
            )
        ]
        neighbors.sort(key=lambda previous: abs(char.x0 - previous.x1))
        if not neighbors or (
            len(neighbors) > 1
            and abs(abs(char.x0 - neighbors[0].x1) - abs(char.x0 - neighbors[1].x1))
            < 0.05 * em
        ):
            continue
        segments[paragraph_id] = left + "." + right
        paragraph_text_chars[paragraph_id].append(char)


def _release_caption_prose_delimiters(
    segments, formulas, formula_paragraphs, paragraphs
):
    """Expose caption punctuation hidden after a subscripted variable.

    Only full-size, horizontal text-font delimiters that close an opening mark
    already visible in caption prose are eligible. A panel transition additionally
    needs consecutive panel labels and an ordinary sentence on its right.
    Mathematical delimiters and glyph coordinates remain untouched.
    """
    for formula_id, chars in enumerate(formulas):
        if not chars:
            continue
        paragraph_id = formula_paragraphs[formula_id]
        paragraph = paragraphs[paragraph_id]
        if paragraph.region_kind != "figure_caption" or paragraph.layout_class <= 1:
            continue
        marker = f"{{v{formula_id}}}"
        source = segments[paragraph_id]
        if source.count(marker) != 1:
            continue
        left, right = source.split(marker)
        if left.count("(") <= left.count(")"):
            continue

        def prose_glyph(char):
            return (
                getattr(char, "_pdf2zh_layout_class", None) != 0
                and _MATH_FONT_RE.search(_formula_font_name(char)) is None
                and _is_non_math_text_font(char.fontname)
                and 0.9 * paragraph.size <= char.size <= 1.1 * paragraph.size
                and not _is_non_horizontal_text_matrix(char.matrix)
            )

        joined = "".join(char.get_text() for char in chars)
        panel = re.fullmatch(r"\)\.\(([b-z])\)", joined)
        previous_panels = re.findall(r"\(([a-z])\)", left)
        if (
            panel is not None
            and previous_panels
            and ord(previous_panels[-1]) + 1 == ord(panel.group(1))
            and re.match(r"\s*[A-Z][a-z]{2,}\b", right)
            and all(prose_glyph(char) for char in chars)
            and max(_char_baseline(char) for char in chars)
            - min(_char_baseline(char) for char in chars)
            < 0.2 * paragraph.size
        ):
            segments[paragraph_id] = left + f"). ({panel.group(1)})" + right
        elif (
            len(chars) > 1
            and chars[-1].get_text() == ")"
            and "(" not in joined
            and joined.count(")") > joined.count("(")
            and prose_glyph(chars[-1])
            and any(char.get_text().isalnum() for char in chars[:-1])
        ):
            formulas[formula_id] = chars[:-1]
            segments[paragraph_id] = left + marker + ")" + right


def _release_caption_title_words(
    segments, formulas, formula_paragraphs, paragraphs, text_chars
):
    """Expose a publisher's ``Figure 1 | Caption`` separator and first word.

    A vertical bar is a math symbol, so a following bold caption word can be
    absorbed into its formula run. Only the introductory caption boundary is
    eligible; absolute values and inline formula bars remain protected.
    """
    for index, chars in enumerate(formulas):
        if not chars:
            continue
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        if paragraph.region_kind not in {"figure_caption", "table_caption"}:
            continue
        marker = f"{{v{index}}}"
        source = segments[paragraph_id]
        if source.count(marker) != 1:
            continue
        left, right = source.split(marker)
        if not re.fullmatch(
            r"\s*(?:Figure|Fig\.?|Table)\s+\d+[A-Za-z]?\s*", left, re.I
        ):
            continue
        value = "".join(char.get_text() for char in chars)
        if not re.fullmatch(r"\|(?:[A-Za-z]+(?:-[A-Za-z]+)*)?", value):
            continue
        if not all(
            getattr(char, "_pdf2zh_layout_class", 0) > 0
            and _is_non_math_text_font(char.fontname)
            and not _is_non_horizontal_text_matrix(char.matrix)
            and 0.88 * paragraph.size <= char.size <= 1.12 * paragraph.size
            for char in chars
        ):
            continue
        segments[paragraph_id] = left + "| " + value[1:] + right
        text_chars[paragraph_id].extend(chars)


def _restore_caption_footnote_anchors(
    sources, targets, paragraphs, formulas, formula_paragraphs, text_chars
):
    """Keep a source-confirmed title footnote after the complete translation.

    Require a raised, smaller source glyph and a matching footnote below the
    caption at its left margin. This avoids treating a variable exponent or a
    compound suffix as a caption note. Only move the opaque token, never its
    original PDF glyph or other formula tokens.
    """
    for paragraph_id, (source, target, paragraph) in enumerate(
        zip(sources, targets, paragraphs)
    ):
        if paragraph.region_kind not in {"table_caption", "figure_caption"}:
            continue
        if not re.match(r"^(?:Table|Figure|Fig\.|Scheme)\s+\d+", source.strip()):
            continue
        match = re.search(r"(\{v(\d+)\})\s*$", source)
        if match is None or target.count(match[1]) != 1:
            continue
        token, formula_id = match[1], int(match[2])
        if (
            formula_id >= len(formulas)
            or formula_paragraphs[formula_id] != paragraph_id
        ):
            continue
        chars = formulas[formula_id]
        if len(chars) != 1 or re.fullmatch(r"[a-z*†‡]", chars[0].get_text()) is None:
            continue
        mark = chars[0]
        if not (
            _is_non_math_text_font(mark.fontname)
            and not _is_non_horizontal_text_matrix(mark.matrix)
            and mark.size <= paragraph.size * 0.8
        ):
            continue
        preceding = [
            char
            for char in text_chars[paragraph_id]
            if -0.08 * paragraph.size <= mark.x0 - char.x1 <= paragraph.size * 0.5
            and 0.15 * paragraph.size
            <= _char_baseline(mark) - _char_baseline(char)
            <= paragraph.size
            and char.size >= paragraph.size * 0.85
        ]
        if not preceding:
            continue
        # A one-letter mathematical base must remain attached to its exponent.
        prefix = source[: match.start()].rstrip()
        if re.search(r"(?:^|\s)[A-Za-z]$", prefix):
            continue
        has_note = False
        for note_id, note_chars in enumerate(formulas):
            if note_id == formula_id or len(note_chars) != 1:
                continue
            note = note_chars[0]
            note_paragraph_id = formula_paragraphs[note_id]
            if note_paragraph_id == paragraph_id or note.get_text() != mark.get_text():
                continue
            note_paragraph = paragraphs[note_paragraph_id]
            if (
                note_paragraph.page_id == paragraph.page_id
                and note_paragraph.region_kind
                in {"table_footnote", "table_cell", "figure_caption", "table_caption"}
                and sources[note_paragraph_id].lstrip().startswith(f"{{v{note_id}}}")
                and abs(note.x0 - paragraph.x0) <= paragraph.size
                and note.y0 < paragraph.y0
                and note.size <= paragraph.size * 0.8
            ):
                has_note = True
                break
        if has_note and not target.rstrip().endswith(token):
            targets[paragraph_id] = target.replace(token, "", 1).rstrip() + token


def _paragraph_first_line_origin(index, sources, paragraphs):
    """Do not shrink a source indent occupied by a separately preserved formula."""
    paragraph = paragraphs[index]
    em = paragraph.size
    if paragraph.layout_class <= 0 or paragraph.x <= paragraph.x0 or em <= 0:
        return paragraph.x0
    for prefix_index, prefix in enumerate(paragraphs):
        if prefix_index == index or not re.fullmatch(
            r"(?:\s*\{v\d+\})+\s*", sources[prefix_index]
        ):
            continue
        if (
            prefix.page_id == paragraph.page_id
            and prefix.layout_class == paragraph.layout_class
            and abs(prefix.y - paragraph.y) <= 0.15 * em
            and prefix.y1 - prefix.y0 <= 1.5 * em
            and paragraph.x0 - 0.1 * em <= prefix.x0 < prefix.x1
            and -0.1 * em <= paragraph.x - prefix.x1 <= em
        ):
            return paragraph.x
    return paragraph.x0


def _split_parenthetical_variable_definitions(
    segments, formulas, lines, offsets, formula_paragraphs, paragraphs, text_chars
):
    """Keep the opening parenthesis of ``(a is the lattice constant)`` in prose.

    The variable itself stays an immutable source-glyph token. When an inline
    expression and the definition share a run, split their tokens so the model
    can see which clause the parenthesis encloses.
    """
    for index in range(len(formulas)):
        chars = formulas[index]
        paragraph_id = formula_paragraphs[index]
        if len(chars) < 2 or lines[index] or paragraphs[paragraph_id].layout_class <= 1:
            continue
        opener, variable = chars[-2:]
        if (
            opener.get_text() != "("
            or re.fullmatch(r"[A-Za-z]", variable.get_text()) is None
            or not _is_non_math_text_font(opener.fontname)
            or not _is_non_math_text_font(variable.fontname)
        ):
            continue
        marker = f"{{v{index}}}"
        source = segments[paragraph_id]
        if source.count(marker) != 1:
            continue
        left, right = source.split(marker)
        if not re.match(r"\s+(?:is|are|denotes?|represents?)\b", right):
            continue
        if ")" not in right or any(
            getattr(char, "_pdf2zh_layout_class", 0) == 0
            or _is_non_horizontal_text_matrix(char.matrix)
            for char in (opener, variable)
        ):
            continue
        if len(chars) == 2:
            formulas[index] = [variable]
            offsets[index] += variable.y0 - chars[0].y0
            replacement = "(" + marker
        else:
            if chars[-3].get_text() not in ")]}":
                continue
            formulas[index] = chars[:-2]
            new_index = len(formulas)
            formulas.append([variable])
            lines.append([])
            offsets.append(offsets[index] + variable.y0 - chars[0].y0)
            formula_paragraphs.append(paragraph_id)
            replacement = marker + f" ({{v{new_index}}}"
        segments[paragraph_id] = left + replacement + right
        text_chars[paragraph_id].append(opener)


def _split_inline_formula_items(
    segments, formulas, lines, offsets, formula_paragraphs, paragraphs, text_chars
):
    """Expose a top-level prose comma between two independent inline items.

    An ordinary-font comma followed by a visible word-sized gap separates
    ``Q_j, n=a†a``. Commas in a bracket, vector, or commutator and formula-font
    punctuation stay inside the original mathematical atom.
    """
    for index in range(len(formulas)):
        chars = formulas[index]
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        marker = f"{{v{index}}}"
        if (
            lines[index]
            or paragraph.layout_class <= 1
            or segments[paragraph_id].count(marker) != 1
        ):
            continue
        split = None
        depth = 0
        for position, char in enumerate(chars):
            value = char.get_text()
            if value in "([{":
                depth += 1
            elif value in ")]}":
                depth = max(0, depth - 1)
            elif (
                value == ","
                and depth == 0
                and 0 < position < len(chars) - 1
                and _is_non_math_text_font(char.fontname)
                and not _MATH_FONT_RE.search(_formula_font_name(char))
                and chars[position + 1].x0 - char.x1 >= 0.25 * paragraph.size
                and abs(_char_baseline(chars[position + 1]) - _char_baseline(char))
                <= 0.25 * paragraph.size
            ):
                split = position
                break
        if split is None:
            continue
        prefix, suffix = chars[:split], chars[split + 1 :]
        if not any(ch.get_text().isalpha() for ch in prefix) or not any(
            ch.get_text().isalpha() for ch in suffix
        ):
            continue
        new_index = len(formulas)
        formulas[index] = prefix
        formulas.append(suffix)
        lines.append([])
        offsets.append(offsets[index] + suffix[0].y0 - chars[0].y0)
        formula_paragraphs.append(paragraph_id)
        text_chars[paragraph_id].append(chars[split])
        segments[paragraph_id] = segments[paragraph_id].replace(
            marker, marker + f", {{v{new_index}}}", 1
        )


def _release_prose_slash_connectives(
    segments, formulas, formula_paragraphs, paragraphs
):
    """Release the ordinary /or suffix of and/or from a spurious formula run."""
    for index, chars in enumerate(formulas):
        paragraph_id = formula_paragraphs[index]
        paragraph = paragraphs[paragraph_id]
        if (
            paragraph.layout_class <= 1
            or paragraph.region_kind not in _INLINE_SCRIPT_REGION_KINDS
        ):
            continue
        if len(chars) != 3 or "".join(char.get_text() for char in chars) != "/or":
            continue
        if any(
            getattr(char, "_pdf2zh_layout_class", None) == 0
            or not 0.9 * paragraph.size <= char.size <= 1.1 * paragraph.size
            or _is_non_horizontal_text_matrix(char.matrix)
            for char in chars
        ):
            continue
        # Some publishers use a math-font slash even in ordinary and/or prose.
        # The two letters must still be text glyphs on the same baseline and
        # touching the slash; do not release mathematical o/r expressions.
        if any(
            not _is_non_math_text_font(char.fontname)
            or re.search(r"(?:Mono|Code|^CMTT|^CMITT)", _font_name(char.fontname), re.I)
            for char in chars[1:]
        ):
            continue
        if (
            max(_char_baseline(char) for char in chars)
            - min(_char_baseline(char) for char in chars)
            > 0.15 * paragraph.size
        ):
            continue
        if any(
            not -0.12 * paragraph.size <= b.x0 - a.x1 <= 0.20 * paragraph.size
            for a, b in zip(chars, chars[1:])
        ):
            continue
        marker = f"{{v{index}}}"
        source = segments[paragraph_id]
        if source.count(marker) != 1:
            continue
        left, right = source.split(marker)
        if re.search(r"\band\s*$", left, re.I) and re.match(
            r"\s+[A-Za-z]{2,}\b", right
        ):
            segments[paragraph_id] = left.rstrip() + "/or" + right


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
    prefix = "".join(char.get_text() for char in chars[:split_at])
    if any(
        prefix.count(left) > prefix.count(right)
        for left, right in (("[", "]"), ("(", ")"), ("{", "}"))
    ):
        return chars, ""
    return (
        chars[:split_at],
        "".join(char.get_text() for char in chars[split_at:]),
    )


def _split_punctuation_only_formula_run(
    chars: list[LTChar],
    *,
    has_prose_context: bool,
    paragraph_layout_class: int | None,
) -> tuple[list[LTChar], str, list[LTChar]]:
    """Release a closing delimiter that was isolated as a formula run.

    A PDF can emit the closing parenthesis of ``Eq. (2.1)`` in a separate font
    run.  Treating that one glyph as ``{vN}`` hides the balanced scientific
    reference from the translation validator.  Only punctuation-only closing
    runs beside established prose are released; display formulas, mathematical
    glyphs, class-0 regions, and opening delimiters remain protected.
    """

    if (
        not chars
        or not has_prose_context
        or paragraph_layout_class == 0
        or any(
            getattr(char, "_pdf2zh_layout_class", None) == 0
            or _MATH_FONT_RE.search(_formula_font_name(char)) is not None
            for char in chars
        )
    ):
        return chars, "", []
    text = "".join(char.get_text() for char in chars)
    if re.fullmatch(r"[\)\]\}）］】]+", text) is None:
        return chars, "", []
    if any(
        _is_non_horizontal_text_matrix(
            getattr(char, "_pdf2zh_source_text_state", None).matrix
            if getattr(char, "_pdf2zh_source_text_state", None) is not None
            else char.matrix
        )
        for char in chars
    ):
        return chars, "", []
    return [], text, chars


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
        "footnote",
        "table_cell",
        "algorithm",
    }
)


def _should_attach_unlabelled_closing_punctuation(
    previous, current, previous_class, current_class, previous_region
) -> bool:
    """Keep a touching line-final mark with text just outside a detected box."""
    if (
        previous is None
        or current_class != 1
        or previous_class <= 1
        or previous_region
        not in {
            "plain text",
            "title",
            "figure_caption",
            "table_caption",
            "table_footnote",
        }
        or current.get_text() not in {".", ",", ";", ":", "!", "?", ")", "]", "}"}
        or previous.fontname != current.fontname
    ):
        return False
    size = float(previous.size)
    if size <= 0 or abs(float(current.size) - size) > size * 0.05:
        return False
    if any(
        abs(float(char.matrix[i])) > 1e-5
        for char in (previous, current)
        for i in (1, 2)
    ):
        return False
    return (
        abs(float(current.y0) - float(previous.y0)) <= size * 0.1
        and -size * 0.05 <= float(current.x0) - float(previous.x1) <= size * 0.15
    )


def _touching_text_glyphs(
    left: LTChar, right: LTChar, *, max_gap: float = 0.12
) -> bool:
    """Recognize horizontal glyphs from one continuous, same-font text run."""

    size = float(left.size)
    return (
        size > 0
        and left.fontname == right.fontname
        and abs(float(right.size) - size) <= 0.05 * size
        and abs(float(right.y0) - float(left.y0)) <= 0.10 * size
        and all(
            not _is_non_horizontal_text_matrix(char.matrix) for char in (left, right)
        )
        and -0.05 * size <= float(right.x0) - float(left.x1) <= max_gap * size
    )


def _attach_overlapping_region_punctuation(
    previous, current, previous_class, current_class, previous_kind, current_kind
):
    if (
        previous is None
        or previous_class <= 1
        or current_class <= 1
        or previous_class == current_class
        or previous_kind != "plain text"
        or current_kind != "plain text"
        or current.get_text().strip()
        not in {"", ",", ".", ";", ":", '"', "'", "”", "’"}
    ):
        return False
    em = max(float(previous.size), float(current.size), 1.0)
    return bool(
        previous.fontname == current.fontname
        and abs(previous.size - current.size) <= 0.05 * em
        and abs(previous.y0 - current.y0) <= 0.10 * em
        and -0.20 * em <= current.x0 - previous.x1 <= 0.25 * em
        and not _is_non_horizontal_text_matrix(current.matrix)
    )


def _recover_detector_boundary_text(
    chars: list[LTChar],
    layout: np.ndarray,
    region_types: dict[int, str],
    protected_ids: set[int],
    is_formula: Callable[[str, str], bool],
) -> dict[int, int]:
    """Recover complete words and reference labels clipped by text-box edges.

    Detector edges are not linguistic boundaries. Only bridge touching source
    glyphs between plain-text/background regions; never move protected glyphs,
    formulas, other region types, or text across a real source space.
    """

    height, width = layout.shape
    classes = [
        int(
            layout[
                min(max(int(c.y0), 0), height - 1), min(max(int(c.x0), 0), width - 1)
            ]
        )
        for c in chars
    ]

    def eligible(index: int) -> bool:
        char = chars[index]
        cls = classes[index]
        return (
            object_id(char) not in protected_ids
            and (cls == 1 or (cls > 1 and region_types.get(cls) == "plain text"))
            and not is_formula(char.fontname, char.get_text())
            and not getattr(char, "_pdf2zh_scan_fragment", False)
        )

    recovered: dict[int, int] = {}
    for index, char in enumerate(chars):
        if char.get_text() != "[" or classes[index] != 1 or not eligible(index):
            continue
        # A full numbered marker followed by an author-like capital is required;
        # arbitrary parentheses and mathematical bracket expressions stay put.
        cursor = index + 1
        digits = 0
        target_class = classes[cursor] if cursor < len(chars) else 1
        while cursor < len(chars) and digits < 4:
            candidate = chars[cursor]
            if (
                re.fullmatch(r"[0-9]", candidate.get_text()) is None
                or classes[cursor] != target_class
                or not eligible(cursor)
                or not _touching_text_glyphs(chars[cursor - 1], candidate)
            ):
                break
            digits += 1
            cursor += 1
        if (
            digits == 0
            or target_class <= 1
            or cursor + 1 >= len(chars)
            or chars[cursor].get_text() != "]"
            or classes[cursor] != target_class
            or not eligible(cursor)
            or not _touching_text_glyphs(chars[cursor - 1], chars[cursor])
        ):
            continue
        following = cursor + 1
        while following < len(chars) and chars[following].get_text() == " ":
            following += 1
        if (
            following < len(chars)
            and re.fullmatch(r"[A-Z]", chars[following].get_text())
            and classes[following] == target_class
            and eligible(following)
            and _touching_text_glyphs(chars[cursor], chars[following], max_gap=0.8)
            and float(chars[following].x0) - float(chars[cursor].x1)
            >= 0.15 * chars[cursor].size
        ):
            recovered[object_id(char)] = target_class

    clipped_words: list[tuple[int, int, int]] = []
    index = 0
    while index < len(chars):
        start = index
        if (
            not eligible(index)
            or re.fullmatch(r"[A-Za-z]", chars[index].get_text()) is None
        ):
            index += 1
            continue
        index += 1
        while (
            index < len(chars)
            and eligible(index)
            and re.fullmatch(r"[A-Za-z]", chars[index].get_text()) is not None
            and _touching_text_glyphs(chars[index - 1], chars[index])
        ):
            index += 1
        run_classes = classes[start:index]
        word = "".join(char.get_text() for char in chars[start:index])
        if (
            len(word) < 3
            or not any(c.islower() for c in word)
            or len(set(run_classes)) < 2
        ):
            continue
        target_class = next((cls for cls in run_classes if cls > 1), None)
        if target_class is None:
            continue
        if set(run_classes) == {1, target_class}:
            clipped_words.append((start, index, target_class))
        # Require preceding prose on the same source line. An isolated Roman
        # mathematical label must not become prose merely by touching a box.
        previous = start - 1
        while previous >= 0 and chars[previous].get_text() == " ":
            previous -= 1
        if (
            previous < 1
            or not eligible(previous)
            or not eligible(previous - 1)
            or classes[previous] != target_class
            or classes[previous - 1] != target_class
            or any(
                re.fullmatch(r"[A-Za-z]", chars[pos].get_text()) is None
                for pos in (previous - 1, previous)
            )
            or not _touching_text_glyphs(chars[previous - 1], chars[previous])
            or not _touching_text_glyphs(chars[previous], chars[start], max_gap=0.8)
            or float(chars[start].x0) - float(chars[previous].x1)
            < 0.15 * chars[start].size
        ):
            continue
        recovered.update((object_id(char), target_class) for char in chars[start:index])
    # A narrow detector box can clip *both* ends of one ordinary source line.
    # Two bisected words prove that neither box edge is a linguistic boundary,
    # even when a formula or sentence punctuation precedes those words. Restore
    # that continuous line as one paragraph; protected layout classes are never
    # released, and mathematical fonts still use the original formula path.
    for left, right in zip(clipped_words, clipped_words[1:]):
        start, left_end, target_class = left
        right_start, end, right_class = right
        if (
            target_class != right_class
            or classes[start] != 1
            or classes[left_end - 1] != target_class
            or classes[right_start] != target_class
            or classes[end - 1] != 1
        ):
            continue
        anchor = chars[start]
        em = float(anchor.size)
        baseline = _char_baseline(anchor)

        def on_line(pos: int) -> bool:
            candidate = chars[pos]
            return (
                object_id(candidate) not in protected_ids
                and classes[pos] in {1, target_class}
                and not getattr(candidate, "_pdf2zh_scan_fragment", False)
                and not _is_non_horizontal_text_matrix(candidate.matrix)
                and 0.65 * em <= float(candidate.size) <= 1.05 * em
                and abs(_char_baseline(candidate) - baseline) <= 0.25 * em
            )

        def connected(a: int, b: int) -> bool:
            return (
                on_line(a)
                and on_line(b)
                and -0.25 * em <= float(chars[b].x0) - float(chars[a].x1) <= 0.8 * em
            )

        if not all(connected(pos, pos + 1) for pos in range(start, end - 1)):
            continue
        # Several full prose words between the two cut words distinguish a
        # sentence from an isolated Roman label or names beside an equation.
        interior = [
            ch
            for ch in chars[left_end:right_start]
            if not is_formula(ch.fontname, ch.get_text())
        ]
        if len(re.findall(r"[A-Za-z]{3,}", _reconstruct_italic_run(interior))) < 3:
            continue
        while start > 0 and connected(start - 1, start):
            start -= 1
        while end < len(chars) and connected(end - 1, end):
            end += 1
        recovered.update(
            (object_id(chars[pos]), target_class)
            for pos in range(start, end)
            if classes[pos] == 1
        )
    # A box can begin after a short grammatical prefix on each source line,
    # e.g. "The | wafer ..." and "is e|xposed ...". Recover that prefix only
    # with several same-line body words to its right. Source gaps, other boxes,
    # mathematical labels, and protected/rotated glyphs remain boundaries.
    for boundary in range(1, len(chars)):
        target_class = classes[boundary]
        if target_class <= 1 or classes[boundary - 1] != 1 or not eligible(boundary):
            continue

        def adjacent(a, b):
            return (
                eligible(a)
                and eligible(b)
                and classes[a] in {1, target_class}
                and classes[b] in {1, target_class}
                and _touching_text_glyphs(chars[a], chars[b], max_gap=0.8)
            )

        start, end = boundary - 1, boundary + 1
        while start > 0 and classes[start - 1] == 1 and adjacent(start - 1, start):
            start -= 1
        while (
            end < len(chars) and classes[end] == target_class and adjacent(end - 1, end)
        ):
            end += 1
        prefix = _reconstruct_italic_run(chars[start:boundary]).strip()
        body = _reconstruct_italic_run(chars[boundary:end])
        if (
            adjacent(boundary - 1, boundary)
            and re.fullmatch(
                r"(?:The|the|An|an|A|a|It|it|is|are|was|were)(?:\s+[a-z]{1,6})?", prefix
            )
            and len(re.findall(r"[A-Za-z]{3,}", body)) >= 3
        ):
            recovered.update(
                (object_id(char), target_class) for char in chars[start:boundary]
            )
    return recovered


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
        or re.search(r"(?:^|[^A-Za-z])([A-Za-z])$", paragraph_text.rstrip()) is None
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
        or re.search(r"(?:^|[^A-Za-z])([A-Za-z])$", paragraph_text.rstrip()) is None
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
    if (
        candidate.get_text() in {"=", "≡", "≈", "≤", "≥", "<", ">", "∝"}
        and _is_prose_emphasis_font(previous.fontname)
        and -0.1 * paragraph_size <= gap <= 0.85 * paragraph_size
        and abs(_char_baseline(previous) - _char_baseline(candidate))
        < 0.2 * paragraph_size
    ):
        return True
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
        and _is_non_math_text_font(previous.fontname)
        and -0.20 * paragraph_size <= gap <= 0.25 * paragraph_size
        and 0.10 * paragraph_size
        <= previous_center - candidate_center
        <= 0.70 * paragraph_size
    ):
        return True
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
    candidate_is_math = _MATH_FONT_RE.search(font_name) is not None or (
        bool(candidate_text)
        and (
            unicodedata.category(candidate_text[0]) in {"Lm", "Mn", "Sk", "Sm"}
            or ord(candidate_text[0]) in range(0x370, 0x400)
        )
    )
    return (
        -0.10 * paragraph_size
        <= float(previous.x0) - previous_formula_right
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
                and -0.10 * size <= float(char.x0) - enclosure_left <= 0.80 * size
            ]
            right_candidates = [
                char
                for char in text_chars
                if char.get_text() == match.group("right")
                and abs(float(char.y0) - baseline) <= 0.30 * size
                and operator_right - 0.20 * size <= float(char.x0)
                and float(char.x1) <= enclosure_right + 0.10 * size
                and -0.10 * size <= enclosure_right - float(char.x1) <= 2.00 * size
            ]
            if not left_candidates or not right_candidates:
                continue
            left_char = max(left_candidates, key=lambda char: float(char.x1))
            right_char = min(right_candidates, key=lambda char: float(char.x0))
            if left_char is right_char:
                continue
            middle_markers = [
                value for value in match.group("middle") if value in ",;:"
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
                        key=lambda char: abs(float(char.x0) - operator_right),
                    )
                )
            if len(middle_chars) != len(middle_markers):
                continue
            if not (
                enclosure_left
                <= float(left_char.x0)
                < float(left_char.x1)
                <= operator_left + 0.20 * size
                and operator_right - 0.20 * size
                <= float(right_char.x0)
                < float(right_char.x1)
                <= enclosure_right + 0.10 * size
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


def _split_formula_prose_boundaries(
    segments: list[str],
    formulas: list[list[LTChar]],
    formula_lines: list[list[LTLine]],
    formula_offsets: list[float],
    formula_paragraphs: list[int],
    paragraphs: list["Paragraph"],
    *,
    theorem_only: bool = False,
) -> None:
    """Split only closed-form prose/math mixtures into independent atoms.

    Some IEEE inline emphasis runs swallow a terminal mathematical variable,
    while others start with one variable and continue with a quoted prose label.
    Translating the complete run would allow the model to rewrite the variable;
    preserving it as one formula leaves the prose in English.  This pass keeps
    the one-glyph math atom in its original formula and creates a separate styled
    run for the natural-language side.

    The accepted shapes are deliberately narrow:

    * three-or-more-word italic prose, comma, one upper-case math variable;
    * one upper-case math variable followed by a short balanced-quote italic
      phrase.
    * one italic prose word followed by a reduced-size numeric footnote marker;
    * one math variable followed by the prose suffix ``-dimensional`` or an
      English ordinal suffix such as ``-th`` in an explicit ordinal context.

    Display formulas, vector-line formulas, class-0 content, non-horizontal or
    mixed-size prose, lower-case identifiers, and multi-glyph math tails fail
    closed through the checks below and ``_is_high_confidence_prose_italic``.
    """

    # Theorem prose is released as ordinary text and is backend independent.
    # Other branches prepare style runs for the Codex styled-text protocol.
    original_formula_count = len(formulas)

    def marker_for(formula_id: int) -> re.Pattern[str]:
        return re.compile(
            rf"\{{\s*v\s*{formula_id}\s*\}}",
            re.IGNORECASE,
        )

    def append_formula(
        chars: list[LTChar],
        paragraph_id: int,
        offset: float,
    ) -> int:
        formula_id = len(formulas)
        formulas.append(chars)
        formula_lines.append([])
        formula_offsets.append(offset)
        formula_paragraphs.append(paragraph_id)
        return formula_id

    def safe_boundary(
        left: LTChar,
        right: LTChar,
        paragraph_size: float,
    ) -> bool:
        """Accept only adjacent, same-line, full-size horizontal atoms."""

        if paragraph_size <= 0:
            return False
        for char in (left, right):
            state = getattr(char, "_pdf2zh_source_text_state", None)
            matrix = state.matrix if state is not None else char.matrix
            if _is_non_horizontal_text_matrix(matrix):
                return False
        left_size = float(left.size)
        right_size = float(right.size)
        if (
            left_size <= 0
            or right_size <= 0
            or not 0.90 <= left_size / paragraph_size <= 1.10
            or not 0.90 <= right_size / paragraph_size <= 1.10
            or max(left_size, right_size) / min(left_size, right_size) > 1.10
        ):
            return False
        em = max(left_size, right_size, 1.0)
        gap = float(right.x0) - float(left.x1)
        return (
            -0.10 * em <= gap <= 0.45 * em
            and abs(_char_baseline(left) - _char_baseline(right)) <= 0.15 * em
        )

    formula_text_by_paragraph: dict[int, list[str]] = {}
    for value, paragraph_id in zip(formulas, formula_paragraphs, strict=True):
        formula_text_by_paragraph.setdefault(paragraph_id, []).append(
            "".join(char.get_text() for char in value)
        )
    theorem_paragraph_ids: set[int] = set()
    in_theorem = False
    theorem_segments = 0
    theorem_cue = re.compile(
        r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Definition|Assumption)"
        r"(?:\s+(?:[A-Z]?\d+(?:\.\d+)*\b|"
        r"(?:[A-Z](?:\.\d+)*|[IVXLCDM]+)(?=\s*[.:(]))|\s*[.:(])",
        re.IGNORECASE,
    )
    for paragraph_id, segment in enumerate(segments):
        opaque_text = " ".join(formula_text_by_paragraph.get(paragraph_id, ()))
        if theorem_cue.search(segment):
            in_theorem = True
            theorem_segments = 0
        if not in_theorem:
            continue
        if theorem_segments and paragraphs[paragraph_id].region_kind == "title":
            in_theorem = False
            continue
        theorem_paragraph_ids.add(paragraph_id)
        theorem_segments += 1
        if re.search(r"\bProof\s*[.:]", segment + " " + opaque_text, re.IGNORECASE):
            in_theorem = False
        elif theorem_segments >= 16:
            in_theorem = False

    def split_theorem_run(
        chars: list[LTChar],
        paragraph_id: int,
    ) -> list[tuple[bool, list[LTChar], str]] | None:
        """Split theorem-statement italic prose from protected math atoms."""

        if paragraph_id not in theorem_paragraph_ids or not chars:
            return None
        if any(
            getattr(char, "_pdf2zh_layout_class", 0) == 0
            or _is_non_horizontal_text_matrix(
                getattr(char, "_pdf2zh_source_text_state", None).matrix
                if getattr(char, "_pdf2zh_source_text_state", None) is not None
                else char.matrix
            )
            for char in chars
        ):
            return None
        groups: list[tuple[bool, list[LTChar]]] = []
        for char in chars:
            is_prose = _is_prose_italic_font(char.fontname)
            if groups and groups[-1][0] == is_prose:
                groups[-1][1].append(char)
            else:
                groups.append((is_prose, [char]))
        if not any(is_prose for is_prose, _ in groups):
            return None
        if len(groups) > 1 and not any(
            _MATH_FONT_RE.search(_formula_font_name(char)) is not None
            for is_prose, group in groups
            if not is_prose
            for char in group
        ):
            return None
        prose_text = " ".join(
            _reconstruct_italic_run(group) for is_prose, group in groups if is_prose
        )
        words = re.findall(r"[^\W\d_]+(?:[-‐‑–—][^\W\d_]+)*", prose_text)
        if not words or not any(len(word) >= 3 for word in words):
            return None
        # An ordinary italic face can also encode mathematical identifiers.
        # Do not expose isolated variables merely because a theorem precedes them.
        if any(len(word) == 1 and word not in {"a", "A", "I"} for word in words):
            return None
        atoms: list[tuple[bool, list[LTChar], str]] = []
        for is_prose, group in groups:
            text = _reconstruct_italic_run(group) if is_prose else ""
            if is_prose and text and any(char.isalpha() for char in text):
                if any(
                    not (
                        char.isalpha() or char.isspace() or char in "-‐‑–—'’.,;:!?()[]"
                    )
                    for char in text
                ):
                    return None
                atoms.append((True, group, text))
            else:
                atoms.append((False, group, text))
        return atoms

    def boundary_separator(
        left: list[LTChar],
        right: list[LTChar],
        paragraph_size: float,
    ) -> str:
        if not left or not right:
            return ""
        right_text = right[0].get_text()
        if right_text and right_text[0] in ".,;:!?)]}）］】":
            return ""
        gap = float(right[0].x0) - float(left[-1].x1)
        em = max(float(left[-1].size), float(right[0].size), paragraph_size, 1.0)
        left_text = left[-1].get_text()
        if gap > 0.12 * em or (
            left_text
            and right_text
            and (left_text[-1].isalnum() or left_text[-1] in ")]）］")
            and right_text[0].isalnum()
        ):
            return " "
        return ""

    def has_natural_word_after_quote(text: str) -> bool:
        match = re.fullmatch(
            r"[\u201c\"](?:[A-Za-z][A-Za-z'-]*)[\u201d\"]\s+(.+)",
            text,
        )
        if match is None:
            return False
        return any(
            len(word) >= 3 and any(char.islower() for char in word)
            for word in re.findall(r"[A-Za-z][A-Za-z'-]*", match.group(1))
        )

    def split_superscript_footnote(
        chars: list[LTChar],
        paragraph_size: float,
    ) -> tuple[list[LTChar], list[LTChar]] | None:
        """Split ``samples²`` without treating mathematical powers as prose."""

        split_at = len(chars)
        while split_at and chars[split_at - 1].get_text().isdigit():
            split_at -= 1
        if split_at in {0, len(chars)}:
            return None
        prose = chars[:split_at]
        script = chars[split_at:]
        prose_text = _is_high_confidence_prose_italic(prose, paragraph_size)
        if (
            prose_text is None
            or re.fullmatch(r"[a-z][a-z'-]{3,}", prose_text) is None
            or not 1 <= len(script) <= 3
            or any(
                _MATH_FONT_RE.search(_formula_font_name(char)) is not None
                or not _is_non_math_text_font(char.fontname)
                for char in script
            )
        ):
            return None
        for char in (*prose[-1:], *script):
            state = getattr(char, "_pdf2zh_source_text_state", None)
            matrix = state.matrix if state is not None else char.matrix
            if _is_non_horizontal_text_matrix(matrix):
                return None
        if paragraph_size <= 0 or float(prose[-1].size) < 0.90 * paragraph_size:
            return None
        script_sizes = [float(char.size) for char in script]
        if (
            min(script_sizes) <= 0
            or max(script_sizes) >= 0.79 * paragraph_size
            or min(script_sizes) < 0.50 * paragraph_size
            or max(script_sizes) / min(script_sizes) > 1.08
        ):
            return None
        gap = float(script[0].x0) - float(prose[-1].x1)
        if not -0.25 * paragraph_size <= gap <= 0.30 * paragraph_size:
            return None
        prose_center = (float(prose[-1].y0) + float(prose[-1].y1)) / 2
        script_center = (float(script[0].y0) + float(script[0].y1)) / 2
        center_offset = abs(script_center - prose_center)
        vertical_overlap = min(float(prose[-1].y1), float(script[0].y1)) - max(
            float(prose[-1].y0), float(script[0].y0)
        )
        if (
            not 0.10 * paragraph_size <= center_offset <= 0.70 * paragraph_size
            or vertical_overlap <= 0
        ):
            return None
        for previous, current in zip(script, script[1:], strict=False):
            em = max(float(previous.size), float(current.size), 1.0)
            if (
                not -0.10 * em <= float(current.x0) - float(previous.x1) <= 0.45 * em
                or abs(_char_baseline(previous) - _char_baseline(current))
                > 0.12 * paragraph_size
            ):
                return None
        return prose, script

    def safe_plain_suffix(
        variable: LTChar,
        suffix: list[LTChar],
        paragraph_size: float,
    ) -> bool:
        if (
            not suffix
            or _MATH_FONT_RE.search(_formula_font_name(variable)) is None
            or any(
                _MATH_FONT_RE.search(_formula_font_name(char)) is not None
                or not _is_non_math_text_font(char.fontname)
                for char in suffix
            )
        ):
            return False
        return all(
            safe_boundary(left, right, paragraph_size)
            for left, right in zip(
                [variable, *suffix[:-1]],
                suffix,
                strict=True,
            )
        )

    for formula_id in range(original_formula_count):
        if formula_id >= len(formula_lines) or formula_lines[formula_id]:
            continue
        paragraph_id = formula_paragraphs[formula_id]
        if not 0 <= paragraph_id < len(paragraphs):
            continue
        paragraph = paragraphs[paragraph_id]
        if (
            paragraph.region_kind not in _INLINE_SCRIPT_REGION_KINDS
            or int(getattr(paragraph, "layout_class", 0)) == 0
        ):
            continue
        chars = formulas[formula_id]
        marker = marker_for(formula_id)
        if len(marker.findall(segments[paragraph_id])) != 1:
            continue
        _, closing_delimiters, _ = _split_punctuation_only_formula_run(
            chars,
            has_prose_context=_segment_contains_prose(segments[paragraph_id]),
            paragraph_layout_class=int(paragraph.layout_class),
        )
        if closing_delimiters and not theorem_only:
            segments[paragraph_id] = marker.sub(
                closing_delimiters,
                segments[paragraph_id],
                count=1,
            )
            continue
        if len(chars) < 4 or any(
            getattr(char, "_pdf2zh_layout_class", None) == 0 for char in chars
        ):
            continue
        offset = formula_offsets[formula_id]
        paragraph_size = float(paragraph.size)
        marker_match = marker.search(segments[paragraph_id])
        if marker_match is None:
            continue
        left_context = segments[paragraph_id][: marker_match.start()].rstrip()
        right_context = segments[paragraph_id][marker_match.end() :].lstrip()

        theorem_atoms = split_theorem_run(chars, paragraph_id)
        if theorem_atoms is not None:
            if len(theorem_atoms) == 1 and theorem_atoms[0][0]:
                segments[paragraph_id] = marker.sub(
                    theorem_atoms[0][2],
                    segments[paragraph_id],
                    count=1,
                )
                continue
            paragraph.brk = True
            replacement_parts: list[str] = []
            previous_group: list[LTChar] | None = None
            previous_is_prose = False
            used_original_formula = False
            for is_prose, group, prose_text in theorem_atoms:
                if previous_group is not None and (
                    previous_is_prose or is_prose or bool(prose_text)
                ):
                    replacement_parts.append(
                        boundary_separator(
                            previous_group,
                            group,
                            paragraph_size,
                        )
                    )
                if is_prose:
                    replacement_parts.append(prose_text)
                elif prose_text:
                    replacement_parts.append(prose_text)
                else:
                    if used_original_formula:
                        math_id = append_formula(group, paragraph_id, offset)
                    else:
                        formulas[formula_id] = group
                        math_id = formula_id
                        used_original_formula = True
                    replacement_parts.append(f"{{v{math_id}}}")
                previous_group = group
                previous_is_prose = is_prose or bool(prose_text)
            segments[paragraph_id] = marker.sub(
                "".join(replacement_parts),
                segments[paragraph_id],
                count=1,
            )
            continue

        if theorem_only:
            continue

        footnote_split = split_superscript_footnote(chars, paragraph_size)
        if footnote_split is not None and _segment_contains_prose(
            segments[paragraph_id]
        ):
            prose_chars, script_chars = footnote_split
            formulas[formula_id] = prose_chars
            setattr(prose_chars[0], _FORCED_STYLED_PROSE_ATTR, True)
            script_offset = (
                offset + float(script_chars[0].y0) - float(prose_chars[0].y0)
            )
            script_id = append_formula(
                script_chars,
                paragraph_id,
                script_offset,
            )
            replacement = f"{{v{formula_id}}}{{v{script_id}}}"
            segments[paragraph_id] = marker.sub(
                replacement,
                segments[paragraph_id],
                count=1,
            )
            continue

        joined = "".join(char.get_text() for char in chars)
        variable = chars[0]
        plain_suffix = chars[1:]
        if (
            re.fullmatch(r"[A-Z]-dimensional", joined) is not None
            and re.search(r"\b(?:a|an|the)\s*$", left_context, re.IGNORECASE)
            is not None
            and re.match(r"[A-Za-z]", right_context) is not None
            and safe_plain_suffix(variable, plain_suffix, paragraph_size)
        ):
            formulas[formula_id] = [variable]
            replacement = f"{{v{formula_id}}}-dimensional"
            segments[paragraph_id] = marker.sub(
                replacement,
                segments[paragraph_id],
                count=1,
            )
            continue

        ordinal_match = re.fullmatch(r"([A-Za-z])-(st|nd|rd|th)", joined)
        if (
            ordinal_match is not None
            and re.search(r"\bthe\s*$", left_context, re.IGNORECASE) is not None
            and re.match(r"[A-Za-z]", right_context) is not None
            and safe_plain_suffix(variable, plain_suffix, paragraph_size)
        ):
            ordinal_suffix = ordinal_match.group(2)
            formulas[formula_id] = [variable]
            replacement = f"{{v{formula_id}}}-{ordinal_suffix}"
            segments[paragraph_id] = marker.sub(
                replacement,
                segments[paragraph_id],
                count=1,
            )
            continue

        # ``of possible SE classes, N`` / ``sources in a mixture, M``.
        variable = chars[-1]
        comma = chars[-2]
        prose_prefix = chars[:-2]
        variable_text = variable.get_text()
        prefix_text = _is_high_confidence_prose_italic(
            prose_prefix,
            float(paragraph.size),
        )
        if (
            re.fullmatch(r"[A-Z]", variable_text) is not None
            and _MATH_FONT_RE.search(_formula_font_name(variable)) is not None
            and comma.get_text() == ","
            and prefix_text is not None
            and len(re.findall(r"[^\W\d_]+", prefix_text)) >= 3
            and safe_boundary(prose_prefix[-1], comma, float(paragraph.size))
            and safe_boundary(comma, variable, float(paragraph.size))
        ):
            gap = float(variable.x0) - float(comma.x1)
            em = max(float(variable.size), float(comma.size), 1.0)
            separator = ", " if gap > 0.12 * em else ","
            formulas[formula_id] = prose_prefix
            variable_id = append_formula([variable], paragraph_id, offset)
            replacement = f"{{v{formula_id}}}{separator}{{v{variable_id}}}"
            segments[paragraph_id] = marker.sub(
                replacement,
                segments[paragraph_id],
                count=1,
            )
            continue

        # ``N “seen” SE classes``: preserve N, translate only the phrase.
        variable = chars[0]
        prose_suffix = chars[1:]
        suffix_text = _is_high_confidence_prose_italic(
            prose_suffix,
            float(paragraph.size),
        )
        if (
            re.fullmatch(r"[A-Z]", variable.get_text()) is not None
            and _MATH_FONT_RE.search(_formula_font_name(variable)) is not None
            and suffix_text is not None
            and _QUOTED_ITALIC_PROSE_RE.fullmatch(suffix_text) is not None
            and has_natural_word_after_quote(suffix_text)
            and safe_boundary(variable, prose_suffix[0], float(paragraph.size))
        ):
            gap = float(prose_suffix[0].x0) - float(variable.x1)
            em = max(float(variable.size), float(prose_suffix[0].size), 1.0)
            separator = " " if gap > 0.12 * em else ""
            formulas[formula_id] = [variable]
            prose_id = append_formula(prose_suffix, paragraph_id, offset)
            replacement = f"{{v{formula_id}}}{separator}{{v{prose_id}}}"
            segments[paragraph_id] = marker.sub(
                replacement,
                segments[paragraph_id],
                count=1,
            )
    # A theorem sentence can be physically split immediately before its
    # quantified range: ``... such that for any`` + ``0 <= m <= k ...``.  Merge
    # the narrow numeric fragment back into the preceding prose paragraph.  The
    # original one-line geometry is too small for a translated CJK sentence and
    # otherwise forces unreadable uniform scaling.
    trailing_quantifier = re.compile(r"\b(such\s+that\s+for\s+any)\s*$", re.IGNORECASE)
    for paragraph_id in sorted(theorem_paragraph_ids):
        next_id = paragraph_id + 1
        if next_id not in theorem_paragraph_ids or next_id >= len(segments):
            continue
        match = trailing_quantifier.search(segments[paragraph_id])
        if (
            match is None
            or re.match(
                r"\s*(?:\d|\{\s*v\d+\s*\})",
                segments[next_id],
                re.IGNORECASE,
            )
            is None
        ):
            continue
        segments[paragraph_id] = (
            segments[paragraph_id].rstrip() + " " + segments[next_id].lstrip()
        )
        segments[next_id] = ""
        paragraphs[paragraph_id].brk = True


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
    if not value or value.isspace() or getattr(candidate, "_pdf2zh_prose_quote", False):
        return False

    previous = chars[-1]
    previous_value = previous.get_text()
    em = max(float(getattr(candidate, "size", 0.0)), float(previous.size), 1.0)
    right_edge = max(float(char.x1) for char in chars)
    gap = float(candidate.x0) - right_edge
    # A reduced roman word below a mathematical base remains its subscript,
    # even when the word is marked as ordinary prose by the lexical detector.
    anchors = [
        ch
        for ch in chars
        if _MATH_FONT_RE.search(_formula_font_name(ch))
        or _is_prose_emphasis_font(ch.fontname)
        or re.search(r"-It$", _formula_font_name(ch))
    ]
    if anchors and len(value) == 1 and value.isalpha():
        anchor = max(anchors, key=lambda ch: float(ch.size))
        anchor_em = float(anchor.size)
        if (
            0.45 * anchor_em <= float(candidate.size) <= 1.02 * anchor_em
            and 0.10 * anchor_em
            <= _char_baseline(anchor) - _char_baseline(candidate)
            <= 0.80 * anchor_em
            and -0.20 * anchor_em <= gap <= 0.30 * anchor_em
            and not _is_non_horizontal_text_matrix(candidate.matrix)
            and (
                float(candidate.size) < 0.90 * anchor_em
                or (
                    _char_baseline(anchor) - _char_baseline(candidate)
                    >= 0.24 * anchor_em
                    and gap <= 0.12 * anchor_em
                )
            )
        ):
            return True
    if (
        previous_value in {"ˆ", "^", "¯", "~"}
        and _MATH_FONT_RE.search(_formula_font_name(previous))
        and len(value) == 1
        and value.isalpha()
        and abs(float(candidate.x0) - float(previous.x0)) <= 0.35 * em
        and abs(_char_baseline(candidate) - _char_baseline(previous)) <= 0.25 * em
    ):
        return True
    # TeX text accents are overprinted glyphs: their following base moves back
    # underneath the accent (Cand\`es, H\"older). Preserve both glyphs as one
    # atom instead of charging the accent's nominal advance as a separate word.
    # Ordinary apostrophes and mathematical prime symbols are not accent codes.
    if (
        re.fullmatch(r"\\[`'\"^~=]", previous_value)
        and len(value) == 1
        and value.isalpha()
        and candidate.fontname == previous.fontname
        and not _is_non_horizontal_text_matrix(candidate.matrix)
        and 0.9 <= float(candidate.size) / float(previous.size) <= 1.1
        and abs(_char_baseline(candidate) - _char_baseline(previous)) <= 0.15 * em
        and abs(float(candidate.x0) - float(previous.x0)) <= 0.25 * em
        and float(candidate.x0) < float(previous.x1) - 0.2 * em
    ):
        return True
    if (
        getattr(candidate, "_pdf2zh_prose_word_start", False)
        and not _is_prose_emphasis_font(candidate.fontname)
        and (
            getattr(candidate, "_pdf2zh_prose_word_length", 0) >= 3
            or gap > 0.12 * em
            or previous_value.startswith("\\")
        )
        and not (
            previous_value == ":"
            and all(_is_prose_emphasis_font(ch.fontname) for ch in chars)
        )
    ):
        return False
    if getattr(candidate, "_pdf2zh_cm_prose", False):
        return False
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
        len(value) == 1
        and value.isalpha()
        and any(
            formula_text.count(left) > formula_text.count(right)
            for left, right in (("[", "]"), ("(", ")"))
        )
    ):
        return True
    if first in "([（［" and re.fullmatch(r"[\d\s,;\-–—.]+", formula_text) is not None:
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


def _tex_prose_quote(char: LTChar, layout_class: int) -> str | None:
    """Decode one TeX quote glyph only in an established prose region.

    Computer Modern maps a single opening/closing quote glyph to two ASCII
    characters. Treating the opening backticks as modifier symbols protects
    them with the following formula while the translator sees only the closing
    pair. Expose balanced Unicode punctuation without touching primes or code.
    """
    if (
        layout_class == 0
        or re.match(r"^CM(?:R|TI|SL|BX|CSC)\d", _font_name(char.fontname)) is None
    ):
        return None
    return {"``": "“", "''": "”", '""': "”"}.get(char.get_text())


def _mark_prose_words(chars: list[LTChar]) -> None:
    """Keep complete text words out of adjacent mathematical glyph islands.

    Computer Modern text italic is a prose font, unlike CMMI math italic.
    The historical CM catch-all protected whole theorem statements and captions.
    Identify words geometrically before segmentation; isolated italic variables,
    math operators, scripts, and symbol fonts remain on the original glyph path.
    Ordinary roman words also stop a formula from swallowing its following prose.
    """
    short_words = {
        "a",
        "an",
        "as",
        "at",
        "be",
        "by",
        "do",
        "if",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "so",
        "to",
        "we",
        "no",
        "vs",
    }
    run: list[LTChar] = []

    def flush() -> None:
        if not run:
            return
        word = "".join(char.get_text() for char in run)
        lowered = word.casefold()
        cm_italic = all(
            re.match(r"^CM(?:TI|SL)\d", _font_name(ch.fontname)) for ch in run
        )
        if (
            len(word) >= 3 or (lowered in short_words and (cm_italic or len(word) > 1))
        ) and lowered not in _MATH_WORDS:
            run[0]._pdf2zh_prose_word_start = True
            run[0]._pdf2zh_prose_word_length = len(word)
            for char in run:
                char._pdf2zh_prose_word = True
                if cm_italic:
                    char._pdf2zh_cm_prose = True
        run.clear()

    for char in chars:
        value = char.get_text()
        font = _font_name(char.fontname)
        if (
            not value.isalpha()
            or _MATH_FONT_RE.search(font)
            or _is_non_horizontal_text_matrix(char.matrix)
            or re.search(r"(?:Mono|Code|^CMTT|^CMITT)", font, re.IGNORECASE)
        ):
            flush()
            continue
        if run:
            previous = run[-1]
            em = max(float(char.size), float(previous.size), 1.0)
            if (
                char.fontname != previous.fontname
                or not -0.12 * em <= float(char.x0) - float(previous.x1) <= 0.16 * em
                or abs(_char_baseline(char) - _char_baseline(previous)) > 0.15 * em
                or min(float(char.size), float(previous.size)) < 0.9 * em
            ):
                flush()
        run.append(char)
    flush()
    # Release punctuation only when it touches proven prose. An isolated
    # italic ``(x)`` must remain one protected formula, including its brackets.
    for ordered in (range(len(chars)), range(len(chars) - 1, -1, -1)):
        for index in ordered:
            char = chars[index]
            if not re.match(
                r"^CM(?:TI|SL)\d", _font_name(char.fontname)
            ) or char.get_text() not in ".,;:!?-()[]/_" + chr(39) + chr(34) + chr(96):
                continue
            for neighbor_id in (index - 1, index + 1):
                if not 0 <= neighbor_id < len(chars):
                    continue
                neighbor = chars[neighbor_id]
                gap = max(char.x0, neighbor.x0) - min(char.x1, neighbor.x1)
                if (
                    getattr(neighbor, "_pdf2zh_cm_prose", False)
                    and char.fontname == neighbor.fontname
                    and -0.12 * char.size <= gap <= 0.16 * char.size
                    and abs(_char_baseline(char) - _char_baseline(neighbor))
                    <= 0.15 * char.size
                ):
                    char._pdf2zh_cm_prose = True
                    break
    # Prose abbreviations use single-letter groups, even in text italic. Keep
    # the entire dotted unit (including its final period) out of math islands.
    prose_abbreviations = {"w.r.t.", "w.h.p.", "i.i.d.", "i.e.", "e.g."}
    for index in range(len(chars)):
        abbreviation = next(
            (
                chars[index : index + length]
                for length in (4, 6)
                if "".join(
                    ch.get_text() for ch in chars[index : index + length]
                ).lower()
                in prose_abbreviations
            ),
            None,
        )
        if abbreviation is None:
            continue
        em = max(ch.size for ch in abbreviation)
        if (
            all(_is_non_math_text_font(ch.fontname) for ch in abbreviation)
            and min(ch.size for ch in abbreviation) >= 0.9 * em
            and max(_char_baseline(ch) for ch in abbreviation)
            - min(_char_baseline(ch) for ch in abbreviation)
            < 0.2 * em
            and all(
                -0.12 * em <= right.x0 - left.x1 <= 0.5 * em
                for left, right in zip(abbreviation, abbreviation[1:])
            )
        ):
            abbreviation[0]._pdf2zh_prose_word_start = True
            abbreviation[0]._pdf2zh_prose_word_length = len(abbreviation)
            for char in abbreviation:
                char._pdf2zh_prose_word = True
                if re.match(r"^CM(?:TI|SL)\d", _font_name(char.fontname)):
                    char._pdf2zh_cm_prose = True


def _clipped_figure_word_chars(chars, layout, detections):
    """Keep a chart word intact when its last glyph falls outside the figure box."""
    figures = [d.bounds for d in detections if d.class_name == "figure"]
    if not figures:
        return set()
    preserved = set()
    run = []

    def flush():
        if len(run) >= 4:
            classes = [
                int(
                    layout[
                        min(max(int(ch.y0), 0), layout.shape[0] - 1),
                        min(max(int(ch.x0), 0), layout.shape[1] - 1),
                    ]
                )
                for ch in run
            ]
            if set(classes) <= {0, 1} and classes.count(0) >= len(run) * 0.75:
                if any(
                    x0 <= run[0].x0 <= x1 and y0 <= run[0].y0 <= y1
                    for x0, y0, x1, y1 in figures
                ):
                    preserved.update(object_id(ch) for ch in run)
        run.clear()

    for ch in chars:
        if not ch.get_text().isalpha() or not _is_non_math_text_font(ch.fontname):
            flush()
            continue
        if run and not _touching_text_glyphs(run[-1], ch, max_gap=0.20):
            flush()
        run.append(ch)
    flush()
    return preserved


def _split_running_header_chars(chars, layout, page_height):
    """Preserve a top-margin word split by the detector's abandoned border.

    Only uppercase words already mostly preserved by the detector qualify.
    This reunites the first ``A`` of ``ARTICLES`` with its source glyphs,
    without classifying an ordinary first-page article title as a header.
    """
    result = set()
    run = []

    def flush():
        if len(run) >= 4:
            value = "".join(char.get_text() for char in run)
            classes = [
                layout[
                    min(max(0, int(char.y0)), layout.shape[0] - 1),
                    min(max(0, int(char.x0)), layout.shape[1] - 1),
                ]
                for char in run
            ]
            if value.isalpha() and value.isupper() and classes.count(0) >= len(run) / 2:
                result.update(object_id(char) for char in run)
        run.clear()

    for char in chars:
        if (
            char.y0 < page_height * 0.94
            or char.size > page_height * 0.03
            or not char.get_text().isalpha()
            or not char.get_text().isupper()
            or not _is_non_math_text_font(char.fontname)
        ):
            flush()
            continue
        if run:
            previous = run[-1]
            em = max(float(previous.size), float(char.size), 1.0)
            if not (
                char.fontname == previous.fontname
                and abs(char.size - previous.size) < 0.05 * em
                and abs(char.y0 - previous.y0) < 0.10 * em
                and -0.15 * em <= char.x0 - previous.x1 <= 0.20 * em
            ):
                flush()
        run.append(char)
    flush()
    return result


def _abandoned_reference_entries(lines: list[list[LTChar]]) -> list[list[LTChar]]:
    """Identify complete bibliographic entries inside an abandoned text box.

    A detector can merge the first references with author email footnotes. Use
    bracketed numbering, author names, a publication year and bibliography
    evidence, rather than treating every line in that box as body prose.
    """
    cells: list[list[LTChar]] = []
    for line in lines:
        cell: list[LTChar] = []
        for char in sorted(line, key=lambda ch: ch.x0):
            if cell and char.x0 - cell[-1].x1 > 2 * max(char.size, cell[-1].size):
                cells.append(cell)
                cell = []
            cell.append(char)
        if cell:
            cells.append(cell)
    entries = []
    used: set[int] = set()
    for index, first in enumerate(cells):
        text = _reconstruct_italic_run(first)
        if index in used or not re.match(r"^\[\s*(?:S\s*)?\d{1,3}\s*\]", text):
            continue
        candidate = list(first)
        indices = [index]
        previous = first
        em = max(ch.size for ch in first)
        for next_index in range(index + 1, len(cells)):
            if re.search(r"\((?:19|20)\d{2}[a-z]?\)[.,;:]?\s*$", text):
                break
            cell = cells[next_index]
            if abs(cell[0].x0 - first[0].x0) > 2.5 * em:
                continue
            baseline_gap = _char_baseline(previous[0]) - _char_baseline(cell[0])
            continuation = _reconstruct_italic_run(cell)
            if (
                next_index in used
                or not 0.7 * em <= baseline_gap <= 1.8 * em
                or abs(max(ch.size for ch in cell) - em) > 0.15 * em
                or re.match(r"^\[\s*(?:S\s*)?\d{1,3}\s*\]", continuation)
                or "@" in continuation
                or re.search(
                    r"copyright|unauthorized|downloaded|redistribution|"
                    r"terms-privacy|all\s+rights\s+reserved",
                    continuation,
                    re.IGNORECASE,
                )
            ):
                break
            candidate.extend(cell)
            indices.append(next_index)
            previous = cell
            text += " " + continuation
        complete = (
            "@" not in text
            and re.search(r"\((?:19|20)\d{2}[a-z]?\)", text)
            and reference_entry_score(text) >= 5
        )
        previous_entry = entries[-1] if entries else []
        marker = re.match(r"^\[\s*(S\s*)?(\d{1,3})\s*\]", text)
        previous_marker = re.match(
            r"^\[\s*(S\s*)?(\d{1,3})\s*\]",
            _reconstruct_italic_run(previous_entry),
        )
        continued_reference = bool(
            previous_entry
            and previous_marker
            and marker
            and marker[1] == previous_marker[1]
            and int(marker[2]) == int(previous_marker[2]) + 1
            and abs(first[0].x0 - previous_entry[0].x0) <= em
            and 0.7 * em
            <= _char_baseline(previous_entry[-1]) - _char_baseline(first[0])
            <= 2.5 * em
            and abs(max(ch.size for ch in previous_entry) - em) <= 0.15 * em
            and reference_entry_score(text) >= 3
            and len(re.findall(r"[A-Za-z]{3,}", text)) >= 5
            and "@" not in text
        )
        if complete or continued_reference:
            entries.append(candidate)
            used.update(indices)
    return entries


def _formula_annotation_islands(chars: list[LTChar]) -> list[list[LTChar]]:
    """Find independent prose labels without releasing the surrounding equation.

    Only geometrically separated words in text fonts qualify. Symbols, operators,
    brackets and underbraces retain their original protected glyphs and anchors.
    A one-word qualifier needs an explicit mathematical grammar around it.
    """
    lines: list[list[LTChar]] = []
    for char in sorted(chars, key=lambda ch: (-_char_baseline(ch), ch.x0)):
        line = next(
            (
                line
                for line in reversed(lines[-5:])
                if abs(_char_baseline(line[0]) - _char_baseline(char))
                <= 0.18 * min(float(line[0].size), float(char.size))
            ),
            None,
        )
        if line is None:
            lines.append([char])
        else:
            line.append(char)
    islands = []
    relation = re.compile(r"[=<>≤≥∼≈∈]|\\(?:sim|in|leq|geq|equiv|approx)\b")
    for line in lines:
        line.sort(key=lambda ch: ch.x0)
        runs: list[list[LTChar]] = []
        run: list[LTChar] = []
        for char in line:
            if (
                getattr(char, "_pdf2zh_prose_word", False)
                and char.get_text().isalpha()
                and _is_non_math_text_font(char.fontname)
            ):
                if run and (
                    char.x0 - run[-1].x1 > 0.8 * min(char.size, run[-1].size)
                    or min(char.size, run[-1].size) < 0.9 * max(char.size, run[-1].size)
                ):
                    runs.append(run)
                    run = []
                run.append(char)
            else:
                if run:
                    runs.append(run)
                    run = []
        if run:
            runs.append(run)
        for run in runs:
            text = _reconstruct_italic_run(run)
            words = re.findall(r"[A-Za-z]+", text)
            if len(words) >= 2 and any(len(word) >= 3 for word in words):
                islands.append(run)
                continue
            # ``A(x) = y, where z ~ N(...)`` is an explanatory clause;
            # ``where(x)`` is not. Keep both mathematical sides in place.
            left = [ch for ch in line if ch.x1 <= run[0].x0]
            right = [ch for ch in line if ch.x0 >= run[-1].x1]
            before = "".join(ch.get_text() for ch in left)
            after = "".join(ch.get_text() for ch in right)
            em = max(ch.size for ch in run)

            def math_neighbor(on_left):
                # Extensible matrix delimiters have raised font baselines.
                # Include just those tall delimiters alongside same-row glyphs.
                candidates = []
                for char in chars:
                    gap = run[0].x0 - char.x1 if on_left else char.x0 - run[-1].x1
                    if not 0 <= gap <= 2.5 * em:
                        continue
                    same_row = (
                        abs(_char_baseline(char) - _char_baseline(run[0])) <= 0.2 * em
                    )
                    tall_delimiter = (
                        re.match(r"\\(?:bigg?|Bigg?)[lr]?[()[\]]", char.get_text())
                        and abs(char.y0 - run[0].y0) <= 2 * em
                    )
                    if same_row or tall_delimiter:
                        candidates.append((gap, char))
                if not candidates:
                    return False
                nearest = min(candidates, key=lambda item: item[0])[1]
                math_script = (
                    nearest.get_text().isdigit()
                    and nearest.size < 0.9 * em
                    and any(
                        _MATH_FONT_RE.search(_font_name(ch.fontname))
                        and -0.1 * em <= nearest.x0 - ch.x1 <= 0.3 * em
                        and abs(_char_baseline(ch) - _char_baseline(nearest))
                        < 0.65 * em
                        for ch in chars
                    )
                )
                return bool(
                    math_script
                    or _MATH_FONT_RE.search(_font_name(nearest.fontname))
                    or re.match(r"\\[A-Za-z]+", nearest.get_text())
                )

            if (
                text.casefold() in {"and", "or"}
                and math_neighbor(True)
                and math_neighbor(False)
            ):
                islands.append(run)
            elif (
                text.casefold() == "where"
                and left
                and right
                and before.rstrip().endswith(",")
                and relation.search(before)
                and relation.search(after)
                and run[0].x0 - left[-1].x1 >= 0.2 * em
                and right[0].x0 - run[-1].x1 >= 0.2 * em
                and any(_MATH_FONT_RE.search(_font_name(ch.fontname)) for ch in left)
                and any(_MATH_FONT_RE.search(_font_name(ch.fontname)) for ch in right)
            ):
                islands.append(run)
            elif (
                text.casefold() in {"small", "large"}
                and left
                and re.search(
                    r"(?:∀|\\forall\s*)\s*(?:\\[A-Za-z]+\s*|[A-Za-z\u0370-\u03ff])$",
                    before,
                )
                and run[0].x0 - left[-1].x1 >= 0.2 * em
                and _MATH_FONT_RE.search(_font_name(left[-1].fontname))
                and not re.search(r"[^\s.,;:)]", after)
            ):
                islands.append(run)
    return islands


def _is_scripted_table_symbol(chars: list[LTChar]) -> bool:
    """Keep a mathematical base and its textual script in a protected cell."""
    visible = [char for char in chars if char.get_text().strip()]
    if len(visible) < 2:
        return False
    base, *script = visible
    em = float(base.size)
    if (
        em <= 0
        or re.fullmatch(r"[A-Za-z\u0370-\u03ff]", base.get_text()) is None
        or not (
            _MATH_FONT_RE.search(_font_name(base.fontname))
            or _is_prose_italic_font(base.fontname)
        )
        or any(
            re.fullmatch(r"[a-z0-9]", char.get_text()) is None
            or float(char.size) >= 0.79 * em
            for char in script
        )
    ):
        return False
    base_center = (float(base.y0) + float(base.y1)) / 2
    script_center = (float(script[0].y0) + float(script[0].y1)) / 2
    if not 0.10 * em <= abs(script_center - base_center) <= 0.70 * em:
        return False
    if min(base.y1, script[0].y1) <= max(base.y0, script[0].y0):
        return False
    previous = base
    for char in script:
        if (
            not -0.25 * em <= char.x0 - previous.x1 <= 0.30 * em
            or abs((float(char.y0) + float(char.y1)) / 2 - script_center) > 0.12 * em
        ):
            return False
        previous = char
    return True


def _table_line_cells(line: list[LTChar], column_starts=()) -> list[list[LTChar]]:
    cells: list[list[LTChar]] = []
    for char in sorted(line, key=lambda ch: (ch.x0, -ch.y0)):
        if (
            not cells
            or char.x0 - cells[-1][-1].x1 > 1.8 * max(char.size, cells[-1][-1].size)
            or any(
                cells[-1][-1].x1 <= x <= char.x0 + 0.1 * char.size
                for x in column_starts
            )
        ):
            cells.append([])
        cells[-1].append(char)
    return cells


def _table_description_columns(lines):
    """Infer repeated value/description boundaries, not arbitrary word gaps."""
    candidates = []
    for row, line in enumerate(lines):
        ordered = sorted(line, key=lambda ch: ch.x0)
        for index in range(1, len(ordered)):
            left, right = ordered[index - 1], ordered[index]
            em = max(left.size, right.size)
            if right.x0 - left.x1 < 0.8 * em:
                continue
            prefix = _reconstruct_italic_run(ordered[:index])
            suffix = _reconstruct_italic_run(ordered[index:])
            if (
                re.search(r"\d", prefix)
                and re.match(r"[A-Za-z]{3,}\b", suffix)
                and len(re.findall(r"[A-Za-z]{2,}", suffix)) >= 2
            ):
                candidates.append((row, right.x0, em))
    return tuple(
        sorted(
            {
                x
                for row, x, em in candidates
                if len({r for r, y, _ in candidates if abs(x - y) < 0.2 * em}) >= 3
            }
        )
    )


def _wrapped_table_label_pairs(lines: list[list[LTChar]]) -> dict[int, int]:
    """Join an indented label continuation with values on its final line.

    A row label can wrap above its numeric columns.  Releasing each physical
    line as an independent table cell loses the noun phrase's context.  Require
    both the hanging indent and two numeric value cells before sharing a class;
    aligned neighboring rows and separate prose columns remain independent.
    """
    pairs: dict[int, int] = {}
    rows = [_table_line_cells(line) for line in lines]
    for previous, current in zip(rows, rows[1:]):
        if len(previous) != 1 or len(current) < 3:
            continue
        left, right = previous[0], current[0]
        before = _reconstruct_italic_run(left).strip()
        after = _reconstruct_italic_run(right).strip()
        if (
            len(re.findall(r"[A-Za-z]{2,}", before)) < 3
            or re.search(r"[A-Za-z]$", before) is None
            or re.match(r"[a-z]{2,}\b", after) is None
            or any(
                re.match(r"^[+\-−]?\d", _reconstruct_italic_run(cell).strip()) is None
                or re.search(r"[A-Za-z]{2,}", _reconstruct_italic_run(cell))
                for cell in current[1:]
            )
        ):
            continue
        left_size = max(float(ch.size) for ch in left)
        right_size = max(float(ch.size) for ch in right)
        if min(left_size, right_size) <= 0:
            continue
        em = max(left_size, right_size)
        indent = right[0].x0 - left[0].x0
        baseline_gap = _char_baseline(left[0]) - _char_baseline(right[0])
        if (
            min(left_size, right_size) < 0.9 * em
            or not 0.35 * em <= indent <= 2 * em
            or not 0.85 * em <= baseline_gap <= 1.5 * em
            or max(ch.x1 for ch in right) > max(ch.x1 for ch in left) + 2 * em
            or any(_is_non_horizontal_text_matrix(ch.matrix) for ch in left + right)
        ):
            continue
        pairs[object_id(right[0])] = object_id(left[0])
    return pairs


def _source_list_markers(chars: list[LTChar], excluded: set[int]) -> set[int]:
    """Recognize physical list prefixes, including TeX-mapped glyphs.

    A marker is separate from the reflowed text, so each item keeps its source
    baseline and hanging indent.  An inline product/minus/asterisk has preceding
    text on the same line and must remain part of its formula instead.
    """
    horizontal = [
        ch
        for ch in chars
        if object_id(ch) not in excluded
        and not _is_non_horizontal_text_matrix(ch.matrix)
        and ch.get_text().strip()
    ]
    prefixes = {"•", "\\bullet", "--", "–", "—", "*", "\\ast"}
    markers: set[int] = set()
    for char in horizontal:
        if char.get_text().strip() not in prefixes:
            continue
        em = float(char.size)
        baseline = _char_baseline(char)
        row = [
            ch
            for ch in horizontal
            if ch is not char and abs(_char_baseline(ch) - baseline) <= 0.25 * em
        ]
        # Other columns can occupy the same baseline; inspect the local gap.
        if any(0 <= char.x0 - ch.x1 < 3 * em for ch in row):
            continue
        following = sorted((ch for ch in row if ch.x0 >= char.x1), key=lambda ch: ch.x0)
        if not following or not 0 <= following[0].x0 - char.x1 <= 1.2 * em:
            continue
        # Word evidence prevents a standalone mathematical bullet/minus from
        # being mistaken for a list.  The item may begin with an inline formula.
        if not any(
            getattr(ch, "_pdf2zh_prose_word", False)
            for ch in following
            if ch.x0 - char.x1 < 14 * em
        ):
            continue
        markers.add(object_id(char))
    return markers


def _algorithm_display_math_chars(
    chars: list[LTChar], recovered: dict[int, int], region_types: dict[int, str]
) -> set[int]:
    """Keep a displayed equation below an algorithm instruction at its anchor.

    Formula detection sometimes wraps an instruction and the following display
    in one box.  Recovering its prose must not pull that display into the line.
    Require a separated relation baseline, not merely a low subscript or a
    fraction denominator, before identifying a display band.
    """
    grouped: dict[int, list[LTChar]] = {}
    for char in chars:
        cls = recovered.get(object_id(char))
        if region_types.get(cls) == "algorithm":
            grouped.setdefault(cls, []).append(char)
    preserved: set[int] = set()
    for group in grouped.values():
        prose = [ch for ch in group if getattr(ch, "_pdf2zh_prose_word", False)]
        if not prose:
            continue
        em = float(np.median([ch.size for ch in prose]))
        baselines = sorted({_char_baseline(ch) for ch in prose})
        for char in group:
            if char.get_text().strip() not in {"=", ":=", "←", "\\leftarrow"}:
                continue
            baseline = _char_baseline(char)
            if min(abs(baseline - y) for y in baselines) <= 0.85 * em:
                continue
            above = [y for y in baselines if y > baseline]
            if not above:
                continue
            below = [y for y in baselines if y < baseline]
            top = (min(above) + baseline) / 2
            bottom = (max(below) + baseline) / 2 if below else -float("inf")
            preserved.update(
                object_id(ch) for ch in group if bottom < _char_baseline(ch) < top
            )
    return preserved


def _recover_preserved_region_prose(
    chars: list[LTChar],
    layout,
    detections,
    region_types: dict[int, str],
    excluded: set[int],
) -> dict[int, int]:
    """Recover footnote, table-cell and algorithm prose with original anchors.

    The detector deliberately protects figures and equations. Only independently
    identifiable text lines inside table/footnote/algorithm boxes are released;
    numeric cells, figure labels, equations and page furniture remain protected.
    Each recovered cell/line gets a separate paragraph to prevent column mixing.
    """
    if not detections:
        return {}
    height, width = layout.shape
    regions = [
        d
        for d in detections
        if d.class_name in {"table", "abandon", "isolate_formula", "figure"}
    ]
    relevant = []
    for char in chars:
        if object_id(char) in excluded or _is_non_horizontal_text_matrix(char.matrix):
            continue
        x, y = float(char.x0), float(char.y0)
        if (
            int(layout[int(np.clip(y, 0, height - 1)), int(np.clip(x, 0, width - 1))])
            != 0
        ):
            continue
        matches = [
            d
            for d in regions
            if d.bounds[0] <= x <= d.bounds[2] and d.bounds[1] <= y <= d.bounds[3]
        ]
        if matches:
            # A detected table provides stronger structural context than a
            # duplicate equation or abandon box over the same table contents.
            region = next((d for d in matches if d.class_name == "table"), matches[0])
            relevant.append((char, region))
    grouped: dict[int, list[LTChar]] = {}
    by_id = {d.value: d for d in regions}
    for char, region in relevant:
        grouped.setdefault(region.value, []).append(char)
    recovered: dict[int, int] = {}
    new_class = max(region_types, default=1) + 1
    for region_id, region_chars in grouped.items():
        region = by_id[region_id]
        if region.class_name == "figure":
            # A displayed comparison with underbraces can be detected as a
            # figure. Require a brace directly beneath each label and a math
            # relation on its baseline; ordinary chart labels stay protected.
            for island in _formula_annotation_islands(region_chars):
                em = max(ch.size for ch in island)
                baseline = _char_baseline(island[0])
                braces = [
                    ch
                    for ch in region_chars
                    if ("\\underbrace" in ch.get_text() or ch.get_text() == "⏟")
                    and _MATH_FONT_RE.search(_font_name(ch.fontname))
                    and 0 < baseline - _char_baseline(ch) < 1.2 * em
                    and ch.x1 > island[0].x0
                    and ch.x0 < island[-1].x1
                ]
                if not braces or not any(
                    re.search(r"[=<>≤≥]|\\(?:leq|geq|equiv)\b", ch.get_text())
                    and abs(_char_baseline(ch) - baseline) < 0.2 * em
                    for ch in region_chars
                ):
                    continue
                # TeX's extensible braces include glyph pieces mapped to a
                # space. Include only matching-font pieces on the brace row.
                braces += [
                    ch
                    for ch in region_chars
                    if not ch.get_text().strip()
                    and ch.x1 > island[0].x0
                    and ch.x0 < island[-1].x1
                    and any(
                        ch.fontname == brace.fontname
                        and abs(_char_baseline(ch) - _char_baseline(brace)) < 0.05 * em
                        for brace in braces
                    )
                ]
                if max(ch.x1 for ch in braces) - min(ch.x0 for ch in braces) < (
                    0.8 * (island[-1].x1 - island[0].x0)
                ):
                    continue
                region_types[new_class] = "formula_annotation"
                for char in island:
                    recovered[object_id(char)] = new_class
                new_class += 1
            continue
        if region.class_name == "isolate_formula":
            prose = _reconstruct_italic_run(region_chars)
            algorithm = re.search(
                r"^\s*(?:(?:\\bullet|•)\s*)?(?:[-–—]{1,2}\s*)?"
                r"(?:Initialize|Let|For|Run|Set|Output|Given|Return|Update|Repeat|Until|While)\s+(?=[A-Za-z\\])",
                prose,
            )
            # A displayed recovery task is prose around protected mathematical
            # objects, e.g. ``recover X from (b, A), where b = A(X)``. Require
            # the complete task framing and actual math fonts; function names
            # or arbitrary explanatory words inside an equation do not qualify.
            task_words = _reconstruct_italic_run(
                [ch for ch in region_chars if getattr(ch, "_pdf2zh_prose_word", False)]
            )
            recovery_task = (
                re.search(r"^\s*recover\s+.+?\s+from\s+.+?\s+where\s+", prose, re.I)
                and re.search(r"\brecover\b.*\bfrom\b.*\bwhere\b", task_words, re.I)
                and any(
                    _MATH_FONT_RE.search(_font_name(ch.fontname)) for ch in region_chars
                )
            )
            if algorithm or recovery_task:
                region_types[new_class] = "algorithm" if algorithm else "plain text"
                for char in region_chars:
                    recovered[object_id(char)] = new_class
                new_class += 1
            else:
                for island in _formula_annotation_islands(region_chars):
                    region_types[new_class] = "formula_annotation"
                    for char in island:
                        recovered[object_id(char)] = new_class
                    new_class += 1
            continue
        if region.class_name == "table":
            for island in _formula_annotation_islands(region_chars):
                if _reconstruct_italic_run(island).casefold() not in {"and", "or"}:
                    continue
                region_types[new_class] = "formula_annotation"
                for char in island:
                    recovered[object_id(char)] = new_class
                new_class += 1
        lines: list[list[LTChar]] = []
        for char in sorted(region_chars, key=lambda ch: (-_char_baseline(ch), ch.x0)):
            if object_id(char) in recovered:
                continue
            line = next(
                (
                    line
                    for line in reversed(lines[-3:])
                    if abs(_char_baseline(line[0]) - _char_baseline(char))
                    <= 0.65 * max(float(line[0].size), float(char.size))
                ),
                None,
            )
            if line is None:
                lines.append([char])
            else:
                line.append(char)
        if region.class_name == "abandon":
            for entry in _abandoned_reference_entries(lines):
                region_types[new_class] = "plain text"
                for char in entry:
                    recovered[object_id(char)] = new_class
                new_class += 1
            lines = [
                remaining
                for line in lines
                if (remaining := [ch for ch in line if object_id(ch) not in recovered])
            ]
        table_continuations = (
            _wrapped_table_label_pairs(lines) if region.class_name == "table" else {}
        )
        description_columns = (
            _table_description_columns(lines) if region.class_name == "table" else ()
        )
        previous_footnote = None
        footnote_history = []
        for line in lines:
            line.sort(key=lambda ch: (ch.x0, -ch.y0))
            text = _reconstruct_italic_run(line)
            words = re.findall(r"[A-Za-z]{3,}", text)
            if re.search(
                r"copyright|unauthorized|downloaded|redistribution|terms-privacy",
                text,
                re.I,
            ):
                previous_footnote = None
                continue
            cells = [line]
            kind = "plain text"
            if region.class_name == "table":
                cells = _table_line_cells(line, description_columns)
                kind = "table_cell"
            elif region.class_name == "abandon":
                if max(ch.y1 for ch in line) > height * 0.30 or (
                    len(words) < 3 and previous_footnote is None
                ):
                    continue
                if not re.match(r"^\d+\s*[A-Z]", text) and previous_footnote is None:
                    # Publication/author notes use symbols; numeric scientific
                    # footnotes are the target of this recovery path.
                    number = [
                        ch
                        for ch in chars
                        if ch.get_text().isdigit()
                        and 0 <= line[0].x0 - ch.x1 <= line[0].size
                        and abs(_char_baseline(ch) - _char_baseline(line[0]))
                        < line[0].size
                    ]
                    if not number:
                        continue
                    line = sorted(number + line, key=lambda ch: ch.x0)
                    cells = [line]
                kind = "footnote"
                cells = [[]]
                for char in line:
                    if cells[-1] and char.x0 - cells[-1][-1].x1 > 2 * max(
                        char.size, cells[-1][-1].size
                    ):
                        cells.append([])
                    cells[-1].append(char)
            else:
                if not re.search(
                    r"(?:^|\s)(?:Initialize|Let|For|Run|Set|Output|Given|Return|Update|Repeat|Until|While)\b",
                    text,
                ):
                    continue
                kind = "algorithm"
            for cell in cells:
                cell_words = [
                    ch for ch in cell if getattr(ch, "_pdf2zh_prose_word", False)
                ]
                if not cell_words:
                    continue
                if kind == "table_cell":
                    prose = "".join(ch.get_text() for ch in cell_words)
                    if len(prose) < 4 or _is_scripted_table_symbol(cell):
                        continue
                if kind == "footnote":
                    baseline = float(np.median([_char_baseline(ch) for ch in cell]))
                    extra = [
                        ch
                        for ch in chars
                        if object_id(ch) not in excluded
                        and abs(_char_baseline(ch) - baseline)
                        <= 0.6 * max(c.size for c in cell)
                        and min(c.x0 for c in cell) - 2
                        <= ch.x0
                        <= max(c.x1 for c in cell)
                        and not _is_non_horizontal_text_matrix(ch.matrix)
                    ]
                    cell = sorted(
                        {object_id(ch): ch for ch in cell + extra}.values(),
                        key=lambda ch: ch.x0,
                    )
                    # Continue only a touching word clipped by the detector,
                    # never a neighboring text column across a whitespace gap.
                    for candidate in sorted(chars, key=lambda ch: ch.x0):
                        previous = cell[-1]
                        if (
                            candidate.x0 >= previous.x1 - 0.1 * previous.size
                            and candidate.x0 - previous.x1 <= 0.16 * previous.size
                            and candidate.fontname == previous.fontname
                            and abs(
                                _char_baseline(candidate) - _char_baseline(previous)
                            )
                            <= 0.15 * previous.size
                            and object_id(candidate) not in excluded
                            and object_id(candidate)
                            not in {object_id(ch) for ch in cell}
                        ):
                            cell.append(candidate)
                class_id = new_class
                if kind == "table_cell":
                    prior_id = table_continuations.get(object_id(cell[0]))
                    if prior_id in recovered:
                        class_id = recovered[prior_id]
                if kind == "footnote":
                    previous_footnote = next(
                        (
                            item
                            for item in reversed(footnote_history)
                            if abs(
                                min(ch.x0 for ch in item[0]) - min(ch.x0 for ch in cell)
                            )
                            <= 2 * max(ch.size for ch in cell)
                        ),
                        None,
                    )
                if kind == "footnote" and previous_footnote is not None:
                    prior, prior_class = previous_footnote
                    gap = min(ch.y0 for ch in prior) - max(ch.y1 for ch in cell)
                    starts_number = bool(
                        re.match(r"^\d+\s*[A-Z]", _reconstruct_italic_run(cell))
                    )
                    if not starts_number and -3 <= gap <= 1.2 * max(
                        ch.size for ch in cell
                    ):
                        class_id = prior_class
                if class_id == new_class:
                    region_types[class_id] = kind
                    new_class += 1
                for char in cell:
                    recovered[object_id(char)] = class_id
                if kind == "footnote":
                    previous_footnote = (cell, class_id)
                    footnote_history.append(previous_footnote)
    return recovered


def _bridge_recovered_footnote_math(chars, recovered, region_types, excluded):
    """Keep a footnote's operator, small domain and objective in one run.

    An abandon box may stop before the domain subscript and resume at the
    objective, breaking continuous footnote prose into independent classes.
    Join only a touching min/max-domain-objective with membership evidence.
    """
    operators = set()
    horizontal = [
        char
        for char in chars
        if object_id(char) not in excluded
        and not _is_non_horizontal_text_matrix(char.matrix)
    ]
    for first in horizontal:
        cls = recovered.get(object_id(first))
        if first.get_text() != "m" or region_types.get(cls) != "footnote":
            continue
        em = float(first.size)
        run = [first]
        for choices in ("ia", "nx"):
            found = [
                char
                for char in horizontal
                if char.get_text() in choices
                and char.fontname == first.fontname
                and abs(char.size - em) <= 0.05 * em
                and abs(_char_baseline(char) - _char_baseline(first)) <= 0.1 * em
                and -0.1 * em <= char.x0 - run[-1].x1 <= 0.1 * em
            ]
            if len(found) != 1:
                break
            run.append(found[0])
        if "".join(char.get_text() for char in run) not in {"min", "max"}:
            continue
        domain = sorted(
            [
                char
                for char in horizontal
                if (
                    run[-1].x1 - 0.1 * em <= char.x0 <= run[-1].x1 + 2.2 * em
                    and 0.5 * em <= char.size <= 0.85 * em
                    and abs(_char_baseline(char) - _char_baseline(first)) <= 0.8 * em
                    and _MATH_FONT_RE.search(_font_name(char.fontname))
                )
            ],
            key=lambda char: char.x0,
        )
        if not domain or not any(
            char.get_text().strip() in {"\\in", "∈"} for char in domain
        ):
            continue
        if domain[0].x0 - run[-1].x1 > 0.2 * em:
            continue
        objective = [
            char
            for char in horizontal
            if (
                -0.1 * em <= char.x0 - domain[-1].x1 <= 0.7 * em
                and 0.9 * em <= char.size <= 1.1 * em
                and abs(_char_baseline(char) - _char_baseline(first)) <= 0.15 * em
                and _MATH_FONT_RE.search(_font_name(char.fontname))
                and region_types.get(recovered.get(object_id(char))) == "footnote"
            )
        ]
        if len(objective) != 1:
            continue
        other_class = recovered[object_id(objective[0])]
        for key in list(recovered):
            if recovered[key] == other_class:
                recovered[key] = cls
        for char in domain:
            recovered[object_id(char)] = cls
        operators.update(object_id(char) for char in run)
    return operators


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
    if any(getattr(char, "_pdf2zh_layout_class", None) == 0 for char in chars):
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


def _is_prose_emphasis_font(value) -> bool:
    """Recognize ordinary bold/italic faces, excluding mathematical fonts."""

    font = _font_name(value)
    return bool(
        _PROSE_ITALIC_FONT_RE.search(font) or _PROSE_BOLD_FONT_RE.search(font)
    ) and not bool(_MATH_FONT_RE.search(font))


def _is_non_math_text_font(value) -> bool:
    font = _font_name(value)
    return bool(font) and not bool(_MATH_FONT_RE.search(font))


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
                line_reset = (
                    abs(_char_baseline(char) - _char_baseline(previous)) > 0.30 * em
                    or float(char.x0) < float(previous.x0) - 0.50 * em
                )
                if gap > 0.12 * em or (
                    line_reset
                    and previous_value[-1] not in "-‐‑–—"
                    and value[0].isalnum()
                ):
                    pieces.append(" ")
        pieces.append(value)
        previous = char
    return re.sub(r"\s+", " ", "".join(pieces)).strip()


def _is_high_confidence_prose_italic(
    chars: list[LTChar],
    paragraph_size: float,
    *,
    allow_heading_wrap: bool = False,
) -> str | None:
    """Return reconstructed prose only when an italic formula run is clearly text."""
    visible = [
        char for char in chars if char.get_text() and not char.get_text().isspace()
    ]
    if not visible:
        return None
    if any(getattr(char, "_pdf2zh_layout_class", 0) == 0 for char in visible):
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
    baseline_tolerance = max(0.8, 0.08 * typical_size)
    if not allow_heading_wrap and max(baselines) - min(baselines) > baseline_tolerance:
        return None
    line_start = visible[0]
    line_count = 1
    for previous, current in zip(visible, visible[1:]):
        baseline_change = _char_baseline(previous) - _char_baseline(current)
        if abs(baseline_change) > baseline_tolerance:
            if (
                not allow_heading_wrap
                or not 0.9 * typical_size <= baseline_change <= 1.8 * typical_size
                or abs(float(current.x0) - float(line_start.x0)) > 0.5 * typical_size
            ):
                return None
            line_count += 1
            if line_count > 3:
                return None
            continue
        gap = float(current.x0) - float(previous.x1)
        if gap > 0.60 * typical_size:
            return None

    text = _reconstruct_italic_run(chars)
    parenthetical_connector = bool(_PARENTHETICAL_ITALIC_CONNECTOR_RE.fullmatch(text))
    quoted_prose = bool(_QUOTED_ITALIC_PROSE_RE.fullmatch(text))
    front_matter_label = bool(_STRUCTURAL_ITALIC_LABEL_RE.match(text))
    subsection_label = bool(_SUBSECTION_ITALIC_LABEL_RE.fullmatch(text))
    runin_heading = bool(_RUNIN_ITALIC_HEADING_RE.fullmatch(text))
    structural_label = front_matter_label or subsection_label or runin_heading
    all_italic = all(_is_prose_italic_font(char.fontname) for char in visible)
    if not all_italic and not (
        (
            (front_matter_label or parenthetical_connector)
            and all(_is_prose_emphasis_font(char.fontname) for char in visible)
        )
        or (
            runin_heading
            and any(_is_prose_italic_font(char.fontname) for char in visible)
            and all(_is_non_math_text_font(char.fontname) for char in visible)
        )
    ):
        # Ordinary bold text is not generally interchangeable with prose:
        # variables, diagram labels, headings, and author metadata often use it.
        # Only the closed front-matter/connector forms above may bridge the mixed
        # BoldItalic -> Bold runs emitted by IEEE PDFs.
        return None
    if (
        not text
        or any(
            not (char.isalpha() or char.isspace() or char in "-‐‑–—'’.")
            for char in text
        )
        and not (
            parenthetical_connector or runin_heading or quoted_prose or subsection_label
        )
    ):
        return None
    letters = [char for char in text if char.isalpha()]
    forced_short_prose = bool(
        len(letters) >= 2
        and any(getattr(char, _FORCED_STYLED_PROSE_ATTR, False) for char in visible)
    )
    if len(letters) < (2 if parenthetical_connector else 4) and not (
        forced_short_prose
    ):
        return None
    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    if not words:
        return None

    compact = "".join(letters).casefold()
    if (
        compact in _NON_TRANSLATABLE_ITALIC_PHRASES
        or _PROTECTED_ITALIC_MEMBERSHIP_RE.fullmatch(compact) is not None
    ):
        return None
    if parenthetical_connector:
        return text
    if not structural_label and words[0].casefold() in _MATH_WORDS:
        return None
    if not structural_label and len(words) >= 2 and len(words[0]) == 1:
        # A one-letter leading atom is much more likely to be a variable label
        # (``Q factor``, ``p value``) than translatable emphasized prose.
        return None
    if not structural_label and any(
        word != word.lower() and word != word.upper() and word != word.capitalize()
        for word in words
    ):
        # Preserve camel/mixed-case model and product identifiers such as
        # SoundBeam, WavLM, and iSWAP.
        return None
    if (
        not structural_label
        and len(words) >= 2
        and all(word.isupper() for word in words)
    ):
        return None
    if (
        not structural_label
        and len(words) >= 2
        and all(word == word.capitalize() for word in words)
    ):
        # Italic author names and personal titles are common in IEEE front
        # matter.  Structural labels are the deliberate exception.
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


def _release_standalone_italic_headings(
    segments: list[str],
    formula_runs: list[list[LTChar]],
    formula_paragraphs: list[int],
    paragraphs: list["Paragraph"],
) -> None:
    """Expose complete subsection headings to every translation backend.

    A wrapped all-italic heading can become one opaque formula even though the
    layout model correctly identified its title region. Only a complete numbered
    heading in that region may cross baselines; inline math and names retain the
    stricter single-line classifier.
    """
    for formula_id, (chars, paragraph_id) in enumerate(
        zip(formula_runs, formula_paragraphs, strict=True)
    ):
        if not 0 <= paragraph_id < len(paragraphs):
            continue
        paragraph = paragraphs[paragraph_id]
        if (
            paragraph.region_kind != "title"
            or segments[paragraph_id].strip() != f"{{v{formula_id}}}"
        ):
            continue
        text = _is_high_confidence_prose_italic(
            chars, paragraph.size, allow_heading_wrap=True
        )
        if text is not None and _SUBSECTION_ITALIC_LABEL_RE.fullmatch(text):
            segments[paragraph_id] = text
            paragraph.brk = True


def _has_inline_prose_context(
    segment: str,
    formula_id: int,
    styled_text: str | None = None,
) -> bool:
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
    prose_boundary_punctuation = r"[\s,;:.!?“”\"'‘’]*"
    left_is_prose = (
        re.search(rf"[A-Za-z]{{2,}}{prose_boundary_punctuation}$", left) is not None
    )
    right_is_prose = (
        re.match(rf"{prose_boundary_punctuation}[A-Za-z]{{2,}}", right) is not None
    )
    if left_is_prose and right_is_prose:
        return True
    if (
        styled_text
        and _is_structural_styled_text(styled_text)
        and marker.fullmatch(segment.strip()) is not None
    ):
        # IEEE emits ``Abstract—In`` and ``Index Terms—Deep`` as their own
        # formula-only paragraph immediately before the remainder of the prose.
        # This closed label form is self-describing, so it does not need ordinary
        # text on either side inside the same parser segment.
        return True
    if not styled_text or not (left_is_prose or right_is_prose):
        words = re.findall(r"[^\W\d_]+", styled_text, flags=re.UNICODE)
        surrounding_prose = _FORMULA_MARKER_RE.sub(" ", segment)
        if len(words) < 3 or len(re.findall(r"[A-Za-z]{2,}", surrounding_prose)) < 2:
            return False
    words = re.findall(r"[^\W\d_]+", styled_text, flags=re.UNICODE)
    # A strong multiword style run can sit at a paragraph boundary or directly
    # beside another style run.  Requiring prose on both immediate sides caused
    # IEEE Abstract/Index Terms labels and adjacent emphasized noun phrases to
    # remain opaque formula placeholders.  Short one-word emphasis retains the
    # stricter two-sided requirement.
    return len(words) >= 2 or bool(
        _PARENTHETICAL_ITALIC_CONNECTOR_RE.fullmatch(styled_text)
    )


def _standalone_italic_affiliation(
    chars: list[LTChar], segment: str, paragraph: "Paragraph"
) -> str | None:
    """Recover a typeset institution line beside its affiliation superscript.

    The paragraph font size can come from the superscript rather than the
    address.  Institution syntax and a formula-only numbered block provide
    stronger evidence here than the inline-prose font-size heuristic.
    """
    if paragraph.region_kind not in {"", "plain text"}:
        return None
    surrounding = _FORMULA_MARKER_RE.sub("", segment)
    if re.search(r"[^\d\s,;*†‡]", surrounding):
        return None
    visible = [char for char in chars if char.get_text().strip()]
    if not visible or any(
        getattr(char, "_pdf2zh_layout_class", 0) == 0
        or not _is_prose_italic_font(char.fontname)
        or _is_non_horizontal_text_matrix(char.matrix)
        for char in visible
    ):
        return None
    sizes = [float(char.size) for char in visible]
    if min(sizes) <= 0 or max(sizes) / min(sizes) > 1.08:
        return None
    baselines = [_char_baseline(char) for char in visible]
    if max(baselines) - min(baselines) > max(0.8, 0.10 * min(sizes)):
        return None
    text = _reconstruct_italic_run(chars)
    if (
        not looks_like_affiliation(text)
        or "," not in text
        or len(re.findall(r"[A-Za-z]{2,}", text)) < 3
        or re.search(r"[=+×÷∑∫<>]", text)
    ):
        return None
    return text


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
        affiliation = _standalone_italic_affiliation(
            chars, segments[paragraph_id], paragraphs[paragraph_id]
        )
        if affiliation is not None:
            candidates[formula_id] = affiliation
            continue
        text = _is_high_confidence_prose_italic(
            chars,
            paragraphs[paragraph_id].size,
        )
        if (
            text is not None
            and _SUBSECTION_ITALIC_LABEL_RE.fullmatch(text) is not None
            and paragraphs[paragraph_id].region_kind != "title"
        ):
            text = None
        forced_styled_prose = any(
            bool(getattr(char, _FORCED_STYLED_PROSE_ATTR, False)) for char in chars
        )
        if text is not None and (
            forced_styled_prose
            or _has_inline_prose_context(segments[paragraph_id], formula_id, text)
        ):
            candidates[formula_id] = text
    return candidates


def _restore_italic_affiliation_layout(
    segments, formulas, paragraphs, candidates, page_height
):
    """Recover body size and separate rows for numbered italic institutions.

    These blocks otherwise contain only small superscript digits in prose;
    the actual 9–10 point addresses live inside recovered italic runs.
    """
    for source, paragraph in zip(segments, paragraphs):
        if paragraph.page_id != 0 or paragraph.y0 < page_height * 0.5:
            continue
        matches = list(re.finditer(r"(\d{1,2})\s*\{v(\d+)\}\s*", source))
        if (
            len(matches) < 2
            or matches[0].start() != 0
            or "".join(match.group() for match in matches) != source
            or [int(match[1]) for match in matches] != list(range(1, len(matches) + 1))
        ):
            continue
        runs = []
        for match in matches:
            index = int(match[2])
            if index not in candidates or index >= len(formulas):
                break
            chars = formulas[index]
            if _standalone_italic_affiliation(chars, source, paragraph) is None:
                break
            runs.append(chars)
        if len(runs) != len(matches):
            continue
        size = float(np.median([char.size for run in runs for char in run]))
        baselines = [_char_baseline(run[0]) for run in runs]
        if any(
            not 0.8 * size <= a - b <= 2.2 * size
            for a, b in zip(baselines, baselines[1:])
        ):
            continue
        paragraph.size = size
        paragraph.y = runs[0][0].y0
        paragraph.x = paragraph.x0
        paragraph.brk = True
        paragraph.affiliation_parts = tuple(
            SegmentPart(ROLE_AFFILIATION, match.group().strip(), break_before=index > 0)
            for index, match in enumerate(matches)
        )


def _collect_reference_title_italic_runs(
    formula_runs: list[list[LTChar]],
    formula_paragraphs: list[int],
    paragraphs: list["Paragraph"],
    segments: list[str],
) -> dict[int, str]:
    """Recover italic book/report titles hidden behind formula placeholders.

    Bibliographies often italicize both work titles and journal metadata.  Only
    release a run when it immediately follows a complete numbered-reference
    author block.  Venue runs follow an already visible title and therefore do
    not satisfy that boundary.  Numeric/identifier-bearing runs remain protected.
    """

    candidates: dict[int, str] = {}
    formula_texts = ["".join(ch.get_text() for ch in run) for run in formula_runs]
    for formula_id, (chars, paragraph_id) in enumerate(
        zip(formula_runs, formula_paragraphs, strict=True)
    ):
        if not 0 <= paragraph_id < len(paragraphs):
            continue
        segment = segments[paragraph_id]
        marker = re.compile(rf"\{{\s*v\s*{formula_id}\s*\}}", re.IGNORECASE)
        match = marker.search(segment)
        if match is None or len(marker.findall(segment)) != 1:
            continue
        preceding_labels = find_reference_markers(
            segment[: match.start()], formula_texts
        )
        if not preceding_labels:
            continue
        entry_start = preceding_labels[-1].start
        entry_prefix = segment[entry_start : match.start()]
        # The same italic title may be split into multiple PDF font runs at a
        # source line break.  Previously accepted runs still belong to this title.
        author_prefix = _FORMULA_MARKER_RE.sub(
            lambda m: (
                "" if int(re.sub(r"\s+", "", m.group(1))) in candidates else m.group(0)
            ),
            entry_prefix,
        )
        year_before_title = re.search(r"\d[a-z]?\s*$", author_prefix)
        publisher_after_title = re.match(
            r"\s*\([^()\r\n]{1,100}:\s*"
            r"[^()\r\n]*(?:Wiley|Springer|Elsevier|Press|Publishers?|Publishing)"
            r"[^()\r\n]*\)",
            segment[match.end() :],
            re.IGNORECASE,
        )
        if not looks_like_reference_title_prefix(author_prefix) or (
            year_before_title and publisher_after_title is None
        ):
            continue
        visible = [char for char in chars if char.get_text()]
        if not visible or any(
            getattr(char, "_pdf2zh_layout_class", 0) == 0 for char in visible
        ):
            continue
        # A roman series/publisher parenthesis can share the final formula run
        # with an italic book title.  Expose it for boundary detection, while
        # the title extractor leaves that suffix verbatim.
        first_roman = next(
            (
                i
                for i, char in enumerate(visible)
                if not _is_prose_italic_font(char.fontname)
            ),
            len(visible),
        )
        if first_roman < len(visible) and (
            first_roman == 0
            or not _reconstruct_italic_run(visible[first_roman:])
            .lstrip()
            .startswith("(")
        ):
            continue
        text = _reconstruct_italic_run(chars)
        # An italic author abbreviation is metadata, not the work's title.
        # Releasing it here makes the title extractor choose "et al" and leave
        # the actual title untranslated, particularly with tight/superscript labels.
        if re.fullmatch(r"et\s*al\.?", text.strip(), re.IGNORECASE):
            continue
        words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
        if (
            not words
            or len(text) > 240
            or re.search(
                r"\b(?:doi|isbn|issn|arxiv|vol(?:ume)?|pages?)\b|"
                r"(?:19|20)\d{2}|10\.\d{4,9}/",
                text,
                re.IGNORECASE,
            )
        ):
            continue
        if any(
            _is_non_horizontal_text_matrix(
                getattr(char, "_pdf2zh_source_text_state", None).matrix
                if getattr(char, "_pdf2zh_source_text_state", None) is not None
                else char.matrix
            )
            for char in visible
        ):
            continue
        sizes = [float(char.size) for char in visible]
        paragraph_size = float(paragraphs[paragraph_id].size)
        if (
            min(sizes) <= 0
            or max(sizes) / min(sizes) > 1.08
            or paragraph_size <= 0
            or not 0.88 <= float(np.median(sizes)) / paragraph_size <= 1.12
        ):
            continue
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


def _join_reference_title_styles(text: str) -> str:
    block = r"\[\[PDF2ZH_ITALIC_\d+_BEGIN\]\].*?\[\[PDF2ZH_ITALIC_\d+_END\]\]"

    def join(match: re.Match[str]) -> str:
        value = match.group(0)
        formula_id = re.search(r"ITALIC_(\d+)_BEGIN", value).group(1)
        content = re.sub(
            r"\[\[PDF2ZH_ITALIC_\d+_END\]\]\s*\[\[PDF2ZH_ITALIC_\d+_BEGIN\]\]",
            " ",
            value,
        )
        return re.sub(
            r"\[\[PDF2ZH_ITALIC_\d+_END\]\]$",
            f"[[PDF2ZH_ITALIC_{formula_id}_END]]",
            content,
        )

    return re.sub(rf"{block}(?:\s*{block})+", join, text)


def _gen_target_text_op(
    font: str,
    size: float,
    x: float,
    y: float,
    rtxt: str,
    italic: bool = False,
) -> str:
    matrix = f"1 0 {ITALIC_SHEAR:f} 1" if italic else "1 0 0 1"
    return f"/{font} {size:f} Tf {matrix} " f"{x:f} {y:f} Tm [<{rtxt}>] TJ "


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


def _is_cjk_ideograph(character: str) -> bool:
    return bool(character) and (
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
    )


def _rebalance_cjk_title_orphan(
    text: str,
    *,
    line_capacity: float,
    character_advance: Callable[[str], float],
    minimum_last_line: int = 4,
) -> str:
    """Move a short CJK suffix to avoid a one/two-character title line.

    This is deliberately limited to unmarked text; formula/style tokens and
    explicit author-supplied newlines retain their exact physical ownership.
    The caller applies it only to detector-confirmed title regions.
    """

    if (
        line_capacity <= 0
        or minimum_last_line < 2
        or "\n" in text
        or "\r" in text
        or _FORMULA_MARKER_RE.search(text)
        or ITALIC_TAG_PREFIX in text
        or FLOW_TOKEN_PREFIX in text
    ):
        return text

    lines: list[tuple[int, int, float]] = []
    line_start = 0
    line_width = 0.0
    for index, character in enumerate(text):
        advance = max(0.0, float(character_advance(character)))
        if index > line_start and line_width + advance > line_capacity:
            lines.append((line_start, index, line_width))
            line_start = index
            line_width = 0.0
        line_width += advance
    lines.append((line_start, len(text), line_width))
    if len(lines) < 2:
        return text

    previous_start, previous_end, _ = lines[-2]
    last_start, last_end, last_width = lines[-1]
    last_count = sum(
        _is_cjk_ideograph(character) for character in text[last_start:last_end]
    )
    if not 0 < last_count < minimum_last_line:
        return text

    move_start = previous_end
    moved = 0
    new_last_width = last_width
    while move_start > previous_start and last_count + moved < minimum_last_line:
        character = text[move_start - 1]
        if not _is_cjk_ideograph(character):
            break
        advance = max(0.0, float(character_advance(character)))
        if new_last_width + advance > line_capacity:
            break
        move_start -= 1
        moved += 1
        new_last_width += advance

    previous_remaining = sum(
        _is_cjk_ideograph(character) for character in text[previous_start:move_start]
    )
    if last_count + moved < minimum_last_line or previous_remaining < minimum_last_line:
        return text
    return text[:move_start].rstrip() + "\n" + text[move_start:].lstrip()


_SUSPENDED_HYPHEN_CONTINUATION_INITIALS = frozenset("aot")


def _should_insert_reconstructed_line_space(
    preceding_text: str,
    next_character: str,
    preceding_formula_text: str = "",
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
    if fragment is None and re.search(r"\{v\d+\}\s*$", preceding_text):
        fragment = re.search(
            r"[A-Za-z][A-Za-z0-9]{1,31}-$",
            preceding_formula_text,
        )
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


_MIN_COMPACT_LINE_HEIGHT = 1.15


def _expand_subsection_title_bounds(segments, paragraphs, page_width):
    """Use the following text column's empty width for a short section title."""
    for text, heading in zip(segments, paragraphs):
        if (
            heading.region_kind != "title"
            or _SUBSECTION_ITALIC_LABEL_RE.fullmatch(text.strip()) is None
        ):
            continue
        em = heading.size
        candidates = [
            p
            for p in paragraphs
            if p is not heading
            and p.region_kind == "plain text"
            and abs(p.x0 - heading.x0) <= em
            and -0.2 * em <= heading.y0 - p.y1 <= 4 * em
            and heading.x1 <= p.x1
            and p.x1 - p.x0 < page_width * 0.58
        ]
        if candidates:
            following = min(candidates, key=lambda p: abs(heading.y0 - p.y1))
            heading.x1 = following.x1


def _fit_target_vertical_layout(
    *,
    line_count: int,
    font_size: float,
    available_height: float,
    default_line_height: float,
) -> TargetVerticalFit:
    """Choose readable leading, then uniformly shrink an overfull paragraph.

    CJK glyphs occupy nearly a full em. Leading below 1.15 makes dense lines
    touch or overlap, especially beside inline superscripts. Keep readable
    leading and uniformly scale the movable
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
    minimum_line_height = min(
        default_line_height, _MIN_COMPACT_LINE_HEIGHT if line_count > 1 else 0.95
    )
    while (
        line_count * font_size * line_height > available_height
        and line_height > minimum_line_height
    ):
        line_height = max(
            minimum_line_height,
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


def _has_reversed_text_matrix(matrix: Matrix) -> bool:
    """Detect reflected glyph axes, including horizontal mirrored symbols."""
    a, _, _, d, _, _ = matrix
    return a < 0 or d < 0


def _requires_source_text_transform(
    state: OriginalTextState,
    tolerance: float = 1e-6,
) -> bool:
    """Keep transforms that one horizontal font size cannot reproduce.

    This is a rendering decision for glyphs already protected by layout.  A
    stretched or sheared prose font alone must not prevent its translation.
    """
    a, b, c, d, _, _ = state.matrix
    scale = max(abs(a), abs(b), abs(c), abs(d), tolerance)
    return (
        _has_reversed_text_matrix(state.matrix)
        or abs(b) > scale * tolerance
        or abs(c) > scale * tolerance
        or abs(a - d) > scale * tolerance
        or abs(state.scaling - 1.0) > tolerance
    )


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


def _source_fill_color_op(char: LTChar) -> str:
    """Keep the device or named fill color of an original protected glyph."""
    color = getattr(getattr(char, "graphicstate", None), "ncolor", None)
    colorspace = getattr(char, "ncs", None)
    name = getattr(colorspace, "name", None)
    expected = {"DeviceGray": (1, "g"), "DeviceRGB": (3, "rg"), "DeviceCMYK": (4, "k")}
    resource_name = getattr(colorspace, "_pdf2zh_resource_name", None)
    prefix = ""
    if name in ("ICCBased", "DeviceN") and isinstance(resource_name, str):
        # PDF names escape delimiters and arbitrary bytes using #XX, not string
        # quoting.  The page or figure retains this source resource dictionary.
        encoded_name = "".join(
            (
                chr(value)
                if 33 <= value <= 126 and chr(value) not in "()<>[]{}/%#"
                else f"#{value:02X}"
            )
            for value in resource_name.encode("utf-8")
        )
        prefix = f"/{encoded_name} cs "
        expected[name] = (colorspace.ncomponents, "scn")
    if name not in expected or color is None:
        return ""
    count, operator = expected[name]
    components = color if isinstance(color, (tuple, list)) else (color,)
    if len(components) != count:
        return ""
    try:
        return (
            prefix
            + " ".join(f"{float(value):f}" for value in components)
            + f" {operator} "
        )
    except (TypeError, ValueError):
        return ""


def _with_source_fill_color(ops: str, fill_color: str) -> str:
    # Color belongs to this source glyph, not the following translated text.
    return f"q {fill_color}{ops}Q " if fill_color else ops


class PDFConverterEx(PDFConverter):
    def __init__(
        self,
        rsrcmgr: PDFResourceManager,
    ) -> None:
        PDFConverter.__init__(self, rsrcmgr, None, "utf-8", 1, None)

    def preserve_source_stroke(self, start, end) -> bool:
        """Keep graph strokes and complete outlines in their original stream."""
        page_id = int(self.cur_item.pageid)
        (x0, y0), (x1, y1) = sorted((start, end))
        for stroke in getattr(self, "source_closed_box_strokes", {}).get(page_id, ()):
            if all(abs(a - b) <= 0.01 for a, b in zip((x0, y0, x1, y1), stroke)):
                return True
        layout = getattr(self, "layout", {}).get(page_id)
        if layout is None:
            return False
        height, width = layout.shape
        samples = (start, end, ((x0 + x1) / 2, (y0 + y1) / 2))
        if any(
            layout[int(np.clip(y, 0, height - 1)), int(np.clip(x, 0, width - 1))] != 0
            for x, y in samples
        ):
            return False
        for detection in getattr(self, "layout_detections", {}).get(page_id, ()):
            if detection.class_name not in {"figure", "table"}:
                continue
            left, bottom, right, top = detection.bounds
            if all(left <= x <= right and bottom <= y <= top for x, y in samples):
                # Preserve the original opacity, blend mode, clipping, cap,
                # color, and transform together instead of rebuilding a rule.
                return True
        return False

    def mark_uri_underline(self, start, end, graphicstate) -> str:
        """Mark only a thin URI text underline; decide removal after reflow."""
        # A Form XObject may contain ordinary publisher prose. Its LTFigure
        # container is not a scientific-figure classification; the final
        # paragraph's layout class determines whether the stroke can go.
        width = float(graphicstate.linewidth or 0)
        color = graphicstate.scolor
        if (
            not 0 < width <= 1.0
            or (graphicstate.dash and graphicstate.dash[0])
            or not isinstance(color, (tuple, list))
            or len(color) != 3
            or max(color) - min(color) < 0.05
            or abs(start[1] - end[1]) > 0.02
        ):
            return ""
        left, right = sorted((float(start[0]), float(end[0])))
        y = float(start[1])
        page_id = int(self.cur_item.pageid)
        for _, rect in getattr(self, "source_uri_links", {}).get(page_id, ()):
            x0, y0, x1, y1 = rect
            if (
                3 <= y1 - y0 <= 16
                and x1 - x0 >= 5
                and abs(left - x0) <= 0.1
                and abs(right - x1) <= 0.1
                and 0 <= y - y0 <= 1.0
            ):
                records = getattr(self, "_uri_underline_records", None)
                if records is None:
                    records = self._uri_underline_records = []
                marker = f"%PDF2ZH_URI_UNDERLINE_{len(records)}%"
                records.append((page_id, tuple(rect)))
                return marker
        return ""

    def _record_reflowed_uri_prose(self, page_id, sources, targets, paragraphs):
        changed = getattr(self, "_reflowed_uri_prose", None)
        if changed is None:
            changed = self._reflowed_uri_prose = []
        authors = getattr(self, "_reflowed_author_addresses", None)
        if authors is None:
            authors = self._reflowed_author_addresses = []
        for source, target, paragraph in zip(sources, targets, paragraphs):
            if source != target and getattr(paragraph, "affiliation_parts", ()):
                authors.append(
                    (
                        int(page_id),
                        (paragraph.x0, paragraph.y0, paragraph.x1, paragraph.y1),
                    )
                )
            if (
                source != target
                and paragraph.brk
                and paragraph.layout_class != 0
                and paragraph.region_kind == "plain text"
            ):
                changed.append(
                    (
                        int(page_id),
                        (paragraph.x0, paragraph.y0, paragraph.x1, paragraph.y1),
                    )
                )

    def mark_orcid_badge_path(self, bounds):
        page_id = int(self.cur_item.pageid)
        x0, y0, x1, y1 = bounds
        for a, b, c, d in getattr(self, "source_orcid_badges", {}).get(page_id, ()):
            if a - 0.05 <= x0 <= x1 <= c + 0.05 and b - 0.05 <= y0 <= y1 <= d + 0.05:
                records = getattr(self, "_orcid_badge_records", None)
                if records is None:
                    records = self._orcid_badge_records = []
                marker = f"%PDF2ZH_ORCID_BADGE_{len(records)}%"
                records.append((page_id, (a, b, c, d)))
                return marker
        return ""

    def resolve_uri_underline_markers(self, operations: str) -> str:
        """Remove stale decorations only after their ordinary prose changed."""
        records = getattr(self, "_uri_underline_records", ())
        changed = getattr(self, "_reflowed_uri_prose", ())

        def resolve(match):
            page_id, rect = records[int(match.group(1))]
            x0, y0, x1, y1 = rect
            for changed_page, bounds in changed:
                a, b, c, d = bounds
                if changed_page == page_id and (
                    a - 0.1 <= x0 <= x1 <= c + 0.1 and b - 1 <= y0 <= y1 <= d + 1
                ):
                    return "n"
            return "S"

        operations = re.sub(r"%PDF2ZH_URI_UNDERLINE_(\d+)%\s*S\b", resolve, operations)
        badges = getattr(self, "_orcid_badge_records", ())

        def resolve_badge(match):
            page_id, (x0, y0, x1, y1) = badges[int(match[1])]
            for changed_page, (a, b, c, d) in getattr(
                self, "_reflowed_author_addresses", ()
            ):
                if (
                    changed_page == page_id
                    and a - 0.1 <= x0 <= x1 <= c + 0.1
                    and b - 1 <= y0 <= y1 <= d + 1
                ):
                    return "n"
            return match[2]

        return re.sub(
            r"%PDF2ZH_ORCID_BADGE_(\d+)%\s*(f\*?|F)(?=\s|$)", resolve_badge, operations
        )

    def begin_page(self, page, ctm) -> None:
        # 重载替换 cropbox
        x0, y0, x1, y1 = page.cropbox
        x0, y0 = apply_matrix_pt(ctm, (x0, y0))
        x1, y1 = apply_matrix_pt(ctm, (x1, y1))
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
        # Ligatures are one source glyph but ordinary prose characters. Expose
        # their letters to translation without changing the source CID/width.
        text = text.translate(
            str.maketrans(
                {
                    "\ufb00": "ff",
                    "\ufb01": "fi",
                    "\ufb02": "fl",
                    "\ufb03": "ffi",
                    "\ufb04": "ffl",
                    "\ufb05": "st",
                    "\ufb06": "st",
                }
            )
        )
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
        item._pdf2zh_source_render_mode = getattr(self, "_source_render_mode", 0)
        self.cur_item.add(item)
        item.cid = cid  # hack 插入原字符编码
        item.font = font  # hack 插入原字符字体
        return item.adv

    def render_string(self, textstate, seq, ncs, graphicstate) -> None:
        previous = getattr(self, "_source_render_mode", 0)
        self._source_render_mode = textstate.render
        try:
            return super().render_string(textstate, seq, ncs, graphicstate)
        finally:
            self._source_render_mode = previous


def _collect_formula_annotation_contexts(
    paragraphs, formulas
) -> dict[int, dict[str, str]]:
    """Read nearby protected glyphs without merging them into a label's layout."""
    protected = [
        ch
        for formula in formulas
        for ch in formula
        if getattr(ch, "_pdf2zh_layout_class", None) == 0
    ]
    contexts = {}
    for index, paragraph in enumerate(paragraphs):
        if paragraph.region_kind != "formula_annotation":
            continue
        em = max(paragraph.size, 1.0)
        row = [
            ch
            for ch in protected
            if (
                abs(_char_baseline(ch) - paragraph.y) <= 0.65 * em
                or (
                    re.match(r"\\(?:bigg?|Bigg?)[lr]?[()[\]]", ch.get_text())
                    and abs(ch.y0 - paragraph.y) <= 2 * em
                )
            )
            and ch.size <= 1.25 * em
        ]

        def side(left):
            candidates = sorted(
                [
                    ch
                    for ch in row
                    if (
                        ch.x1 <= paragraph.x0 + 0.05
                        if left
                        else ch.x0 >= paragraph.x1 - 0.05
                    )
                ],
                key=lambda ch: (ch.x0, -ch.y0),
                reverse=left,
            )
            edge = paragraph.x0 if left else paragraph.x1
            selected = []
            for ch in candidates:
                gap = edge - ch.x1 if left else ch.x0 - edge
                if gap > 2.5 * em or len(selected) >= 80:
                    break
                selected.append(ch)
                edge = min(edge, ch.x0) if left else max(edge, ch.x1)
                if ch.get_text().strip() in ({"(", "["} if left else {")", "]"}):
                    break
            return "".join(
                ch.get_text() for ch in sorted(selected, key=lambda ch: ch.x0)
            ).strip()

        left, right = side(True), side(False)
        meaningful = lambda text: bool(re.search(r"[\w\\≤≥∼=]", text))
        position = (
            "between-math"
            if meaningful(left) and meaningful(right)
            else (
                "before-math"
                if meaningful(right)
                else "after-math" if meaningful(left) else "label"
            )
        )
        contexts[index] = {"position": position, "left_math": left, "right_math": right}
    return contexts


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
        self.formula_annotation_context: dict[str, str] = {}
        self.affiliation_parts: tuple[SegmentPart, ...] = ()


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


def _classify_running_header_regions(
    sstk: list[str],
    pstk: list[Paragraph],
    *,
    page_width: float,
    page_height: float,
) -> tuple[int, ...]:
    """Mark top-margin running-head fragments with one structural kind."""

    if len(sstk) != len(pstk):
        raise ValueError(
            f"paragraph text/property count mismatch: {len(sstk)} != {len(pstk)}"
        )
    classified: list[int] = []
    for index, (text, paragraph) in enumerate(zip(sstk, pstk, strict=True)):
        source = SourceSegment(
            index=index,
            text=text,
            x0=paragraph.x0,
            x1=paragraph.x1,
            y0=paragraph.y0,
            y1=paragraph.y1,
            size=paragraph.size,
            page_width=page_width,
            page_height=page_height,
            break_offsets=tuple(paragraph.break_offsets),
            region_kind=paragraph.region_kind,
            page_index=paragraph.page_id,
        )
        if is_running_header_segment(source):
            paragraph.region_kind = RUNNING_HEADER_REGION_KIND
            classified.append(index)
    return tuple(classified)


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
        chatgpt_task_id: str = None,
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
        translators = [GoogleTranslator, BingTranslator, DeepLTranslator, DeepLXTranslator, OllamaTranslator, XinferenceTranslator, AzureOpenAITranslator,
                       OpenAITranslator, ZhipuTranslator, ModelScopeTranslator, SiliconTranslator, GeminiTranslator, AzureTranslator, TencentTranslator, DifyTranslator, AnythingLLMTranslator, ArgosTranslator, GrokTranslator, GroqTranslator, DeepseekTranslator, OpenAIlikedTranslator, CodexTranslator, QwenMtTranslator]
        if service_name == "chatgpt-browser":
            from pdf2zh.chatgpt import ChatGPTTranslator
            translators.append(ChatGPTTranslator)
        for translator in translators:
            if service_name == translator.name:
                task_options = {"task_id": chatgpt_task_id} if service_name == "chatgpt-browser" and chatgpt_task_id is not None else {}
                self.translator = translator(lang_in, lang_out, service_model, envs=envs, prompt=prompt, ignore_cache=ignore_cache, **task_options)
        if not self.translator:
            raise ValueError("Unsupported translation service")
        if _supports_scientific_translation(self.translator):
            self.translator.set_concurrency(self.thread)
        self.translation_policy = DocumentTranslationPolicy()
        self.layout_region_types: dict[int, dict[int, str]] = {}
        self._pending_page: PageLayoutDraft | None = None
        self._completed_page_xrefs: list[int] = []
        self._flow_token_counter = 0

    def _check_cancelled(self) -> None:
        event = getattr(self, "cancellation_event", None)
        if event is not None and event.is_set():
            raise CancelledError("task cancelled")

    def _page_translation_started(self, page_number: int) -> None:
        self._check_cancelled()
        callback = getattr(self, "page_translation_callback", None)
        if callback is not None:
            callback(page_number)

    def _page_translation_completed(self, page_xref: int) -> None:
        self._check_cancelled()
        if not hasattr(self, "_completed_page_xrefs"):
            self._completed_page_xrefs = []
        self._completed_page_xrefs.append(page_xref)

    def take_completed_page_xrefs(self) -> tuple[int, ...]:
        """Drain after the interpreter has attached the returned page operators."""
        completed = tuple(getattr(self, "_completed_page_xrefs", ()))
        self._completed_page_xrefs = []
        return completed

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

        @retry(
            wait=wait_fixed(1), retry=retry_if_exception(_retryable_translation_error)
        )
        def worker(s: str):
            self._check_cancelled()
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

        @retry(
            wait=wait_fixed(1),
            stop=stop_after_attempt(3),
            retry=retry_if_exception(_retryable_translation_error),
        )
        def batch_worker(texts: list[str]):
            self._check_cancelled()
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

        @retry(
            wait=wait_fixed(1),
            stop=stop_after_attempt(3),
            retry=retry_if_exception(_retryable_translation_error),
        )
        def contextual_batch_worker(
            texts: list[str],
            contexts: list[dict[str, str]],
        ):
            self._check_cancelled()
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

        if _supports_scientific_translation(self.translator):
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
            futures = [executor.submit(copy_context().run, worker, item) for item in sstk]
            results = [future.result() for future in futures]
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
            not _supports_scientific_translation(self.translator)
            or not callable(reference_translator)
        ):
            return normalized_entries
        masked_entries, literal_replacements = self._mask_protected_literal_batch(
            normalized_entries
        )

        @retry(
            wait=wait_fixed(1),
            stop=stop_after_attempt(3),
            retry=retry_if_exception(_retryable_translation_error),
        )
        def reference_worker(
            texts: list[str], contexts: list[str]
        ) -> list[str]:
            self._check_cancelled()
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
        self._check_cancelled()
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
        except (CancelledError, TranslationAbortedError, CodexPermanentError):
            raise
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
        page_height: float = 0.0,
        italic_candidates: dict[int, str] | None = None,
        readonly_formula_contexts: dict[int, str] | None = None,
        reference_title_italic_candidates: dict[int, str] | None = None,
    ) -> list[str]:
        if len(sstk) != len(pstk):
            raise ValueError(
                f"paragraph text/property count mismatch: {len(sstk)} != {len(pstk)}"
            )

        already_english = english_scan_sections_to_preserve(
            sstk,
            scanned=bool(pstk) and pstk[0].page_id in getattr(self, "scan_backgrounds", {}),
            source_language=getattr(self.translator, "lang_in", ""),
            target_language=getattr(self.translator, "lang_out", ""),
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
                page_height=page_height,
                break_offsets=tuple(paragraph.break_offsets),
                region_kind=paragraph.region_kind,
                page_index=paragraph.page_id,
            )
            if segment_index in already_english:
                plans.append(SegmentPlan(source, (SegmentPart(ROLE_PRESERVE, text),)))
                continue
            if (
                (
                    paragraph.region_kind == "figure_caption"
                    or (paragraph.layout_class <= 1 and paragraph.region_kind == "")
                )
                and re.fullmatch(r"[A-Za-z]", text.strip())
            ):
                plans.append(SegmentPlan(source, (SegmentPart(ROLE_PRESERVE, text),)))
                continue
            if paragraph.affiliation_parts:
                plans.append(SegmentPlan(source, paragraph.affiliation_parts))
                continue
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
        annotation_locations: list[tuple[int, int]] = []
        annotation_texts: list[str] = []
        annotation_contexts: list[dict[str, str]] = []
        annotation_translator = getattr(self.translator, "translate_formula_annotations", None)

        for plan_index, plan in enumerate(plans):
            for part_index, part in enumerate(plan.parts):
                if part.role == ROLE_PRESERVE:
                    part_results[plan_index][part_index] = (
                        normalize_protected_literals(part.text)
                    )
                elif part.role in {ROLE_TRANSLATE, ROLE_AFFILIATION}:
                    if (
                        pstk[plan_index].region_kind == "formula_annotation"
                        and _supports_scientific_translation(self.translator)
                        and callable(annotation_translator)
                    ):
                        annotation_locations.append((plan_index, part_index))
                        annotation_texts.append(part.text)
                        annotation_contexts.append(pstk[plan_index].formula_annotation_context)
                        continue
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
                    reference_text, _ = _tag_translatable_italic_formulas(
                        part.text,
                        reference_title_italic_candidates or {},
                    )
                    # Adjacent font runs are one continuous book title.  Keep
                    # one style pair so translation sees the complete phrase.
                    reference_text = _join_reference_title_styles(reference_text)
                    reference_locations.append((plan_index, part_index))
                    reference_texts.append(reference_text)
                    reference_cache_contexts.append(
                        formula_cache_signature(part.text, formula_texts)
                    )
                else:
                    raise ValueError(f"Unsupported translation role: {part.role}")

        if annotation_texts:
            self._check_cancelled()
            annotation_results = self._validate_batch_result(
                annotation_translator(annotation_texts, annotation_contexts),
                len(annotation_texts),
                "translate_formula_annotations",
            )
            for (plan_index, part_index), result in zip(
                annotation_locations, annotation_results, strict=True
            ):
                part_results[plan_index][part_index] = result

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
        for location, translated, formula_context, tagged_source in zip(
            styled_locations,
            styled_results,
            styled_formula_contexts,
            styled_texts,
            strict=True,
        ):
            plan_index, part_index, role, source_text = location
            if translated is None:
                # If Codex repeatedly damages a style marker, expose the styled
                # wording as ordinary prose for one final validated translation.
                # Losing emphasis is preferable to silently restoring an English
                # phrase through the opaque {vN} formula path.
                plain_source = ITALIC_TAG_RE.sub("", tagged_source)
                translated = self._translate_text_segments(
                    [plain_source],
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
            inline_affiliations = plan.source.region_kind == "footnote" and all(
                part.role == ROLE_AFFILIATION for part in plan.parts
            )
            for part, translated in zip(plan.parts, results, strict=True):
                if translated is None:
                    raise ValueError("A planned translation part has no result")
                if part.break_before and pieces:
                    preceding = "".join(pieces).rstrip(" \t\u00a0")
                    pieces = [preceding]
                    if preceding and not preceding.endswith(("\n", "\r")):
                        # Compact footnote addresses share a physical row.
                        # Forced rows shrink them unnecessarily during fitting.
                        pieces.append(" " if inline_affiliations else "\n")
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
                region_kind="affiliation" if paragraph.affiliation_parts else paragraph.region_kind,
                formula_texts=tuple(draft.formula_texts),
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
        self._check_cancelled()
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
        except (CancelledError, TranslationAbortedError, CodexPermanentError):
            raise
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
        self._check_cancelled()
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
            kwargs = {}
            if _FORMULA_MARKER_RE.search(left_entry + right_prefix):
                contexts = []
                for text, draft in ((left_entry, left_draft), (right_prefix, right_draft)):
                    formulas = getattr(draft, "formula_texts", [])
                    context = {}
                    for match in _FORMULA_MARKER_RE.finditer(text):
                        index = int(match.group(1))
                        if index >= len(formulas):
                            return None
                        context[match.group()] = formulas[index]
                    contexts.append(context)
                kwargs["formula_contexts"] = tuple(contexts)
            replacements = translate(left_entry, right_prefix, **kwargs)
        except (CancelledError, TranslationAbortedError, CodexPermanentError):
            raise
        except BaseException as exc:
            if log.isEnabledFor(logging.DEBUG):
                log.exception(exc)
            else:
                log.warning("Reference continuation translation failed: %s", exc)
            return None
        if replacements is None or len(replacements) != 2:
            return None
        left_replacement, right_replacement = replacements
        overrides = []
        for entry, span, replacement in (
            (left_entry, left_span, left_replacement),
            (right_prefix, right_span, right_replacement),
        ):
            if not replacement.source:
                if replacement.translated:
                    return None
                continue
            if entry.count(replacement.source) != 1:
                return None
            start = span.start + entry.index(replacement.source)
            overrides.append(FragmentOverride(
                FragmentSpan(span.ref, start, start + len(replacement.source)),
                replacement.translated,
            ))
        return overrides or None

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
        reference_pairs = [(flow_by_page[pending.page_id], flow_by_page[pending.page_id])]
        if following is not None:
            reference_pairs.append((flow_by_page[pending.page_id], flow_by_page[following.page_id]))
        for previous_flow, following_flow in reference_pairs:
            reference_group = detect_reference_continuation(
                previous_flow,
                following_flow,
                occupied=occupied,
                allow_formulas=True,
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
        for group in detect_reference_note_continuations(
            flow_by_page[pending.page_id], occupied=occupied
        ):
            overrides = self._translate_continuation_group(group, draft_lookup)
            if overrides is not None:
                for override in overrides:
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
            _supports_scientific_translation(self.translator)
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
        self._page_translation_started(pending.page_id + 1)
        self._prepare_continuation_overrides(pending, following)
        self.fontid = dict(pending.fontid)
        self.fontmap = dict(pending.fontmap)
        operations = self.receive_layout(
            pending.ltpage,
            page_xref=pending.page_xref,
            overrides=pending.overrides,
        )
        self._page_translation_completed(pending.page_xref)
        return operations

    def end_page(self, page):
        self._check_cancelled()
        if not self._reading_flow_enabled():
            self._page_translation_started(int(self.cur_item.pageid) + 1)
            operations = self.receive_layout(self.cur_item)
            self._page_translation_completed(int(getattr(page, "page_xref", -1)))
            return operations
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
        self._check_cancelled()
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
        scan_background = getattr(self, "scan_backgrounds", {}).get(ltpage.pageid)
        if scan_background is not None:
            recover_scan_gap_fragments(ltpage, scan_background, self.layout[ltpage.pageid])
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
        toc_layout = detect_toc_layout(ltpage)
        region_types = dict(
            getattr(self, "layout_region_types", {}).get(ltpage.pageid, {})
        )
        toc_class_start = max(region_types, default=1) + 1
        if toc_layout.entries:
            toc_class_start = max(
                toc_class_start, int(np.max(self.layout[ltpage.pageid])) + 1
            )
            region_types.update({
                toc_class_start + index: "toc_entry"
                for index in range(len(toc_layout.entries))
            })

        def vflag(font: str, char: str):    # 匹配公式（和角标）字体
            if isinstance(font, bytes):     # 不一定能 decode，直接转 str
                try:
                    font = font.decode('utf-8')  # 尝试使用 UTF-8 解码
                except UnicodeDecodeError:
                    font = ""
            font = font.split("+")[-1]      # 字体名截断
            if re.match(r"\(cid:|\\", char):
                return True
            # 基于字体名规则的判定
            if self.vfont:
                if re.match(self.vfont, font):
                    return True
            else:
                if _MATH_FONT_RE.search(font) or re.match(              # latex 字体
                    r"(CM(?!R|BX|B\d|CSC)[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Ital|.*Sym|.*Math)",
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
                        or char in "〈〉⟨⟩"  # preserve expectation-value delimiters
                    )
                ):
                    return True
            return False

        ############################################################
        # A. 原文档解析
        _mark_prose_words([child for child in ltpage if isinstance(child, LTChar)])
        split_header_chars = _split_running_header_chars(
            [child for child in ltpage if isinstance(child, LTChar)],
            self.layout[ltpage.pageid],
            float(ltpage.height),
        )
        split_header_chars |= _clipped_figure_word_chars(
            [child for child in ltpage if isinstance(child, LTChar)],
            self.layout[ltpage.pageid],
            getattr(self, "layout_detections", {}).get(ltpage.pageid, ()),
        )
        code_chars = code_block_chars(ltpage)
        identity_chars = orcid_list_chars(ltpage) | author_byline_chars(ltpage)
        recovered_region_classes = _recover_preserved_region_prose(
            [child for child in ltpage if isinstance(child, LTChar)],
            self.layout[ltpage.pageid],
            getattr(self, "layout_detections", {}).get(ltpage.pageid, ()),
            region_types,
            {object_id(char) for char in code_chars | identity_chars},
        )
        footnote_operators = _bridge_recovered_footnote_math(
            [child for child in ltpage if isinstance(child, LTChar)],
            recovered_region_classes,
            region_types,
            {object_id(char) for char in code_chars | identity_chars},
        )
        source_list_markers = _source_list_markers(
            [child for child in ltpage if isinstance(child, LTChar)],
            {object_id(char) for char in code_chars | identity_chars},
        )
        algorithm_displays = _algorithm_display_math_chars(
            [child for child in ltpage if isinstance(child, LTChar)],
            recovered_region_classes,
            region_types,
        )
        boundary_text_classes = _recover_detector_boundary_text(
            [child for child in ltpage if isinstance(child, LTChar)],
            self.layout[ltpage.pageid],
            region_types,
            {object_id(char) for char in code_chars | identity_chars}
            | set(toc_layout.omitted_chars)
            | set(toc_layout.page_number_chars)
            | set(toc_layout.entry_by_char),
            vflag,
        )
        for child in ltpage:
            if isinstance(child, LTChar):
                if object_id(child) in toc_layout.omitted_chars and child not in code_chars:
                    continue
                cur_v = False
                layout = self.layout[ltpage.pageid]
                # ltpage.height 可能是 fig 里面的高度，这里统一用 layout.shape
                h, w = layout.shape
                # 读取当前字符在 layout 中的类别
                cx, cy = np.clip(int(child.x0), 0, w - 1), np.clip(int(child.y0), 0, h - 1)
                cls = layout[cy, cx]
                toc_entry_index = toc_layout.entry_by_char.get(object_id(child))
                child._pdf2zh_preserved_code = child in code_chars
                child._pdf2zh_preserved_identity = child in identity_chars
                if child in code_chars or child in identity_chars or object_id(child) in split_header_chars or child.get_text() in {"■", "□", "▪", "▫", "●", "○", "◆", "◇"}:
                    cls = 0
                elif toc_entry_index is not None:
                    cls = toc_class_start + toc_entry_index
                elif object_id(child) in toc_layout.page_number_chars:
                    cls = 0
                elif object_id(child) in recovered_region_classes:
                    cls = recovered_region_classes[object_id(child)]
                elif object_id(child) in boundary_text_classes:
                    cls = boundary_text_classes[object_id(child)]
                actual_cls = int(cls)
                quote = (
                    _tex_prose_quote(child, actual_cls)
                    if not self.vfont and not self.vchar
                    else None
                )
                child._pdf2zh_prose_quote = quote is not None
                child._pdf2zh_layout_class = actual_cls
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
                elif _should_attach_unlabelled_closing_punctuation(
                    xt, child, xt_cls, actual_cls, logical_region_kind
                ):
                    cls = xt_cls
                elif _attach_overlapping_region_punctuation(
                    xt, child, int(xt_cls), actual_cls, logical_region_kind, candidate_region_kind
                ):
                    # Small overlapping text detections can isolate only the
                    # final quote/space of a reference row. Keeping that glyph
                    # with its touching source row avoids splitting one entry
                    # into title, journal, and year paragraphs.
                    cls = xt_cls
                # Source list markers end the preceding item's reflow even
                # when the detector puts the entire list in one region.
                if object_id(child) in source_list_markers | algorithm_displays:
                    cls = 0
                    # The formula renderer must use the original first glyph
                    # anchor for this preserved run. A later list marker may
                    # lie left of a display in the same PDF content sequence.
                    child._pdf2zh_layout_class = 0
                # 判定当前字符是否属于公式
                if (                                                                                        # 判定当前字符是否属于公式
                    cls == 0                                                                                # 1. 类别为保留区域
                    or getattr(child, "_pdf2zh_scan_fragment", False)
                    or object_id(child) in footnote_operators
                    or (cls == xt_cls and len(sstk[-1].strip()) > 1 and child.size < pstk[-1].size * 0.79)  # 2. 角标字体，有 0.76 的角标和 0.799 的大写，这里用 0.79 取中，同时考虑首字母放大的情况
                    or (vflag(child.fontname, quote or child.get_text())
                        and not (not self.vfont and getattr(child, "_pdf2zh_cm_prose", False)))                                              # 3. 公式字体
                    or _is_non_horizontal_text_matrix(child.matrix)                                      # 4. 旋转文字
                    or _has_reversed_text_matrix(child.matrix)
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
                        continues_runin_prose = False
                        if not cur_v and cls == xt_cls:
                            formula_chars, prose_openers = (
                                _split_trailing_prose_openers(vstk)
                            )
                            prose_suffix = prose_openers
                            (
                                formula_chars,
                                prose_clause,
                                prose_clause_chars,
                            ) = _split_trailing_formula_prose_clause(
                                formula_chars,
                                paragraph_layout_class=int(
                                    pstk[-1].layout_class
                                ),
                            )
                            if prose_clause:
                                paragraph_text_chars[-1].extend(
                                    prose_clause_chars
                                )
                                prose_suffix = prose_clause + prose_suffix
                            (
                                formula_chars,
                                runin_connector,
                                runin_connector_chars,
                            ) = _split_trailing_runin_connector(
                                formula_chars,
                                paragraph_layout_class=int(
                                    pstk[-1].layout_class
                                ),
                            )
                            if runin_connector:
                                paragraph_text_chars[-1].extend(
                                    runin_connector_chars
                                )
                                prose_suffix = runin_connector + prose_suffix
                                continues_runin_prose = True
                        (
                            formula_chars,
                            closing_delimiters,
                            closing_delimiter_chars,
                        ) = _split_punctuation_only_formula_run(
                            formula_chars,
                            has_prose_context=_segment_contains_prose(sstk[-1]),
                            paragraph_layout_class=int(pstk[-1].layout_class),
                        )
                        if closing_delimiters:
                            paragraph_text_chars[-1].extend(
                                closing_delimiter_chars
                            )
                            prose_suffix = closing_delimiters + prose_suffix
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
                        if not cur_v and cls == xt_cls:
                            formula_chars, prose_word, prose_word_chars = (
                                _split_trailing_formula_prose_word(
                                    formula_chars,
                                    has_prose_context=_segment_contains_prose(
                                        sstk[-1]
                                    ),
                                    paragraph_layout_class=int(
                                        pstk[-1].layout_class
                                    ),
                                )
                            )
                            if prose_word:
                                paragraph_text_chars[-1].extend(prose_word_chars)
                                prose_suffix = prose_word + prose_suffix
                        if (
                            sstk[-1] == ""
                            and formula_chars
                            and not continues_runin_prose
                        ):
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
                            preceding_formula_text = ""
                            formula_match = re.search(
                                r"\{v(\d+)\}\s*$",
                                sstk[-1],
                            )
                            if (
                                formula_match is not None
                                and int(formula_match.group(1)) < len(var)
                            ):
                                preceding_formula_text = "".join(
                                    char.get_text()
                                    for char in var[
                                        int(formula_match.group(1))
                                    ]
                                )
                            if _should_insert_reconstructed_line_space(
                                sstk[-1],
                                child.get_text(),
                                preceding_formula_text,
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
                                region_kind=region_types.get(int(cls), ""),
                            )
                        )
                if not cur_v:                                               # 文字入栈
                    if (                                                    # 根据当前字符修正段落属性
                        child.size > pstk[-1].size                          # 1. 当前字符比段落字体大
                        or len(sstk[-1].strip()) == 1                       # 2. 当前字符为段落第二个文字（考虑首字母放大的情况）
                    ) and child.get_text() != " ":                          # 3. 当前字符不是空格
                        pstk[-1].y -= child.size - pstk[-1].size            # 修正段落初始纵坐标，假设两个不同大小字符的上边界对齐
                        pstk[-1].size = child.size
                    sstk[-1] += quote or child.get_text()
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
            (
                formula_chars,
                closing_delimiters,
                closing_delimiter_chars,
            ) = _split_punctuation_only_formula_run(
                formula_chars,
                has_prose_context=_segment_contains_prose(sstk[-1]),
                paragraph_layout_class=int(pstk[-1].layout_class),
            )
            if closing_delimiters:
                paragraph_text_chars[-1].extend(closing_delimiter_chars)
                prose_suffix = closing_delimiters
            (
                formula_chars,
                prose_clause,
                prose_clause_chars,
            ) = _split_trailing_formula_prose_clause(
                formula_chars,
                paragraph_layout_class=int(pstk[-1].layout_class),
            )
            if prose_clause:
                paragraph_text_chars[-1].extend(prose_clause_chars)
                prose_suffix = prose_clause + prose_suffix
            if _segment_contains_prose(sstk[-1]):
                before_punctuation_split = formula_chars
                formula_chars, punctuation_suffix = (
                    _split_trailing_prose_punctuation(formula_chars)
                )
                paragraph_text_chars[-1].extend(
                    before_punctuation_split[len(formula_chars) :]
                )
                prose_suffix = punctuation_suffix + prose_suffix
            formula_chars, prose_word, prose_word_chars = (
                _split_trailing_formula_prose_word(
                    formula_chars,
                    has_prose_context=_segment_contains_prose(sstk[-1]),
                    paragraph_layout_class=int(pstk[-1].layout_class),
                )
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
        for index, paragraph in enumerate(pstk):
            if paragraph.layout_class <= 1:
                continue
            sstk[index], offsets = normalize_source_wraps(
                sstk[index],
                paragraph.break_offsets,
                getattr(self, "source_spelling", None),
            )
            paragraph.break_offsets = list(offsets)
        _merge_overlapping_split_math_islands(
            sstk,
            var,
            varp,
            paragraph_text_chars,
        )
        _release_chemical_direction_qualifiers(
            sstk,
            var,
            varp,
            paragraph_text_chars,
        )
        _release_parenthetical_prose_articles(sstk, var, varp, paragraph_text_chars)
        _release_formula_prose_suffixes(sstk, var, varp, pstk, paragraph_text_chars)
        _release_inline_prose_closers(sstk, var, varp, pstk, paragraph_text_chars)
        _merge_encoded_formula_gaps(sstk, var, varp, pstk, paragraph_text_chars, varl)
        _release_formula_case_explanations(sstk, var, varp, pstk, paragraph_text_chars)
        if not self.vfont and not self.vchar:
            _release_reference_number_labels(sstk, var, varl, varp, pstk, paragraph_text_chars)
            while _merge_adjacent_reference_tail_lines(sstk,var,varp,pstk,paragraph_text_chars):
                pass
            _release_tex_accented_prose_words(sstk, var, varp, pstk, paragraph_text_chars)
            _release_reference_prose_fragments(sstk, var, varp, pstk, paragraph_text_chars)
            _release_standalone_prose_periods(sstk, var, varp, pstk, paragraph_text_chars)
            _restore_drawn_uri_underscores(
                sstk, pstk, paragraph_text_chars, lstk,
                getattr(self, "source_uri_links", {}).get(ltpage.pageid, ()),
            )
            _restore_wrapped_source_uris(
                sstk, pstk, paragraph_text_chars,
                getattr(self, "source_uri_links", {}).get(ltpage.pageid, ()),
            )
        _release_caption_prose_delimiters(sstk, var, varp, pstk)
        _release_caption_title_words(sstk, var, varp, pstk, paragraph_text_chars)
        _release_bold_chemical_names(sstk, var, varp, pstk, paragraph_text_chars)
        _split_parenthetical_variable_definitions(
            sstk, var, varl, varf, varp, pstk, paragraph_text_chars
        )
        _split_inline_formula_items(
            sstk, var, varl, varf, varp, pstk, paragraph_text_chars
        )
        _recover_author_information_affiliations(sstk, var, varp, pstk)
        _release_prose_slash_connectives(sstk, var, varp, pstk)
        for paragraph in pstk:
            if paragraph.region_kind == "toc_entry":
                entry = toc_layout.entries[paragraph.layout_class - toc_class_start]
                paragraph.x1 = entry.x1
                paragraph.y0 = entry.y0
                paragraph.y1 = entry.y1
                paragraph.brk = True
        # Occasionally the source itself wraps a destination number onto the
        # next line at the left margin.  Keep that baseline while restoring the
        # page-number column established by the surrounding contents rows.
        toc_numbers = {
            object_id(char): entry
            for entry in toc_layout.entries
            for char in entry.page_number
        }
        for formula, paragraph_id in zip(var, varp):
            if formula and all(object_id(char) in toc_numbers for char in formula):
                entry = toc_numbers[object_id(formula[0])]
                shift = entry.page_right - max(char.x1 for char in entry.page_number)
                pstk[paragraph_id].x += shift
                pstk[paragraph_id].x0 += shift
                pstk[paragraph_id].x1 += shift
        if not self.vfont:
            _split_formula_prose_boundaries(
                sstk,
                var,
                varl,
                varf,
                varp,
                pstk,
                theorem_only=not _supports_scientific_translation(self.translator),
            )
        if not self.vfont:
            _release_standalone_italic_headings(sstk, var, varp, pstk)
        _expand_subsection_title_bounds(sstk, pstk, float(ltpage.width))
        log.debug("\n==========[VSTACK]==========\n")
        for id, v in enumerate(var):  # 计算公式宽度
            anchor_x, l = _formula_horizontal_geometry(v)
            log.debug(f'< {l:.1f} {v[0].x0:.1f} {v[0].y0:.1f} {v[0].cid} {v[0].fontname} {len(varl[id])} > v{id} = {"".join([ch.get_text() for ch in v])}')
            vx0.append(anchor_x)
            vlen.append(l)

        formula_texts = [
            "".join(ch.get_text() for ch in value) for value in var
        ]
        for paragraph_id, context in _collect_formula_annotation_contexts(pstk, var).items():
            pstk[paragraph_id].formula_annotation_context = context
        running_headers = _classify_running_header_regions(
            sstk,
            pstk,
            page_width=float(ltpage.width),
            page_height=float(ltpage.height),
        )
        if running_headers:
            log.debug("Running-header segments preserved: %s", running_headers)
        hidden_formula_probes = {
            formula_id
            for formula_id, value in enumerate(var)
            if _is_hidden_glyph_repertoire_probe(value, float(ltpage.width))
        }
        italic_candidates = (
            _collect_translatable_italic_runs(var, varp, pstk, sstk)
            if _supports_scientific_translation(self.translator) and not self.vfont
            else {}
        )
        _restore_italic_affiliation_layout(sstk, var, pstk, italic_candidates, float(ltpage.height))
        reference_title_italic_candidates = (
            _collect_reference_title_italic_runs(var, varp, pstk, sstk)
            if _supports_scientific_translation(self.translator) and not self.vfont
            else {}
        )
        if italic_candidates:
            log.debug("Translatable italic formula runs: %s", italic_candidates)
        if reference_title_italic_candidates:
            log.debug(
                "Translatable italic reference titles: %s",
                reference_title_italic_candidates,
            )
        readonly_formula_contexts = (
            _collect_readonly_formula_contexts(
                var,
                varl,
                varp,
                pstk,
                sstk,
                italic_candidates,
            )
            if _supports_scientific_translation(self.translator)
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
            page_height=ltpage.height,
            italic_candidates=italic_candidates,
            readonly_formula_contexts=readonly_formula_contexts,
            reference_title_italic_candidates=reference_title_italic_candidates,
        )
        if replacement_map:
            news = self._restore_fragment_overrides(news, replacement_map)
        if len(source_sstk) != len(news):
            raise ValueError("continuation translation changed segment count")
        _restore_caption_footnote_anchors(
            source_sstk, news, pstk, var, varp, paragraph_text_chars
        )
        self._record_reflowed_uri_prose(ltpage.pageid, source_sstk, news, pstk)

        ############################################################
        # C. 新文档排版
        def raw_string(fcur: str, cstk: str):  # 编码字符串
            if fcur == self.noto_name:
                return "".join(["%04x" % self.noto.has_glyph(ord(c)) for c in cstk])
            elif fcur == getattr(self, "latin_name", None):
                return "".join("%04x" % self.latin.has_glyph(ord(c)) for c in cstk)
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
                if (
                    ord(character) < 256
                    and self.fontmap["tiro"].to_unichr(ord(character)) == character
                ):
                    font_name = "tiro"
            except Exception:
                pass
            if font_name is None:
                font_name = self.noto_name
                latin = getattr(self, "latin", None)
                if (
                    not self.noto.has_glyph(ord(character))
                    and latin is not None
                    and latin.has_glyph(ord(character))
                ):
                    font_name = self.latin_name
            if font_name == self.noto_name:
                advance = self.noto.char_lengths(character, font_size)[0]
            elif font_name == getattr(self, "latin_name", None):
                advance = self.latin.char_lengths(character, font_size)[0]
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
        scan_background = getattr(self, "scan_backgrounds", {}).get(ltpage.pageid)
        scan_masks: list[str] = []

        def gen_op_line(x, y, xlen, ylen, linewidth):
            return f"ET q 1 0 0 1 {x:f} {y:f} cm [] 0 d 0 J {linewidth:f} w 0 0 m {xlen:f} {ylen:f} l S Q BT "

        for id, new in enumerate(news):
            source_chars = list(paragraph_text_chars[id])
            for formula_chars, paragraph_id in zip(var, varp):
                if paragraph_id == id:
                    source_chars.extend(formula_chars)
            scanned_paragraph = (
                scan_background is not None
                and bool(source_chars)
                and all(is_hidden_ocr(char) for char in source_chars)
            )
            if scanned_paragraph:
                if "".join(new.split()) == "".join(source_sstk[id].split()):
                    # The scan already contains protected headers, page numbers,
                    # and untranslated text. Its hidden OCR must stay hidden.
                    continue
                scan_masks.append(scan_line_mask_ops(source_chars, scan_background))
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
            target_right = x0
            log.debug(f"< {y} {x} {x0} {x1} {size} {brk} > {sstk[id]} | {new}")

            if pstk[id].region_kind == "title" and self.translator.lang_out.lower() in {
                "zh",
                "zh-cn",
                "zh-tw",
                "zh-hans",
                "zh-hant",
            }:
                new = _rebalance_cjk_title_orphan(
                    new,
                    line_capacity=x1 - x0 + 0.1 * size,
                    character_advance=lambda character: text_font_and_advance(
                        character,
                        size,
                    )[1],
                )

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
                target_right = max(target_right, visual_end)
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
                    if scanned_paragraph and all(is_hidden_ocr(char) for char in var[vid]):
                        # OCR fonts are often unembedded or have unusable glyph
                        # codes. Move the exact original scan fragment instead.
                        bounds = scan_text_bounds(var[vid], scan_background)
                        ops_vals.append({
                            "type": OpType.SCANNED_FRAGMENT,
                            "bounds": bounds,
                            "x": x + bounds[0] - vx0[vid],
                            "dy": fix + bounds[1] - var[vid][0].y0,
                            "lidx": lidx,
                        })
                    for vch in var[vid]:  # 排版公式字符
                        if scanned_paragraph and is_hidden_ocr(vch):
                            continue
                        vc = chr(vch.cid)
                        source_state = getattr(
                            vch,
                            "_pdf2zh_source_text_state",
                            None,
                        )
                        preserve_source_transform = (
                            source_state is not None
                            and (
                                _is_non_horizontal_text_matrix(source_state.matrix)
                                or _has_reversed_text_matrix(source_state.matrix)
                                or (
                                    getattr(vch, "_pdf2zh_layout_class", None) == 0
                                    and (
                                        _requires_source_text_transform(source_state)
                                        or getattr(vch, "_pdf2zh_preserved_code", False)
                                        or getattr(vch, "_pdf2zh_preserved_identity", False)
                                    )
                                )
                            )
                        )
                        if preserve_source_transform:
                            source_font = self.fontid[vch.font]
                            ops_vals.append({
                                "type": OpType.PRESERVED_TEXT,
                                "font": source_font,
                                "state": source_state,
                                "rtxt": raw_string(source_font, vc),
                                "fill_color": _source_fill_color_op(vch),
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
                            "fill_color": _source_fill_color_op(vch),
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
            if not brk and (
                scanned_paragraph or pstk[id].region_kind == "formula_annotation"
            ):
                render_scale = min(
                    render_scale,
                    horizontal_fit_scale(
                        source_left=x0,
                        source_right=x1,
                        target_right=target_right,
                    ),
                )
            toc_entry = None
            if pstk[id].region_kind == "toc_entry":
                toc_entry = toc_layout.entries[pstk[id].layout_class - toc_class_start]
                # Align the final translated line with the preserved destination
                # page, including entries whose source title spans several lines.
                y = (
                    sum(char.y0 for char in toc_entry.page_number)
                    / len(toc_entry.page_number)
                    + lidx * size * render_scale * line_height
                )
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

            first_line_origin = _paragraph_first_line_origin(id, source_sstk, pstk)

            def render_x(vals):
                origin = first_line_origin if vals["lidx"] == 0 else x0
                return origin + (vals["x"] - origin) * render_scale

            for vals in ops_vals:
                if vals["type"] == OpType.TEXT:
                    ops_list.append(_with_source_fill_color(_gen_target_text_op(
                        vals["font"],
                        vals["size"] * render_scale,
                        render_x(vals),
                        vals["dy"] * render_scale
                        + y
                        - vals["lidx"] * size * render_scale * line_height,
                        vals["rtxt"],
                        vals.get("italic", False),
                    ), vals.get("fill_color", "")))
                elif vals["type"] == OpType.PRESERVED_TEXT:
                    ops_list.append(_with_source_fill_color(
                        _gen_preserved_text_op(vals["font"], vals["state"], vals["rtxt"]),
                        vals.get("fill_color", ""),
                    ))
                elif vals["type"] == OpType.SCANNED_FRAGMENT:
                    ops_list.append(scan_background.fragment_ops(
                        vals["bounds"],
                        render_x(vals),
                        vals["dy"] * render_scale + y
                        - vals["lidx"] * size * render_scale * line_height,
                        render_scale,
                    ))
                elif vals["type"] == OpType.LINE:
                    ops_list.append(gen_op_line(
                        render_x(vals),
                        vals["dy"] * render_scale
                        + y
                        - vals["lidx"] * size * render_scale * line_height,
                        vals["xlen"] * render_scale,
                        vals["ylen"] * render_scale,
                        vals["linewidth"] * render_scale,
                    ))

            if toc_entry is not None:
                ops_list.append(toc_leader_op(
                    toc_entry,
                    x0 + (x - x0) * render_scale,
                    y - lidx * size * render_scale * line_height,
                    size * render_scale,
                ))

        for l in lstk:  # 排版全局线条
            if l.linewidth < 5:  # hack 有的文档会用粗线条当图片背景
                ops_list.append(gen_op_line(l.pts[0][0], l.pts[0][1], l.pts[1][0] - l.pts[0][0], l.pts[1][1] - l.pts[0][1], l.linewidth))

        ops = f"{''.join(scan_masks)}BT {''.join(ops_list)}ET "
        return ops


class OpType(Enum):
    TEXT = "text"
    PRESERVED_TEXT = "preserved_text"
    SCANNED_FRAGMENT = "scanned_fragment"
    LINE = "line"
