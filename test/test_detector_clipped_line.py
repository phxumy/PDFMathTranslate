import pytest

from test_code_blocks import page_with_lines
from test_detector_boundary_text import recover

TEXT = "Let p. Recall that a function f is said to be sharp in the p norm if"


def narrow_box(text=TEXT):
    converter, page = page_with_lines([text], fontname="Times-Roman")
    chars = list(page)
    left = text.index("Recall") + 2
    right = text.index("norm") + 1
    converter.layout[0][:] = 1
    converter.layout[0][:, int(chars[left].x0) : int(chars[right].x0)] = 21
    converter.layout_region_types = {0: {21: "plain text"}}
    for i, c in enumerate(chars):
        if (
            text[i] in "pf"
            and (i == 0 or text[i - 1] == " ")
            and text[i + 1 : i + 2] in {" ", "."}
        ):
            c.fontname = "MathFont"
    return converter, page, chars, left, right


def test_both_clipped_edges_restore_one_line_even_after_math_or_punctuation():
    converter, page, chars, left, right = narrow_box()
    geometry = [(c.bbox, c.fontname, c.cid) for c in chars]
    mapping = recover(converter, chars)
    assert all(mapping[id(c)] == 21 for c in chars[:left] + chars[right:])
    assert [(c.bbox, c.fontname, c.cid) for c in chars] == geometry
    draft = converter.receive_layout(page, preview_only=True)
    assert len(draft.sstk) == 1
    assert "Recall that a function" in draft.sstk[0]
    assert "norm if" in draft.sstk[0]
    protected = [c for group in draft.var for c in group]
    assert all(c in protected for c in chars if c.fontname == "MathFont")


@pytest.mark.parametrize(
    "change",
    [
        "gap",
        "different_line",
        "protected",
        "title",
        "code",
        "scan",
        "rotation",
    ],
)
def test_clipped_edges_do_not_merge_across_non_prose_boundaries(change):
    converter, page, chars, left, right = narrow_box()
    middle = TEXT.index("function")
    protected = set()
    if change == "gap":
        for c in chars[middle:]:
            c.x0 += 15
            c.x1 += 15
    elif change == "different_line":
        for c in chars[middle:]:
            c.y0 -= 16
            c.y1 -= 16
            c._pdf2zh_source_text_state = None
    elif change in {"protected", "title"}:
        converter.layout[0][:, int(chars[middle].x0) : int(chars[middle + 1].x1)] = (
            0 if change == "protected" else 30
        )
        converter.layout_region_types[0][30] = "title"
    elif change == "code":
        protected.add(id(chars[middle]))
    elif change == "scan":
        chars[middle]._pdf2zh_scan_fragment = True
    elif change == "rotation":
        chars[middle].matrix = (0, 1, -1, 0, 0, 0)
    mapping = recover(converter, chars, protected)
    assert id(chars[0]) not in mapping
    assert id(chars[-1]) not in mapping


def test_two_clipped_label_words_without_sentence_context_are_not_expanded():
    converter, _, chars, _, _ = narrow_box("Recall p norm")
    mapping = recover(converter, chars)
    assert not mapping
