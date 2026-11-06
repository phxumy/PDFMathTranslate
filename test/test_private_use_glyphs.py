from test_code_blocks import page_with_lines
from test_cm_prose_and_region_recovery import run
from types import SimpleNamespace
from pdf2zh.converter import _release_private_punctuation_articles


def test_unknown_private_use_glyph_keeps_its_source_cid_instead_of_cjk_replacement():
    converter, page = page_with_lines(
        ["A scientific title \ue0d5 with ordinary words."], fontname="Times-Roman"
    )
    private = next(char for char in page if char.get_text() == "\ue0d5")
    source_cid, source_font = private.cid, private.font
    draft = converter.receive_layout(page, preview_only=True)
    source = " ".join(draft.sstk)
    assert "scientific title" in source and "ordinary words" in source
    assert "\ue0d5" not in source
    assert any(private is char for group in draft.var for char in group)
    assert private.cid == source_cid and private.font is source_font
    assert any("\ue0d5" in value for value in draft.formula_texts)
    assert all(
        "\ue0d5" not in value for value in draft.readonly_formula_contexts.values()
    )


def test_article_after_private_punctuation_is_prose_but_chemical_prefix_is_not():
    for suffix, following, expected in [
        (
            "An",
            " investigation of substituents.",
            "Part XXVII{v0} An investigation of substituents.",
        ),
        ("O-", "acylation reactions.", "Part XXVII{v0}acylation reactions."),
        ("An", " = 2", "Part XXVII{v0} = 2"),
    ]:
        chars = run("\ue0d5" + suffix, font="Times-Roman", size=10)
        texts = ["Part XXVII{v0}" + following]
        formulas = [chars]
        _release_private_punctuation_articles(
            texts, formulas, [0], [SimpleNamespace(region_kind="plain text")], [[]]
        )
        assert texts == [expected]
        if suffix == "An" and "investigation" in following:
            assert formulas[0] == chars[:1]
