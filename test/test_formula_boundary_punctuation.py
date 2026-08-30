from __future__ import annotations

import unittest

from pdf2zh.converter import _split_trailing_prose_openers


class FakeChar:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text


def chars(text: str) -> list[FakeChar]:
    return [FakeChar(value) for value in text]


class FormulaBoundaryPunctuationTests(unittest.TestCase):
    def test_unmatched_opening_parenthesis_is_returned_to_following_prose(self) -> None:
        formula, suffix = _split_trailing_prose_openers(chars("|010⟩("))

        self.assertEqual("".join(item.get_text() for item in formula), "|010⟩")
        self.assertEqual(suffix, "(")

    def test_balanced_formula_parentheses_are_not_detached(self) -> None:
        source = chars("sin(x)")

        formula, suffix = _split_trailing_prose_openers(source)

        self.assertIs(formula, source)
        self.assertEqual(suffix, "")

    def test_closing_formula_punctuation_is_not_detached(self) -> None:
        source = chars("|010⟩)")

        formula, suffix = _split_trailing_prose_openers(source)

        self.assertIs(formula, source)
        self.assertEqual(suffix, "")


if __name__ == "__main__":
    unittest.main()
