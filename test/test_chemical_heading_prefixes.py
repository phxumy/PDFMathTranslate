from pdf2zh.converter import _release_bold_chemical_names
from test_italic_translation import fake_run, paragraph


def body(x, y, x1, *, kind="plain text", layout=2):
    value = paragraph(region_kind=kind)
    value.x = value.x0 = x
    value.x1 = x1
    value.y = value.y0 = y
    value.y1 = y + 10
    value.size = 10
    value.page_id = 0
    value.layout_class = layout
    return value


def bold(value, layout=2):
    chars = fake_run(value, fontname="Times-BoldItalic")
    for char in chars:
        char._pdf2zh_layout_class = layout
    return chars


def test_same_line_alkyl_prefix_joins_the_complete_numbered_chemical_heading():
    prefix, following = body(50, 500, 90), body(90.05, 500, 550)
    following.x0 = 50  # Wrapped body text extends back to the column margin.
    following.y0 = 440
    following.break_offsets = [80]
    texts = [
        "{v0}",
        " 1-(4-cyanophenyl)cyclobutylcarbamate (12). Compound 3 was added.",
    ]
    owners = [0]
    chars = [[], []]
    _release_bold_chemical_names(
        texts, [bold("Tert-butyl")], owners, [prefix, following], chars
    )
    assert texts == [
        "",
        "Tert-butyl 1-(4-cyanophenyl)cyclobutylcarbamate (12). Compound 3 was added.",
    ]
    assert owners == [1]
    assert following.x == 50 and following.y == 500 and following.y0 == 440
    assert following.break_offsets == [90]
    assert chars[1] and not chars[0]


def test_alkyl_prefix_never_releases_diagram_glyphs_or_unrelated_paragraphs():
    for change in (
        "glyph-zero",
        "region-figure",
        "class-zero",
        "next-line",
        "different-page",
        "ordinary-prose",
    ):
        prefix, following = body(50, 500, 90), body(90.05, 500, 550)
        formula = bold("Tert-butyl", layout=0 if change == "glyph-zero" else 2)
        source = " 1-(4-cyanophenyl)cyclobutylcarbamate (12)."
        if change == "region-figure":
            prefix.region_kind = "figure"
        elif change == "class-zero":
            prefix.layout_class = following.layout_class = 0
        elif change == "next-line":
            following.y -= 14
        elif change == "different-page":
            following.page_id = 1
        elif change == "ordinary-prose":
            source = " The value was calculated at 2.0 V."
        texts = ["{v0}", source]
        _release_bold_chemical_names(
            texts, [formula], [0], [prefix, following], [[], []]
        )
        assert texts == ["{v0}", source], change


def test_wrapped_bold_name_reaches_its_number_across_title_body_class_boundary():
    before = body(50, 500, 500, kind="title", layout=6)
    after = body(50, 486, 550, layout=3)
    texts = ["2-(4-({v0}", "carboxylic acid (17). Aqueous base was added."]
    formulas = [bold("tert-butoxycarbonylamino)phenyl)-3-phenylpyridine-7-")]
    owners = [0]
    _release_bold_chemical_names(texts, formulas, owners, [before, after], [[], []])
    assert texts[0] == ""
    assert texts[1].startswith(
        "2-(4-(tert-butoxycarbonylamino)phenyl)-3-phenylpyridine-7-carboxylic acid (17)."
    )
    assert owners == [1]
    assert after.y == 500 and after.x == 50


def test_wrapped_name_requires_numbered_continuation_and_rejects_graphic_atoms():
    for change in (
        "missing-number",
        "different-column",
        "glyph-zero",
        "different-page",
    ):
        before, after = body(50, 500, 500, kind="title", layout=6), body(
            50, 486, 550, layout=3
        )
        source = "carboxylic acid (17). Base was added."
        if change == "missing-number":
            source = "The next reaction followed."
        elif change == "different-column":
            after.x0 = after.x = 320
        elif change == "different-page":
            after.page_id = 1
        formula = bold(
            "tert-butoxycarbonylamino)phenyl)-3-phenylpyridine-7-",
            layout=0 if change == "glyph-zero" else 2,
        )
        texts = ["2-(4-({v0}", source]
        _release_bold_chemical_names(texts, [formula], [0], [before, after], [[], []])
        assert texts == ["2-(4-({v0}", source], change


def test_alkyl_prefix_and_wrapped_name_can_join_across_a_whitespace_only_segment():
    parts = [
        body(50, 500, 90),
        body(90.05, 500, 550),
        body(550, 500, 553, kind="", layout=1),
        body(50, 486, 550),
    ]
    texts = ["{v0}", " 1-(4-({v1}", " ", "(18a). Reagent 2 was added."]
    formulas = [
        bold("Tert-butyl"),
        bold("tert-butylimino)methyl)phenyl)cyclobutylcarbamate"),
    ]
    owners = [0, 1]
    _release_bold_chemical_names(texts, formulas, owners, parts, [[], [], [], []])
    assert texts[:2] == ["", ""]
    assert texts[3].startswith(
        "Tert-butyl 1-(4-(tert-butylimino)methyl)phenyl)cyclobutylcarbamate (18a)."
    )
    assert owners == [3, 3]
