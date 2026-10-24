import pytest

from pdf2zh.translation_policy import (
    ExactReplacement,
    looks_like_reference_author_block,
)
from pdf2zh.translator import CodexTranslator


@pytest.mark.parametrize(
    "entry,title",
    [
        (
            "(1) Touat, M.; Ileana, E.; Postel-Vinay, S.; André, F.; Soria, J.-C. "
            "Targeting FGFR Signaling in Cancer. Clin. Cancer Res. 2015, 21 (12), 2684–2694.",
            "Targeting FGFR Signaling in Cancer",
        ),
        (
            "(2) Brooks, A. N.; Kilgour, E.; Smith, P. D. Molecular Pathways: "
            "Fibroblast Growth Factor Signaling: A New Therapeutic Opportunity in Cancer. "
            "Clin. Cancer Res. 2012, 18 (7), 1855–1862.",
            "Molecular Pathways: Fibroblast Growth Factor Signaling: A New Therapeutic Opportunity in Cancer",
        ),
    ],
)
def test_acs_author_initials_are_not_a_title_and_full_catalogue_is_preserved(
    entry, title
):
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh"
    translated, valid = translator._apply_reference_title_replacements(
        entry, [ExactReplacement(title, "完整的中文论文标题")]
    )
    assert valid
    assert translated == entry.replace(title, "完整的中文论文标题")
    if ":" in title or " in " in title:
        partial = title.split(":")[0].split(" in ")[0]
        assert not translator._apply_reference_title_replacements(
            entry, [ExactReplacement(partial, "部分标题")]
        )[1]


def test_semicolon_prose_does_not_become_author_metadata():
    assert not looks_like_reference_author_block("Smith, A.; This is ordinary prose")
    assert not looks_like_reference_author_block("(1) Novel Methods; Improved Results")
    assert (
        CodexTranslator._structured_reference_title_spans(
            "(1) Prepare the cells; measure the response. Nature 1, 2 (2020)."
        )
        == ()
    )


@pytest.mark.parametrize(
    "entry,title",
    [
        (
            "[47] H. Koeman and J. S. Bottman, Method and apparatus to measure far end crosstalk "
            "for the determination of equal level far end crosstalk, U.S. Patent No. 6,285,653 B1 (2001).",
            "Method and apparatus to measure far end crosstalk for the determination of equal level far end crosstalk",
        ),
        (
            "[41] D. M. Pozar, Microwave Engineering, 4th ed. (Wiley, Hoboken, NJ, 2011).",
            "Microwave Engineering",
        ),
    ],
)
def test_patent_and_edition_metadata_stay_outside_the_title(entry, title):
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh"
    assert translator._apply_reference_title_replacements(
        entry, [ExactReplacement(title, "完整的中文题名")]
    ) == (entry.replace(title, "完整的中文题名"), True)
    assert translator._apply_reference_title_replacements(
        entry + " ", [ExactReplacement(title, "完整的中文题名")]
    ) == (entry.replace(title, "完整的中文题名") + " ", True)
    assert not CodexTranslator._reference_complete_metadata_tail_is_safe(
        "4th method of measuring the spectrum (2020)."
    )


def test_long_multiword_author_list_does_not_translate_a_journal_name():
    title = "Cryo-CMOS bias-voltage generation and de-multiplexing at mK temperatures for large-scale arrays of quantum devices"
    entry = (
        "[8] J. van Staveren, L. Enthoven, P. L. Bavdaz, M. Meyer, C. D{v0}eprez, "
        "V. Nuutinen, R. Lake, D. Degli Esposti, C. Carlsson, A. Tosato, J. Gong, "
        "B. Prabowo, M. Babaie, C. G. Almudever, M. Veldhorst, G. Scappucci, and "
        f"F. Sebastiano, {title}, IEEE Trans. Quantum Eng. 6, 1 (2025)."
    )
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh"
    assert not translator._apply_reference_title_replacements(
        entry, [ExactReplacement("Quantum Eng", "量子工程")]
    )[1]


def test_literal_spectral_law_is_not_untranslated_title_prose():
    title = "Evolution of 1/f flux noise in superconducting qubits with weak magnetic fields"
    good = "弱磁场下超导量子比特中1/f磁通噪声的演化"
    assert not CodexTranslator._reference_title_has_unsafe_residue(title, good)
    assert CodexTranslator._reference_title_has_unsafe_residue(
        title, good.replace("1/f", "1/g")
    )
    assert CodexTranslator._reference_title_has_unsafe_residue(
        title, "Evolution of 1/f 磁通噪声"
    )


