from types import SimpleNamespace

from pymupdf import Document, TEXTFLAGS_DICT, TEXT_PRESERVE_IMAGES, get_text_length

from pdf2zh.source_spelling import (
    SourceSpellingIndex,
    collect_source_spelling,
    normalize_source_wraps,
)


def normalize(text, lines, offsets=None):
    if offsets is None:
        offsets = [text.index("-") + 1]
    # Normalization unit cases supply explicit already-verified byline names.
    # Production geometry and ordinary-text rejection are tested separately.
    return normalize_source_wraps(
        text, offsets, SourceSpellingIndex.from_lines(lines, affiliation_names=lines)
    )


def test_recover_name_with_same_document_context_and_real_wrap():
    text = "with Maria Mon-tes at the laboratory."
    offset = text.index("-") + 1
    assert normalize(text, ["MARIA MONTES1"]) == (text, (offset,))
    assert normalize(text, ["MARIA MONTES"]) == (
        "with Maria Montes at the laboratory.",
        (offset - 1,),
    )
    assert normalize(text, ["MARIA MONTES"], []) == (text, ())


def test_context_and_whole_word_evidence_are_required():
    text = "with Maria Mon-tes and friends"
    for lines in (
        ["MONTES"],
        ["ANNA MONTES"],
        ["MARIA MONTESANO"],
        ["MARIA MONTES_2"],
        ["MARIA", "MONTES"],
        ["MARIA MON-", "TES"],
        ["MARIA O'MONTES"],
    ):
        assert normalize(text, lines)[0] == text


def test_explicit_hyphenated_spelling_vetoes_even_matching_context():
    text = "Ann Ma-rie participated."
    for hyphen in ("-", "\u2010", "\u2011"):
        assert normalize(text, ["Ann Marie", f"Another Ma{hyphen}rie"])[0] == text
    assert normalize(text, ["Ann Marie"])[0] == "Ann Marie participated."


def test_names_without_evidence_and_ordinary_prose_are_preserved():
    for text, lines in (
        ("A. Mo-ntes wrote this.", ["MARIA MONTES"]),
        ("Maria Mon-tes wrote this.", ["MARIA MONTS"]),
        ("We re-cover the surface.", ["We recover everything."]),
        ("Anna Smith-Jones wrote this.", ["ANNA SMITHJONES"]),
        ("Maria Mon-tes2 wrote this.", ["MARIA MONTES"]),
        ("Maria Mon-tes-Other wrote this.", ["MARIA MONTES"]),
        ("Maria Mon-tes{v1} wrote this.", ["MARIA MONTES"]),
        ("Maria Mon-{v1}tes wrote this.", ["MARIA MONTES"]),
        ("Maria Mon-[[PDF2ZH_ITALIC_0]]tes", ["MARIA MONTES"]),
    ):
        assert normalize(text, lines)[0] == text


def test_unicode_names_multiple_wraps_and_offsets():
    text = "Élise Bé-rard met Maria Mon- tes. More text."
    offsets = [text.index("-") + 1, text.rindex("-") + 1, len(text) - 5]
    normalized, shifted = normalize(text, ["ÉLISE BÉRARD", "MARIA MONTES"], offsets)
    assert normalized == "Élise Bérard met Maria Montes. More text."
    assert shifted == (offsets[0] - 1, offsets[1] - 2, offsets[2] - 3)


def test_invalid_and_duplicate_offsets_do_not_change_other_text():
    text = "Maria Mon-tes"
    offset = text.index("-") + 1
    assert normalize(text, ["MARIA MONTES"], [-1, 0, len(text)]) == (
        text,
        (-1, 0, len(text)),
    )
    assert normalize(text, ["MARIA MONTES"], [offset, offset]) == (
        "Maria Montes",
        (offset - 1, offset - 1),
    )


def span(text, x0, x1, *, y=100, size=10, flags=0):
    return {
        "text": text,
        "origin": (x0, y),
        "bbox": (x0, y - size, x1, y),
        "size": size,
        "flags": flags,
    }


def document(*lines):
    content = {"blocks": [{"lines": lines}]}

    def get_text(mode, *, flags):
        assert mode == "dict"
        assert flags == TEXTFLAGS_DICT & ~TEXT_PRESERVE_IMAGES
        return content

    return [SimpleNamespace(get_text=get_text)]


def test_collect_source_handles_affiliation_superscripts_and_split_spans():
    index = collect_source_spelling(
        document(
            {
                "spans": [
                    span(", MARIA ", 0, 40),
                    span("MONTES", 40, 90),
                    span("1", 90, 95, y=96, size=7, flags=1),
                    span(",", 96, 99),
                ]
            },
            {"spans": [span("ANNA MONTES1", 0, 90)]},
            {
                "spans": [
                    span("JULIA", 0, 30),
                    span("MONTES", 34, 80),
                    span("2", 80, 85, y=96, size=7, flags=1),
                ]
            },
        )
    )
    assert ("maria", "montes") in index.contexts
    assert ("julia", "montes") in index.contexts
    assert ("anna", "montes") not in index.contexts


def test_collect_source_ignores_rotated_text_columns_and_shifted_baselines():
    index = collect_source_spelling(
        document(
            {"dir": (0, -1), "spans": [span("MARIA MONTES", 0, 90)]},
            {"wmode": 1, "spans": [span("MARIA MONTES", 0, 90)]},
            {"spans": [span("MARIA", 0, 30), span("MONTES", 200, 250)]},
            {"spans": [span("MARIA", 0, 30), span("MONTES", 34, 80, y=120)]},
        )
    )
    assert not index.contexts


