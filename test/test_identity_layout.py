from pymupdf import Font
from types import SimpleNamespace

from pdf2zh.identity_layout import orcid_list_chars
from test_code_blocks import page_with_lines


def test_orcid_rows_keep_original_positions_and_never_reach_translator():
    converter, page = page_with_lines(
        [
            "Overview of author identifiers.",
            "Alice Smith https://orcid.org/0000-0001-9560-9932",
            "Bob Jones https://orcid.org/0000-0002-2578-306X",
            "Discussion of the result.",
        ],
        fontname="Times-Roman",
    )
    selected = orcid_list_chars(page)
    assert len(selected) > 80
    sources = []

    def translate(texts, *args, **kwargs):
        sources.extend(texts)
        return texts

    converter._translate_planned_segments = translate
    ops = converter.receive_layout(page)
    assert not any("orcid" in source for source in sources)
    assert any("Overview" in source for source in sources)
    for char in selected:
        matrix = " ".join(f"{v:f}" for v in char._pdf2zh_source_text_state.matrix)
        assert f"{matrix} Tm [<{char.cid:02x}>] TJ" in ops


def test_single_orcid_or_prose_mentions_do_not_preserve_body_text():
    for lines in (
        ["Alice Smith https://orcid.org/0000-0001-9560-9932"],
        [
            "See https://orcid.org/0000-0001-9560-9932 for details.",
            "See https://orcid.org/0000-0002-2578-306X for details.",
        ],
    ):
        _, page = page_with_lines(lines, fontname="Times-Roman")
        assert not orcid_list_chars(page)


def test_extended_latin_characters_use_unicode_fallback_not_glyph_zero():
    converter, page = page_with_lines(["Author names"], fontname="Times-Roman")
    converter.noto = SimpleNamespace(has_glyph=lambda codepoint: 0)
    converter.latin = Font("tiro")
    converter.latin_name = "pdf2zh_latin"
    converter._translate_planned_segments = lambda *args, **kwargs: ["Łł"]
    assert not converter.noto.has_glyph(ord("Ł"))
    assert not converter.noto.has_glyph(ord("ł"))
    ops = converter.receive_layout(page)
    glyphs = "".join(f"{converter.latin.has_glyph(ord(c)):04x}" for c in "Łł")
    assert "/pdf2zh_latin " in ops
    assert f"[<{glyphs}>] TJ" in ops
    assert "0000" not in glyphs


