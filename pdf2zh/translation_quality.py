"""Conservative quality checks for translated prose."""

from __future__ import annotations

import difflib
import re
import unicodedata

from pdf2zh.source_segmentation import statistical_abbreviation_spans

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


def has_detached_norm_relation(source: str, target: str) -> bool:
    """Reject an isolated norm fragment after a translated sentence boundary.

    This checks the narrow signature of an incorrectly split ``w.r.t.`` clause,
    including cached results from older segmentation. It does not prescribe the
    Chinese wording or reject a norm that starts a complete target sentence.
    """
    relation = re.compile(
        r"\b(?:w\.\s*r\.\s*t\.|with\s+respect\s+to)\s+(?:the\s+)?"
        r"(?P<norm>\{\{v\d+\}\}|\{v\d+\})\s+norm\b",
        re.IGNORECASE,
    )
    for match in relation.finditer(source):
        token = re.escape(match.group("norm"))
        if re.search(
            r"[。！？.!?]\s*[（(]?\s*"
            + token
            + r"\s*(?:范数|範數)\s*[）)]?\s*[；;。.!?！？]",
            target,
        ):
            return True
    return False


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
    r"[A-Za-z]\.\d+(?:\.\d+)*[A-Za-z]?(?:\s*[（(]\s*[A-Za-z]\s*[）)])?|"
    r"[A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?(?:\s*[（(]\s*[A-Za-z]\s*[）)])?|"
    r"(?-i:[IVXLCDM]+)"
)
_PANEL_IDENTIFIER_RE = re.compile(
    r"(?P<number>(?:[A-Za-z]\.)?[A-Za-z]?\d+(?:\.\d+)*)"
    r"\s*(?:[（(]\s*(?P<wrapped>[A-Za-z])\s*[）)]|(?P<bare>[A-Za-z]))$"
)
_SOURCE_CROSS_REFERENCE_RE = re.compile(
    r"\b(?P<label>figures?|figs?\.?|tables?|tbls?\.?|"
    r"equations?|eqs?\.?|references?|refs?\.?)(?![A-Za-z])"
    r"\s*(?:[（(［\[]\s*)?"
    rf"(?P<identifier>{_SOURCE_CROSS_REFERENCE_IDENTIFIER})(?![A-Za-z0-9])"
    r"(?:\s*[）)］\]])?",
    re.IGNORECASE,
)
_SOURCE_REFERENCE_LIST_SEPARATOR_RE = re.compile(
    r"\s*(?P<separator>,\s*(?:(?:and|or)\b\s*)?|" r"(?:and|or|to)\b\s*|[&–—-]\s*)",
    re.IGNORECASE,
)
_SOURCE_REFERENCE_LIST_ITEM_RE = re.compile(
    rf"(?P<open>[（(［\[])?\s*(?P<identifier>{_SOURCE_CROSS_REFERENCE_IDENTIFIER})"
    r"(?![A-Za-z0-9]|\.(?=[A-Za-z0-9]))\s*(?P<close>[）)］\]])?",
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
        "on",
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
    normalized = target
    consumed_groups: dict[tuple, int] = {}
    for start, end, kind, identifiers, separators in _source_reference_groups(
        masked_source
    ):
        group_pattern = _target_reference_group_pattern(kind, identifiers, separators)
        matches = tuple(
            group_pattern.finditer(_mask_cross_reference_opaque_text(normalized))
        )
        key = (kind, identifiers, separators)
        used = consumed_groups.get(key, 0)
        if len(matches) <= used:
            # A list is one reference unit.  Finding only its first number is
            # insufficient when later numbers have moved into unrelated prose.
            return None
        match = matches[used]
        group_text = normalized[match.start() : match.end()]
        group_text = re.sub(
            rf"(?i)(?<![A-Za-z])(?:{_SOURCE_CROSS_REFERENCE_LABELS[kind]})"
            r"\.?(?![A-Za-z])",
            _TARGET_CROSS_REFERENCE_CANONICAL_LABELS[kind],
            group_text,
        )
        normalized = (
            normalized[: match.start()] + group_text + normalized[match.end() :]
        )
        consumed_groups[key] = used + 1
        # Group members were validated together, including shared parentheses.
        # Do not subsequently repair the first member as an isolated reference.
        masked_source = (
            masked_source[:start] + " " * (end - start) + masked_source[end:]
        )
    references: list[tuple[str, str, bool]] = []
    for match in _SOURCE_CROSS_REFERENCE_RE.finditer(masked_source):
        identifier_end = match.end("identifier")
        while suffix := _FORMULA_PLACEHOLDER_RE.match(masked_source, identifier_end):
            # A font boundary can split a reference into a visible prefix and
            # an opaque suffix, such as ``Eqs. (7{v0}``.  The placeholder may
            # contain the rest of a range and its closing delimiter.  Keep the
            # compound identifier intact instead of balancing the visible prefix
            # as though it were a complete reference.
            identifier_end = suffix.end()
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
                source[match.start("identifier") : identifier_end],
                _CROSS_REFERENCE_TERMINAL_RE.match(following) is not None,
            )
        )
    if not references:
        return normalized

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
        correct_count = len(_balanced_cross_reference_matches(correct, normalized))
        if correct_count > used:
            consumed[key] = used + 1
            continue
        if _FORMULA_PLACEHOLDER_RE.search(identifier) and not (
            _FORMULA_PLACEHOLDER_RE.fullmatch(identifier)
        ):
            # The hidden suffix prevents safe delimiter or placement repairs.
            # Still require a correctly adjacent label; do not accept arbitrary
            # intervening prose simply because part of the identifier is opaque.
            return None
        repaired = _repair_one_misplaced_cross_reference(
            normalized,
            kind,
            identifier,
            source_is_terminal=source_is_terminal,
        )
        if (
            repaired is None
            or len(_balanced_cross_reference_matches(correct, repaired)) <= used
        ):
            return None
        normalized = repaired
        consumed[key] = used + 1
    normalized = _normalize_terminal_figure_example_artifact(source, normalized)
    return _normalize_simplifying_equation_phrases(source, normalized)


