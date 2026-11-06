import pytest

from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    SourceSegment,
    find_reference_markers,
    split_numbered_reference_region,
)


@pytest.mark.parametrize("value", ["(37)", "[37]", "（37）", "［37］", "(3 7)"])
def test_wrapped_formula_label_keeps_reference_sequence_and_journal_metadata(value):
    entries = []
    for number in range(33, 42):
        marker = "{v0}" if number == 37 else f"({number})"
        journal = "Methods Princ. Med. Chem." if number == 40 else "J. Med. Chem."
        entries.append(
            f"{marker} Smith, A.; Jones, B. Ligand synthesis and binding. {journal} 2010, 12, 100-108."
        )
    text = " ".join(entries)
    policy = DocumentTranslationPolicy()
    policy.last_reference_number = 32
    region = split_numbered_reference_region(text, [value], expected_number=33)
    assert region is not None
    assert [marker.number for marker in region.markers] == list(range(33, 42))
    plan = policy.plan_segment(
        SourceSegment(0, text, region_kind="plain text"), [value]
    )
    assert all(part.role == "reference" for part in plan.parts)
    assert "".join(part.text for part in plan.parts) == text
    assert policy.last_reference_number == 41


@pytest.mark.parametrize("value", ["(3+7)", "(37", "37)", "(3.7)", "(2010)"])
def test_formula_expressions_and_incomplete_wrappers_are_not_reference_labels(value):
    assert find_reference_markers("{v0} Alpha expression.", [value]) == ()


def test_wrapped_affiliation_and_isolated_equation_keep_existing_safety_gates():
    assert find_reference_markers("A. Smith{v0}, B. Jones", ["(1)"]) == ()
    assert (
        split_numbered_reference_region(
            "The expression is {v0} and it is finite.", ["(37)"]
        )
        is None
    )


def test_exact_continuation_accepts_unpublished_observations_then_software_catalog():
    observations = "(58) Smith, A.; Jones, B.; Brown, C.; White, D.; Green, E.; Black, F.; Gray, G.; Miller, H.; Wilson, I.; Clark, J. Unreported observations, private preprint."
    software = "(59) Smith, A.; Jones, B.; Brown, C. Molecular Tool; Example, Inc.: London, U.K., 2010."
    catalog = "(60) Toolkit, v7.8; Example, L.L.C.: London, U.K."
    policy = DocumentTranslationPolicy()
    policy.last_reference_number = 57
    for number, text in ((58, observations), (59, software), (60, catalog)):
        plan = policy.plan_segment(
            SourceSegment(number, text, region_kind="plain text")
        )
        assert all(part.role == "reference" for part in plan.parts)
        assert policy.last_reference_number == number
    for text in (observations, catalog):
        assert split_numbered_reference_region(text) is None
        assert split_numbered_reference_region(text, expected_number=99) is None