def test_collect_real_pdf_keeps_superscript_affiliations_out_of_name_spelling():
    with Document() as pdf:
        page = pdf.new_page()
        text = "MARIA MONTES"
        size = 12
        page.insert_text((72, 100), text, fontname="helv", fontsize=size)
        name_end = 72 + get_text_length(text, fontname="helv", fontsize=size)
        page.insert_text((name_end, 96), "1", fontname="helv", fontsize=8)
        next_start = name_end + 6
        next_name = ", ANNA STONE"
        page.insert_text((next_start, 100), next_name, fontname="helv", fontsize=size)
        next_end = next_start + get_text_length(
            next_name, fontname="helv", fontsize=size
        )
        page.insert_text((next_end, 96), "2", fontname="helv", fontsize=8)
        page.insert_text((72, 140), "ANNA MONTES1", fontname="helv", fontsize=size)

        index = collect_source_spelling(pdf)
        assert ("maria", "montes") in index.contexts
        assert ("anna", "montes") not in index.contexts
        wrapped = "Maria Mon-tes contributed."
        assert normalize_source_wraps(wrapped, [wrapped.index("-") + 1], index) == (
            "Maria Montes contributed.",
            (9,),
        )


def test_ordinary_title_case_never_provides_name_evidence():
    for title in ("The Recreation Of A Pattern", "Maria Montes"):
        assert not SourceSpellingIndex.from_lines([title]).contexts
        assert not collect_source_spelling(
            document({"spans": [span(title, 0, 120)]})
        ).contexts


def test_sentence_citations_and_detached_numbers_are_not_affiliations():
    for text in (
        "The Recreation Of A Pattern",
        "The Recreation",
        "We Thank Maria Montes",
        "Experiments Were Performed By Maria Montes",
        "Measurements, Maria Montes",
        "Maria Montes.",
        "Maria Montes Reported Results",
    ):
        index = collect_source_spelling(
            document(
                {
                    "spans": [
                        span(text, 0, 120),
                        span("1", 120, 125, y=96, size=7, flags=1),
                    ]
                }
            )
        )
        assert not index.contexts, text
    for number in (
        span("1", 120, 125, y=100, size=7, flags=1),
        span("1", 150, 155, y=96, size=7, flags=1),
        span("1", 120, 125, y=96, size=10, flags=1),
    ):
        assert not collect_source_spelling(
            document({"spans": [span("Maria Montes", 0, 120), number]})
        ).contexts


def test_comma_separated_byline_with_numeric_affiliations():
    index = collect_source_spelling(
        document(
            {
                "spans": [
                    span(", MARIA MONTES", 0, 90),
                    span("1", 90, 95, y=96, size=7, flags=1),
                    span(", ANNA STONE", 96, 190),
                    span("2", 190, 195, y=96, size=7, flags=1),
                    span(",", 196, 200),
                ]
            }
        )
    )
    assert index.contexts == {("maria", "montes"), ("anna", "stone")}


def test_combining_accents_do_not_supply_unaccented_or_partial_word_evidence():
    for marked in ("MARIA MONTES\u0301", "MARIA MONTES\u0338"):
        index = SourceSpellingIndex.from_lines([], affiliation_names=[marked])
        assert ("maria", "montes") not in index.contexts
        text = "Maria Mon-tes"
        assert normalize_source_wraps(text, [10], index)[0] == text
    for text in ("Maria Mon-tes\u0338", "Ma\u0338ria Mon-tes"):
        index = SourceSpellingIndex.from_lines([], affiliation_names=["MARIA MONTES"])
        assert normalize_source_wraps(text, [text.index("-") + 1], index)[0] == text


def test_decomposed_accent_evidence_can_match_same_precomposed_name():
    index = SourceSpellingIndex.from_lines(
        [], affiliation_names=["E\u0301LISE BE\u0301RARD"]
    )
    text = "Élise Bé-rard"
    assert (
        normalize_source_wraps(text, [text.index("-") + 1], index)[0] == "Élise Bérard"
    )


def test_author_list_structure_is_required_within_one_block():
    rows = [
        {
            "spans": [
                span(text, 0, 120),
                span("1", 120, 125, y=96, size=7, flags=1),
            ]
        }
        for text in ("New Results", "Further Measurements", ", MARIA MONTES")
    ]
    for row in rows:
        assert not collect_source_spelling(document(row)).contexts

    def get_text(mode, *, flags):
        return {"blocks": [{"lines": [row]} for row in rows]}

    # Different heading blocks must never be pooled into an author list.
    assert not collect_source_spelling([SimpleNamespace(get_text=get_text)]).contexts
    # Two name-like headings lacking list punctuation are also insufficient.
    assert not collect_source_spelling(document(*rows[:2])).contexts


def test_multiple_component_names_are_allowed_in_verified_author_lists():
    index = collect_source_spelling(
        document(
            {
                "spans": [
                    span("MARIA ELENA MONTES", 0, 120),
                    span("1", 120, 125, y=96, size=7, flags=1),
                    span(", ANNA STONE", 126, 220),
                    span("2", 220, 225, y=96, size=7, flags=1),
                ]
            }
        )
    )
    assert ("elena", "montes") in index.contexts