def _source_reference_groups(
    source: str,
) -> list[tuple[int, int, str, tuple[str, ...], tuple[str, ...]]]:
    """Read explicit citation lists without guessing from arbitrary nearby numbers."""

    groups = []
    covered_until = 0
    for first in _SOURCE_CROSS_REFERENCE_RE.finditer(source):
        if first.start() < covered_until:
            continue
        label = first.group("label").casefold().rstrip(".")
        kind = (
            "figure"
            if label.startswith("fig")
            else (
                "table"
                if label.startswith(("tab", "tbl"))
                else "equation" if label.startswith("eq") else "reference"
            )
        )
        if _FORMULA_PLACEHOLDER_RE.match(source, first.end("identifier")):
            continue
        before_identifier = source[first.start() : first.start("identifier")].rstrip()
        after_identifier = source[first.end("identifier") : first.end()].strip()
        first_open = (
            before_identifier[-1:]
            if before_identifier.endswith(("(", "（", "[", "［"))
            else ""
        )
        first_wrapped = bool(first_open) and after_identifier in (")", "）", "]", "］")
        shared_open = first_open if first_open and not first_wrapped else ""
        plural = label.endswith("s")
        identifiers = [first.group("identifier")]
        separators = []
        cursor = first.end()
        while separator := _SOURCE_REFERENCE_LIST_SEPARATOR_RE.match(source, cursor):
            item_start = separator.end()
            labelled = _SOURCE_CROSS_REFERENCE_RE.match(source, item_start)
            if labelled is not None:
                same_kind = (
                    re.fullmatch(
                        _SOURCE_CROSS_REFERENCE_LABELS[kind] + r"\.?",
                        labelled.group("label"),
                        re.IGNORECASE,
                    )
                    is not None
                )
                if not same_kind:
                    break
                item = labelled
            else:
                item = _SOURCE_REFERENCE_LIST_ITEM_RE.match(source, item_start)
                previous_panel = _PANEL_IDENTIFIER_RE.fullmatch(identifiers[-1])
                shorthand_panel = re.match(
                    r"[（(]\s*(?P<letter>[A-Za-z])\s*[）)]", source[item_start:]
                )
                if item is None and previous_panel is not None and shorthand_panel:
                    # ``Figs. 4(c) and (d)`` repeats only the panel suffix.
                    # Retain the inherited number so its second panel cannot
                    # silently disappear or attach to an unrelated figure.
                    identifiers.append(
                        previous_panel.group("number")
                        + "("
                        + shorthand_panel.group("letter")
                        + ")"
                    )
                    raw_separator = separator.group("separator").strip().casefold()
                    separators.append(
                        "range"
                        if raw_separator in {"-", "–", "—", "to"}
                        else "or" if raw_separator.endswith("or") else "list"
                    )
                    cursor = item_start + shorthand_panel.end()
                    continue
                shared_close = bool(
                    item is not None
                    and shared_open
                    and not item.group("open")
                    and item.group("close")
                    and (
                        (shared_open in "(（" and item.group("close") in ")）")
                        or (shared_open in "[［" and item.group("close") in "]］")
                    )
                )
                if item is None or (
                    bool(item.group("open")) != bool(item.group("close"))
                    and not shared_close
                ):
                    break
                # Singular "Eq. 1 and 2 layers" does not prove that 2 is another
                # reference. Paired parentheses or a plural/repeated label do.
                if (
                    not plural
                    and not shared_open
                    and not (first_wrapped and item.group("open"))
                ):
                    break
            if _FORMULA_PLACEHOLDER_RE.match(source, item.end("identifier")):
                break
            raw_separator = separator.group("separator").strip().casefold()
            relation = (
                "range"
                if raw_separator in {"-", "–", "—", "to"}
                else "or" if raw_separator.endswith("or") else "list"
            )
            identifiers.append(item.group("identifier"))
            separators.append(relation)
            cursor = item.end()
            if labelled is None and shared_close:
                break
        if len(identifiers) > 1:
            groups.append(
                (first.start(), cursor, kind, tuple(identifiers), tuple(separators))
            )
            covered_until = cursor
    return groups


