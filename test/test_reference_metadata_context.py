import json

import pytest

from pdf2zh.translator import CodexTranslator
from pdf2zh.translation_policy import ExactReplacement
from test_codex_reference_cache import translator_stub


@pytest.mark.parametrize(
    "tail",
    [
        "Org. Biomol. Chem. 2020, 7, 100{v0}.",
        "Bioorg. Med. Chem. Lett. 2020, 7, 100{v0}.",
        "J. Pharmacol. Toxicol. Methods 2020, 7, 100{v0}.",
        "Angew. Chem., Int. Ed. 2020, 7, 100{v0}.",
        "J. Chem. Res., Synop. 2020, 7, 100{v0}.",
        "J. Chem. Soc., Perkin. Trans. 2 2020, 7, 100{v0}.",
        "Acta Crystallogr. 2020, B58, 100{v0}.",
        "Drug Discovery Today 2020, 7, 100{v0}.",
        "Xenobiotica 2020, 7, 100{v0}.",
        "Pharma Chem. 2020, 7, 100{v0}.",
    ],
)
def test_context_confirms_complete_metadata_but_never_changes_rendered_fields(tail):
    title = "A complete molecular binding study"
    entry = f"(12) Smith, A.; Jones, B. {title}. {tail}"
    context = {"{v0}": "−110"}
    assert CodexTranslator._structured_reference_title_spans(
        entry, formula_context=context
    ) == (title,)
    assert CodexTranslator._reference_title_boundary_is_safe(
        entry, title, formula_context=context
    )
    translator = translator_stub()
    translated, valid = translator._apply_reference_title_replacements(
        entry, [ExactReplacement(title, "完整的分子结合研究")], formula_context=context
    )
    assert valid and translated == entry.replace(title, "完整的分子结合研究")
    assert tail in translated  # Original punctuation and opaque glyph token survive.
    for wrong in ("A complete", "Smith, A.", tail.split("2020")[0].strip()):
        assert not CodexTranslator._reference_title_boundary_is_safe(
            entry, wrong, formula_context=context
        )


def test_missing_or_nonmetadata_formula_context_never_proves_a_title_boundary():
    title = "A complete molecular binding study"
    entry = f"(12) Smith, A.; Jones, B. {title}. J. Med. Chem. 2020, 7, 100{{v0}}."
    for context in ({}, {"{v0}": "x + y"}, {"{v0}": "−110. More work-title prose"}):
        assert (
            CodexTranslator._structured_reference_title_spans(
                entry, formula_context=context
            )
            == ()
        )


def test_new_complete_metadata_route_does_not_expand_alphabetic_venue_phrases():
    for value in (
        "Physics, 2020, 7, 100-110",
        "Matter. Physical Review A, 12:34, 2020",
        "control, signals and systems, 2(4):303-314, 1989",
    ):
        assert not CodexTranslator._reference_metadata_tail_is_safe(
            "Mathematics of {v0}.",
            formula_context={"{v0}": value},
            require_complete=True,
        )


def test_a_titleless_publication_tail_never_turns_a_journal_into_a_work_title():
    entry = "(12) Smith, A.; Jones, B. J. Med. Chem. 2020, 7, 100{v0}."
    context = {"{v0}": "−110"}
    assert (
        CodexTranslator._structured_reference_title_spans(
            entry, formula_context=context
        )
        == ()
    )
    for wrong in ("J", "J. Med. Chem", "Med. Chem"):
        assert not CodexTranslator._reference_title_boundary_is_safe(
            entry, wrong, formula_context=context
        )


@pytest.mark.parametrize(
    "catalogue",
    [
        "MolecularTool 09; MolecularTool, Inc.: London, U.K., 2010.",
        "Toolkit, v7.8; Example, L.L.C.: London, U.K.",
        "ACME Python Tool-Kit, v1.7.2; Example Scientific Software, Inc.: London, U.K.",
    ],
)
def test_versioned_software_catalogues_are_preserved_not_translated_as_long_titles(
    catalogue,
):
    entry = "(59) Smith, A.; Jones, B. " + catalogue
    assert CodexTranslator._reference_complete_metadata_tail_is_safe(catalogue)
    assert CodexTranslator._structured_reference_title_spans(entry) == ()
    assert not CodexTranslator._reference_title_boundary_is_safe(
        entry, catalogue.split(":")[0]
    )


