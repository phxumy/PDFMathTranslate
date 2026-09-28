from dataclasses import replace

import pytest

from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    ROLE_AFFILIATION,
    ROLE_PRESERVE,
    ROLE_TRANSLATE,
    SourceSegment,
)
from test_converter_translation_policy import FakeCodexTranslator, converter_with, paragraph


ADDRESS = (
    "IBM T J Watson Research Center, Yorktown Heights, NY 10598, "
    "USA E-mail: chowmj@us.ibm.com"
)
FIELDS = (
    "Received 9 July 2013",
    "Published 19 November 2013",
    "Online at http://www.njp.org/",
    "doi:10.1088/1367-2630/15/11/115012",
)


def segment(text, *, lines=()):
    return SourceSegment(
        index=2, text=text, x0=147, x1=487, y0=551, y1=577,
        size=11.955, page_width=595, page_height=842, region_kind="plain text",
        break_offsets=tuple(len(" ".join(lines[:i])) for i in range(1, len(lines))),
    )


@pytest.mark.parametrize("address", [ADDRESS,
    "Example Research Institute, Cambridge CB3 0HE, UK E-mail: author@example.org",
    "National Laboratory, Pasadena, CA 91125, USA",
])
def test_postal_or_contact_address_is_not_preserved_as_capitalized_person_names(address):
    plan = DocumentTranslationPolicy().plan_segment(segment(address))
    assert [(part.role, part.text) for part in plan.parts] == [(ROLE_AFFILIATION, address)]


def test_address_does_not_change_author_or_combined_byline_roles():
    authors = "John Smith{v0}, Jane Doe and Alice Jones"
    plan = DocumentTranslationPolicy().plan_segment(segment(authors), formula_texts=["1"])
    assert [part.role for part in plan.parts] == [ROLE_PRESERVE]
    combined = "A. Smith, B. Jones {v0}Department of Physics, Example University, NY 10598"
    plan = DocumentTranslationPolicy().plan_segment(segment(combined), formula_texts=["1"])
    assert [part.role for part in plan.parts] == [ROLE_PRESERVE, ROLE_AFFILIATION]


def test_postal_address_rule_does_not_override_reference_or_named_prose_context():
    for heading in ("References", "Acknowledgments"):
        policy = DocumentTranslationPolicy()
        policy.plan_segment(segment(heading))
        text = "[1] A. Smith, Research Center, Journal 20, 12345 (2020)."
        plan = policy.plan_segment(segment(text))
        assert ROLE_AFFILIATION not in [part.role for part in plan.parts]
    text = "We measured the response at the Research Center, NY 10598, USA."
    plan = DocumentTranslationPolicy().plan_segment(segment(text))
    assert [part.role for part in plan.parts] == [ROLE_TRANSLATE]


def test_publication_fields_keep_physical_line_boundaries_and_preserve_doi():
    source = " ".join(FIELDS)
    plan = DocumentTranslationPolicy().plan_segment(segment(source, lines=FIELDS))
    assert [part.role for part in plan.parts] == [ROLE_TRANSLATE] * 3 + [ROLE_PRESERVE]
    assert [part.break_before for part in plan.parts] == [False, True, True, True]
    assert [part.text.strip() for part in plan.parts] == list(FIELDS)
    assert "".join(part.text for part in plan.parts) == source


@pytest.mark.parametrize("lines", [
    ("Received: 2021-04-02", "Accepted: June 4, 2021"),
    ("Revised 8 October 2018", "Published online 12 November 2018"),
])
def test_other_publication_date_formats_use_source_line_breaks(lines):
    plan = DocumentTranslationPolicy().plan_segment(segment(" ".join(lines), lines=lines))
    assert len(plan.parts) == 2
    assert plan.parts[1].break_before


def test_metadata_like_prose_or_absent_source_break_does_not_force_newlines():
    source = " ".join(FIELDS)
    for candidate in (
        segment(source),
        segment("Received signal from an experiment. Published results are accurate.",
                lines=("Received signal from an experiment.", "Published results are accurate.")),
        segment("Received 9 July 2013. We discuss a later experiment.",
                lines=("Received 9 July 2013.", "We discuss a later experiment.")),
        replace(segment(source, lines=FIELDS), region_kind="figure_caption"),
    ):
        assert len(DocumentTranslationPolicy().plan_segment(candidate).parts) == 1


def test_explicit_metadata_newlines_retain_field_boundaries():
    source = "\n".join(FIELDS)
    plan = DocumentTranslationPolicy().plan_segment(segment(source))
    assert [part.role for part in plan.parts] == [ROLE_TRANSLATE] * 3 + [ROLE_PRESERVE]
    assert "".join(part.text for part in plan.parts) == source
    assert [part.text.strip() for part in plan.parts] == list(FIELDS)


def test_standalone_numbered_institutions_translate_wholly_and_remain_separate():
    source = (
        "1Center for Quantum Information, Institute for Interdisciplinary "
        "Information Sciences, Tsinghua University, Beijing 100084, China "
        "{v2}Guangdong Provincial Key Laboratory of Quantum Engineering and "
        "Quantum Materials, GPETR Center for Quantum Precision Measurement, "
        "and School of Physics and Telecommunication Engineering, "
        "South China Normal University, Guangzhou 510006, China"
    )

    class Translator(FakeCodexTranslator):
        def __init__(self):
            super().__init__()
            self.seen = []

        def translate_batch(self, texts):
            self.seen.extend(texts)
            return ["1清华大学机构，北京100084，中国{v2}华南师范大学机构，广州510006，中国"]

    translator = Translator()
    converter = converter_with(translator)
    address_paragraph = paragraph()
    address_paragraph.y0, address_paragraph.y1, address_paragraph.size = 605, 660, 9.5
    output = converter._translate_planned_segments(
        [source], [address_paragraph], formula_texts=["1", "1", "2"],
        page_width=612, page_height=792,
    )
    assert translator.seen == [source]
    assert output == ["1清华大学机构，北京100084，中国\n{v2}华南师范大学机构，广州510006，中国"]
    assert not translator.reference_entries


def test_converter_delivers_distinct_metadata_fields_and_preserves_email_literal():
    class Translator(FakeCodexTranslator):
        def __init__(self):
            super().__init__()
            self.seen = []

        def translate_batch(self, texts):
            self.seen.extend(texts)
            return [text.replace("IBM T J Watson Research Center", "IBM研究中心")
                    .replace("Received", "收稿日期").replace("Published", "发表日期")
                    .replace("Online at", "在线地址") for text in texts]

    translator = Translator()
    converter = converter_with(translator)
    address_paragraph = paragraph()
    address_paragraph.y0, address_paragraph.y1, address_paragraph.size = 551, 577, 11.955
    fields_paragraph = paragraph()
    fields_paragraph.break_offsets = list(segment(" ".join(FIELDS), lines=FIELDS).break_offsets)
    output = converter._translate_planned_segments(
        [ADDRESS, " ".join(FIELDS)], [address_paragraph, fields_paragraph],
        formula_texts=[], page_width=595, page_height=842,
    )
    assert "IBM研究中心" in output[0]
    assert "chowmj@us.ibm.com" in output[0]
    assert all("chowmj@us.ibm.com" not in text for text in translator.seen)
    assert len(output[1].splitlines()) == 4
    assert "http://www.njp.org/" in output[1].splitlines()[2]
    assert output[1].splitlines()[3].strip() == FIELDS[3]
    assert all("http://www.njp.org/" not in text for text in translator.seen)
    assert all(FIELDS[3] not in text for text in translator.seen)
    assert not translator.reference_entries
