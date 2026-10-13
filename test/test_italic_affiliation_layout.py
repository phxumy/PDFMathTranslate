import pytest

from pdf2zh.converter import (
    Paragraph,
    _collect_translatable_italic_runs,
    _restore_italic_affiliation_layout,
)
from test_italic_translation import FakeStyledTranslator, converter_with, fake_run


def block():
    source = "1{v0} 2{v1} 3 {v2}"
    labels = [
        "Department of Physics, ETH Zurich, Switzerland",
        "Quantum Center, ETH Zurich, Switzerland",
        "Institute of Physics, Example University, Switzerland",
    ]
    runs = [fake_run(text) for text in labels]
    for i, run in enumerate(runs):
        for char in run:
            char.size = 9.5
            char.y0 = 660.0 - 14.0 * i
            char.matrix = (1, 0, 0, 1, char.x0, char.y0)
    p = Paragraph(
        668,
        175,
        112,
        499,
        632,
        672,
        6.4,
        True,
        page_id=0,
        layout_class=3,
        region_kind="plain text",
    )
    candidates = _collect_translatable_italic_runs(runs, [0, 0, 0], [p], [source])
    assert len(candidates) == 3
    return source, runs, p, candidates


def test_superscript_size_does_not_shrink_three_complete_institutions():
    source, runs, p, candidates = block()
    _restore_italic_affiliation_layout([source], runs, [p], candidates, 792)
    assert p.size == 9.5
    assert p.y == 660
    assert [part.text for part in p.affiliation_parts] == ["1{v0}", "2{v1}", "3 {v2}"]
    assert [part.break_before for part in p.affiliation_parts] == [False, True, True]
    result = converter_with(FakeStyledTranslator())._translate_planned_segments(
        [source],
        [p],
        ["".join(ch.get_text() for ch in run) for run in runs],
        612,
        792,
        italic_candidates=candidates,
    )[0]
    assert len(result.splitlines()) == 3
    assert all("PDF2ZH_ITALIC" in row for row in result.splitlines())


@pytest.mark.parametrize(
    "change",
    ["later_page", "body", "missing_candidate", "same_baseline", "nonsequential"],
)
def test_ordinary_numbered_material_does_not_acquire_affiliation_layout(change):
    source, runs, p, candidates = block()
    if change == "later_page":
        p.page_id = 1
    elif change == "body":
        p.y0 = 200
    elif change == "missing_candidate":
        candidates.pop(1)
    elif change == "same_baseline":
        for run in runs:
            for char in run:
                char.matrix = (1, 0, 0, 1, char.x0, 660)
                char.y0 = 660
    else:
        source = source.replace("2{v1}", "9{v1}")
    _restore_italic_affiliation_layout([source], runs, [p], candidates, 792)
    assert p.size == 6.4
    assert not p.affiliation_parts
