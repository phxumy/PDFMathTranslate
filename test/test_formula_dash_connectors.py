from types import SimpleNamespace

import pytest

from pdf2zh.converter import _split_trailing_formula_prose_word


def glyphs(text):
    return [
        SimpleNamespace(
            get_text=lambda text=ch: text,
            fontname="STIXTwoMath-Regular" if ch in "|⟩" else "HardingText-Regular",
            x0=i * 4,
            x1=i * 4 + 4,
            y0=100,
            y1=108,
            size=8,
            matrix=(8, 0, 0, 8, i * 4, 100),
            _pdf2zh_layout_class=3,
        )
        for i, ch in enumerate(text)
    ]


def test_grammar_connector_after_closed_ket_returns_to_prose():
    chars = glyphs("|01⟩—to")
    formula, prose, released = _split_trailing_formula_prose_word(
        chars, has_prose_context=True, paragraph_layout_class=3
    )
    assert "".join(c.get_text() for c in formula) == "|01⟩"
    assert prose == "—to"
    assert released == chars[-3:]


@pytest.mark.parametrize("text", ["x—to", "|01⟩−to", "|01⟩—time"])
def test_math_identifiers_and_unknown_dash_suffixes_are_not_released(text):
    chars = glyphs(text)
    assert _split_trailing_formula_prose_word(
        chars, has_prose_context=True, paragraph_layout_class=3
    ) == (chars, "", [])


@pytest.mark.parametrize("context, layout", [(False, 3), (True, 0)])
def test_protected_or_standalone_runs_remain_unchanged(context, layout):
    chars = glyphs("|01⟩—to")
    assert _split_trailing_formula_prose_word(
        chars, has_prose_context=context, paragraph_layout_class=layout
    ) == (chars, "", [])