@pytest.mark.parametrize(
    "entry,title,target",
    [
        (
            "22. Hayward, A. L. C., Martin, A. M. & Greentree, A. D. Fractional quantum Hall physics in Jaynes–Cummings–Hubbard lattices. Phys. Rev. Lett. 108, 223602 (2012).",
            "Fractional quantum Hall physics in Jaynes–Cummings–Hubbard lattices",
            "Jaynes–Cummings–Hubbard 晶格中的分数量子霍尔物理",
        ),
        (
            "32. Nunnenkamp, A., Koch, J. & Girvin, S. M. Synthetic gauge fields and homodyne transmission in Jaynes–Cummings lattices. New J. Phys. 13, 095008 (2011).",
            "Synthetic gauge fields and homodyne transmission in Jaynes–Cummings lattices",
            "Jaynes–Cummings 晶格中的合成规范场与零差传输",
        ),
    ],
)
def test_inverted_comma_authors_and_named_physics_lattice(entry, title, target):
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh"
    assert translator._apply_reference_title_replacements(
        entry, [ExactReplacement(title, target)]
    )[1]
    assert not translator._apply_reference_title_replacements(
        entry, [ExactReplacement(title, target + " transmission")]
    )[1]


def test_parenthetical_subentries_translate_each_title_without_metadata():
    from pdf2zh.translation_policy import find_reference_markers

    entry = (
        "(7)(a) Zhang, P.; Yue, L. Targeting FGFR for cancer therapy. Nature 2024, 17, 39. "
        "(b) Xie, L.; Su, X. FGFR2 gene amplification predicts sensitivity. Science 2013, 19, 2583. "
    )
    titles = (
        "Targeting FGFR for cancer therapy",
        "FGFR2 gene amplification predicts sensitivity",
    )
    numbers = [m.number for m in find_reference_markers("No. ra6. " + entry)]
    assert 7 in numbers and 6 not in numbers
    assert CodexTranslator._structured_reference_title_spans(entry) == titles
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh"
    changed, valid = translator._apply_reference_title_replacements(
        entry,
        [
            ExactReplacement(titles[0], "靶向FGFR治疗癌症"),
            ExactReplacement(titles[1], "FGFR2基因扩增可预测敏感性"),
        ],
    )
    assert valid
    assert changed == entry.replace(titles[0], "靶向FGFR治疗癌症").replace(
        titles[1], "FGFR2基因扩增可预测敏感性"
    )


def test_conference_continuation_requires_complete_publication_tail():
    metadata = "Presented at IEEE International Conference on Quantum Computing and Engineering (QCE20), IEEE Quantum Week 2020 (IEEE, Piscataway, NJ, 2020). "
    assert CodexTranslator._reference_complete_metadata_tail_is_safe(metadata)
    assert not CodexTranslator._reference_complete_metadata_tail_is_safe(
        "Presented at a novel operating point"
    )


def test_corporate_website_title_keeps_the_url_literal():
    entry = "[40] Bluefors, High-density wiring, [[PDF2ZH_FLOW_2]] (accessed: November 20, 2025)."
    assert CodexTranslator._structured_reference_title_spans(entry) == (
        "High-density wiring",
    )


def test_parenthetical_author_list_et_al_and_drug_name_are_valid():
    title = "Discovery of lirafugratinib (RLY-4008), a highly selective irreversible small-molecule inhibitor of FGFR2"
    entry = f"(22) Smith, A.; Jones, B.; et al. {title}. {{v0}}."
    tr = CodexTranslator.__new__(CodexTranslator)
    tr.lang_out = "zh"
    assert tr._apply_reference_title_replacements(
        entry,
        [
            ExactReplacement(
                title,
                "lirafugratinib（RLY-4008）的发现：一种高选择性不可逆FGFR2小分子抑制剂",
            )
        ],
    )[1]
    assert not tr._apply_reference_title_replacements(
        entry,
        [
            ExactReplacement(
                title, "lirafugratinib（RLY-4008）的 highly selective 不可逆抑制剂"
            )
        ],
    )[1]


def test_receptor_ratio_cannot_become_a_reference_number():
    from pdf2zh.translation_policy import find_reference_markers

    text = "Selective inhibitors of FGFR 2/3. {v0}. (35) Smith, A. A title. Nature 2020, 1, 22."
    assert [m.number for m in find_reference_markers(text)] == [35]
