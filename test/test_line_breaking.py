from __future__ import annotations

import unittest

from pdf2zh.line_breaking import (
    CJK_PROHIBITED_LINE_END,
    CJK_PROHIBITED_LINE_START,
    iter_line_break_units,
    iter_protected_literals,
    iter_render_atoms,
    normalize_protected_literals,
)


class RenderAtomTests(unittest.TestCase):
    def test_latin_words_and_internal_separators_are_atomic(self) -> None:
        atoms = list(iter_render_atoms("Josephson energy-based ratio_2.0/day"))

        self.assertEqual(
            [(atom.kind, atom.text) for atom in atoms],
            [
                ("latin", "Josephson"),
                ("char", " "),
                ("latin", "energy-based"),
                ("char", " "),
                ("latin", "ratio_2.0/day"),
            ],
        )

    def test_sentence_period_does_not_swallow_next_word(self) -> None:
        atoms = list(iter_render_atoms("word.Next"))

        self.assertEqual([atom.text for atom in atoms], ["word", ".", "Next"])

    def test_url_is_one_non_splittable_literal_atom(self) -> None:
        atoms = list(iter_render_atoms("https://example.org/a-b_2?q=one."))

        self.assertEqual(
            [atom.text for atom in atoms], ["https://example.org/a-b_2?q=one", "."]
        )
        self.assertEqual("literal", atoms[0].kind)
        self.assertFalse(atoms[0].fallback_splittable)

    def test_doi_and_email_are_non_splittable_literal_atoms(self) -> None:
        atoms = list(
            iter_render_atoms("doi:10.1038/s41534-021-00461-8 author.name@example.edu")
        )

        literals = [atom for atom in atoms if atom.kind == "literal"]
        self.assertEqual(
            [atom.text for atom in literals],
            ["doi:10.1038/s41534-021-00461-8", "author.name@example.edu"],
        )
        self.assertTrue(all(not atom.fallback_splittable for atom in literals))

    def test_pdf_structural_spaces_inside_literals_are_removed(self) -> None:
        source = (
            "Code at http:// github.com/zlatko-minev/pyEPR; paper at "
            "https:// www.nature.com/articles/s41586- 020-2603-3. "
            "License: http://creativecommons. org/licenses/by/4.0/. "
            "Mail: author.name @ example . edu."
        )

        normalized = normalize_protected_literals(source)

        self.assertEqual(
            normalized,
            "Code at http://github.com/zlatko-minev/pyEPR; paper at "
            "https://www.nature.com/articles/s41586-020-2603-3. "
            "License: http://creativecommons.org/licenses/by/4.0/. "
            "Mail: author.name@example.edu.",
        )
        self.assertEqual(
            [literal.text for literal in iter_protected_literals(normalized)],
            [
                "http://github.com/zlatko-minev/pyEPR",
                "https://www.nature.com/articles/s41586-020-2603-3",
                "http://creativecommons.org/licenses/by/4.0/",
                "author.name@example.edu",
            ],
        )

    def test_ordinary_sentence_spaces_are_not_joined_into_url(self) -> None:
        source = "See https://example.org/path. Next sentence stays separate."

        self.assertEqual(source, normalize_protected_literals(source))

    def test_number_after_completed_url_is_not_joined_to_its_path(self) -> None:
        source = "Preprint at http://arxiv.org/abs/2006.04130. 2006.04130."

        self.assertEqual(source, normalize_protected_literals(source))

    def test_formula_placeholders_are_atomic(self) -> None:
        atoms = list(iter_render_atoms("甲{v12}乙{{ V 3 }}丙"))

        self.assertEqual(
            [(atom.kind, atom.text) for atom in atoms],
            [
                ("char", "甲"),
                ("formula", "{v12}"),
                ("char", "乙"),
                ("formula", "{{ V 3 }}"),
                ("char", "丙"),
            ],
        )

    def test_italic_markers_are_zero_width_atoms(self) -> None:
        atoms = list(
            iter_render_atoms("[[PDF2ZH_ITALIC_7_BEGIN]]in situ[[PDF2ZH_ITALIC_7_END]]")
        )

        self.assertEqual(
            [atom.kind for atom in atoms], ["style", "latin", "char", "latin", "style"]
        )
        self.assertTrue(atoms[0].zero_width)
        self.assertTrue(atoms[-1].zero_width)

    def test_crlf_and_single_newlines_are_explicit_atoms(self) -> None:
        atoms = list(iter_render_atoms("甲\r\n乙\n丙\r丁"))

        self.assertEqual(
            [(atom.kind, atom.text) for atom in atoms],
            [
                ("char", "甲"),
                ("newline", "\r\n"),
                ("char", "乙"),
                ("newline", "\n"),
                ("char", "丙"),
                ("newline", "\r"),
                ("char", "丁"),
            ],
        )


