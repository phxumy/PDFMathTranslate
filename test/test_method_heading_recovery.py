import pytest

from pdf2zh.converter import (
    _is_high_confidence_prose_italic,
    _release_standalone_italic_headings,
)
from test_italic_translation import fake_run, paragraph

METHOD_HEADINGS = [
    "Determination of logD",
    "Determination of aqueous solubility",
    "Determination of pKa",
    "Determination of metabolic stability in liver microsomes",
    "Determination of compound activity on hERG",
    "Automated Method for Cytochrome P450 Reaction Phenotyping Using Six Isoforms of Human Recombinant Enzymes",
    "Automated TDI assay in Human Liver Microsomes with LC-MS analysis",
    "Determination of CYPs Inhibition using Human Recombinant Cytochrome P450s",
]


@pytest.mark.parametrize("heading", METHOD_HEADINGS)
def test_known_complete_italic_method_heading_is_released_in_title_region(heading):
    run = fake_run(heading)
    assert _is_high_confidence_prose_italic(run, 10.0) == heading
    source = ["{v0}"]
    paragraphs = [paragraph(region_kind="title")]
    _release_standalone_italic_headings(source, [run], [0], paragraphs)
    assert source == [heading]
    assert paragraphs[0].brk


@pytest.mark.parametrize("heading", METHOD_HEADINGS)
def test_complete_method_heading_repairs_only_ambiguous_text_region_classification(
    heading,
):
    source = ["{v0}"]
    _release_standalone_italic_headings(
        source, [fake_run(heading)], [0], [paragraph(region_kind="plain text")]
    )
    assert source == [heading]
    source = ["Compare {v0} with the result."]
    _release_standalone_italic_headings(
        source, [fake_run(heading)], [0], [paragraph(region_kind="title")]
    )
    assert source == ["Compare {v0} with the result."]


@pytest.mark.parametrize("font", ["Times-Roman", "CMMI10"])
def test_method_words_do_not_override_the_original_font_safety_check(font):
    source = ["{v0}"]
    _release_standalone_italic_headings(
        source,
        [fake_run("Determination of pKa", fontname=font)],
        [0],
        [paragraph(region_kind="title")],
    )
    assert source == ["{v0}"]


def test_wrapped_p450_method_heading_preserves_the_whole_title():
    first = fake_run(
        "Automated Method for Cytochrome P450 Reaction Phenotyping Using Six Isoforms of Human"
    )
    second = fake_run("Recombinant Enzymes")
    for char in second:
        char.y0 -= 23.3
        char.matrix = (*char.matrix[:5], char.matrix[5] - 23.3)
    source = ["{v0}"]
    _release_standalone_italic_headings(
        source, [first + second], [0], [paragraph(region_kind="title")]
    )
    assert source == [METHOD_HEADINGS[5]]


@pytest.mark.parametrize("region", ["figure", "table", "formula", "formula_annotation"])
def test_method_heading_words_do_not_release_protected_region_kinds(region):
    source = ["{v0}"]
    _release_standalone_italic_headings(
        source, [fake_run(METHOD_HEADINGS[0])], [0], [paragraph(region_kind=region)]
    )
    assert source == ["{v0}"]


def test_class_zero_exception_requires_complete_known_isolated_method_title():
    run = fake_run(METHOD_HEADINGS[0])
    for char in run:
        char._pdf2zh_layout_class = 0
    assert _is_high_confidence_prose_italic(run, 10.0) is None
    source = ["{v0}"]
    node = paragraph(region_kind="")
    _release_standalone_italic_headings(source, [run], [0], [node])
    assert source == [METHOD_HEADINGS[0]]
    assert node.region_kind == "title"


@pytest.mark.parametrize(
    "text",
    [
        "Determination of x y z",
        "Determination of pKa = x",
        "Automated Product Name",
        "John Smith",
        "pKa logD",
    ],
)
def test_unlisted_titles_and_mathematical_labels_are_not_generalized(text):
    source = ["{v0}"]
    _release_standalone_italic_headings(
        source, [fake_run(text)], [0], [paragraph(region_kind="title")]
    )
    assert source == ["{v0}"]