def _target_reference_group_pattern(
    kind: str,
    identifiers: tuple[str, ...],
    separators: tuple[str, ...],
) -> re.Pattern[str]:
    label = (
        rf"(?:{_TARGET_CROSS_REFERENCE_LABELS[kind]}|"
        rf"(?i:(?<![A-Za-z]){_SOURCE_CROSS_REFERENCE_LABELS[kind]}\.?(?![A-Za-z])))"
    )
    joins = {
        "list": r"(?:[,，、]\s*(?:(?:以及|和|及|与|(?i:and))\s*)?|以及|和|及|与|&|(?i:and))",
        "or": r"(?:[,，、]\s*)?(?:或者|或|(?i:or))",
        "range": r"(?:[-–—~～]|至|到|(?i:to))",
    }
    bare = [
        _reference_identifier_body_pattern(identifier)
        + r"(?![A-Za-z0-9]|\.(?=[A-Za-z0-9]))"
        for identifier in identifiers
    ]
    wrapped = [
        rf"(?:[（(]\s*{item}\s*[）)]|[［\[]\s*{item}\s*[］\]]|"
        rf"{item}(?!\s*[）)］\]]))"
        for item in bare
    ]
    for index in range(1, len(identifiers)):
        previous_panel = _PANEL_IDENTIFIER_RE.fullmatch(identifiers[index - 1])
        panel = _PANEL_IDENTIFIER_RE.fullmatch(identifiers[index])
        if (
            panel is not None
            and previous_panel is not None
            and panel.group("number") == previous_panel.group("number")
        ):
            letter = re.escape(panel.group("wrapped") or panel.group("bare"))
            wrapped[index] = rf"(?:{wrapped[index]}|[（(]\s*{letter}\s*[）)])"

    def joined(items: list[str]) -> str:
        result = items[0]
        for relation, item in zip(separators, items[1:], strict=True):
            result += rf"\s*{joins[relation]}\s*(?:{label}\s*)?{item}"
        return result

    return re.compile(
        rf"{label}\s*(?:{joined(wrapped)}|[（(]\s*{joined(bare)}\s*[）)]|"
        rf"[［\[]\s*{joined(bare)}\s*[］\]])"
    )


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


