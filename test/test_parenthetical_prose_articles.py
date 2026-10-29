from pdf2zh.converter import _release_parenthetical_prose_articles
from test_cm_prose_and_region_recovery import run


def release(following, *, article="a", font="Times-Roman", gap=2, category=3, shift=0):
    symbol = run("ϵ", x=20, y=100, font="Symbol", size=10)
    suffix = run(
        "(" + article, x=symbol[-1].x1 + gap, y=100 + shift, font=font, size=10
    )
    for char in symbol + suffix:
        char._pdf2zh_layout_class = category
    formulas = [symbol + suffix]
    texts = ["Frequency error {v0}" + following]
    source_chars = [[]]
    original = [
        (char.get_text(), char.x0, char.y0, char.fontname) for char in formulas[0]
    ]
    _release_parenthetical_prose_articles(texts, formulas, [0], source_chars)
    assert original == [
        (char.get_text(), char.x0, char.y0, char.fontname) for char in symbol + suffix
    ]
    return texts[0], formulas[0], source_chars[0]


def test_article_of_descriptive_parenthesis_returns_to_prose_with_original_symbol():
    for article, phrase in (
        ("a", " relative offset in frequency)"),
        ("an", " additional error term)"),
        ("a", " proxy of {v1})"),
        ("an", " estimate for the decay time)"),
    ):
        text, formula, prose = release(phrase, article=article)
        assert text == "Frequency error {v0} (" + article + phrase
        assert "".join(char.get_text() for char in formula) == "ϵ"
        assert "".join(char.get_text() for char in prose) == "(" + article


def test_variables_functions_protected_regions_and_scripts_are_not_articles():
    for phrase, options in (
        ("+b)", {}),
        (" relative to b)", {}),
        (" relative offset in frequency", {}),
        (" relative offset in frequency)", {"font": "CMMI10"}),
        (" relative offset in frequency)", {"font": "Times-Italic"}),
        (" relative offset in frequency)", {"gap": 0}),
        (" relative offset in frequency)", {"category": 0}),
        (" relative offset in frequency)", {"shift": 3}),
    ):
        text, formula, prose = release(phrase, **options)
        assert text == "Frequency error {v0}" + phrase
        assert "".join(char.get_text() for char in formula) == "ϵ(a"
        assert prose == []
