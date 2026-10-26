from pdf2zh.translation_policy import find_reference_markers


def test_journal_volume_cannot_take_the_next_reference_number():
    source = "(7) Smith, A. A scientific title. eLife 2019, {v0}, No. e43333. (8) Jones, B. Another title."
    markers = find_reference_markers(source, ["8"])
    assert [(m.number, m.raw) for m in markers] == [(7, "(7)"), (8, "(8)")]


def test_hidden_year_volume_pair_is_data_but_label_after_terminal_year_is_not():
    assert not find_reference_markers("Nature {v0}, {v1}, 100-110.", ["2019", "8"])
    assert [
        m.number
        for m in find_reference_markers("Academic Press, 2019. {v0} Smith, A.", ["8"])
    ] == [8]
