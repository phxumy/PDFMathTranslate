import json

import pytest

from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translator import CodexTranslator
from test_codex_reference_translation import translator_stub


@pytest.mark.parametrize(
    "venue, partial",
    [
        ("New J. Phys.", "New J"),
        ("Int. J. Mod. Phys. A", "Int. J"),
        ("Eur. Phys. J. D", "Eur. Phys"),
    ],
)
def test_titleless_reference_does_not_translate_a_journal_prefix(venue, partial):
    entry = f"[37] X.-Y. Lu, S. Ashhab, and F. Nori, {venue} 14, 073041 (2012)."
    assert CodexTranslator._structured_reference_title_spans(entry) == ()
    translator = translator_stub()
    assert translator._apply_reference_title_replacements(
        entry, [ExactReplacement(partial, "错误题名")]
    ) == (entry, False)


def test_cached_journal_prefix_translation_is_rejected_and_metadata_preserved():
    entry = "[18] J.-M. Cai and A. Retzker, New J. Phys. 14, 113023 (2012)."
    translator = translator_stub()
    translator.cache.set(
        translator._reference_replacements_cache_key(entry),
        json.dumps(
            {
                "format": "reference-title-replacements-v1",
                "replacements": [{"source": "New J", "translated": "新 J"}],
            }
        ),
    )
    translator._run_reference_title_batch = lambda entries: [[] for _ in entries]
    assert translator.translate_reference_entries([entry]) == [entry]


@pytest.mark.parametrize("venue", ["New J. Phys.", "Int. J. Mod. Phys. A"])
def test_real_title_before_abbreviated_journal_still_translates(venue):
    title = "New methods for quantum control"
    entry = f"[1] J. Smith, {title}, {venue} 14, 073041 (2012)."
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    assert translator_stub()._apply_reference_title_replacements(
        entry, [ExactReplacement(title, "量子控制的新方法")]
    ) == (entry.replace(title, "量子控制的新方法"), True)


def test_invest_journal_with_protected_issue_and_page_tail():
    title = "Akt inhibitors in clinical development for the treatment of cancer"
    entry = (
        f"(12) Pal, S. K.; Reckamp, K.; Yu, H.; Figlin, R. A. {title}. "
        "Expert Opin. Invest. Drugs 2010, 19 (11{v102} "
    )
    assert CodexTranslator._reference_venue_name_is_plausible(
        "Expert Opin. Invest. Drugs"
    )
    assert CodexTranslator._reference_title_boundary_is_safe(
        entry, title, formula_context={"{v102}": "),1355−1366."}
    )


def test_complete_pct_wo_metadata_allows_the_work_title():
    title = "Preparation of cyclopenta[d]pyrimidine derivatives as Akt protein kinase inhibitors"
    entry = f"(11) J. Smith, {title}. PCT Int. Appl. WO2008006040, 2008."
    assert CodexTranslator._reference_complete_metadata_tail_is_safe(
        "PCT Int. Appl. WO2008006040, 2008."
    )
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    assert CodexTranslator._reference_title_boundary_is_safe(entry, title)


@pytest.mark.parametrize(
    "tail",
    [
        "PCT Int. Appl. WO2008006040, 2008. Untranslated title words",
        "PCT Int. Appl. WO2008006040",
        "PCT Int. Appl. WO2008oops, 2008.",
    ],
)
def test_pct_publication_tail_must_be_complete(tail):
    assert not CodexTranslator._reference_complete_metadata_tail_is_safe(tail)
