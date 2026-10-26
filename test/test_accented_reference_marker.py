from pdf2zh.converter import _release_reference_number_labels
from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    SourceSegment,
    ROLE_REFERENCE,
)
from test_italic_translation import fake_run, paragraph


def test_split_author_accent_does_not_break_reference_number_sequence():
    source = "(13) Smith, A.; Jones, B. Clinical cancer biology. Nature 2020, 10 (2), 100-110. {v0} Fuca{v1}, G.; Jones, B. Resistance in human cancer. Nature 2021, 11 (3), 120-130. (15) Chen, C.; Smith, A. Cancer dependencies. Nature 2022, 12 (4), 140-150."
    texts = [source]
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    _release_reference_number_labels(
        texts, [fake_run("(14)"), fake_run("\u0300")], [[], []], [0, 0], [props], [[]]
    )
    assert "(14) Fuca{v1}" in texts[0]
    policy = DocumentTranslationPolicy()
    plan = policy.plan_segment(
        SourceSegment(0, texts[0], region_kind="plain text"),
        formula_texts=["(14)", "\u0300"],
    )
    assert len(plan.parts) == 3
    assert all(part.role == ROLE_REFERENCE for part in plan.parts)
    assert policy.last_reference_number == 15
    following = "(16) Jones, B.; Chen, C. Drug discovery in human cells. Nature 2023, 13 (5), 160-170."
    assert (
        policy.plan_segment(SourceSegment(1, following, region_kind="plain text"))
        .parts[0]
        .role
        == ROLE_REFERENCE
    )


def test_next_reference_opener_is_released_from_preceding_publication_data():
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    texts = [
        "Rao, Y.; Smith, A. A complete scientific title. Cancer Res. 2025, 85 (8_Supplement_1{v0}26) Structures of candidate drugs. Nature 2025, 10, 20-30."
    ]
    formulas = [fake_run("),6393.(")]
    _release_reference_number_labels(texts, formulas, [[]], [0], [props], [[]])
    assert "{v0} (26) Structures" in texts[0]
    assert "".join(c.get_text() for c in formulas[0]) == "),6393."


def test_subtitle_article_after_drug_alias_is_not_a_protected_variable():
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    source = "(23) Smith, A.; Jones, B. Discovery of Drug275 (IDE275{v0} Novel Selective Inhibitor. Nature 2025, 10 (2), 20-30."
    texts = [source]
    formulas = [fake_run("):A")]
    _release_reference_number_labels(texts, formulas, [[]], [0], [props], [[]])
    assert "{v0}A Novel" in texts[0]
    assert "".join(c.get_text() for c in formulas[0]) == "):"
    protected = [fake_run("):A", fontname="STIXMath-Regular")]
    texts = [source]
    _release_reference_number_labels(texts, protected, [[]], [0], [props], [[]])
    assert texts == [source]