@pytest.mark.parametrize(
    "title",
    [
        "Discovery of {v1}secretase inhibitors",
        "Molecular binding studies. II—A systematic comparison of ligand interactions",
    ],
)
def test_formula_bearing_and_multisentence_titles_keep_their_complete_span(title):
    entry = f"(12) Smith, A.; Jones, B. {title}. J. Med. Chem. 2020, 7, 100{{v0}}."
    context = {"{v0}": "−110", "{v1}": "γ-"}
    assert CodexTranslator._structured_reference_title_spans(
        entry, formula_context=context
    ) == (title,)
    assert CodexTranslator._reference_title_boundary_is_safe(
        entry, title, formula_context=context
    )
    assert not CodexTranslator._reference_title_boundary_is_safe(
        entry, title.split(" ")[0], formula_context=context
    )


def test_context_routes_exact_titles_and_revalidates_partial_cache_entries():
    title = "A complete molecular binding study"
    entry = f"(12) Smith, A.; Jones, B. {title}. Bioorg. Med. Chem. 2020, 7, 100{{v0}}."
    signature = "{v0}=−110"
    translator = translator_stub()
    calls = []
    translator.cache.set(
        translator._reference_replacements_cache_key(entry, signature),
        json.dumps(
            {
                "format": "reference-title-replacements-v1",
                "replacements": [{"source": "A complete", "translated": "完整的"}],
            }
        ),
    )
    translator._run_exact_reference_title_batch = lambda titles: calls.append(
        titles
    ) or ["完整的分子结合研究"]
    translator._run_discovered_reference_title_batch = lambda entries: pytest.fail(
        "A proven full title must use the exact route"
    )
    expected = entry.replace(title, "完整的分子结合研究")
    assert translator.translate_reference_entries([entry], [signature]) == [expected]
    assert calls == [[title]]
    assert translator.translate_reference_entries([entry], [signature]) == [expected]
    assert calls == [[title]]


@pytest.mark.parametrize(
    "candidate",
    [
        "A Novel Method. Bioorg. Med. Chem.",
        "Hydrogen bonding, J. Chem. Res.",
        "Binding. Angew. Chem., Int. Ed.",
        "A new approach. Drugs Future",
    ],
)
def test_title_prose_before_a_real_venue_is_not_a_container(candidate):
    assert not CodexTranslator._reference_venue_name_is_plausible(candidate)


@pytest.mark.parametrize("label", ["log P", "log D", "cLogP"])
def test_reference_title_retains_exact_source_partition_measurement_label(label):
    source = f"Calculation of molecular lipophilicity: A comparison of {label} methods"
    target = f"分子亲脂性的计算：{label.replace(' ', '')}方法的比较"
    assert not CodexTranslator._reference_title_has_unsafe_residue(source, target)
    assert CodexTranslator._reference_title_has_unsafe_residue(
        source, target + " methods"
    )
    assert CodexTranslator._reference_title_has_unsafe_residue(
        source, target.replace(label.replace(" ", ""), "logP logP")
    )
    assert CodexTranslator._reference_title_has_unsafe_residue(
        source, "分子亲脂性的计算：方法的比较"
    )


def test_reference_measurement_exception_never_changes_variable_or_computed_prefix():
    source = "Calculation of molecular lipophilicity with cLogP"
    for wrong in ("logP", "cLogD", "cLogp"):
        assert CodexTranslator._reference_title_has_unsafe_residue(
            source, "分子亲脂性计算：" + wrong
        )
    assert CodexTranslator._reference_title_has_unsafe_residue(
        "How to log practical methods", "如何 log practical methods"
    )
