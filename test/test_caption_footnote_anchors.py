import pytest

from pdf2zh.converter import _restore_caption_footnote_anchors
from test_italic_translation import FakeItalicChar, paragraph


def fixture():
    caption = paragraph(region_kind="table_caption")
    caption.y0 = 690
    caption.page_id = 3
    note = paragraph(region_kind="table_cell")
    note.y0 = 500
    note.page_id = 3
    mark = FakeItalicChar("a", 102, 105, size=6, baseline=704)
    base = FakeItalicChar("3", 96, 102, size=10, baseline=700)
    foot = FakeItalicChar("a", 50, 53, size=6, baseline=504)
    return (
        ["Table 1. Profiling of Leads 2 and 3{v0}", "{v1}"],
        ["表1。先导化合物2和3{v0}的特性分析", "{v1}"],
        [caption, note],
        [[mark], [foot]],
        [0, 1],
        [[base], []],
    )


def test_title_footnote_moves_after_complete_translation_with_matching_note():
    data = fixture()
    _restore_caption_footnote_anchors(*data)
    assert data[1][0] == "表1。先导化合物2和3的特性分析{v0}"
    assert data[3][0][0].get_text() == "a"
    assert data[3][0][0].x0 == 102


def test_caption_footnote_allows_small_source_kerning_overlap():
    data = fixture()
    data[3][0][0].x0 = 101.96
    _restore_caption_footnote_anchors(*data)
    assert data[1][0] == "表1。先导化合物2和3的特性分析{v0}"


@pytest.mark.parametrize(
    "case",
    ["body", "same_size", "baseline", "no_note", "exponent", "duplicate", "math_font"],
)
def test_ambiguous_or_non_caption_markers_remain_in_place(case):
    data = fixture()
    if case == "body":
        data[2][0].region_kind = "plain text"
    elif case == "same_size":
        data[3][0][0].size = 10
    elif case == "baseline":
        data[3][0][0].y0 = 700
    elif case == "no_note":
        data[3][1][0]._text = "b"
    elif case == "exponent":
        data[0][0] = "Table 1. Values of x{v0}"
    elif case == "duplicate":
        data[1][0] += "{v0}"
    elif case == "math_font":
        data[3][0][0].fontname = "STIXMath-Regular"
    before = list(data[1])
    _restore_caption_footnote_anchors(*data)
    assert data[1] == before