def _reference_identifier_body_pattern(identifier: str) -> str:
    """Treat a panel suffix as part of its figure/equation identifier."""

    panel = _PANEL_IDENTIFIER_RE.fullmatch(identifier)
    if panel is None:
        return re.escape(identifier)
    number = re.escape(panel.group("number"))
    letter = re.escape(panel.group("wrapped") or panel.group("bare"))
    return rf"{number}\s*(?:[（(]\s*{letter}\s*[）)]|{letter})"


def _target_identifier_pattern(
    identifier: str, *, enclosed_reference: bool = False
) -> str:
    escaped = _reference_identifier_body_pattern(identifier)
    if _FORMULA_PLACEHOLDER_RE.fullmatch(identifier):
        return escaped
    if _FORMULA_PLACEHOLDER_RE.search(identifier):
        # A compound identifier may end with an opaque closing delimiter.  Only
        # match its exact visible prefix and tokens, allowing an opening delimiter
        # before them without manufacturing a closing one inside the atom.
        return rf"(?:[（(]\s*)?{escaped}"
    # Parentheses are optional, but they must be balanced when present.  The
    # former pair of independent optionals accepted malformed output such as
    # ``式(8中`` and allowed it into the cache as a valid cross-reference.
    bare_end = "" if enclosed_reference else r"(?!\s*[）)］\]])"
    return (
        rf"(?<![A-Za-z0-9.])(?:"
        rf"[（(]\s*{escaped}\s*[）)]|[［\[]\s*{escaped}\s*[］\]]|"
        rf"{escaped}{bare_end}"
        # A sentence/caption-final ASCII full stop is punctuation, not part of
        # the identifier.  Still reject decimal/version continuations such as
        # ``1.2`` so a shorter identifier cannot match their prefix.
        rf")(?![A-Za-z0-9]|\.(?=[A-Za-z0-9]))"
    )


def _labelled_reference_pattern(label: str, body: str, enclosed_body: str) -> str:
    """Distinguish brackets around an identifier from a whole reference.

    In ``[Fig. 1(d)]`` the last bracket belongs to the outer reference, not to
    the panel identifier. Relax its trailing-close check only inside a matched
    outer pair, retaining rejection of stray or missing identifier brackets.
    """
    alternatives = [rf"{label}[\s\u00a0]*{body}"]
    for opening, closing in (("[", "]"), ("［", "］"), ("(", ")"), ("（", "）")):
        alternatives.append(
            rf"{re.escape(opening)}\s*{label}[\s\u00a0]*"
            rf"{enclosed_body}\s*{re.escape(closing)}"
        )
    return "(?:" + "|".join(alternatives) + ")"


def _target_cross_reference_pattern(kind: str, identifier: str) -> re.Pattern[str]:
    return re.compile(
        _labelled_reference_pattern(
            rf"(?:{_TARGET_CROSS_REFERENCE_LABELS[kind]})",
            _target_identifier_pattern(identifier),
            _target_identifier_pattern(identifier, enclosed_reference=True),
        )
    )


