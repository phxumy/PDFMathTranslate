from pdf2zh.converter import _release_reference_number_labels
from pdf2zh.translation_policy import (
    find_reference_markers,
    looks_like_reference_title_prefix,
)
from test_italic_translation import FakeItalicChar, paragraph


def chars(text, *, x=50, baseline=100, size=10):
    return [
        FakeItalicChar(
            c,
            x + i * 5,
            x + (i + 1) * 5,
            fontname="Times-Roman",
            size=size,
            baseline=baseline,
        )
        for i, c in enumerate(text)
    ]


def test_superscript_reference_label_detaches_from_author_initial():
    p = paragraph(region_kind="plain text")
    p.layout_class = 3
    for label, suffix in (
        ("5C", ". Wang"),
        ("10A", " Smith"),
        ("46Mo", " Chen"),
        ("4Wu", ", Y."),
    ):
        n = len(label.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"))
        digits = chars(label[:n], baseline=103, size=7.5)
        initial = chars(label[n:], x=50 + n * 5)
        formulas = [digits + initial]
        source = f"4A. Jones. Quantum coherence. Nature 2024, 10, 20. {{v0}}{suffix}, B. Jones. New quantum circuits. Nature 2025, 11, 30."
        texts = [source]
        _release_reference_number_labels(texts, formulas, [[]], [0], [p], [[]])
        assert label + suffix in texts[0]
        assert int(label[:n]) in [m.number for m in find_reference_markers(texts[0])]


def test_next_line_superscript_is_not_part_of_previous_publication_year():
    p = paragraph(region_kind="plain text")
    p.layout_class = 3
    venue = chars("Nature,10:20,2024.", x=100, baseline=110)
    digits = chars("17", x=50, baseline=103, size=7.5)
    formulas = [venue + digits]
    texts = [
        "16A. Jones. Quantum devices. {v0}Conal Smith. Quantum materials. Nature 2025, 11, 30."
    ]
    _release_reference_number_labels(texts, formulas, [[]], [0], [p], [[]])
    assert "{v0} 17Conal Smith" in texts[0]
    assert "".join(c.get_text() for c in formulas[0]) == "Nature,10:20,2024."


def test_normal_sized_numerals_and_nonreference_prose_stay_protected():
    p = paragraph(region_kind="plain text")
    p.layout_class = 3
    for source, size in (
        ("A sample at {v0}. Wang reports a measurement.", 7.5),
        (
            "4A. Jones. Quantum coherence. Nature 2024, 10, 20. {v0}. Wang. New circuits. Nature 2025, 11, 30.",
            10,
        ),
    ):
        texts = [source]
        formula = chars("5", size=size, baseline=103) + chars("C", x=55)
        _release_reference_number_labels(texts, [formula], [[]], [0], [p], [[]])
        assert texts == [source]


def test_title_boundary_strips_tight_numbers_before_undotted_author_names():
    for prefix in (
        "1C Yu Smith and Hervé M Jones.",
        "10A Smith, B Jones, C Brown, et al.",
        "41M Smith, AV Jones, and SE de Graaf.",
        "49Nikita S Smith, Elizaveta A Jones, and Ilya A Brown.",
    ):
        assert looks_like_reference_title_prefix(prefix), prefix
        assert not looks_like_reference_title_prefix(
            prefix + " Quantum coherence in devices."
        )


def test_superscript_after_parenthesized_year_starts_next_reference():
    p = paragraph(region_kind="plain text")
    p.layout_class = 3
    venue = chars("Nature 10, 20 (2024).", x=100, baseline=110)
    formulas = [venue + chars("2", x=50, baseline=103, size=7.5)]
    texts = [
        "1Smith, A. Quantum devices. {v0}Jones, B. Quantum materials. Nature 11, 30 (2025)."
    ]
    _release_reference_number_labels(texts, formulas, [[]], [0], [p], [[]])
    assert "{v0} 2Jones" in texts[0]


def test_year_outside_formula_still_proves_next_line_reference_number():
    p = paragraph(region_kind="plain text")
    p.layout_class = 3
    ending = chars(").", x=130, baseline=110)
    formulas = [ending + chars("6", x=50, baseline=103, size=7.5)]
    texts = [
        "5Smith, A. Quantum devices. Nature 10, 20 (2024{v0}Jones, B. Quantum materials. Nature 11, 30 (2025)."
    ]
    _release_reference_number_labels(texts, formulas, [[]], [0], [p], [[]])
    assert "{v0} 6Jones" in texts[0]
    assert "".join(ch.get_text() for ch in formulas[0]) == ")."
