from test_cm_prose_and_region_recovery import detection, recover, run


def test_multiline_equation_releases_only_separate_prose_labels():
    math = run("X=Y", x=20, y=500, font="CMMI10")
    opening = run("(", x=300, y=500)
    label = run("disjoint supports", x=opening[-1].x1, y=500)
    closing = run(")", x=label[-1].x1, y=500)
    second = run("bound on", x=300, y=470)
    norm = run("δ", x=second[-1].x1 + 3, y=470, font="CMMI10")
    chars = math + opening + label + closing + second + norm
    geometry = [(ch.x0, ch.y0, ch.x1, ch.y1, ch.fontname) for ch in chars]
    mapping, kinds = recover(chars, [detection("isolate_formula", (0, 450, 599, 550))])
    assert set(mapping) == {id(ch) for ch in label + second}
    assert mapping[id(label[0])] != mapping[id(second[0])]
    assert {kinds[cls] for cls in mapping.values()} == {"formula_annotation"}
    assert [(ch.x0, ch.y0, ch.x1, ch.y1, ch.fontname) for ch in chars] == geometry


def test_underbraced_labels_are_independent_and_symbols_remain_protected():
    left = run("Relative change in solution", x=20, y=500)
    operator = run("≤κ", x=left[-1].x1 + 3, y=500, font="CMSY10")
    right = run("Relative change in data vector", x=operator[-1].x1 + 3, y=500)
    underbrace = run("_", x=20, y=480, font="CMEX10")
    fraction = run("xy", x=65, y=460, font="CMMI5", size=5)
    mapping, _ = recover(
        left + underbrace + fraction + operator + right,
        [detection("isolate_formula", (0, 400, 599, 550))],
    )
    assert set(mapping) == {id(ch) for ch in left + right}
    assert mapping[id(left[0])] != mapping[id(right[0])]


def test_figure_requires_both_direct_underbrace_and_math_relation():
    label = run("Relative change in solution", x=20, y=500)
    operator = run("≤", x=label[-1].x1 + 3, y=500, font="CMSY10")
    brace = run("_", x=20, y=494, font="CMEX10")[0]
    brace.x1 = label[-1].x1
    brace.get_text = lambda: r"\underbrace{} "
    figure = [detection("figure", (0, 450, 599, 550))]
    mapping, _ = recover(label + operator + [brace], figure)
    assert set(mapping) == {id(ch) for ch in label}
    for chars in (label + operator, label + [brace]):
        mapping, _ = recover(chars, figure)
        assert not mapping
    brace.y0 = 480
    brace.matrix = (*brace.matrix[:-1], 480)
    mapping, _ = recover(label + operator + [brace], figure)
    assert not mapping


def test_where_requires_a_separate_explanatory_clause_between_equations():
    def make(left_text="x=y,", right_text="g∼N", gap=5, font="CMR8"):
        left = run(left_text, x=20, y=500, font="CMMI10")
        word = run("where", x=left[-1].x1 + gap, y=500, font=font)
        right = run(right_text, x=word[-1].x1 + gap, y=500, font="CMMI10")
        mapping, _ = recover(left + word + right, [detection("isolate_formula", (0, 450, 599, 550))])
        return mapping, word

    mapping, word = make()
    assert set(mapping) == {id(ch) for ch in word}
    for params in (
        {"left_text": "x=y"}, {"left_text": "x,"},
        {"right_text": "(x)"}, {"gap": 0}, {"font": "CMMI10"},
    ):
        mapping, _ = make(**params)
        assert not mapping


def test_small_requires_quantified_variable_and_stays_separate_from_math():
    quantifier = run("∀ε", x=20, y=500, font="CMMI10")
    small = run("small", x=quantifier[-1].x1 + 3, y=500)
    mapping, _ = recover(quantifier + small, [detection("isolate_formula", (0, 450, 599, 550))])
    assert set(mapping) == {id(ch) for ch in small}
    for prefix in ("ε", "small", "∀"):
        prior = run(prefix, x=20, y=500, font="CMMI10")
        word = run("small", x=prior[-1].x1 + 3, y=500)
        mapping, _ = recover(prior + word, [detection("isolate_formula", (0, 450, 599, 550))])
        assert not mapping


def test_operators_variables_and_single_labels_do_not_become_prose():
    for text, font in (
        ("sin x + cos y", "CMR10"),
        ("x y z", "CMMI10"),
        ("rank sign dist", "CMR10"),
        ("Polyak-RMD", "CMR10"),
        ("mirror", "CMR10"),
        ("where(x)", "CMR10"),
    ):
        chars = run(text, y=500, font=font)
        mapping, _ = recover(chars, [detection("isolate_formula", (0, 450, 599, 550))])
        assert not mapping
