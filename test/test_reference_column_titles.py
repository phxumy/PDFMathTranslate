from __future__ import annotations

from types import SimpleNamespace

import pytest

from pdf2zh.converter import TranslateConverter
from pdf2zh.reading_flow import detect_reference_continuation
from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translator import CodexTranslator
from test_codex_reference_continuation import translator_stub
from test_reference_continuation import reference_pages


@pytest.mark.parametrize("venue", ["PRX Quantum", "Quantum"])
def test_complete_quantum_publication_tail_allows_only_work_title(venue):
    translator = translator_stub()
    entry = f"[17] A. Smith, Quantum circuit compilation, {venue} 8, 1343 (2024)."
    target, valid = translator._apply_reference_title_replacements(
        entry, [ExactReplacement("Quantum circuit compilation", "量子电路编译")]
    )
    assert valid
    assert target == entry.replace("Quantum circuit compilation", "量子电路编译")
    for selected in (venue, f"Quantum circuit compilation, {venue}"):
        assert not translator._apply_reference_title_replacements(
            entry, [ExactReplacement(selected, "错误译文")]
        )[1]


@pytest.mark.parametrize("tail", [
    "Quantum methods 8, 1343 (2024).", "PRX Quantum Methods 8, 1343 (2024).",
    "Quantum", "Quantum 8, 1343 (2024). Another title sentence",
])
def test_quantum_word_alone_cannot_certify_an_incomplete_metadata_tail(tail):
    assert not CodexTranslator._reference_metadata_tail_is_safe(tail, require_complete=True)


def test_author_only_column_tail_keeps_authors_and_translates_entire_right_title():
    left = "[14] A. Peruzzo, J. McClean, and P. Shadbolt, "
    title = "A variational eigenvalue solver on a photonic quantum processor"
    right = title + ", Nat. Commun. 5, 4213 (2014). "
    previous, following = reference_pages(
        left_text=left, right_text=right + "[15] A. Smith, Next title, Nature 1, 2 (2020).",
        previous_page=17, following_page=17, left_column=0, right_column=1,
    )
    group = detect_reference_continuation(previous + following, previous + following)
    assert group is not None
    translator = translator_stub()
    calls = []

    def translate(entries):
        calls.append(entries)
        return [[ExactReplacement(title, "光量子处理器上的变分本征值求解器")]]

    translator._run_reference_title_batch = translate
    converter = TranslateConverter.__new__(TranslateConverter)
    converter.translator = translator
    drafts = {
        previous[0].ref: SimpleNamespace(sstk=["", "", "", "", left]),
        following[0].ref: SimpleNamespace(sstk=[following[0].text]),
    }
    overrides = converter._translate_reference_continuation_group(group, drafts)
    assert len(overrides) == 1
    override = overrides[0]
    assert override.span.ref == following[0].ref
    assert following[0].text[override.span.start:override.span.end] == title
    assert override.translation == "光量子处理器上的变分本征值求解器"
    assert translator.translate_reference_continuation_fragments(left, right) == (
        ExactReplacement("", ""), ExactReplacement(title, override.translation)
    )
    assert len(calls) == 1  # validated one-sided cache is reusable


def test_same_page_reference_detection_requires_left_to_right_columns():
    for left_column, right_column in ((1, 0), (0, 0), (1, 1), (None, None)):
        previous, following = reference_pages(
            previous_page=17, following_page=17,
            left_column=left_column, right_column=right_column,
        )
        assert detect_reference_continuation(previous + following, previous + following) is None


def test_one_sided_title_cannot_start_inside_an_author_name():
    translator = translator_stub()
    source = "Smith, Quantum circuit compilation"
    translator._run_reference_title_batch = lambda entries: [[ExactReplacement(
        source, "史密斯，量子电路编译"
    )]]
    assert translator.translate_reference_continuation_fragments(
        "[17] J. McClean, and A. ", source + ", Quantum 8, 1343 (2024)."
    ) is None
    assert translator.cache.set_calls == []


def test_one_sided_title_must_not_truncate_a_title_continuing_with_in():
    translator = translator_stub()
    source = "High-fidelity quantum gates"
    translator._run_reference_title_batch = lambda entries: [[ExactReplacement(
        source, "高保真量子门"
    )]]
    assert translator.translate_reference_continuation_fragments(
        "[17] A. Smith, " + source,
        "in a superconducting quantum processor, Phys. Rev. Lett. 126, 220502 (2021).",
    ) is None
    assert translator.cache.set_calls == []


def test_formula_detection_is_explicitly_opted_in_and_rejects_layout_markers():
    previous, following = reference_pages(
        left_text="17. Solgun, F., DiVincenzo, D. P. & Gambetta, J. M. A gate with {v0}"
    )
    assert detect_reference_continuation(previous, following) is None
    assert detect_reference_continuation(previous, following, allow_formulas=True) is not None
    previous, following = reference_pages(
        left_text="17. A. Smith, A gate with {v0} [[PDF2ZH_FLOW_0]]"
    )
    assert detect_reference_continuation(previous, following, allow_formulas=True) is None


