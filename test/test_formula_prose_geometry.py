from types import SimpleNamespace

import pytest

from pdf2zh.converter import Paragraph, _repair_leading_whitespace_anchors
from pdf2zh.formula_prose import definition_rows, image_adjacent_prose, image_clusters


def text(value, x, y, *, size=10, font="Times-Roman"):
    chars = []
    for letter in value:
        width = 2.5 if letter.isspace() else size * 0.45
        chars.append(
            SimpleNamespace(
                get_text=lambda letter=letter: letter,
                x0=x,
                x1=x + width,
                y0=y,
                y1=y + size,
                size=size,
                fontname=font,
                matrix=(1, 0, 0, 1, x, y),
            )
        )
        x += width
    return chars


def definitions(
    *, spacing=24, subscript_drop=1.5, extra_column=False, font="Times-Italic"
):
    chars = []
    for index, suffix in enumerate(("eff", "obs", "eof")):
        y = 100 - index * spacing
        chars += text("m", 20, y, font=font)
        chars += text(suffix, 25, y - subscript_drop, size=6.5, font=font)
        chars += text("the measured mobility of the sample", 60, y)
        if extra_column:
            chars += text("another unrelated value column", 290, y)
    return chars


def test_aligned_variable_definitions_keep_each_script_with_its_own_row():
    chars = definitions()
    slots = definition_rows(chars)
    assert len(slots) == 3
    assert all(slot.kind == "formula_definition" for slot in slots)
    assert [
        "".join(char.get_text() for char in slot.chars).split("the")[0]
        for slot in slots
    ] == ["meff", "mobs", "meof"]
    assert {id(char) for slot in slots for char in slot.chars} == {
        id(char) for char in chars
    }


def test_definition_detection_rejects_ambiguous_script_rows_and_multicolumn_tables():
    assert not definition_rows(definitions(spacing=11, subscript_drop=6))
    assert not definition_rows(definitions(extra_column=True))
    assert not definition_rows(definitions(font="Times-Roman"))
    chars = definitions()
    assert not definition_rows(chars[: len(chars) * 2 // 3])
    assert not definition_rows(text("   ", 20, 100))


def test_inline_image_strips_form_one_occupied_region_and_split_neighboring_text():
    figures = [
        SimpleNamespace(bbox=(100, 94, 119, 100)),
        SimpleNamespace(bbox=(121.16, 94, 150, 100)),
        SimpleNamespace(bbox=(100, 102.16, 150, 103)),
        SimpleNamespace(bbox=(119, 105.16, 130, 111)),
    ]
    clusters = image_clusters(figures)
    assert clusters == [(100, 94, 150, 111)]
    left = text("the value equals", 20, 100)
    right = text("and the result follows", 153, 100)
    left[-1].x1 = 98
    slots = image_adjacent_prose(left + right, clusters)
    assert len(slots) == 2
    assert ["".join(c.get_text() for c in slot.chars) for slot in slots] == [
        "the value equals",
        "and the result follows",
    ]
    assert all(slot.kind == "formula_prose" for slot in slots)


def test_assignment_symbol_stays_with_the_following_unchanged_image_operand():
    intro = text("calculated through ", 10, 100)
    variable = text("CL", 100, 100) + text("int", 109, 98.5, size=6.5)
    relation = text(" = ", 119, 100)
    after = text(", and the values", 185, 100)
    slots = image_adjacent_prose(
        intro + variable + relation + after, [(130, 94, 182, 111)]
    )
    assert [slot.kind for slot in slots] == [
        "formula_prose",
        "fixed_math",
        "formula_prose",
    ]
    assert "".join(char.get_text() for char in slots[1].chars) == "CLint = "
    assert all(char not in slots[0].chars for char in variable + relation)


@pytest.mark.parametrize(
    "box", [(30, 95, 150, 111), (100, 50, 150, 130), (350, 94, 400, 111)]
)
def test_overlapping_tall_or_distant_graphics_do_not_split_prose(box):
    chars = text("ordinary prose goes here", 20, 100) + text(
        "and continues here", 153, 100
    )
    assert not image_adjacent_prose(chars, [box])


def test_leading_blank_on_previous_heading_row_cannot_anchor_body_text():
    blank = text(" ", 300, 125)
    body = text("The body follows", 20, 100)
    paragraph = Paragraph(
        125, 300, 20, 350, 100, 135, 10, True, region_kind="plain text"
    )
    _repair_leading_whitespace_anchors(
        [" The body follows"], [paragraph], [], [blank + body]
    )
    assert (paragraph.x, paragraph.y, paragraph.y1) == (20, 100, 110)


def test_definition_description_uses_first_visible_column_not_gap_whitespace():
    chars = text(" ", 30, 100) + text("the measured quantity", 60, 100)
    paragraph = Paragraph(
        100, 30, 30, 180, 100, 110, 10, False, region_kind="formula_definition"
    )
    _repair_leading_whitespace_anchors(
        [" the measured quantity"], [paragraph], [], [chars]
    )
    assert paragraph.x == paragraph.x0 == 60


def test_same_line_ordinary_indent_and_formula_prefix_are_not_reanchored():
    chars = text(" ", 30, 100) + text("The body", 60, 100)
    paragraph = Paragraph(
        100, 30, 30, 120, 100, 110, 10, False, region_kind="plain text"
    )
    _repair_leading_whitespace_anchors([" The body"], [paragraph], [], [chars])
    assert paragraph.x == 30
    _repair_leading_whitespace_anchors([" {v0} The body"], [paragraph], [], [chars])
    assert paragraph.x == 30
