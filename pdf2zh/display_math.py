"""Recover fixed display fractions from source geometry, without layout inference.

Filled rules remain in the source graphics stream.  Their rectangles are only
evidence for recovering the surrounding glyphs; this module never redraws them.
Coordinates use pdfminer's page space (origin at the lower left).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DisplayMathLayout:
    preserved_char_ids: set[int] = field(default_factory=set)
    annotation_groups: list[list] = field(default_factory=list)
    line_ids: set[int] = field(default_factory=set)
    regions: list[tuple[float, float, float, float]] = field(default_factory=list)


def _text(char):
    return char.get_text().strip()


def _rows(chars):
    rows = []
    for char in sorted(chars, key=lambda c: (-c.y0, c.x0)):
        row = next(
            (
                r
                for r in reversed(rows[-3:])
                if abs(r[0].y0 - char.y0) <= 0.19 * min(r[0].size, char.size)
            ),
            None,
        )
        if row is None:
            rows.append([char])
        else:
            row.append(char)
    return [sorted(row, key=lambda c: c.x0) for row in rows]


def _words(chars):
    """Tokenize physical rows, respecting PDF word gaps and font size changes."""
    words = []
    for row in _rows(chars):
        word = []
        for char in row:
            if not _text(char).isalpha():
                if word:
                    words.append(word)
                    word = []
                continue
            if word and (
                char.x0 - word[-1].x1 > 0.2 * min(char.size, word[-1].size)
                or min(char.size, word[-1].size) < 0.85 * max(char.size, word[-1].size)
            ):
                words.append(word)
                word = []
            word.append(char)
        if word:
            words.append(word)
    return words


def _word_text(word):
    return "".join(_text(c) for c in word)


def _is_label(word):
    text = _word_text(word)
    return (
        len(text) >= 4
        and text.isascii()
        and not text.isupper()
        and not re.fullmatch(r"(?:arcsin|arccos|arctan|sinh|cosh|tanh|sqrt)", text)
        and not any(
            re.search(r"(?:CMMI|CMSY|CMEX|Math|Symbol)", str(c.fontname)) for c in word
        )
    )


def _fraction_side(chars):
    if any(re.search(r"[0-9%α-ωΑ-Ω∑∏√]", _text(c)) for c in chars):
        return True
    words = _words(chars)
    return bool(words) and all(len(_word_text(word)) <= 3 for word in words)


def _annotation_groups(chars, fraction_rules):
    words = _words(chars)
    labels = [word for word in words if _is_label(word)]
    # Keep short connecting prose ("No", "of", "in") with an adjacent label.
    for word in words:
        text = _word_text(word)
        if text not in {"No", "no", "of", "in", "on", "to", "and", "or"}:
            continue
        if any(
            abs(word[0].y0 - label[0].y0) <= 0.2 * word[0].size
            and -0.1 <= word[0].x0 - label[-1].x1 <= word[0].size
            for label in labels
        ):
            labels.append(word)
    # A fraction condition such as "control activity No NADPH" must reach the
    # translator as one phrase: separating "No" from its abbreviation loses
    # what is absent. Require a long, same-font abbreviation on a fraction row;
    # one-letter mathematical variables and intervening operators stay fixed.
    for word in words:
        if re.fullmatch(r"[A-Z]{4,}", _word_text(word)) is None:
            continue
        em = float(word[0].size)
        if not any(
            x0 - 0.1 * em <= word[0].x0 <= word[-1].x1 <= x1 + 0.1 * em
            and (
                y + 0.1 <= word[0].y0 <= y + 1.6 * em
                or y - 1.6 * em <= word[0].y1 <= y - 0.1
            )
            for x0, y, x1, _, _ in fraction_rules
        ):
            continue
        for label in labels:
            previous = label[-1]
            if not (
                abs(word[0].y0 - previous.y0) <= 0.1 * em
                and -0.1 <= word[0].x0 - previous.x1 <= 0.8 * em
                and all(
                    abs(c.size - previous.size) <= 0.05 * em
                    and c.fontname == previous.fontname
                    for c in word
                )
            ):
                continue
            if any(
                _text(c)
                and previous.x1 < (c.x0 + c.x1) / 2 < word[0].x0
                and abs(c.y0 - previous.y0) <= 0.2 * em
                for c in chars
            ):
                continue
            labels.append(word)
            break
    groups = []
    for word in sorted(labels, key=lambda w: (-w[0].y0, w[0].x0)):
        if groups and (
            abs(word[0].y0 - groups[-1][0].y0) <= 0.2 * word[0].size
            and -0.1 <= word[0].x0 - groups[-1][-1].x1 <= word[0].size
        ):
            groups[-1].extend(word)
        else:
            groups.append(list(word))
    # Add actual source spaces in a label, where present, rather than making
    # the converter join adjacent words without their physical gap.
    for group in groups:
        left, right, baseline = group[0].x0, group[-1].x1, group[0].y0
        group.extend(
            c
            for c in chars
            if not _text(c)
            and left < (c.x0 + c.x1) / 2 < right
            and abs(c.y0 - baseline) <= 0.2 * c.size
        )
        group.sort(key=lambda c: c.x0)
    return groups


def detect_display_math(chars, lines=(), filled_rules=(), *, detections=()):
    """Return class overrides for isolated fractions with a relation baseline.

    ``filled_rules`` contains page-space ``(x0, y0, x1, y1)`` rectangles recorded
    by the interpreter.  ``line_ids`` identifies extracted LTLines that must
    remain at source coordinates instead of joining a movable formula run.
    Annotation groups should receive separate ``formula_annotation`` classes;
    all ``preserved_char_ids`` should receive class zero after other overrides.
    """
    chars = list(chars)
    visible = [c for c in chars if _text(c) and abs(c.matrix[1]) <= 1e-6]
    rules = []
    for line in lines:
        if len(line.pts) != 2 or abs(line.pts[0][1] - line.pts[1][1]) > 0.02:
            continue
        x0, x1 = sorted((line.pts[0][0], line.pts[1][0]))
        rules.append((x0, line.pts[0][1], x1, float(line.linewidth), id(line)))
    for x0, y0, x1, y1 in filled_rules:
        rules.append((x0, (y0 + y1) / 2, x1, y1 - y0, None))
    found = []
    for x0, y, x1, width, line_id in rules:
        if not (0 <= width <= 2 and x1 - x0 >= max(4, 4 * width)):
            continue
        if any(
            d.class_name == "table"
            and d.bounds[0] <= x0 <= x1 <= d.bounds[2]
            and d.bounds[1] <= y <= d.bounds[3]
            for d in detections
        ):
            continue
        near = [c for c in visible if c.x1 > x0 and c.x0 < x1 and abs(c.y0 - y) <= 24]
        if not near:
            continue
        em = max(c.size for c in near)
        above = [c for c in near if y + 0.1 <= c.y0 <= y + 1.6 * em]
        below = [c for c in near if y - 1.6 * em <= c.y1 <= y - 0.1]
        if not (above and below and _fraction_side(above) and _fraction_side(below)):
            continue
        bottom, top = min(c.y0 for c in below), max(c.y1 for c in above)
        band = [
            c for c in visible if c.y1 > bottom - 0.15 * em and c.y0 < top + 0.15 * em
        ]
        left, right = x0, x1
        group = []
        while True:
            expanded = [
                c for c in band if c.x1 >= left - 2 * em and c.x0 <= right + 2 * em
            ]
            if len(expanded) == len(group):
                break
            group = expanded
            left = min(x0, *(c.x0 for c in group))
            right = max(x1, *(c.x1 for c in group))
        relations = [
            c
            for c in group
            if _text(c) in {"=", "≈", "≃", "≤", "≥", "<", ">", ":="}
            and abs((c.y0 + c.y1) / 2 - y) < 0.8 * em
        ]
        if not relations:
            continue
        first_relation = min(c.x0 for c in relations)
        labels = [word for word in _words(group) if _is_label(word)]
        # Ordinary running text beside an inline fraction is not a display.
        # Explanatory labels may occupy either fraction row or the left side
        # of its relation, but never trail the mathematical right-hand side.
        if any(
            word[0].x0 > first_relation
            and not (
                word[0].x0 >= x0 - em
                and word[-1].x1 <= x1 + em
                and (word[0].y0 > y or word[0].y1 < y)
            )
            for word in labels
        ):
            continue
        lhs_words = [word for word in _words(group) if word[-1].x1 < first_relation]
        if (
            len([word for word in lhs_words if _is_label(word)]) > 4
            or lhs_words
            and _word_text(lhs_words[0]).casefold()
            in {"the", "this", "these", "we", "using"}
        ):
            continue
        bottom = min(c.y0 for c in group)
        top = max(c.y1 for c in group)
        group += [
            c
            for c in chars
            if not _text(c)
            and left - 0.2 * em <= c.x0 <= right + 0.2 * em
            and c.y1 > bottom
            and c.y0 < top
        ]
        ids = {id(c) for c in group}
        previous = next((item for item in found if item[0] & ids), None)
        if previous is not None:
            previous[0].update(ids)
            previous[1].update({line_id} if line_id is not None else set())
        else:
            found.append((ids, {line_id} if line_id is not None else set()))
    result = DisplayMathLayout()
    for ids, line_ids in found:
        group = [c for c in chars if id(c) in ids]
        annotations = _annotation_groups(group, rules)
        annotation_ids = {id(c) for annotation in annotations for c in annotation}
        result.preserved_char_ids.update(ids - annotation_ids)
        result.annotation_groups.extend(annotations)
        result.line_ids.update(line_ids)
        result.regions.append(
            (
                min(c.x0 for c in group),
                min(c.y0 for c in group),
                max(c.x1 for c in group),
                max(c.y1 for c in group),
            )
        )
    return result