def _balanced_cross_reference_matches(
    pattern: re.Pattern[str], target: str
) -> tuple[re.Match[str], ...]:
    """Check only outer delimiters immediately surrounding a reference.

    A regex can otherwise match the valid inner fragment of ``[图1(d)`` or
    ``[图1(d)]]``. Allow prose inside a balanced wrapper, as in ``(图1中所示)``,
    while keeping missing identifier-parenthesis repairs independent.
    """
    masked = _mask_cross_reference_opaque_text(target)
    pairs = {"(": ")", "（": "）", "[": "]", "［": "］"}
    closing = set(pairs.values())
    accepted = []
    for match in pattern.finditer(masked):
        start = match.start()
        while (prefix := masked[:start].rstrip()) and prefix[-1] in pairs:
            start = len(prefix) - 1
        if start == match.start() and masked[start] not in pairs:
            accepted.append(match)
            continue
        stack = []
        cursor = start
        valid = True
        while cursor < len(masked):
            char = masked[cursor]
            if cursor >= match.end() and not stack and not char.isspace():
                if char in closing:
                    valid = False
                break
            if char in pairs:
                stack.append(pairs[char])
            elif char in closing:
                if not stack or stack.pop() != char:
                    valid = False
                    break
            cursor += 1
        if valid and not stack:
            accepted.append(match)
    return tuple(accepted)


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
    label_pattern = rf"(?<![A-Za-z])(?:{source_label})\.?(?![A-Za-z])"
    pattern = re.compile(
        _labelled_reference_pattern(
            label_pattern,
            _target_identifier_pattern(identifier),
            _target_identifier_pattern(identifier, enclosed_reference=True),
        ),
        re.IGNORECASE,
    )
    canonical = _TARGET_CROSS_REFERENCE_CANONICAL_LABELS[kind]
    return pattern.sub(
        lambda match: re.sub(
            label_pattern, canonical, match.group(0), count=1, flags=re.IGNORECASE
        ),
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
    panel = _PANEL_IDENTIFIER_RE.fullmatch(identifier)
    if panel is not None:
        # A panel is not an independent prose parenthesis.  In results such as
        # ``图4所示(b)`` the old number-only check silently accepted a detached
        # suffix.  Move it across only an unambiguous locative/display tail;
        # arbitrary intervening prose must be retried instead of rearranged.
        number = re.escape(panel.group("number"))
        letter = re.escape(panel.group("wrapped") or panel.group("bare"))
        detached_panel = re.compile(
            rf"(?P<label>{label_pattern})(?P<space>[\s\u00a0]*)"
            rf"(?P<number>{number})(?![A-Za-z0-9.])"
            rf"(?P<tail>中所示|所示|中)\s*"
            rf"(?P<panel>[（(]\s*{letter}\s*[）)])"
        )
        matches = tuple(
            detached_panel.finditer(_mask_cross_reference_opaque_text(target))
        )
        if len(matches) == 1:
            match = matches[0]
            return (
                target[: match.start()]
                + match.group("label")
                + match.group("space")
                + match.group("number")
                + match.group("panel")
                + match.group("tail")
                + target[match.end() :]
            )
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
        or _has_untranslated_mathematical_prose(clean_source, clean_target)
        or _has_broken_statistical_abbreviation(source, target)
    )


