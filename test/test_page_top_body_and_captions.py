from pdf2zh.converter import (
    Paragraph,
    _classify_running_header_regions,
    _recover_supplementary_caption_lines,
)
from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    SourceSegment,
    ROLE_TRANSLATE,
)
from test_cm_prose_and_region_recovery import run


def paragraph(x0, x1, y0, y1, size=12):
    return Paragraph(y0, x0, x0, x1, y0, y1, size, False, region_kind="plain text")


def test_top_body_line_and_split_heading_keep_body_role():
    paragraphs = [
        paragraph(54, 106, 745, 757),
        paragraph(106, 560, 745, 757),
        paragraph(54, 560, 635, 743.3),
    ]
    texts = ["{v0}", "Compound name (12).", "The mixture was heated and purified."]
    assert (
        _classify_running_header_regions(
            texts, paragraphs, page_width=612, page_height=792
        )
        == ()
    )
    assert paragraphs[0].body_text_at_top and paragraphs[1].body_text_at_top
    source = SourceSegment(
        index=1,
        text=texts[1],
        y0=745,
        y1=757,
        size=12,
        page_height=792,
        region_kind="plain text",
        body_text_at_top=True,
    )
    assert (
        DocumentTranslationPolicy().plan_segment(source).parts[0].role == ROLE_TRANSLATE
    )


def test_separated_small_running_header_stays_protected():
    paragraphs = [paragraph(54, 560, 753, 760, 7), paragraph(54, 560, 635, 742, 10)]
    assert _classify_running_header_regions(
        ["Journal running header", "Ordinary body prose."],
        paragraphs,
        page_width=612,
        page_height=792,
    ) == (0,)


def test_complete_numbered_supplementary_caption_recovers_all_fragments():
    chars = run(
        "Supplementary Scheme 5. Synthesis of fused analogues 12 and 13.",
        y=745,
        size=12,
    )
    kinds = {1: "plain text", 2: "figure"}
    recovered = _recover_supplementary_caption_lines(chars, kinds, set())
    assert len(recovered) == len(chars)
    assert len(set(recovered.values())) == 1
    assert kinds[next(iter(recovered.values()))] == "figure_caption"


def test_running_heads_and_ordinary_figure_labels_are_not_caption_prose():
    for text in (
        "Supplementary Information",
        "Supplementary Scheme 5",
        "Reaction time",
        "Figure 1",
    ):
        assert not _recover_supplementary_caption_lines(run(text), {}, set())


def test_caption_does_not_absorb_a_distant_figure_label_on_the_same_baseline():
    caption = run("Supplementary Scheme 2. Synthesis of scaffolds.", x=20, y=500)
    label = run("NHR", x=500, y=500)
    recovered = _recover_supplementary_caption_lines(caption + label, {}, set())
    assert all(id(char) in recovered for char in caption)
    assert all(id(char) not in recovered for char in label)
