from dataclasses import replace

from pdf2zh.reading_flow import detect_reference_continuation
from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translator import CodexTranslator
from test_reference_continuation import segment


def test_separate_prefix_block_uses_adjacent_entry_and_source_catalogue():
    left = segment(
        0, 0, "(12) Smith, A.; Jones, B.; White, C.;", column=0, y0=50, y1=200
    )
    prefix = segment(
        0, 1, "Brown, D. A reliable scientific title. {v0}. ", column=1, y0=700, y1=750
    )
    prefix = replace(prefix, formula_texts=("J.Med.Chem.2022,65(22),15433−15442",))
    following = segment(
        0,
        2,
        "(13) Green, E.; Black, F. Another work. Nature 12, 34 (2020).",
        column=1,
        y0=650,
        y1=698,
    )
    result = detect_reference_continuation(
        [left, prefix, following], [left, prefix, following], allow_formulas=True
    )
    assert result is not None and result.reference_number == 12
    assert result.fragments[1].ref == prefix.ref
    assert result.fragments[1].end == len(prefix.text)
    assert (
        detect_reference_continuation(
            [left], [replace(prefix, formula_texts=()), following], allow_formulas=True
        )
        is None
    )


def test_title_after_continued_authors_uses_resolved_readonly_catalogue():
    tr = CodexTranslator.__new__(CodexTranslator)
    tr.lang_out = "zh"
    left = "(12) Smith, A.; Jones, B.; "
    title = "A reliable scientific title"
    right = "White, C. " + title + ". {v0}. "
    result = tr._reference_continuation_replacements(
        left,
        right,
        [ExactReplacement(title, "可靠的科研题名")],
        formula_context={"{v0}": "J.Med.Chem.2022,65(22),15433−15442"},
    )
    assert result == (
        ExactReplacement("", ""),
        ExactReplacement(title, "可靠的科研题名"),
    )
    assert (
        tr._reference_continuation_replacements(
            left, right, [ExactReplacement(title, "可靠的科研题名")]
        )
        is None
    )
