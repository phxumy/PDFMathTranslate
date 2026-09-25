from pdf2zh.converter import _recover_detector_boundary_text
from test_code_blocks import page_with_lines
from test_converter_translation_policy import FakeCodexTranslator


def setup(text, split_at, tail_class=3):
    converter, page = page_with_lines([text], fontname="Times-Roman")
    converter.layout[0][:] = 2
    chars = list(page)
    converter.layout[0][:, int(chars[split_at].x0) :] = tail_class
    converter.layout_region_types = {0: {2: "plain text", 3: "plain text"}}
    return converter, page, chars


def recover(converter, chars, protected=()):
    return _recover_detector_boundary_text(
        chars,
        converter.layout[0],
        converter.layout_region_types[0],
        set(protected),
        lambda font, text: "Math" in font or "Italic" in font,
    )


def test_split_word_crossing_two_plain_text_boxes_or_background_is_whole():
    for text, boundary, tail_class in (
        ("Keeping the modes", "des", 3),
        ("The coupling rates will", "ill", 1),
    ):
        converter, page, chars = setup(text, text.index(boundary), tail_class)
        recovered = recover(converter, chars)
        assert all(recovered[id(c)] == 2 for c in chars[text.rindex(" ") + 1 :])
        draft = converter.receive_layout(page, preview_only=True)
        assert draft.sstk == [text]


def test_complete_reference_marker_rejoins_reference_policy():
    text = "[10] A. Smith, First work title. Phys. Rev. Lett. 113, 220502 (2014)."
    converter, page, chars = setup(text, len(text) - 1, 2)
    converter.layout[0][:, : int(chars[1].x0)] = 1
    translator = FakeCodexTranslator()
    converter.translator = translator
    draft = converter.receive_layout(page, preview_only=True)
    assert draft.sstk == [text]
    converter.translation_policy.last_reference_number = 9
    result = converter._translate_planned_segments(
        draft.sstk, draft.pstk, formula_texts=[], page_width=600
    )
    assert translator.reference_entries == [text]
    assert "第一项作品题名" in result[0]
    assert "Phys. Rev. Lett. 113, 220502 (2014)." in result[0]


def test_real_spaces_formulas_protected_regions_and_labels_are_not_bridged():
    for change in (
        "space",
        "baseline",
        "font",
        "math_font",
        "italic_font",
        "rotation",
        "protected",
        "figure",
        "formula_region",
        "code",
        "scan_fragment",
    ):
        text = "Keeping the modes"
        boundary = text.index("des")
        converter, page, chars = setup(text, boundary)
        protected = set()
        if change == "space":
            for char in chars[boundary:]:
                char.x0 += 4
                char.x1 += 4
        elif change == "baseline":
            for char in chars[boundary:]:
                char.y0 += 4
                char.y1 += 4
        elif change == "font":
            chars[boundary].fontname = "OtherFont"
        elif change == "math_font":
            chars[boundary].fontname = "MathFont"
        elif change == "italic_font":
            chars[boundary].fontname = "Times-Italic"
        elif change == "rotation":
            chars[boundary].matrix = (0, 1, -1, 0, 0, 0)
        elif change == "protected":
            converter.layout[0][:, int(chars[boundary].x0) :] = 0
        elif change == "figure":
            converter.layout_region_types[0][3] = "figure"
        elif change == "formula_region":
            converter.layout_region_types[0][3] = "isolate_formula"
        elif change == "code":
            protected.add(id(chars[boundary]))
        elif change == "scan_fragment":
            chars[boundary]._pdf2zh_scan_fragment = True
        assert not recover(converter, chars, protected), change
    for text in ("modes", "ABC", "x yz"):
        converter, _, chars = setup(text, len(text) - 1)
        assert not recover(converter, chars), text


def test_arbitrary_openers_and_protected_number_markers_are_not_attached():
    for text in ("(10) Author", "[x] Author", "[10 Author", "[10] x", "[10]A", "[10]"):
        converter, _, chars = setup(text, len(text) - 1, 2)
        converter.layout[0][:, : int(chars[1].x0)] = 1
        assert not recover(converter, chars), text
    for change in ("gap", "font", "baseline", "protected", "figure", "code"):
        converter, _, chars = setup("[10] Author", 10, 2)
        converter.layout[0][:, : int(chars[1].x0)] = 1
        protected = set()
        if change == "gap":
            chars[0].x0 -= 3
            chars[0].x1 -= 3
        elif change == "font":
            chars[0].fontname = "OtherFont"
        elif change == "baseline":
            chars[0].y0 -= 3
        elif change == "protected":
            converter.layout[0][:, : int(chars[1].x0)] = 0
        elif change == "figure":
            converter.layout_region_types[0][2] = "figure"
        elif change == "code":
            protected.add(id(chars[0]))
        assert not recover(converter, chars, protected), change
