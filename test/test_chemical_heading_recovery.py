from pdf2zh.converter import _release_bold_chemical_names
from test_italic_translation import fake_run, paragraph
from pdf2zh.converter import _merge_adjacent_reference_tail_lines


def test_bold_systematic_name_suffix_reaches_translation_but_nmr_stays_protected():
    text = [
        "tert-Butyl 4-Amino-8,9-dihydropyrazino[1,2]pyrimidine-7(6H{v0}. Compound 15a was dissolved. NMR {v1}."
    ]
    formulas = [
        fake_run(")-carboxylate(16a)", fontname="MyriadPro-Semibold"),
        fake_run("(s,1H),6.95(s,2H)", fontname="ArnoPro-Regular"),
    ]
    _release_bold_chemical_names(
        text, formulas, [0, 0], [paragraph(region_kind="plain text")], [[]]
    )
    assert ")-carboxylate(16a)" in text[0] and "{v0}" not in text[0]
    assert "NMR {v1}" in text[0]


def test_ordinary_bold_math_and_spectrum_are_never_expanded():
    text = ["The formula is {v0}."]
    _release_bold_chemical_names(
        text,
        [fake_run("(x+y)", fontname="Times-Bold")],
        [0],
        [paragraph(region_kind="plain text")],
        [[]],
    )
    assert text == ["The formula is {v0}."]


def test_adjacent_split_reference_title_keeps_its_catalogue_and_formula_owner():
    before, after = paragraph(region_kind="plain text"), paragraph(
        region_kind="plain text"
    )
    before.size = after.size = 8
    before.x0 = 50
    before.x1 = 290
    before.y0 = 100
    after.x0 = 65
    after.x1 = 250
    after.y0 = 89
    after.y1 = 97
    text = [
        "56. Hammerer, K., Vidal, G. & Cirac, J. I. Characterization of nonlocal",
        "gates. {v0} 66, 062321 (2002).",
    ]
    owners = [1]
    _merge_adjacent_reference_tail_lines(
        text, [fake_run("Phys. Rev. A")], owners, [before, after], [[], []]
    )
    assert text[0].endswith("nonlocal gates. {v0} 66, 062321 (2002).")
    assert text[1] == "" and owners == [0]


def test_reference_tail_never_consumes_next_numbered_entry():
    before, after = paragraph(region_kind="plain text"), paragraph(
        region_kind="plain text"
    )
    before.size = after.size = 8
    before.x0 = after.x0 = 50
    before.y0 = 100
    after.y1 = 97
    original = [
        "56. Hammerer, K., Vidal, G. & Cirac, J. I. Incomplete title",
        "57. Smith, A. A different title. Nature 12, 34 (2020).",
    ]
    text = list(original)
    _merge_adjacent_reference_tail_lines(text, [], [], [before, after], [[], []])
    assert text == original
