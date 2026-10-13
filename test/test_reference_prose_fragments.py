from types import SimpleNamespace

import pytest

from pdf2zh.converter import Paragraph, _release_reference_prose_fragments


def chars(word, x=100, y=500):
    result = []
    for letter in word:
        char = SimpleNamespace(
            x0=x,
            x1=x + 4,
            y0=y,
            size=8,
            fontname="ABC+CMTI8",
            matrix=(1, 0, 0, 1, x, y),
            _pdf2zh_layout_class=2,
            get_text=lambda value=letter: value,
        )
        result.append(char)
        x += 4
    return result


def reference(title):
    return f"[1] A. Smith and B. Jones, {title}, J. Math., 12 (2020), pp. 1--9."


def paragraph():
    return Paragraph(
        500,
        100,
        60,
        430,
        450,
        510,
        8,
        True,
        page_id=26,
        layout_class=2,
        region_kind="plain text",
    )


@pytest.mark.parametrize(
    "title,word,expected",
    [
        ("Low-{v0} matrix recovery", "rank", "Low-rank matrix recovery"),
        (
            "Recovery via {v0}-one projections",
            "rank",
            "Recovery via rank-one projections",
        ),
        (
            "Robust {v0} one bilinear sensing",
            "rank",
            "Robust rank one bilinear sensing",
        ),
        ("Minimum-{v0} solutions", "rank", "Minimum-rank solutions"),
        (
            "A scheme for speeding {v0} first-order methods",
            "up",
            "A scheme for speeding up first-order methods",
        ),
    ],
)
def test_ordinary_reference_compounds_reach_translation(title, word, expected):
    source = [reference(title)]
    runs = [chars(word)]
    original = list(runs[0])
    _release_reference_prose_fragments(source, runs, [0], [paragraph()], [[]])
    assert source == [reference(expected)]
    assert runs[0] == original


@pytest.mark.parametrize(
    "change",
    [
        "body",
        "operator",
        "math_font",
        "code_font",
        "wrong_size",
        "spaced",
        "figure",
        "rotated",
    ],
)
def test_math_and_non_bibliography_fragments_remain_protected(change):
    source = [reference("Low-{v0} matrix recovery")]
    p = paragraph()
    run = chars("rank")
    if change == "body":
        source[0] = "We study low-{v0} matrix recovery."
    elif change == "operator":
        source[0] = reference("The {v0}(A) of a matrix")
    elif change == "math_font":
        run[0].fontname = "ABC+CMMI8"
    elif change == "code_font":
        run[0].fontname = "ABC+CMTT8"
    elif change == "wrong_size":
        run[0].size = 5
    elif change == "spaced":
        run[1].x0 += 20
    elif change == "figure":
        p.layout_class = 0
    elif change == "rotated":
        run[0].matrix = (0, 1, -1, 0, 100, 500)
    before = source.copy()
    _release_reference_prose_fragments(source, [run], [0], [p], [[]])
    assert source == before


def test_word_wrapped_inside_protected_macro_is_joined_from_source_glyphs():
    source = [reference("Conditioning of semi-algebraic {v0}els")]
    suffix = chars("els", x=76, y=490)
    _release_reference_prose_fragments(
        source, [chars("mod-")], [0], [paragraph()], [suffix]
    )
    assert "semi-algebraic models" in source[0]


def test_overprinted_accent_and_visible_suffix_form_one_title_word():
    source = [reference("Functions with {v0}lderian growth")]
    run = chars(["H", '\\"', "o"])
    run[-1].x0, run[-1].x1 = run[1].x0, run[1].x1
    suffix = chars("lderian", x=run[-1].x1)
    _release_reference_prose_fragments(source, [run], [0], [paragraph()], [suffix])
    assert "Hölderian growth" in source[0]
