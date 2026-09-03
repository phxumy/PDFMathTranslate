"""Conservative quality checks for translated prose."""

from __future__ import annotations

import difflib
import re
import unicodedata

_FORMULA_PLACEHOLDER_RE = re.compile(r"\{\{v\d+\}\}|\{v\d+\}", re.IGNORECASE)
_INTERNAL_MARKER_RE = re.compile(
    r"\[\[PDF2ZH_(?:FLOW|ITALIC|REF_BOUNDARY)[^\]]*\]\]",
    re.IGNORECASE,
)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\r\n]*`")
_DISPLAY_DOLLAR_MATH_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_DOLLAR_MATH_RE = re.compile(r"(?<!\\)\$(?:\\.|[^$\r\n])*?\$")
_PAREN_MATH_RE = re.compile(r"\\\(.*?\\\)", re.DOTALL)
_BRACKET_MATH_RE = re.compile(r"\\\[.*?\\\]", re.DOTALL)
_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(
    r"\b(?:https?://|ftp://|www\.)[^\s<>\[\]{}]+",
    re.IGNORECASE,
)
_DOI_RE = re.compile(
    r"\b(?:doi\s*:\s*)?10\.\d{4,9}/[-._;()/:A-Z0-9]+",
    re.IGNORECASE,
)
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
_VISIBLE_SEPARATOR_RE = re.compile(r"[^\w]+", re.UNICODE)
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;。！？；\r\n]")
_MODEL_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:/-]*")
_LEADING_REFERENCE_NAME_RE = re.compile(
    r"^\s*(?P<name>[^:：\r\n]{1,80}?)\s*[:：]\s*(?P<description>.+)$",
    re.DOTALL,
)
_SOFTWARE_PRODUCT_SUFFIXES = (
    "kit",
    "lab",
    "studio",
    "ware",
    "works",
)
_SOURCE_CROSS_REFERENCE_IDENTIFIER = (
    r"\{\{v\d+\}\}|\{v\d+\}|"
    r"[A-Za-z]\.\d+(?:\.\d+)*[A-Za-z]?|"
    r"[A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?|"
    r"(?-i:[IVXLCDM]+)"
)
_SOURCE_CROSS_REFERENCE_RE = re.compile(
    r"\b(?P<label>figures?|figs?\.?|tables?|tbls?\.?|"
    r"equations?|eqs?\.?|references?|refs?\.?)(?![A-Za-z])"
    r"\s*(?:[（(]\s*)?"
    rf"(?P<identifier>{_SOURCE_CROSS_REFERENCE_IDENTIFIER})(?![A-Za-z0-9])"
    r"(?:\s*[）)])?",
    re.IGNORECASE,
)
_CROSS_REFERENCE_TERMINAL_RE = re.compile(r"^[\s\u00a0]*[。．.!?！？]")
_TARGET_CROSS_REFERENCE_LABELS = {
    "figure": r"图",
    "table": r"表",
    "equation": r"(?:方程|公式|(?<![模方形公算等样])式)",
    "reference": r"(?:参考文献|文献)",
}
_TARGET_CROSS_REFERENCE_CANONICAL_LABELS = {
    "figure": "图",
    "table": "表",
    "equation": "式",
    "reference": "参考文献",
}
_TARGET_CROSS_REFERENCE_SAFE_TAILS = {
    "figure": r"(?:所示|的结果)?",
    "table": r"(?:所示|中)?",
    "equation": r"(?:所示|中|可见)?",
    "reference": r"(?:中报道|中所述|所述)?",
}
_SOURCE_CROSS_REFERENCE_LABELS = {
    "figure": r"fig(?:ure)?s?",
    "table": r"(?:tables?|tbls?)",
    "equation": r"eq(?:uation)?s?",
    "reference": r"(?:refs?|references?)",
}
_SCIENTIFIC_UNIT_SYMBOLS = frozenset(
    {
        "a",
        "cm",
        "ev",
        "fs",
        "gev",
        "ghz",
        "hz",
        "j",
        "k",
        "kev",
        "khz",
        "kj",
        "m",
        "ma",
        "mev",
        "mhz",
        "mk",
        "mm",
        "ms",
        "mv",
        "mw",
        "nm",
        "ns",
        "pa",
        "pm",
        "ps",
        "s",
        "thz",
        "ua",
        "um",
        "us",
        "uv",
        "uw",
        "v",
        "w",
    }
)

# Model translations occasionally duplicate a Chinese structural word at the
# same grammatical slot (for example ``式式 (5)`` for ``Eq. (5)`` or
# ``系统的的响应``).  These patterns intentionally cover only cases where
# deleting one copy is safe.  In particular, ``式式`` is corrected only when it
# is immediately followed by a parenthesized equation label; ordinary phrases
# such as ``显式式子`` are left alone.  The two lexical/grammatical sequences
# ``目的的确`` and ``标的的价值`` are likewise protected from the
# duplicate-particle rule.
_DUPLICATE_EQUATION_DESIGNATOR_RE = re.compile(
    r"式[ \t\u00a0]*式"
    r"(?=[ \t\u00a0]*[（(][ \t\u00a0]*"
    r"(?:[A-Za-z]?\d+(?:[A-Za-z]|[.\-–]\d+)?|[IVXLCDMivxlcdm]+)"
    r"[ \t\u00a0]*[）)])"
)
_DUPLICATE_ATTRIBUTE_PARTICLE_RE = re.compile(
    r"(?<![目标])的[ \t\u00a0]*的(?![ \t\u00a0]*确)"
)
_DUPLICATE_EQUATION_CUE_RE = re.compile(
    r"(?P<cue>在|由|根据|依据|利用|通过|参见)式"
    r"[ \t\u00a0]*(?:在|由|根据|依据|利用|通过|参见)式"
    r"(?=[ \t\u00a0]*[（(])"
)

# These are deliberately limited to unambiguous English grammatical glue.  The
# broader residue checks below need several words to avoid flagging preserved
# terminology, but a single untranslated predicate can still corrupt an
# otherwise Chinese scientific sentence (for example ``{v0} denotes 空间位置``).
_EMBEDDED_ENGLISH_GRAMMAR_GLUE = frozenset(
    {
        "are",
        "be",
        "been",
        "being",
        "called",
        "denote",
        "denoted",
        "denotes",
        "correspond",
        "corresponded",
        "corresponds",
        "is",
        "represent",
        "represented",
        "represents",
        "respectively",
        "so-called",
        "was",
        "were",
        "where",
    }
)

_FUNCTION_WORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "although",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "between",
        "but",
        "by",
        "can",
        "could",
        "despite",
        "did",
        "do",
        "does",
        "during",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "must",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "or",
        "our",
        "ours",
        "over",
        "shall",
        "she",
        "should",
        "since",
        "so",
        "some",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "under",
        "unless",
        "until",
        "up",
        "upon",
        "us",
        "via",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "whether",
        "which",
        "while",
        "who",
        "whom",
        "whose",
        "will",
        "with",
        "within",
        "without",
        "would",
        "you",
        "your",
        "yours",
    }
)


def normalize_cjk_compatibility_ideographs(text: str) -> str:
    """Normalize only characters in the two CJK compatibility blocks."""

    return "".join(
        (
            unicodedata.normalize("NFKC", char)
            if _is_cjk_compatibility_ideograph(char)
            else char
        )
        for char in text
    )


def normalize_cjk_structural_repetitions(text: str) -> str:
    """Remove only high-confidence duplicate Chinese structural words.

    This is deliberately not a general repeated-character normalizer: Chinese
    reduplication is productive, and even ``式式``/``的的`` can occur across a
    legitimate word boundary.  The closed patterns above retain those known
    contexts while repairing malformed translation output before it is cached.
    Formula placeholders and internal layout tokens contain none of the matched
    sequences and therefore remain byte-for-byte unchanged.
    """

    normalized = _DUPLICATE_EQUATION_DESIGNATOR_RE.sub("式", text)
    normalized = _DUPLICATE_EQUATION_CUE_RE.sub(
        lambda match: match.group("cue") + "式",
        normalized,
    )
    return _DUPLICATE_ATTRIBUTE_PARTICLE_RE.sub("的", normalized)


def normalize_scientific_cross_reference_placement(
    source: str,
    target: str,
) -> str | None:
    """Validate and narrowly repair translated Fig./Eq./Ref. number placement.

    The rule is enabled only by an explicit English cross-reference in ``source``.
    A valid Chinese result keeps the same identifier immediately after ``图``,
    ``式``/``方程``/``公式``, or ``参考文献``.  The only automatic repair moves an
    identifier across one erroneous full stop and a small closed set of Chinese
    tails such as ``所示`` or ``中报道``.  Missing labels, arbitrary intervening
    prose, URLs, mathematics, ordinary numbers, and ambiguous placements fail
    closed so the caller can retry the translation.
    """

    masked_source = _mask_cross_reference_opaque_text(source)
    references: list[tuple[str, str, bool]] = []
    for match in _SOURCE_CROSS_REFERENCE_RE.finditer(masked_source):
        raw_label = match.group("label").casefold()
        kind = (
            "figure"
            if raw_label.startswith("fig")
            else (
                "table"
                if raw_label.startswith(("tab", "tbl"))
                else "equation" if raw_label.startswith("eq") else "reference"
            )
        )
        following = masked_source[match.end() :]
        references.append(
            (
                kind,
                source[match.start("identifier") : match.end("identifier")],
                _CROSS_REFERENCE_TERMINAL_RE.match(following) is not None,
            )
        )
    if not references:
        return target

    normalized = target
    consumed: dict[tuple[str, str], int] = {}
    for kind, identifier, source_is_terminal in references:
        normalized = _normalize_duplicate_cross_reference_labels(
            normalized,
            kind,
            identifier,
        )
        normalized = _normalize_english_cross_reference_label(
            normalized,
            kind,
            identifier,
        )
        key = (kind, identifier.casefold())
        used = consumed.get(key, 0)
        correct = _target_cross_reference_pattern(kind, identifier)
        correct_count = len(
            tuple(correct.finditer(_mask_cross_reference_opaque_text(normalized)))
        )
        if correct_count > used:
            consumed[key] = used + 1
            continue
        repaired = _repair_one_misplaced_cross_reference(
            normalized,
            kind,
            identifier,
            source_is_terminal=source_is_terminal,
        )
        if (
            repaired is None
            or correct.search(_mask_cross_reference_opaque_text(repaired)) is None
        ):
            return None
        normalized = repaired
        consumed[key] = used + 1
    normalized = _normalize_terminal_figure_example_artifact(source, normalized)
    return _normalize_simplifying_equation_phrases(source, normalized)


def _mask_cross_reference_opaque_text(text: str) -> str:
    """Mask non-prose while retaining offsets and formula placeholders."""

    masked = text
    for pattern in (
        _FENCED_CODE_RE,
        _INLINE_CODE_RE,
        _DISPLAY_DOLLAR_MATH_RE,
        _INLINE_DOLLAR_MATH_RE,
        _PAREN_MATH_RE,
        _BRACKET_MATH_RE,
        _EMAIL_RE,
        _URL_RE,
        _DOI_RE,
    ):
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def _target_identifier_pattern(identifier: str) -> str:
    escaped = re.escape(identifier)
    if _FORMULA_PLACEHOLDER_RE.fullmatch(identifier):
        return escaped
    # Parentheses are optional, but they must be balanced when present.  The
    # former pair of independent optionals accepted malformed output such as
    # ``式(8中`` and allowed it into the cache as a valid cross-reference.
    return (
        rf"(?<![A-Za-z0-9.])(?:"
        rf"[（(]\s*{escaped}\s*[）)]|{escaped}(?!\s*[）)])"
        # A sentence/caption-final ASCII full stop is punctuation, not part of
        # the identifier.  Still reject decimal/version continuations such as
        # ``1.2`` so a shorter identifier cannot match their prefix.
        rf")(?![A-Za-z0-9]|\.(?=[A-Za-z0-9]))"
    )


def _target_cross_reference_pattern(kind: str, identifier: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:{_TARGET_CROSS_REFERENCE_LABELS[kind]})"
        rf"[\s\u00a0]*{_target_identifier_pattern(identifier)}"
    )


def _normalize_duplicate_cross_reference_labels(
    target: str,
    kind: str,
    identifier: str,
) -> str:
    """Collapse one bilingual or duplicated label for an explicit source ref."""

    target_label = _TARGET_CROSS_REFERENCE_LABELS[kind]
    identifier_pattern = _target_identifier_pattern(identifier)
    bilingual = re.compile(
        rf"(?i)(?<![A-Za-z])(?:{_SOURCE_CROSS_REFERENCE_LABELS[kind]})"
        rf"\.?(?![A-Za-z])\s*(?=(?:{target_label})\s*{identifier_pattern})"
    )
    normalized = bilingual.sub("", target, count=1)
    duplicated = re.compile(
        rf"(?P<label>{target_label})\s*(?:{target_label})"
        rf"(?=\s*{identifier_pattern})"
    )
    return duplicated.sub(lambda match: match.group("label"), normalized, count=1)


def _normalize_english_cross_reference_label(
    target: str,
    kind: str,
    identifier: str,
) -> str:
    """Translate an otherwise correctly placed English reference label locally.

    A complete Chinese sentence that preserves ``Eq. (2.5)`` or ``Figure C.1``
    is safe and useful; rejecting it caused the caller to discard the whole
    translation and restore the English source paragraph.  The source document
    already proves the reference kind and identifier, so replacing only that
    adjacent label is deterministic.
    """

    source_label = _SOURCE_CROSS_REFERENCE_LABELS[kind]
    identifier_pattern = _target_identifier_pattern(identifier)
    pattern = re.compile(
        rf"(?i)(?<![A-Za-z])(?P<label>{source_label})\.?(?![A-Za-z])"
        rf"(?P<space>[\s\u00a0]*)(?P<identifier>{identifier_pattern})"
    )
    canonical = _TARGET_CROSS_REFERENCE_CANONICAL_LABELS[kind]
    return pattern.sub(
        lambda match: canonical + match.group("space") + match.group("identifier"),
        target,
    )


def _repair_one_misplaced_cross_reference(
    target: str,
    kind: str,
    identifier: str,
    *,
    source_is_terminal: bool,
) -> str | None:
    label_pattern = _TARGET_CROSS_REFERENCE_LABELS[kind]
    tail_pattern = _TARGET_CROSS_REFERENCE_SAFE_TAILS[kind]
    if not _FORMULA_PLACEHOLDER_RE.fullmatch(identifier):
        escaped_identifier = re.escape(identifier)
        # If the source supplies the exact scientific reference, balancing one
        # adjacent delimiter is deterministic.  This avoids accepting
        # ``式(8中`` or repeatedly regenerating a formula-dense paragraph.
        missing_close = re.compile(
            rf"(?P<label>{label_pattern})(?P<space>[\s\u00a0]*)"
            rf"(?P<open>[（(])(?P<inner>[\s\u00a0]*)"
            rf"(?P<identifier>{escaped_identifier})(?P<after>[\s\u00a0]*)"
            rf"(?![）)A-Za-z0-9.])"
        )
        missing_close_matches = tuple(
            missing_close.finditer(_mask_cross_reference_opaque_text(target))
        )
        if len(missing_close_matches) == 1:
            match = missing_close_matches[0]
            closing = "）" if match.group("open") == "（" else ")"
            # Some translations move the closing delimiter to the end of a
            # short Chinese clause: ``式(8中所得结果的推广).``.  Relocate that
            # delimiter instead of inserting a second one.
            displaced_close = re.match(
                r"(?P<body>[^()（）\r\n。！？；;]{1,96})"
                r"(?P<close>[）)])(?=[\s\u00a0]*[。．.!！?？,，;；])",
                target[match.end() :],
            )
            if (
                displaced_close is not None
                and displaced_close.group("close") == closing
            ):
                return (
                    target[: match.end()]
                    + closing
                    + displaced_close.group("body")
                    + target[match.end() + displaced_close.end() :]
                )
            return target[: match.end()] + closing + target[match.end() :]

        missing_open = re.compile(
            rf"(?P<label>{label_pattern})(?P<space>[\s\u00a0]*)"
            rf"(?P<identifier>{escaped_identifier})(?P<inner>[\s\u00a0]*)"
            rf"(?P<close>[）)])"
        )
        missing_open_matches = tuple(
            missing_open.finditer(_mask_cross_reference_opaque_text(target))
        )
        if len(missing_open_matches) == 1:
            match = missing_open_matches[0]
            opening = "（" if match.group("close") == "）" else "("
            return (
                target[: match.start("identifier")]
                + opening
                + target[match.start("identifier") :]
            )

    misplaced = re.compile(
        rf"(?P<label>{label_pattern})(?P<tail>{tail_pattern})"
        rf"[\s\u00a0]*[。．.]\s*"
        rf"(?P<identifier>{_target_identifier_pattern(identifier)})"
    )
    matches = tuple(misplaced.finditer(_mask_cross_reference_opaque_text(target)))
    if len(matches) == 1:
        match = matches[0]
        following = target[match.end() :]
        following_has_terminal = (
            _CROSS_REFERENCE_TERMINAL_RE.match(following) is not None
        )
        terminal = "。" if source_is_terminal and not following_has_terminal else ""
        replacement = (
            target[match.start("label") : match.end("label")]
            + target[match.start("identifier") : match.end("identifier")]
            + target[match.start("tail") : match.end("tail")]
            + terminal
        )
        return target[: match.start()] + replacement + target[match.end() :]

    if kind != "equation":
        return None
    # A common Chinese MT failure translates ``in Eq. (6)`` as ``在式中……。
    # (6)``.  Moving the identifier back is safe only within one short clause,
    # with an explicit equation label and the same source identifier.
    delayed = re.compile(
        rf"(?P<prefix>在?)(?P<label>{label_pattern})(?P<tail>中)"
        rf"(?P<body>[^。！？；;\r\n]{{1,96}})"
        rf"[\s\u00a0]*[。．.]\s*"
        rf"(?P<identifier>{_target_identifier_pattern(identifier)})"
    )
    delayed_matches = tuple(delayed.finditer(_mask_cross_reference_opaque_text(target)))
    if len(delayed_matches) != 1:
        return None
    match = delayed_matches[0]
    replacement = (
        target[match.start("prefix") : match.end("prefix")]
        + target[match.start("label") : match.end("label")]
        + target[match.start("identifier") : match.end("identifier")]
        + target[match.start("tail") : match.end("tail")]
        + target[match.start("body") : match.end("body")]
    )
    return target[: match.start()] + replacement + target[match.end() :]


def _normalize_simplifying_equation_phrases(source: str, target: str) -> str:
    """Repair a narrow MT calque of ``Simplifying Eq. (N)`` in Chinese."""

    masked_source = _mask_cross_reference_opaque_text(source)
    identifiers = tuple(
        match.group("identifier")
        for match in re.finditer(
            r"\bsimplif(?:y|ies|ied|ying)\s+"
            r"(?:eq(?:uation)?)\.?(?:\s*[（(])?\s*"
            r"(?P<identifier>\{\{v\d+\}\}|\{v\d+\}|"
            r"[A-Za-z]?\d+(?:\.\d+)?[A-Za-z]?)",
            masked_source,
            re.IGNORECASE,
        )
    )
    normalized = target
    for identifier in identifiers:
        identifier_pattern = _target_identifier_pattern(identifier)
        calque = re.compile(
            rf"化简(?:方程|公式)\s*(?:[。；;，,]\s*)?"
            rf"(?:通过|利用|由)\s*"
            rf"(?:方程|公式|(?<![模方形公算等样])式)\s*"
            rf"(?P<identifier>{identifier_pattern})"
        )
        matches = tuple(calque.finditer(normalized))
        if len(matches) != 1:
            continue
        match = matches[0]
        normalized = (
            normalized[: match.start()]
            + "化简式"
            + normalized[match.start("identifier") : match.end("identifier")]
            + normalized[match.end() :]
        )
    return normalized


def _normalize_terminal_figure_example_artifact(source: str, target: str) -> str:
    """Drop a stray ``中。`` before a translated ``For example`` sentence.

    Some MT results render ``see Fig. 3c. For example, ...`` as
    ``见图3c。中。例如，...``.  Removing that otherwise malformed locative is
    safe only when the source itself proves that the figure reference ends the
    sentence and that the next sentence begins with ``For example``.  Correct
    ``图中`` prose, non-terminal references, and arbitrary target-only occurrences
    therefore remain untouched.
    """

    masked_source = _mask_cross_reference_opaque_text(source)
    identifiers = tuple(
        match.group("identifier")
        for match in re.finditer(
            r"\b(?:see|cf\.)?\s*"
            r"fig(?:ure)?\.?\s*(?:[（(]\s*)?"
            r"(?P<identifier>\{\{v\d+\}\}|\{v\d+\}|"
            r"[A-Za-z]?\d+(?:\.\d+)?[A-Za-z]?)"
            r"(?:\s*[）)])?\s*[.!?]\s*"
            r"for\s+example\b",
            masked_source,
            re.IGNORECASE,
        )
    )
    normalized = target
    for identifier in identifiers:
        reference = _target_cross_reference_pattern("figure", identifier).pattern
        artifact = re.compile(
            rf"(?P<reference>{reference})"
            r"(?P<terminal>[。．.!?])\s*"
            r"中\s*[。．.]\s*(?P<example>例如)"
        )
        matches = tuple(
            artifact.finditer(_mask_cross_reference_opaque_text(normalized))
        )
        if len(matches) != 1:
            continue
        match = matches[0]
        normalized = (
            normalized[: match.start()]
            + normalized[match.start("reference") : match.end("reference")]
            + normalized[match.start("terminal") : match.end("terminal")]
            + normalized[match.start("example") : match.end("example")]
            + normalized[match.end() :]
        )
    return normalized


def _is_cjk_compatibility_ideograph(char: str) -> bool:
    codepoint = ord(char)
    return 0xF900 <= codepoint <= 0xFAFF or 0x2F800 <= codepoint <= 0x2FA1F


def has_suspicious_english_residue(source: str, target: str) -> bool:
    """Return whether translated prose conservatively looks left in English."""

    clean_source = _strip_non_prose(source)
    clean_target = _strip_non_prose(target)
    source_words = _english_words(clean_source)
    target_words = _english_words(clean_target)
    if _is_near_source_copy(
        clean_source,
        clean_target,
        source_words,
    ):
        return True
    return (
        _has_shared_english_clause(source_words, target_words)
        or _is_target_only_english_prose(clean_target, target_words)
        or _has_embedded_english_grammar_glue(clean_source, clean_target)
    )


def has_unchanged_translatable_english(
    source: str,
    target: str,
    *,
    minimum_words: int = 1,
) -> bool:
    """Return whether a translation is an unchanged English source fallback.

    This check is intentionally separate from the broader residue heuristic.
    Short headings and captions may contain too little grammar for that heuristic,
    but an exact source copy must still never become a successful ``ROLE_TRANSLATE``
    cache entry.  Read-only formula/layout tokens and identifiers are removed before
    comparison.  Pure all-capital abbreviations and one-token model identifiers are
    conservatively exempt because translating them would normally be incorrect.
    """

    clean_source = _strip_non_prose(source).strip()
    clean_target = _strip_non_prose(target).strip()
    normalized_source = _normalize_visible_text(clean_source)
    normalized_target = _normalize_visible_text(clean_target)
    if not normalized_source or normalized_source != normalized_target:
        return False
    source_words = tuple(_ENGLISH_WORD_RE.finditer(clean_source))
    if len(source_words) < max(1, minimum_words):
        return False
    return not _is_preserved_english_identifier(clean_source)


def has_suspicious_reference_title_residue(source: str, target: str) -> bool:
    """Apply a stricter complete-or-preserve gate to a cited work title.

    Author, venue, DOI, and page fields are outside the structured title payload,
    so a two-word source clause left inside an otherwise Chinese title is a
    partial translation rather than legitimate bibliography metadata.  Proper
    names and short technical terms below that threshold remain conservative.
    """

    if has_unchanged_translatable_english(
        source,
        target,
    ) or has_suspicious_english_residue(source, target):
        return True

    clean_source = _strip_non_prose(source)
    clean_target = _strip_non_prose(target)
    source_matches = tuple(_ENGLISH_WORD_RE.finditer(clean_source))
    source_words = tuple(match.group(0).casefold() for match in source_matches)
    target_matches = tuple(_ENGLISH_WORD_RE.finditer(clean_target))
    target_words = tuple(match.group(0).casefold() for match in target_matches)
    preserved_product_name = _preserved_reference_product_name(
        clean_source,
        clean_target,
    )
    preserved_product_words = (
        _english_words(preserved_product_name)
        if preserved_product_name is not None
        else ()
    )
    if (
        any(_is_cjk_ideograph(char) for char in clean_target)
        and len(target_matches) == 1
        and target_matches[0].group(0).islower()
        and target_words[0] in source_words
        and not _is_numeric_scientific_unit(clean_target, target_matches[0])
    ):
        # A reference-title payload contains no authors, venue, DOI, or other
        # bibliography metadata.  Consequently a lone ordinary lower-case word
        # copied into an otherwise CJK title is a partial translation, even
        # though it is too short for the general prose residue heuristic.
        return True
    if len(source_words) < 2 or len(target_words) < 2:
        return False
    matcher = difflib.SequenceMatcher(
        None,
        source_words,
        target_words,
        autojunk=False,
    )
    for block in matcher.get_matching_blocks():
        if block.size < 2:
            continue
        if (
            preserved_product_words
            and block.a == 0
            and block.b == 0
            and block.size == len(preserved_product_words)
            and source_words[: block.size] == preserved_product_words
        ):
            continue
        source_clause = tuple(
            match.group(0) for match in source_matches[block.a : block.a + block.size]
        )
        # A fully capitalized named entity with an acronym (for example
        # ``Google Quantum AI``) may legitimately remain in a Chinese title.
        named_entity = (
            all(word[:1].isupper() for word in source_clause)
            and any(word.isupper() for word in source_clause)
            and not _has_function_words(
                tuple(word.casefold() for word in source_clause),
                1,
            )
        )
        if not named_entity:
            return True
    return False


def _preserved_reference_product_name(source: str, target: str) -> str | None:
    """Return a narrowly certified, untranslated leading product/method name.

    Reference titles sometimes have the form ``Product name: translated
    explanation``.  Preserve that short name only when it is the complete
    colon-delimited lead, the explanation is visibly translated, and the title
    supplies an identifier signal: stylized casing/model syntax or conservative
    software-product morphology in its first token.  This avoids treating an
    ordinary multi-word English title prefix as a product.
    """

    source_match = _LEADING_REFERENCE_NAME_RE.match(source)
    target_match = _LEADING_REFERENCE_NAME_RE.match(target)
    if source_match is None or target_match is None:
        return None
    source_name = " ".join(source_match.group("name").split())
    target_name = " ".join(target_match.group("name").split())
    if source_name != target_name:
        return None
    name_tokens = source_name.split()
    name_words = tuple(_ENGLISH_WORD_RE.finditer(source_name))
    if (
        not 1 <= len(name_words) <= 2
        or len(name_tokens) != len(name_words)
        or any(
            token.casefold() in _FUNCTION_WORDS
            for token in (match.group(0) for match in name_words)
        )
        or any(_MODEL_IDENTIFIER_RE.fullmatch(token) is None for token in name_tokens)
    ):
        return None
    source_description = source_match.group("description")
    target_description = target_match.group("description")
    if len(_ENGLISH_WORD_RE.findall(source_description)) < 3 or not any(
        _is_cjk_ideograph(char) for char in target_description
    ):
        return None
    name_has_identifier = any(
        _is_stylized_identifier_token(token) for token in name_tokens
    ) or _is_software_product_brand(name_tokens[0])
    if not name_has_identifier:
        return None
    return source_name


def _is_software_product_brand(token: str) -> bool:
    letters = "".join(char for char in token if char.isalpha())
    folded = letters.casefold()
    return bool(
        letters[:1].isupper()
        and letters[1:].islower()
        and any(
            len(folded) > len(suffix) and folded.endswith(suffix)
            for suffix in _SOFTWARE_PRODUCT_SUFFIXES
        )
    )


def _is_stylized_identifier_token(token: str) -> bool:
    letters = "".join(char for char in token if char.isalpha())
    if not letters:
        return any(char.isdigit() for char in token)
    return bool(
        any(char.isdigit() for char in token)
        or any(char in "._+:/" for char in token)
        or letters.isupper()
        or any(char.isupper() for char in letters[1:])
    )


def _is_numeric_scientific_unit(text: str, match: re.Match[str]) -> bool:
    """Recognize an unchanged SI-style symbol only in an explicit value-unit pair."""

    if match.group(0).casefold() not in _SCIENTIFIC_UNIT_SYMBOLS:
        return False
    return (
        re.search(
            r"(?:\d(?:[\d.,]*\d)?|[)\]}])\s*$",
            text[: match.start()],
        )
        is not None
    )


def has_unchanged_reference_title_fragment(
    source: str,
    target: str,
    *,
    title_source: str | None = None,
    title_target: str | None = None,
) -> bool:
    """Reject an unchanged physical title fragment without harming proper names."""

    clean_source = _strip_non_prose(source).strip()
    clean_target = _strip_non_prose(target).strip()
    if (
        title_source is not None
        and title_target is not None
        and _normalize_visible_text(clean_source)
        == _normalize_visible_text(clean_target)
    ):
        product_name = _preserved_reference_product_name(
            _strip_non_prose(title_source),
            _strip_non_prose(title_target),
        )
        fragment_words = _english_words(clean_source)
        product_words = _english_words(product_name or "")
        if fragment_words and any(
            product_words[index : index + len(fragment_words)] == fragment_words
            for index in range(len(product_words) - len(fragment_words) + 1)
        ):
            return False

    if has_unchanged_translatable_english(
        source,
        target,
        minimum_words=2,
    ):
        return True
    words = tuple(match.group(0) for match in _ENGLISH_WORD_RE.finditer(clean_source))
    return bool(
        len(words) == 1
        and words[0].islower()
        and has_unchanged_translatable_english(source, target)
    )


def _is_preserved_english_identifier(text: str) -> bool:
    compact = text.strip()
    if any(char.isdigit() for char in compact) and _MODEL_IDENTIFIER_RE.fullmatch(
        compact
    ):
        return True

    words = tuple(match.group(0) for match in _ENGLISH_WORD_RE.finditer(text))
    if (
        not words
        or not all(word.isupper() for word in words)
        or any(word.casefold() in _FUNCTION_WORDS for word in words)
    ):
        return False
    if all(len(word) <= 4 for word in words):
        return True
    if len(words) != 1 or len(words[0]) > 8:
        return False
    # Product abbreviations such as PYEPR are longer than a classic three-letter
    # acronym but still do not look like an ordinary all-caps English word.
    return sum(character in "AEIOU" for character in words[0]) <= 1


def _strip_non_prose(text: str) -> str:
    stripped = text
    for pattern in (
        _FENCED_CODE_RE,
        _INLINE_CODE_RE,
        _DISPLAY_DOLLAR_MATH_RE,
        _INLINE_DOLLAR_MATH_RE,
        _PAREN_MATH_RE,
        _BRACKET_MATH_RE,
        _FORMULA_PLACEHOLDER_RE,
        _INTERNAL_MARKER_RE,
        _EMAIL_RE,
        _URL_RE,
        _DOI_RE,
    ):
        stripped = pattern.sub(" ", stripped)
    return stripped


def _english_words(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in _ENGLISH_WORD_RE.finditer(text))


def _is_near_source_copy(
    source: str,
    target: str,
    source_words: tuple[str, ...],
) -> bool:
    if len(source_words) < 4 or not _has_function_words(source_words, 1):
        return False
    normalized_source = _normalize_visible_text(source)
    normalized_target = _normalize_visible_text(target)
    if not normalized_source or not normalized_target:
        return False
    similarity = difflib.SequenceMatcher(
        None,
        normalized_source,
        normalized_target,
        autojunk=False,
    ).ratio()
    return similarity >= 0.9


def _normalize_visible_text(text: str) -> str:
    return " ".join(_VISIBLE_SEPARATOR_RE.sub(" ", text.casefold()).split())


def _has_shared_english_clause(
    source_words: tuple[str, ...],
    target_words: tuple[str, ...],
) -> bool:
    if len(source_words) < 6 or len(target_words) < 6:
        return False
    matcher = difflib.SequenceMatcher(
        None,
        source_words,
        target_words,
        autojunk=False,
    )
    return any(
        block.size >= 6
        and _has_function_words(
            source_words[block.a : block.a + block.size],
            2,
        )
        for block in matcher.get_matching_blocks()
    )


def _is_target_only_english_prose(
    target: str,
    target_words: tuple[str, ...],
) -> bool:
    """Reject a long English paraphrase even when it no longer copies the source."""

    if len(target_words) < 6 or not _has_function_words(target_words, 2):
        return False
    return sum(_is_cjk_ideograph(char) for char in target) <= 1


def _is_cjk_ideograph(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0x20000 <= codepoint <= 0x3134F
        or _is_cjk_compatibility_ideograph(char)
    )


def _has_embedded_english_grammar_glue(source: str, target: str) -> bool:
    """Detect one untranslated English predicate inside a CJK clause.

    Only a closed set of lower-case grammatical words is eligible, and the
    identical inflected word must occur in the source prose.  Requiring any CJK
    in the same clause catches a copula separated from Chinese by a legitimately
    preserved term (for example ``is transmon 结处``), while the lower-case and
    source-membership requirements keep author names, journal names, product
    identifiers, and ordinary retained Latin terminology out of this narrow
    safety net.
    """

    if not any(_is_cjk_ideograph(char) for char in target):
        return False
    source_words = set(_english_words(source))
    for match in _ENGLISH_WORD_RE.finditer(target):
        word = match.group(0)
        normalized = word.casefold()
        if (
            word != normalized
            or normalized not in _EMBEDDED_ENGLISH_GRAMMAR_GLUE
            or normalized not in source_words
        ):
            continue
        clause_start = _last_clause_boundary(target, 0, match.start())
        clause_end = _next_clause_boundary(target, match.end(), len(target))
        left = target[clause_start : match.start()]
        right = target[match.end() : clause_end]
        left_has_cjk = any(_is_cjk_ideograph(char) for char in left)
        right_has_cjk = any(_is_cjk_ideograph(char) for char in right)
        if left_has_cjk or right_has_cjk:
            return True
    return False


def _last_clause_boundary(text: str, start: int, end: int) -> int:
    boundaries = tuple(_CLAUSE_BOUNDARY_RE.finditer(text, start, end))
    return boundaries[-1].end() if boundaries else start


def _next_clause_boundary(text: str, start: int, end: int) -> int:
    boundary = _CLAUSE_BOUNDARY_RE.search(text, start, end)
    return boundary.start() if boundary is not None else end


def _has_function_words(words: tuple[str, ...], minimum: int) -> bool:
    # Repeated author initials such as ``A. Blais, A. Petrescu, A. Eickbusch``
    # tokenize as several copies of the English article ``a``.  Counting distinct
    # function words keeps such preserved personal-name lists from masquerading as
    # an untranslated clause while real prose still supplies multiple grammatical
    # signals (for example ``the``, ``of`` and ``is``).
    return len({word for word in words if word in _FUNCTION_WORDS}) >= minimum
