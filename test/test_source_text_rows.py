from pymupdf import Document
from pdf2zh.high_level import _build_layout_mask
from test_layout_overlap import _FakeLayout, _FakeBox
from test_code_blocks import page_with_lines


def test_supplement_title_recovers_from_header_abandon_region():
    with Document() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((50, 65), "Supplementary Note B.", fontsize=9)
        page.insert_text((160, 65), "EXPERIMENTAL SETUP", fontsize=9)
        page.insert_text((550, 30), "S2", fontsize=10)
        mask, kinds = _build_layout_mask(
            _FakeLayout([_FakeBox(0, (40, 15, 580, 70))], {0: "abandon"}),
            800,
            600,
            page,
        )
        assert kinds[int(mask[740, 55])] == "title"
        assert kinds[int(mask[740, 165])] == "title"
        assert mask[775, 552] == 0


def test_numbered_institution_rows_are_separate_from_the_byline():
    with Document() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((70, 70), "A. Smith and B. Jones", fontsize=10)
        page.insert_text(
            (70, 85), "1)Institute of Physics, Example University, Germany", fontsize=9
        )
        page.insert_text((70, 98), "2)Research Center, Mountain View, USA", fontsize=9)
        mask, kinds = _build_layout_mask(
            _FakeLayout([_FakeBox(0, (60, 55, 400, 105))], {0: "plain text"}),
            800,
            600,
            page,
        )
        assert kinds[int(mask[718, 72])] == "source_affiliation"
        assert kinds[int(mask[705, 72])] == "source_affiliation"
        assert mask[718, 72] != mask[705, 72]
        assert kinds[int(mask[735, 72])] == "plain text"


def test_expectation_value_brackets_keep_original_font_glyphs():
    converter, page = page_with_lines(
        ["The observable 〈ZZ〉 is measured."], fontname="Times-Roman"
    )
    draft = converter.receive_layout(page, preview_only=True)
    assert "〈" not in draft.sstk[0]
    assert any("〈" in text and "〉" in text for text in draft.formula_texts)


def test_single_unnumbered_institution_row_is_recovered():
    with Document() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text(
            (70, 85), "Institute of Physics, Example University, Germany", fontsize=9
        )
        mask, kinds = _build_layout_mask(
            _FakeLayout([_FakeBox(0, (60, 55, 400, 105))], {0: "plain text"}),
            800,
            600,
            page,
        )
        assert kinds[int(mask[718, 72])] == "source_affiliation"


def test_isolated_panel_letter_in_caption_box_stays_a_label():
    converter, page = page_with_lines(["a"], fontname="Times-Roman")
    draft = converter.receive_layout(page, preview_only=True)
    draft.pstk[0].region_kind = "figure_caption"
    draft.pstk[0].layout_class = 15
    result = converter._translate_planned_segments(
        draft.sstk, draft.pstk, draft.formula_texts, 600, 800
    )
    assert result == ["a"]
