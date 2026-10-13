from types import SimpleNamespace

import pytest

from pdf2zh.converter import _restore_wrapped_source_uris


def make_case():
    first, second = "https://doi.org/10.1016/j.acha.", "2014.09.004"
    chars, links = [], []
    for text, start, y in ((first, 100, 100), (second, 30, 88)):
        row = [SimpleNamespace(get_text=lambda value=value: value,
                x0=start+4*i, x1=start+4*i+4, y0=y-2, y1=y+8, size=10,
                matrix=(1, 0, 0, 1, start+4*i, y), _pdf2zh_layout_class=2)
               for i, value in enumerate(text)]
        chars.extend(row)
        links.append((first+second, (start-1, y-3, row[-1].x1+1, y+9)))
    source = "A reference, " + first + " " + second + ". Next reference."
    paragraph = SimpleNamespace(layout_class=2, region_kind="plain text", size=10,
        break_offsets=[source.index(second), source.index("Next")])
    return first+second, source, paragraph, chars, links


def test_annotation_and_two_glyph_rows_restore_wrapped_doi_and_offsets():
    uri, source, paragraph, chars, links = make_case()
    segments, offsets = [source], list(paragraph.break_offsets)
    before = [(c.get_text(), c.x0, c.x1, c.y0, c.y1, c.matrix) for c in chars]
    _restore_wrapped_source_uris(segments, [paragraph], [chars], links)
    assert segments == ["A reference, " + uri + ". Next reference."]
    assert paragraph.break_offsets == [offset-1 for offset in offsets]
    assert [(c.get_text(), c.x0, c.x1, c.y0, c.y1, c.matrix) for c in chars] == before


@pytest.mark.parametrize("change", [
    "single_annotation", "different_destination", "wrong_glyph", "same_baseline",
    "far_rows", "glyph_gap", "protected_glyph", "rotated_glyph", "small_glyph",
    "two_rows_in_one_rectangle", "wrong_rectangle", "duplicate_source",
    "longer_path", "longer_dot_suffix", "figure", "real_space_glyph", "different_paragraph",
])
def test_unproven_uri_spaces_are_preserved(change):
    uri, source, paragraph, chars, links = make_case()
    if change == "single_annotation":
        links.pop()
    elif change == "different_destination":
        links[1] = (uri + "0", links[1][1])
    elif change == "wrong_glyph":
        chars[-1].get_text = lambda: "5"
    elif change == "same_baseline":
        for c in chars[-10:]:
            c.matrix = (*c.matrix[:5], 100)
            c.y0 += 12
            c.y1 += 12
    elif change == "far_rows":
        for c in chars[-10:]:
            c.matrix = (*c.matrix[:5], 60)
            c.y0 -= 28
            c.y1 -= 28
    elif change == "glyph_gap":
        chars[1].x0 += 4
    elif change == "protected_glyph":
        chars[-1]._pdf2zh_layout_class = 0
    elif change == "rotated_glyph":
        chars[-1].matrix = (0, 1, -1, 0, 0, 88)
    elif change == "small_glyph":
        chars[-1].size = 6
    elif change == "two_rows_in_one_rectangle":
        links[0] = (uri, (0, 80, 250, 110))
    elif change == "wrong_rectangle":
        links[1] = (uri, (300, 80, 360, 100))
    elif change == "duplicate_source":
        source += " " + source
    elif change in {"longer_path", "longer_dot_suffix"}:
        source = source.replace("2014.09.004.", "2014.09.004" + ("/extra." if change == "longer_path" else ".extra."))
    elif change == "figure":
        paragraph.region_kind = "figure"
    elif change == "real_space_glyph":
        chars[1].get_text = lambda: " "
    elif change == "different_paragraph":
        chars = chars[:-10]
    segments, before_offsets = [source], list(paragraph.break_offsets)
    _restore_wrapped_source_uris(segments, [paragraph], [chars], links)
    assert segments == [source]
    assert paragraph.break_offsets == before_offsets