def _has_untranslated_mathematical_prose(source: str, target: str) -> bool:
    """Catch short mathematical prose without treating names as translatable.

    These source-matched forms are lower-case property words, numbered internal
    statement references, and quoted labels made wholly of ordinary scientific
    words. External citation locators retain their original bibliographic form.
    """
    if not any(_is_cjk_ideograph(char) for char in target):
        return False
    for word in ("sharp", "sharpness"):
        pattern = rf"(?<![A-Za-z]){word}(?![A-Za-z])"
        if re.search(pattern, source) and re.search(pattern, target):
            return True

    citation = re.compile(r"\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*,[^\]\r\n]*\]")
    source_references = citation.sub(" ", source)
    target_references = citation.sub(" ", target)
    statement = re.compile(
        r"\b(?:theorem|lemma|proposition|corollary|definition|assumption|remark)"
        r"\s+(?:[A-Za-z]\.)?\d+(?:\.\d+)*(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    normalized_source = {
        match.group().casefold() for match in statement.finditer(source_references)
    }
    if any(
        match.group().casefold() in normalized_source
        for match in statement.finditer(target_references)
    ):
        return True

    # A closed vocabulary excludes product, author and method names, while
    # quotes identify the complete local problem label rather than an arbitrary
    # technical phrase embedded in the surrounding sentence.
    scientific_label_words = {
        "covariance",
        "matrix",
        "vector",
        "signal",
        "phase",
        "sparse",
        "low-rank",
        "estimation",
        "recovery",
        "sensing",
        "retrieval",
    }
    quote = re.compile(r"""["“‘'](?P<label>[A-Za-z][A-Za-z0-9 -]{2,80})["”’']""")
    for match in quote.finditer(source):
        label = match.group("label").strip()
        content = re.sub(r"\s+(?:[IVXLCDM]+|\d+)\s*$", "", label)
        words = tuple(word.casefold() for word in _ENGLISH_WORD_RE.findall(content))
        if len(words) < 2 or not all(word in scientific_label_words for word in words):
            continue
        pattern = (
            r"(?<![A-Za-z])"
            + r"\s+".join(re.escape(word) for word in label.split())
            + r"(?![A-Za-z0-9])"
        )
        if re.search(pattern, target, re.IGNORECASE):
            return True
    return False


def _has_broken_statistical_abbreviation(source: str, target: str) -> bool:
    """Reject a qualifier that separates a Chinese verb from its operand.

    A condition may legitimately move before the qualifier in Chinese, leaving
    the abbreviation at a complete sentence end. Reject only when an incomplete
    verb precedes the stop and the original following operand still follows it.
    """
    operand = r"(?:[A-Z]{2,}\s*)?(?:\{\{v\d+\}\}|\{v\d+\})"
    for start, end in statistical_abbreviation_spans(source):
        following_operand = re.match(r"\s*,?\s*(" + operand + r")", source[end:])
        if following_operand is None:
            continue
        expected_operand = re.sub(r"\s+", "", following_operand.group(1))
        letters = re.findall(r"[a-z]", source[start:end])
        prefix = r"\b" + r"\.\s*".join(letters)
        for match in re.finditer(prefix + r"\.?\s*[。！？]", target):
            preceding = target[: match.start()].rstrip()
            if not re.search(r"(?:设|令|取|选取|选择|设置|采样|抽取)\s*$", preceding):
                continue
            actual_operand = re.match(r"\s*(" + operand + r")", target[match.end() :])
            if (
                actual_operand is not None
                and re.sub(r"\s+", "", actual_operand.group(1)) == expected_operand
            ):
                return True
    return False


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
    clean_source, clean_target, damaged_identifier = _mask_reference_identifiers(
        clean_source, clean_target
    )
    if damaged_identifier:
        return True
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
    if source_name.casefold() != target_name.casefold():
        return None
    name_tokens = source_name.split()
    target_tokens = target_name.split()
    # A complete trailing version belongs to the name, but it cannot on its
    # own certify an ordinary title phrase as a software/product identifier.
    if re.fullmatch(r"v?\d+(?:\.\d+)*", name_tokens[-1], re.IGNORECASE):
        name_tokens = name_tokens[:-1]
        target_tokens = target_tokens[:-1]
    name_words = tuple(_ENGLISH_WORD_RE.finditer(" ".join(name_tokens)))
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
    # Bibliographies often sentence-case an acronym-bearing name.  Accept a
    # canonicalized two-letter acronym only in this complete, short,
    # colon-delimited name; ordinary function words and mere Title Case fail.
    if not name_has_identifier and name_tokens[0][:1].isupper():
        name_has_identifier = any(
            len(token) == 2
            and token.isalpha()
            and token.isupper()
            and token.casefold() not in _FUNCTION_WORDS
            and token.casefold()
            not in {"am", "be", "do", "go", "me", "my", "no", "oh", "so", "up", "us"}
            for token in target_tokens
        )
    if not name_has_identifier:
        return None
    return source_name


