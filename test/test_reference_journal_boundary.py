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