class LineBreakUnitTests(unittest.TestCase):
    def test_chinese_closing_punctuation_binds_backward(self) -> None:
        units = list(iter_line_break_units("甲，乙。"))

        self.assertEqual([unit.text for unit in units], ["甲，", "乙。"])
        self.assertIn("，", CJK_PROHIBITED_LINE_START)

    def test_chinese_opening_punctuation_binds_forward(self) -> None:
        units = list(iter_line_break_units("甲（乙）丙《丁》"))

        self.assertEqual(
            [unit.text for unit in units],
            ["甲", "（乙）", "丙", "《丁》"],
        )
        self.assertIn("（", CJK_PROHIBITED_LINE_END)

    def test_short_chinese_figure_reference_is_one_unit(self) -> None:
        units = list(iter_line_break_units("结果（见图1）所示"))

        self.assertEqual(
            [unit.text for unit in units],
            ["结", "果", "（见图1）", "所", "示"],
        )

    def test_short_half_width_reference_is_one_unit(self) -> None:
        units = list(iter_line_break_units("result (see Fig. 2) shows"))

        self.assertIn("(see Fig. 2)", [unit.text for unit in units])

    def test_short_nested_and_formula_reference_is_one_unit(self) -> None:
        units = list(iter_line_break_units("结果（见图（a）及式{v12}）一致"))

        self.assertIn("（见图（a）及式{v12}）", [unit.text for unit in units])

    def test_long_parenthesized_paragraph_keeps_internal_breaks(self) -> None:
        phrase = "（" + "很长的括号内容" * 5 + "）"
        units = list(iter_line_break_units(phrase))

        self.assertGreater(len(units), 1)
        self.assertNotIn(phrase, [unit.text for unit in units])

    def test_formula_and_latin_word_remain_separate_atomic_units(self) -> None:
        units = list(iter_line_break_units("量子{v0}Josephson。"))

        self.assertEqual(
            [unit.text for unit in units],
            ["量", "子", "{v0}", "Josephson。"],
        )
        self.assertFalse(units[2].fallback_splittable)
        self.assertTrue(units[3].fallback_splittable)

    def test_base_letter_and_formula_placeholder_stay_on_one_line(self) -> None:
        units = list(iter_line_break_units("记作p{v10}，随后说明"))

        self.assertIn("p{v10}，", [unit.text for unit in units])

    def test_split_font_inline_expression_is_one_line_break_unit(self) -> None:
        text = "{v2} t ð Þ :¼ R {v3} v{v4} ð Þ d{v5}, where v is voltage"
        units = list(iter_line_break_units(text))

        self.assertEqual(
            units[0].text,
            "{v2} t ð Þ :¼ R {v3} v{v4} ð Þ d{v5},",
        )
        self.assertEqual(units[1].text, " ")
        self.assertEqual(units[2].text, "where")

    def test_formula_cluster_stops_before_natural_language_conjunction(self) -> None:
        units = list(iter_line_break_units("{v0} E{v1} and H{v2}"))

        self.assertEqual(units[0].text, "{v0} E{v1}")
        self.assertEqual(units[2].text, "and")
        self.assertEqual(units[4].text, "H{v2}")

    def test_formula_cluster_keeps_misdecoded_parentheses_and_chinese_comma(
        self,
    ) -> None:
        units = list(iter_line_break_units("Z{v12} ð Þ，其中"))

        self.assertEqual(units[0].text, "Z{v12} ð Þ，")
        self.assertEqual(units[1].text, "其")

    def test_long_latin_unit_allows_character_fallback(self) -> None:
        units = list(iter_line_break_units("SuperconductingQuantumCircuit"))

        self.assertEqual(len(units), 1)
        self.assertTrue(units[0].fallback_splittable)

    def test_style_markers_stay_with_adjacent_visible_content(self) -> None:
        units = list(
            iter_line_break_units(
                "[[PDF2ZH_ITALIC_1_BEGIN]]in[[PDF2ZH_ITALIC_1_END]]（位）"
            )
        )

        self.assertEqual(
            [unit.text for unit in units],
            [
                "[[PDF2ZH_ITALIC_1_BEGIN]]in[[PDF2ZH_ITALIC_1_END]]",
                "（位）",
            ],
        )

    def test_explicit_newline_is_its_own_break_unit(self) -> None:
        units = list(iter_line_break_units("甲，\r\n（乙）"))

        self.assertEqual([unit.text for unit in units], ["甲，", "\r\n", "（乙）"])


if __name__ == "__main__":
    unittest.main()
