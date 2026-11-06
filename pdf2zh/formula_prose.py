"""Recover source text rows and slots around fixed mathematical content."""

from __future__ import annotations

from dataclasses import dataclass
import re
from statistics import median


@dataclass
class ProseSlot:
    chars: list
    kind: str
    bounds: tuple[float, float, float, float] | None = None


def _visible(char):
    return bool(char.get_text().strip())


def _baseline(char):
    return float(char.matrix[5])


def _horizontal(char):
    a, b, c, d, *_ = char.matrix
    return abs(b) < 1e-5 and abs(c) < 1e-5 and a > 0 and d > 0


def _text(chars):
    pieces = []
    previous = None
    for char in sorted(chars, key=lambda item: item.x0):
        if previous is not None and char.x0 - previous.x1 > 0.15 * max(
            char.size, previous.size
        ):
            pieces.append(" ")
        pieces.append(char.get_text())
        previous = char
    return "".join(pieces).strip()


def _rows(chars, em):
    """Anchor rows on body-size glyphs, then attach their smaller scripts."""
    anchors = []
    for char in sorted(chars, key=lambda item: (-_baseline(item), item.x0)):
        if not _visible(char) or not 0.85 * em <= char.size <= 1.2 * em:
            continue
        row = next(
            (
                r
                for r in reversed(anchors[-4:])
                if abs(_baseline(r[0]) - _baseline(char)) <= 0.18 * em
            ),
            None,
        )
        if row is None:
            anchors.append([char])
        else:
            row.append(char)
    rows = [[] for _ in anchors]
    for char in chars:
        if not anchors:
            break
        index = min(
            range(len(anchors)),
            key=lambda i: abs(_baseline(anchors[i][0]) - _baseline(char)),
        )
        if abs(_baseline(anchors[index][0]) - _baseline(char)) <= 0.65 * em:
            rows[index].append(char)
    return [sorted(row, key=lambda item: item.x0) for row in rows if row]


def definition_rows(chars, *, excluded=frozenset()):
    """Recognize repeated one-symbol/script labels followed by aligned prose.

    Three aligned, independently complete rows are required. Ordinary lists,
    author names, table values and mathematical equalities do not satisfy the
    short italic/math symbol plus multiword prose pattern.
    """
    chars = [char for char in chars if id(char) not in excluded and _horizontal(char)]
    if not chars:
        return []
    em = (
        median(float(char.size) for char in chars if _visible(char))
        if any(_visible(c) for c in chars)
        else 0
    )
    if em <= 0:
        return []
    candidates = []
    for row in _rows(chars, em):
        visible = [char for char in row if _visible(char)]
        for index in range(1, len(visible)):
            left, right = visible[:index], visible[index:]
            if right[0].x0 - left[-1].x1 < 0.8 * em:
                continue
            base = [char for char in left if char.size >= 0.85 * em]
            if (
                len(base) != 1
                or re.fullmatch(r"[A-Za-zα-ωΑ-Ω]", base[0].get_text()) is None
                or not re.search(
                    r"Italic|Math|CMMI|CMSY|Symbol", str(base[0].fontname), re.I
                )
                or any(not char.get_text().isalnum() for char in left)
                or len(left) > 10
                or len(re.findall(r"[A-Za-z]{2,}", _text(right))) < 3
                or any(b.x0 - a.x1 > 2 * em for a, b in zip(right, right[1:]))
            ):
                break
            candidates.append((row, left[0].x0, right[0].x0, _baseline(base[0]), em))
            break
    result = []
    for row, label_x, prose_x, baseline, em in candidates:
        peers = [
            item
            for item in candidates
            if abs(item[1] - label_x) <= 0.25 * em
            and abs(item[2] - prose_x) <= 0.5 * em
        ]
        if len(peers) < 3 or any(
            0 < abs(item[3] - baseline) < 1.3 * em for item in peers
        ):
            continue
        result.append(ProseSlot(row, "formula_definition"))
    return result


def image_clusters(figures, gap=3.0):
    """Join nearby inline pixel strips into their original occupied rectangles."""
    rectangles = []
    for figure in figures:
        box = tuple(float(v) for v in figure.bbox)
        if box[2] > box[0] and box[3] > box[1]:
            rectangles.append(box)
    active, finished = [], []
    for box in sorted(rectangles, key=lambda item: (item[1], item[0])):
        finished.extend(item for item in active if item[3] + gap < box[1])
        active = [item for item in active if item[3] + gap >= box[1]]
        merged = box
        while True:
            adjacent = [
                item
                for item in active
                if item[0] <= merged[2] + gap
                and merged[0] <= item[2] + gap
                and item[1] <= merged[3] + gap
                and merged[1] <= item[3] + gap
            ]
            if not adjacent:
                break
            for item in adjacent:
                active.remove(item)
            merged = (
                min(item[0] for item in [merged, *adjacent]),
                min(item[1] for item in [merged, *adjacent]),
                max(item[2] for item in [merged, *adjacent]),
                max(item[3] for item in [merged, *adjacent]),
            )
        active.append(merged)
    return finished + active


