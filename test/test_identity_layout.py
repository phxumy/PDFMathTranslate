from pymupdf import Font
from types import SimpleNamespace

from pdf2zh.identity_layout import author_byline_chars, orcid_list_chars
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


def test_first_page_author_rows_retain_source_transforms_and_translate_affiliation():
    converter, page = page_with_lines(
        [
            "Experimental implementation of quantum gates",
            "A. Smith1, B. Jones2, C. White1,",
            "D. Black2 and E. Brown1",
            "1Department of Physics, Example University, China",
            "This experiment implements geometric gates.",
        ],
        fontname="Times-Roman",
    )
    selected = author_byline_chars(page)
    assert "".join(char.get_text() for char in page if char in selected) == (
        "A. Smith1, B. Jones2, C. White1,D. Black2 and E. Brown1"
    )
    original = [
        (char.get_text(), char.cid, char.matrix, char.size) for char in selected
    ]
    sources = []

    def translate(texts, *args, **kwargs):
        sources.extend(texts)
        return texts

    converter._translate_planned_segments = translate
    ops = converter.receive_layout(page)
    assert not any("Smith" in source or "Brown" in source for source in sources)
    assert any("Department of Physics" in source for source in sources)
    assert any("Experimental implementation" in source for source in sources)
    assert original == [
        (char.get_text(), char.cid, char.matrix, char.size) for char in selected
    ]
    for char in selected:
        matrix = " ".join(f"{v:f}" for v in char._pdf2zh_source_text_state.matrix)
        assert f"{matrix} Tm [<{char.cid:02x}>] TJ" in ops


def test_author_geometry_requires_first_page_affiliation_and_strict_name_rows():
    for lines in (
        ["A. Smith and B. Jones", "We study geometric quantum gates."],
        [
            "Experimental Implementation and Universal Quantum Gates",
            "Department of Physics, Example University",
        ],
        [
            "A. Smith and B. Jones measured the quantum gates",
            "Department of Physics, Example University",
        ],
    ):
        _, page = page_with_lines(lines, fontname="Times-Roman")
        assert not author_byline_chars(page)
    _, page = page_with_lines(
        ["A. Smith and B. Jones", "Department of Physics, Example University"],
        fontname="Times-Roman",
    )
    page.pageid = 2
    assert not author_byline_chars(page)


def test_numbered_affiliation_anchors_ligatures_and_overprinted_name_accents():
    _, page = page_with_lines(
        [
            "A study of continuous quantum gates",
            "Stefania Laza˘r1, A. Wallraﬀ2 and K. Hanke1",
            "1Department of Physics, ETH Zurich, Switzerland",
        ],
        fontname="Times-Roman",
    )
    selected = author_byline_chars(page)
    assert "".join(char.get_text() for char in page if char in selected) == (
        "Stefania Laza˘r1, A. Wallraff2 and K. Hanke1"
    )


def test_corporate_postal_address_anchors_author_rows_beside_orcid_icons():
    _, page = page_with_lines(
        [
            "What Is the Maximum Density of Microwave Control Lines?",
            "Nikolas Klemola Tango, Mar Francis De Guzman,",
            "Ville Nuutinen, Massimo Borrelli and Russell E. Lake",
            "Bluefors Oy, Arinatie 10, 00370 Helsinki, Finland",
            "Scaling superconducting computers requires denser wiring.",
        ],
        fontname="Times-Roman",
    )
    selected = author_byline_chars(page)
    assert "".join(char.get_text() for char in page if char in selected) == (
        "Nikolas Klemola Tango, Mar Francis De Guzman,"
        "Ville Nuutinen, Massimo Borrelli and Russell E. Lake"
    )


def test_marked_first_page_byline_keeps_glyphs_when_addresses_are_in_footer():
    converter, page = page_with_lines(
        [
            "Chiral ground-state currents of interacting photons",
            "P. Roushan1, C. Neill2, A. Megrant1, Y. Chen1,",
            "R. Babbush3, R. Barends1 and J. Martinis1,2",
            "The intriguing many-body phases arise from particle interactions.",
        ],
        fontname="Times-Roman",
    )
    selected = author_byline_chars(page)
    assert "".join(ch.get_text() for ch in page if ch in selected) == (
        "P. Roushan1, C. Neill2, A. Megrant1, Y. Chen1,"
        "R. Babbush3, R. Barends1 and J. Martinis1,2"
    )
    sources = []
    converter._translate_planned_segments = lambda texts, *a, **kw: (
        sources.extend(texts) or texts
    )
    ops = converter.receive_layout(page)
    assert not any("Roushan" in source for source in sources)
    assert any("many-body" in source for source in sources)
    for char in selected:
        matrix = " ".join(f"{v:f}" for v in char._pdf2zh_source_text_state.matrix)
        assert f"{matrix} Tm [<{char.cid:02x}>] TJ" in ops


def test_author_like_body_listing_without_markers_is_not_anchored():
    _, page = page_with_lines(
        ["A. Smith, B. Jones and C. White", "This is a discussion of related work."],
        fontname="Times-Roman",
    )
    assert not author_byline_chars(page)