def _mask_reference_identifiers(source: str, target: str) -> tuple[str, str, bool]:
    """Remove only source-anchored complete model/software identifiers.

    This supplies lexical units to the residue checker, not permission to keep
    surrounding English prose.  An extension or version changed by translation
    does not match, and source-only fragments cannot be masked.
    """
    if not any(_is_cjk_ideograph(char) for char in target):
        return source, target, False
    source_lead = _LEADING_REFERENCE_NAME_RE.match(source)
    if source_lead:
        version = re.search(
            r"\bv?\d+\.\d+(?:\.\d+)*$", source_lead.group("name"), re.IGNORECASE
        )
        if (
            version
            and re.search(
                r"(?<![\d.])v?" + re.escape(version[0].lstrip("vV")) + r"(?![\d.])",
                target,
                re.IGNORECASE,
            )
            is None
        ):
            return source, target, True
    patterns = (
        re.compile(
            r"(?<![A-Za-z0-9_])(?P<name>[A-Z][A-Za-z0-9]{1,12}-\d+(?:\.\d+)*"
            r"\s+(?:tiny|small|base|medium|large|xl|xxl))"
            r"(?=\s*(?:$|[),;:.!?]))"
        ),
        re.compile(
            r"(?<=\s)(?P<name>[A-Za-z][A-Za-z0-9_]{2,}\s*\.\s*[a-z]{2,3})"
            r"(?=\s*(?:$|[),;:!?]))"
        ),
    )
    for pattern in patterns:
        for match in reversed(list(pattern.finditer(source))):
            name = match.group("name")
            is_dotted_software = "." in name and "-" not in name
            if is_dotted_software:
                left, extension = re.split(r"\s*\.\s*", name)
                if (
                    extension.casefold() in _FUNCTION_WORDS
                    or left.casefold() in _FUNCTION_WORDS
                    or re.search(
                        r"\b(?:with|using|via|and)\s*$",
                        source[: match.start()],
                        re.IGNORECASE,
                    )
                    is None
                ):
                    continue
            # Whitespace around a PDF-split dot and conventional casing may be
            # normalized, while every letter, digit, hyphen and dot must remain.
            parts = re.split(r"(\s+|\.)", name)
            expression = "".join(
                (
                    r"\s*"
                    if part.isspace()
                    else r"\s*\.\s*" if part == "." else re.escape(part)
                )
                for part in parts
            )
            candidates = list(
                re.finditer(
                    r"(?<![A-Za-z0-9_])" + expression + r"(?![A-Za-z0-9_])",
                    target,
                    re.IGNORECASE,
                )
            )
            if len(candidates) != 1:
                if is_dotted_software:
                    return source, target, True
                continue
            candidate = candidates[0]
            target = target[: candidate.start()] + " " + target[candidate.end() :]
            source = source[: match.start()] + " " + source[match.end() :]
    return source, target, False


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
        if normalized == "on" and (
            re.search(r"['\"‘“]\s*$", target[: match.start()])
            or re.match(r"\s*['\"’”]", target[match.end() :])
            or re.match(r"\s*/\s*off\b", target[match.end() :], re.IGNORECASE)
            or re.search(
                r"(?<![A-Za-z])off\s*/\s*$", target[: match.start()], re.IGNORECASE
            )
        ):
            # Quoted device-state labels and on/off pairs are identifiers,
            # unlike a stray source preposition in translated prose.
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
