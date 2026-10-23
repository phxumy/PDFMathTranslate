from pdf2zh.converter import _should_extend_formula_run
from test_italic_translation import FakeItalicChar


def test_wordlike_subscript_remains_formula_even_with_unreduced_font_size():
    base = FakeItalicChar(
        "I", 20, 23, fontname="MinionPro-It", size=9.2653, baseline=100
    )
    base.y1 = 109.2653
    chars = [base]
    for index, letter in enumerate("chiral"):
        ch = FakeItalicChar(
            letter,
            23 + index * 4,
            27 + index * 4,
            fontname="MinionPro-Regular",
            size=9.2653,
            baseline=97.219,
        )
        ch.y1 = 106.4843
        ch._pdf2zh_prose_word_start = index == 0
        ch._pdf2zh_prose_word_length = 6
        assert _should_extend_formula_run(chars, ch)
        chars.append(ch)


def test_same_baseline_prose_word_is_not_a_subscript():
    base = FakeItalicChar(
        "I", 20, 23, fontname="MinionPro-It", size=9.2653, baseline=100
    )
    base.y1 = 109.2653
    ch = FakeItalicChar(
        "c", 25.5, 29.5, fontname="MinionPro-Regular", size=9.2653, baseline=100
    )
    ch.y1 = 109.2653
    ch._pdf2zh_prose_word_start = True
    ch._pdf2zh_prose_word_length = 7
    assert not _should_extend_formula_run([base], ch)
