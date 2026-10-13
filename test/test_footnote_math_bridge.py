from types import SimpleNamespace

import pytest

from pdf2zh.converter import _bridge_recovered_footnote_math


def char(text, x, size=8, y=100, font="ABC+CMR8"):
    return SimpleNamespace(get_text=lambda: text, x0=x, x1=x+4, y0=y,
                           size=size, fontname=font, matrix=(1, 0, 0, 1, x, y))


def fixture():
    operator = [char("m", 100), char("i", 104), char("n", 108)]
    domain = [char("x", 112, 6, 98, "ABC+CMMI6"),
              char("\\in ", 116, 6, 98, "ABC+CMSY6"),
              char("X", 120, 6, 98, "ABC+CMSY6")]
    objective = [char("f", 127, font="ABC+CMMI8"), char("(", 131),
                 char("x", 135, font="ABC+CMMI8"), char(")", 139)]
    recovered = {id(c): cls for group, cls in [(operator, 2), (objective, 3)] for c in group}
    return operator, domain, objective, recovered, {2: "footnote", 3: "footnote"}


def test_operator_domain_and_objective_form_one_unbreakable_footnote_formula():
    operator, domain, objective, recovered, kinds = fixture()
    glyphs = operator + domain + objective
    before = [(c.get_text(), c.matrix, c.size, c.fontname) for c in glyphs]
    operators = _bridge_recovered_footnote_math(glyphs, recovered, kinds, set())
    assert operators == {id(c) for c in operator}
    assert set(recovered.values()) == {2}
    assert all(id(c) in recovered for c in domain)
    assert before == [(c.get_text(), c.matrix, c.size, c.fontname) for c in glyphs]


@pytest.mark.parametrize("change", ["body", "excluded", "missing_membership", "domain_gap", "other_line", "another_column", "rotated"])
def test_unrelated_math_or_prose_is_not_merged(change):
    operator, domain, objective, recovered, kinds = fixture()
    excluded = set()
    if change == "body": kinds[2] = "plain text"
    elif change == "excluded": excluded.add(id(operator[0]))
    elif change == "missing_membership": domain[1].get_text = lambda: "+"
    elif change == "domain_gap":
        for c in domain: c.x0 += 30; c.x1 += 30
    elif change == "other_line": objective[0].y0 -= 15
    elif change == "another_column":
        for c in objective: c.x0 += 50; c.x1 += 50
    elif change == "rotated": operator[0].matrix = (0, 1, -1, 0, 100, 100)
    before = recovered.copy()
    assert not _bridge_recovered_footnote_math(operator + domain + objective, recovered, kinds, excluded)
    assert recovered == before
