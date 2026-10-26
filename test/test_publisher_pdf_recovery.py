from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from pdfminer.psparser import LIT
from pdfminer.pdffont import PDFType1Font
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdftypes import PDFStream

from pdf2zh.converter import (
    _abandoned_reference_entries,
    _attach_overlapping_region_punctuation,
    _release_caption_title_words,
    _release_standalone_italic_headings,
    _split_running_header_chars,
    _split_parenthetical_variable_definitions,
    _split_inline_formula_items,
    _release_tex_accented_prose_words,
    _clipped_figure_word_chars,
    _expand_subsection_title_bounds,
    _release_reference_number_labels,
    _recover_author_information_affiliations,
    _should_extend_formula_run,
    _should_absorb_trailing_formula_base,
)
from pdf2zh.source_font import (
    repair_embedded_cff_encoding,
    repair_stix_replacement_mapping,
)
from test_italic_translation import FakeItalicChar, fake_run, paragraph


def _stix_font_spec(name="ABCDEF+STIXNonUnicode-Regular"):
    return {
        "Subtype": LIT("Type1"),
        "BaseFont": LIT(name),
        "FirstChar": 1,
        "Widths": [1000, 1000, 1000, 1000],
        "Encoding": {
            "BaseEncoding": LIT("WinAnsiEncoding"),
            "Differences": [
                1,
                LIT("uniE0D5"),
                LIT("uniE0D6"),
                LIT("A"),
                LIT("uniE0D5"),
            ],
        },
        "ToUnicode": PDFStream(
            {},
            b"begincmap\n1 begincodespacerange\n<00><FF>\nendcodespacerange\n"
            b"4 beginbfchar\n<01><FFFD>\n<02><FFFD>\n<03><0041>\n<04><002D>\n"
            b"endbfchar\nendcmap\n",
        ),
    }


def test_stix_broken_tounicode_recovers_known_dash_without_changing_source_glyph():
    spec = _stix_font_spec()
    font = PDFType1Font(PDFResourceManager(), spec)
    assert font.to_unichr(1) == "\ufffd"
    before = (font.char_width(1), font.char_disp(1), list(font.decode(b"\x01")))
    repair_stix_replacement_mapping(font, spec)
    assert font.to_unichr(1) == "—"
    assert (font.char_width(1), font.char_disp(1), list(font.decode(b"\x01"))) == before
    assert font.cid2unicode[1] == "\ue0d5"
    assert font.to_unichr(2) == "\ufffd"  # a different chemical-bond glyph
    assert font.to_unichr(3) == "A"
    assert font.to_unichr(4) == "-"  # explicit valid mapping wins
    repair_stix_replacement_mapping(font, spec)
    assert font.to_unichr(1) == "—"


@pytest.mark.parametrize(
    "name", ["OtherFont", "STIXTwoMath-Regular", "STIXNonUnicode-Regular-Unknown"]
)
def test_stix_recovery_does_not_guess_pua_meaning_in_other_fonts(name):
    spec = _stix_font_spec(name)
    font = PDFType1Font(PDFResourceManager(), spec)
    repair_stix_replacement_mapping(font, spec)
    assert font.to_unichr(1) == "\ufffd"


@pytest.mark.parametrize("field", ["Encoding", "ToUnicode"])
def test_stix_recovery_requires_both_explicit_pdf_mappings(field):
    spec = _stix_font_spec()
    font = PDFType1Font(PDFResourceManager(), spec)
    del spec[field]
    repair_stix_replacement_mapping(font, spec)
    assert font.to_unichr(1) == "\ufffd"


def test_cff_builtin_ligatures_recover_without_overriding_explicit_pdf_encoding():
    font = SimpleNamespace(cid2unicode={65: "A"})
    encoding = [".notdef"] * 256
    encoding[27], encoding[30], encoding[65] = "ff", "ffi", "A"
    stream = {"Subtype": LIT("Type1C")}
    spec = {"Subtype": LIT("Type1"), "FontDescriptor": {"FontFile3": stream}}
    with (
        patch("pdf2zh.source_font.stream_value") as value,
        patch("pdf2zh.source_font.CFFFontSet") as cff,
    ):
        value.return_value = SimpleNamespace(
            get=lambda key: LIT("Type1C"), get_data=lambda: b"font program"
        )
        cff.return_value.fontNames = ["Example"]
        cff.return_value.__getitem__.return_value.Encoding = encoding
        repair_embedded_cff_encoding(font, spec)
        assert font.cid2unicode[27] == "\ufb00"
        assert font.cid2unicode[30] == "\ufb03"
        assert font.cid2unicode[65] == "A"
        for field in ("Encoding", "ToUnicode"):
            protected = SimpleNamespace(cid2unicode={27: "explicit"})
            repair_embedded_cff_encoding(protected, {**spec, field: object()})
            assert protected.cid2unicode == {27: "explicit"}