def test_duplicate_page_local_tokens_are_scoped_and_restored_per_fragment():
    translator = translator_stub()
    boundary = translator.REFERENCE_BOUNDARY_TOKEN
    left = "17. A. Smith, Gates using {v0} and "
    right = "{v0} control. Nature 1, 2 (2020)."
    calls = []

    def translate(entries):
        calls.append(entries)
        assert f"Gates using {{v0}} and {boundary}{{v1}} control" in entries[0]
        return [[ExactReplacement(
            f"Gates using {{v0}} and {boundary}{{v1}} control",
            f"使用{{v0}}与{boundary}{{v1}}控制的量子门",
        )]]

    translator._run_reference_title_batch = translate
    contexts = ({"{v0}": "Z"}, {"{v0}": "X"})
    result = translator.translate_reference_continuation_fragments(
        left, right, formula_contexts=contexts
    )
    assert result == (
        ExactReplacement("Gates using {v0} and ", "使用{v0}与"),
        ExactReplacement("{v0} control", "{v0}控制的量子门"),
    )
    assert translator.translate_reference_continuation_fragments(
        left, right, formula_contexts=contexts
    ) == result
    assert len(calls) == 1
    translator.translate_reference_continuation_fragments(
        left, right, formula_contexts=({"{v0}": "Y"}, contexts[1])
    )
    assert len(calls) == 2  # same placeholder, different original glyph meaning


@pytest.mark.parametrize("target", [
    "使用{v0}与{boundary}控制的量子门",
    "使用{v1}与{boundary}{v0}控制的量子门",
    "使用{v0}{v1}与{boundary}控制的量子门",
])
def test_scoped_formulas_cannot_drop_swap_or_cross_boundary(target):
    translator = translator_stub()
    boundary = translator.REFERENCE_BOUNDARY_TOKEN
    source = f"Gates using {{v0}} and {boundary}{{v1}} control"
    translator._run_reference_title_batch = lambda entries: [[ExactReplacement(
        source, target.replace("{boundary}", boundary)
    )]]
    result = translator.translate_reference_continuation_fragments(
        "17. A. Smith, Gates using {v0} and ", "{v0} control. Nature 1, 2 (2020).",
        formula_contexts=({"{v0}": "Z"}, {"{v0}": "X"}),
    )
    assert result is None
    assert translator.cache.set_calls == []


def test_formula_context_required_for_every_source_token():
    translator = translator_stub()
    translator._run_reference_title_batch = lambda entries: pytest.fail("no backend call")
    for contexts in (None, ({}, {}), ({"{v0}": "<missing>"}, {})):
        assert translator.translate_reference_continuation_fragments(
            "17. A. Smith, Gates using {v0} and ", "controls. Nature 1, 2 (2020).",
            formula_contexts=contexts,
        ) is None


def test_whole_left_title_before_metadata_keeps_original_symbol():
    translator = translator_stub()
    source = "High-fidelity controlled-{v0} gates"
    translator._run_reference_title_batch = lambda entries: [[ExactReplacement(
        source, "高保真受控-{v0}门"
    )]]
    result = translator.translate_reference_continuation_fragments(
        "17. A. Smith, High-fidelity controlled-{v25} gates",
        ", Phys. Rev. Lett. 126, 220502 (2021).",
        formula_contexts=({"{v25}": "Z"}, {}),
    )
    assert result == (
        ExactReplacement("High-fidelity controlled-{v25} gates", "高保真受控-{v25}门"),
        ExactReplacement("", ""),
    )


def test_named_transition_compound_is_preserved_but_not_ordinary_title_words():
    translator = translator_stub()
    title = "Nona-diabatic Landau–Zener–Stückelberg–Majorana transitions, dynamics, and interference"
    translated = "非绝热 Landau–Zener–Stückelberg–Majorana 跃迁、动力学与干涉"
    entry = "[65] O. V. Ivakhnenko, S. N. Shevchenko, and F. Nori, " + title + ", Phys. Rep. 995, 1 (2023). "
    assert translator._apply_reference_title_replacements(
        entry, [ExactReplacement(title, translated)]
    ) == (entry.replace(title, translated), True)
    for source, target in (
        ("Quantum transitions", "Quantum 跃迁"),
        ("Non-adiabatic transitions", "Non-adiabatic 跃迁"),
        ("quantum–phase transitions", "quantum–phase 跃迁"),
        ("Landau–Zener transitions", "Landau–Majorana 跃迁"),
    ):
        assert not translator._reference_preserves_single_eponym_name(source, target)
    for source, target in (
        ("Landau–Zener transitions and interference", "Landau–Zener 跃迁和 interference"),
        ("Landau–Zener transitions", "Landau–Zener transitions"),
    ):
        assert translator._reference_title_has_unsafe_residue(source, target)
