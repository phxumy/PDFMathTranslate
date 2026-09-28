import pytest

from pdf2zh.converter import _wrapped_table_label_pairs
from test_cm_prose_and_region_recovery import detection, recover, run


def wrapped_rows(
    *, indent=12.69, gap=10.9535, first="Echo time at the interaction",
    second="frequency,", second_size=9.5,
):
    left = run(first, x=20, y=500, font="Times-Roman", size=9.5)
    right = run(second, x=20 + indent, y=500 - gap, font="Times-Roman", size=second_size)
    values = [run(value, x=x, y=500 - gap, size=9.5) for value, x in [("8.2(1)", 300), ("8.5(1)", 400)]]
    return left, right, values


def test_wrapped_parameter_label_is_one_cell_but_values_stay_protected():
    left, right, values = wrapped_rows()
    symbol = run("T", x=right[-1].x1 + 3, y=489.0465, font="CMMI10", size=9.5)
    right += symbol
    chars = left + right + sum(values, [])
    geometry = [(ch.get_text(), ch.x0, ch.y0, ch.x1, ch.y1, ch.fontname) for ch in chars]
    mapping, kinds = recover(chars, [detection("table", (0, 450, 599, 550))])
    assert len({mapping[id(ch)] for ch in left + right}) == 1
    assert kinds[mapping[id(left[0])]] == "table_cell"
    assert not any(id(ch) in mapping for ch in sum(values, []))
    assert [(ch.get_text(), ch.x0, ch.y0, ch.x1, ch.y1, ch.fontname) for ch in chars] == geometry


@pytest.mark.parametrize("options", [
    {"indent": 0},
    {"indent": 30},
    {"gap": 20},
    {"gap": 4},
    {"first": "Echo time at the interaction."},
    {"first": "Echo time"},
    {"second": "Frequency,"},
    {"second_size": 7},
])
def test_adjacent_independent_rows_do_not_join(options):
    left, right, values = wrapped_rows(**options)
    assert _wrapped_table_label_pairs([left, right + sum(values, [])]) == {}


@pytest.mark.parametrize("case", ["prior_values", "one_value", "prose_column", "rotation"])
def test_row_structure_and_rotation_guard_continuation(case):
    left, right, values = wrapped_rows()
    if case == "prior_values":
        left += run("12.3", x=300, y=500, size=9.5)
    elif case == "one_value":
        values.pop()
    elif case == "prose_column":
        values[1] = run("example prose", x=400, y=489.0465, size=9.5)
    else:
        right[0].matrix = (0, 1, -1, 0, right[0].x0, right[0].y0)
    assert _wrapped_table_label_pairs([left, right + sum(values, [])]) == {}


def test_separate_tables_never_share_label_class():
    left, right, values = wrapped_rows()
    mapping, _ = recover(
        left + right + sum(values, []),
        [detection("table", (0, 499, 599, 510)), detection("table", (0, 480, 599, 499), 3)],
    )
    assert mapping[id(left[0])] != mapping[id(right[0])]


def test_unrelated_following_label_remains_its_own_cell():
    left, right, values = wrapped_rows()
    following = run("Qubit coupling frequency", x=20, y=475, size=9.5)
    mapping, _ = recover(left + right + sum(values, []) + following,
                         [detection("table", (0, 450, 599, 550))])
    assert mapping[id(left[0])] == mapping[id(right[0])]
    assert mapping[id(left[0])] != mapping[id(following[0])]