@pytest.mark.parametrize("word", ["The", "Single-photon", "Signature", "Chiral"])
def test_caption_bar_does_not_hide_first_title_word(word):
    props = paragraph(region_kind="figure_caption")
    chars = fake_run("| " + word, fontname="Whitney-Semibold")
    text = ["Figure 2 {v0} currents in the ground state."]
    targets = [[]]
    _release_caption_title_words(text, [chars], [0], [props], targets)
    assert text == [f"Figure 2 | {word} currents in the ground state."]
    assert targets[0] == chars


def test_inline_absolute_value_remains_protected():
    props = paragraph(region_kind="figure_caption")
    text = ["Figure 2 shows {v0} currents."]
    _release_caption_title_words(
        text, [fake_run("|Chiral", fontname="Whitney-Semibold")], [0], [props], [[]]
    )
    assert text == ["Figure 2 shows {v0} currents."]


@pytest.mark.parametrize(
    "title",
    ["3.1. IQHE and phase transition", "4.2. Hofstadter butterflies of energy spectra"],
)
def test_numbered_italic_subsection_exposes_all_title_words(title):
    text = ["{v0}"]
    _release_standalone_italic_headings(
        text, [fake_run(title)], [0], [paragraph(region_kind="title")]
    )
    assert text == [title]


def test_top_margin_word_split_at_detector_border_retains_source_glyphs():
    chars = fake_run("ARTICLES", fontname="Whitney-Medium")
    for char in chars:
        char.y0 = 760
    layout = np.ones((800, 600))
    for char in chars[1:]:
        layout[int(char.y0), int(char.x0)] = 0
    assert _split_running_header_chars(chars, layout, 800) == {id(c) for c in chars}
    for char in chars:
        char.y0 = 600
    assert not _split_running_header_chars(chars, layout, 800)


def test_formula_commutator_keeps_bold_second_operand_inside_brackets():
    chars = fake_run("=(i/h)[H,", fontname="Times-Italic")
    for char in chars:
        char.y1 = char.y0 + char.size
    right = FakeItalicChar(
        "R", chars[-1].x1 + 1.6, chars[-1].x1 + 8.6, fontname="Times-Bold"
    )
    right.y1 = right.y0 + right.size
    assert _should_extend_formula_run(chars, right)


def test_bold_velocity_base_stays_with_equals_expression():
    base = FakeItalicChar("v", 20, 25, fontname="Times-Bold")
    equals = FakeItalicChar("=", 30, 36, fontname="MTSYN")
    base.y1 = equals.y1 = 110
    assert _should_absorb_trailing_formula_base(base, equals, "defined as v", 10)


def test_roman_qubit_base_keeps_actual_subscript_but_not_author_superscript():
    base = FakeItalicChar("Q", 20, 27, fontname="MinionPro-Regular")
    script = FakeItalicChar("j", 27, 30, fontname="MinionPro-It", size=6, baseline=99)
    base.y1, script.y1 = 110, 105
    assert _should_absorb_trailing_formula_base(base, script, "qubit Q", 10)
    script.y0, script.y1 = 106, 112
    assert not _should_absorb_trailing_formula_base(base, script, "author Q", 10)


def test_math_hat_stays_over_its_roman_base():
    hat = FakeItalicChar("ˆ", 20, 24, fontname="MTSYN")
    base = FakeItalicChar("n", 20, 25, fontname="MinionPro-Regular")
    hat.y1 = base.y1 = 110
    assert _should_extend_formula_run([hat], base)


def test_prose_comma_separates_qubit_and_particle_number_formula():
    chars = fake_run("Qj, n=a", fontname="MinionPro-Regular")
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    text = ["frequency of qubit {v0} is the particle number operator"]
    formulas, lines, offsets, ids, text_chars = [chars], [[]], [0], [0], [[]]
    _split_inline_formula_items(
        text, formulas, lines, offsets, ids, [props], text_chars
    )
    assert text == ["frequency of qubit {v0}, {v1} is the particle number operator"]
    assert ["".join(c.get_text() for c in run) for run in formulas] == ["Qj", "n=a"]
    bracketed = fake_run("[Qj, n]", fontname="MinionPro-Regular")
    protected = ["operator {v0}"]
    _split_inline_formula_items(protected, [bracketed], [[]], [0], [0], [props], [[]])
    assert protected == ["operator {v0}"]


def test_variable_definition_parenthesis_is_visible_to_translation():
    chars = fake_run("(a")
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    text = ["vectors {v0} is the lattice constant) denote the sites."]
    runs = [chars]
    _split_parenthetical_variable_definitions(text, runs, [[]], [0], [0], [props], [[]])
    assert text == ["vectors ({v0} is the lattice constant) denote the sites."]
    assert "".join(c.get_text() for c in runs[0]) == "a"


def test_tiny_overlapping_quote_region_does_not_split_reference():
    left = FakeItalicChar(",", 529.34, 532.50, fontname="Aptos", size=11.04)
    quote = FakeItalicChar('"', 531.16, 535.23, fontname="Aptos", size=11.04)
    assert _attach_overlapping_region_punctuation(
        left, quote, 3, 7, "plain text", "plain text"
    )
    assert not _attach_overlapping_region_punctuation(
        left, quote, 3, 0, "plain text", ""
    )
    quote.x0 = 550
    assert not _attach_overlapping_region_punctuation(
        left, quote, 3, 7, "plain text", "plain text"
    )