def image_adjacent_prose(chars, clusters, *, excluded=frozenset()):
    """Keep text on either side of a small inline image in separate source slots."""
    chars = [char for char in chars if id(char) not in excluded and _horizontal(char)]
    visible = [char for char in chars if _visible(char)]
    if not visible:
        return []
    em = median(float(char.size) for char in visible)
    slots = []
    for row in _rows(chars, em):
        meaningful = [char for char in row if _visible(char)]
        if not meaningful:
            continue
        baseline = median(
            _baseline(char) for char in meaningful if char.size >= 0.85 * em
        )
        barriers = []
        for x0, y0, x1, y1 in clusters:
            if not (
                0.5 * em <= x1 - x0 <= 25 * em
                and 0.2 * em <= y1 - y0 <= 3.5 * em
                and y0 - 0.2 * em <= baseline <= y1 + 0.2 * em
            ):
                continue
            if any(
                char.x0 < x1 - 0.1 * em and char.x1 > x0 + 0.1 * em
                for char in meaningful
            ):
                continue
            left = [char for char in meaningful if char.x1 <= x0 + 0.1 * em]
            right = [char for char in meaningful if char.x0 >= x1 - 0.1 * em]
            if not left or not right:
                continue
            if (
                x0 - max(c.x1 for c in left) > 2 * em
                or min(c.x0 for c in right) - x1 > 2 * em
            ):
                continue
            if not any(
                len(re.findall(r"[A-Za-z]{2,}", _text(side))) >= 2
                for side in (left, right)
            ):
                continue
            barriers.append((x0, x1))
        if not barriers:
            continue
        cuts = [
            -float("inf"),
            *[sum(pair) / 2 for pair in sorted(barriers)],
            float("inf"),
        ]
        for left, right in zip(cuts, cuts[1:]):
            group = [
                char
                for char in row
                if left <= (char.x0 + char.x1) / 2 < right
                and not any(x0 < (char.x0 + char.x1) / 2 < x1 for x0, x1 in barriers)
            ]
            content = [char for char in group if _visible(char)]
            if content:
                beside_image = any(
                    -0.1 * em <= x0 - content[-1].x1 <= 1.5 * em for x0, _ in barriers
                )
                assignment = _trailing_assignment(group, em) if beside_image else []
                if assignment:
                    preserved = {id(char) for char in assignment}
                    prose = [char for char in group if id(char) not in preserved]
                    if any(_visible(char) for char in prose):
                        slots.append(ProseSlot(prose, "formula_prose"))
                    slots.append(ProseSlot(assignment, "fixed_math"))
                else:
                    slots.append(ProseSlot(group, "formula_prose"))
    return slots


def _trailing_assignment(chars, em):
    """Protect a short variable-and-relation suffix adjoining an image operand.

    This prevents a translated lead-in from inserting its verb between ``C_i =``
    and the unchanged fraction image. Ordinary words such as ``result =`` are
    not inferred to be symbolic identifiers.
    """
    ordered = sorted(chars, key=lambda char: char.x0)
    visible_indices = [i for i, char in enumerate(ordered) if _visible(char)]
    if not visible_indices or ordered[visible_indices[-1]].get_text() not in {
        "=",
        "≈",
        "≃",
    }:
        return []
    relation = visible_indices[-1]
    end = relation
    while end and not _visible(ordered[end - 1]):
        end -= 1
    start = end
    while start and _visible(ordered[start - 1]):
        if start < end and ordered[start].x0 - ordered[start - 1].x1 > 0.5 * em:
            break
        start -= 1
    variable = ordered[start:end]
    base = [char for char in variable if char.size >= 0.85 * em]
    base_text = "".join(char.get_text() for char in base)
    if (
        not base
        or not (
            re.fullmatch(r"[A-Za-zα-ωΑ-Ω]", base_text)
            or re.fullmatch(r"[A-Z]{2,3}", base_text)
        )
        or any(not char.get_text().isalnum() for char in variable)
        or len(variable) > 12
        or any(
            abs(_baseline(char) - _baseline(base[0])) > 0.65 * em for char in variable
        )
        or ordered[relation].x0 - variable[-1].x1 > 1.2 * em
    ):
        return []
    return ordered[start:]
