from unittest.mock import Mock

import pytest

from pdf2zh.translation_quality import has_omitted_scientific_content
from test_codex_formula_context import translator_stub

SOURCE = (
    "The amine was introduced in a sequence of activation to give 12, "
    "coupling and deprotection to yield scaffold 9."
)
PREMATURE = "通过活化引入胺基，得到12；随后经偶联和脱保护，得到骨架9。"
COMPLETE = "胺基通过一系列反应引入：活化得到12，再经偶联和脱保护得到骨架9。"


def test_sequence_wide_result_must_not_be_assigned_to_first_intermediate():
    assert has_omitted_scientific_content(SOURCE, PREMATURE)
    assert translator_stub()._accepted_translation(SOURCE, PREMATURE) is None


@pytest.mark.parametrize(
    "target",
    [
        COMPLETE,
        "通过活化得到12，随后经偶联和脱保护，引入胺基并得到骨架9。",
        "通过活化、偶联和脱保护三步引入胺基，先得到12，再得到骨架9。",
        "经一系列反应引入胺基，包括活化得到12，然后经偶联和脱保护得到骨架9。",
    ],
)
def test_overall_summary_can_precede_or_follow_the_reaction_sequence(target):
    assert not has_omitted_scientific_content(SOURCE, target)
    assert translator_stub()._accepted_translation(SOURCE, target) == target


@pytest.mark.parametrize(
    "source,target",
    [
        (
            "The amine was introduced by activation to give 12, followed by "
            "coupling and deprotection to yield scaffold 9.",
            PREMATURE,
        ),
        (
            "Sequential coupling and deprotection yielded scaffold 9.",
            "连续偶联和脱保护得到骨架9。",
        ),
        (
            "The amine was introduced in a sequence of reactions.",
            "经一系列反应引入胺基。",
        ),
    ],
)
def test_different_or_underspecified_reaction_scopes_are_not_inferred(source, target):
    assert not has_omitted_scientific_content(source, target)


def test_existing_cached_sequence_is_revalidated_before_reuse():
    translator = translator_stub()
    translator.max_concurrency = 1
    translator.cache.set(SOURCE, PREMATURE)
    translator._run_batch_translation = Mock(return_value=[COMPLETE])
    assert translator.translate_batch([SOURCE]) == [COMPLETE]
    translator._run_batch_translation.assert_called_once()
    assert translator.cache.get(SOURCE) == COMPLETE
