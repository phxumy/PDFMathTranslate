from __future__ import annotations

import unittest

from pdf2zh.translator import CodexTranslator


class ScientificSourceSegmentationTests(unittest.TestCase):
    def setUp(self):
        self.translator = CodexTranslator.__new__(CodexTranslator)

    def test_abbreviations_and_formula_placeholders_remain_in_their_sentence(self):
        for label in ("Fig.", "Figs.", "Eq.", "Eqs.", "Sec.", "Refs."):
            for number in (
                "1(a)",
                "(2)",
                "2.1",
                "C.1",
                "VI",
                "{v12}",
                "{{v5}}",
                "(7{v32}",
            ):
                source = f"Consider {label} {number} here. Next sentence."
                with self.subTest(source=source):
                    self.assertEqual(
                        self.translator._split_formula_context_text(source),
                        [f"Consider {label} {number} here. ", "Next sentence."],
                    )

    def test_length_splitting_does_not_cut_reference_units(self):
        source = "alpha " * 48 + "Fig. 12(a) controls the visible result."
        for limit in range(290, 301):
            self.translator.MAX_ITEM_CHARS = limit
            with self.subTest(limit=limit):
                chunks = self.translator._split_long_text(source)
                self.assertEqual("".join(chunks), source)
                self.assertTrue(all(len(chunk) <= limit for chunk in chunks))
                self.assertEqual(sum("Fig. 12(a)" in chunk for chunk in chunks), 1)

    def test_empty_and_actual_sentence_boundaries_are_preserved(self):
        for source, expected in (
            ("", [""]),
            ("See Fig. 1(a). Next.", ["See Fig. 1(a). ", "Next."]),
            ("A Fig. This is prose.", ["A Fig. ", "This is prose."]),
            (
                "Values: first; second? Third!",
                ["Values: ", "first; ", "second? ", "Third!"],
            ),
            ("No terminal punctuation", ["No terminal punctuation"]),
            ("One.\nTwo.", ["One.\n", "Two."]),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    self.translator._split_formula_context_text(source), expected
                )

    def test_long_unspaced_text_does_not_break_formula_or_layout_tokens(self):
        for source in (
            "x" * 20 + " p{v123456} " + "z" * 30,
            "{v0}{v1} " + "z" * 30,
            "x" * 20 + "[[PDF2ZH_FLOW_123]]" + "z" * 30,
        ):
            for limit in range(5, 29):
                self.translator.MAX_ITEM_CHARS = limit
                chunks = self.translator._split_long_text(source)
                with self.subTest(source=source, limit=limit):
                    self.assertEqual("".join(chunks), source)
                    for start, end, _ in self.translator._formula_atom_spans(source):
                        atom = source[start:end]
                        self.assertEqual(sum(atom in chunk for chunk in chunks), 1)
                    if "[[PDF2ZH_FLOW_123]]" in source:
                        self.assertEqual(
                            sum("[[PDF2ZH_FLOW_123]]" in chunk for chunk in chunks), 1
                        )


if __name__ == "__main__":
    unittest.main()