def test_abandoned_unfinished_reference_recovers_only_after_numbered_neighbor():
    texts = [
        "[5] D. P. DiVincenzo, The physical implementation of quantum computation, Phys. Rev. 48, 771 (2000).",
        "[6] M. Martin, C. Hughes, G. Moreno, E. Jones, D. Sickinger,",
        "S. Narumanchi, and R. Grout, Designing energy-efficient",
    ]
    lines = [fake_run(text, fontname="Times-Roman") for text in texts]
    for i, line in enumerate(lines):
        for char in line:
            char.y0 = 120 - 12 * i
            char.matrix = (1, 0, 0, 1, char.x0, char.y0)
    result = _abandoned_reference_entries(lines)
    assert len(result) == 2
    assert result[-1] == lines[1] + lines[2]
    assert not _abandoned_reference_entries(lines[1:])


def test_separate_roman_acute_accent_restores_complete_kagome_word():
    props = paragraph(region_kind="title")
    props.layout_class = 3
    prefix = fake_run("kagom", fontname="Times-Bold")
    accent = FakeItalicChar("´", 20, 24, fontname="Times-Bold")
    base = FakeItalicChar("e", 20, 24, fontname="Times-Bold")
    for char in prefix + [accent, base]:
        char.y1 = char.y0 + char.size
    text = ["A kagom {v0}e lattice"]
    _release_tex_accented_prose_words(text, [[accent]], [0], [props], [prefix + [base]])
    assert text == ["A kagomé lattice"]
    accent.fontname = "MTSYN"
    protected = ["A kagom{v0}e lattice"]
    _release_tex_accented_prose_words(
        protected, [[accent]], [0], [props], [prefix + [base]]
    )
    assert protected == ["A kagom{v0}e lattice"]


def test_chart_label_last_letter_outside_box_stays_in_original_figure():
    chars = fake_run("unitary", fontname="Arial")
    layout = np.ones((200, 200))
    for ch in chars[:-1]:
        layout[int(ch.y0), int(ch.x0)] = 0
    figure = SimpleNamespace(class_name="figure", bounds=(0, 90, 24, 120))
    assert _clipped_figure_word_chars(chars, layout, [figure]) == {id(c) for c in chars}
    assert not _clipped_figure_word_chars(chars, layout, [])


def test_short_subsection_heading_uses_empty_width_of_following_column():
    heading, body = paragraph(region_kind="title"), paragraph(region_kind="plain text")
    heading.x0, heading.x1, heading.y0 = 305, 429, 158
    body.x0, body.x1, body.y1 = 305, 550, 150
    _expand_subsection_title_bounds(
        ["4.1. IQHE and phase transition", "Ordinary body prose."], [heading, body], 595
    )
    assert heading.x1 == 550


def test_reference_label_split_from_author_row_is_rejoined_without_equation_release():
    marker, body = paragraph(region_kind="plain text"), paragraph(
        region_kind="plain text"
    )
    marker.layout_class = body.layout_class = 3
    marker.x, marker.x0, marker.x1 = 50, 50, 66
    body.x, body.x0 = 72, 72
    text = ["{v0}", "Wu, L.; Zhang, C. A selective inhibitor. Nature 10, 20 (2020)."]
    chars = fake_run("(13)", fontname="Times-Roman")
    _release_reference_number_labels(text, [chars], [[]], [0], [marker, body], [[], []])
    assert text == [
        "",
        "(13) Wu, L.; Zhang, C. A selective inhibitor. Nature 10, 20 (2020).",
    ]
    assert body.x == 50
    protected = ["{v0}", "H is the Hamiltonian of this system."]
    _release_reference_number_labels(
        protected, [chars], [[]], [0], [marker, body], [[], []]
    )
    assert protected[0] == "{v0}"


def test_contact_affiliation_style_runs_translate_without_changing_author_name():
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    text = ["Yongheng Chen {v0}, {v1}; Email: author@example.org"]
    runs = [
        fake_run("− Department of Oncology, Example University"),
        fake_run("Changsha 410008, China"),
    ]
    _recover_author_information_affiliations(text, runs, [0, 0], [props])
    assert "{v" not in text[0]
    assert props.affiliation_parts[0].role == "preserve"
    assert props.affiliation_parts[0].text == "Yongheng Chen − "
    assert props.affiliation_parts[1].role == "affiliation"
    assert "Department of Oncology" in props.affiliation_parts[1].text
    ordinary = paragraph(region_kind="plain text")
    ordinary.layout_class = 3
    source = ["The Hamiltonian {v0} is important."]
    _recover_author_information_affiliations(
        source, [fake_run("H=x+y")], [0], [ordinary]
    )
    assert source == ["The Hamiltonian {v0} is important."]
    assert not ordinary.affiliation_parts
