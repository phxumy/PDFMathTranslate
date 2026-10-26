from pdf2zh.converter import _recover_author_information_affiliations
from test_italic_translation import fake_run, paragraph


def test_corporate_affiliations_keep_author_names_and_separate_every_entry():
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    source = "Nikolay Sitnikov {v0} Florian Bartels {v1} Eric Talbot {v2}; Present Address: Example, London, EC2M 4AG, UK Hanchu Kong {v3}"
    props.break_offsets = []
    texts = [source]
    runs = [
        fake_run("− Nuvisan ICB GmbH, 13353 Berlin, Germany"),
        fake_run("− Nuvisan ICB GmbH, 13353 Berlin, Germany"),
        fake_run("− Pharmaron UK Ltd., Hoddesdon EN11 9FH, U.K."),
        fake_run("− Department of Chemistry, Beijing 100176, China"),
    ]
    _recover_author_information_affiliations(texts, runs, [0] * 4, [props])
    names = [p for p in props.affiliation_parts if p.role == "preserve"]
    assert [p.text.strip() for p in names] == [
        "Nikolay Sitnikov −",
        "Florian Bartels −",
        "Eric Talbot −",
        "Hanchu Kong −",
    ]
    assert [p.break_before for p in names] == [False, True, True, True]
    assert "".join(p.text for p in props.affiliation_parts) == texts[0]
    assert "Germany" in props.affiliation_parts[1].text


def test_country_punctuation_outside_italic_run_does_not_hide_next_author():
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    texts = ["Alice Smith {v0}. Ying Yin {v1}"]
    runs = [
        fake_run("− Pharmaron UK Ltd., Hoddesdon EN11 9FH, U.K"),
        fake_run("− Department of Chemistry, Beijing 100176, China"),
    ]
    _recover_author_information_affiliations(texts, runs, [0, 0], [props])
    assert [
        p.text.strip() for p in props.affiliation_parts if p.role == "preserve"
    ] == ["Alice Smith −", "Ying Yin −"]


def test_correspondence_email_cannot_merge_with_next_author_name():
    props = paragraph(region_kind="plain text")
    props.layout_class = 3
    texts = [
        "Alice Smith − Example Therapeutics, Boston 02210, United States; Email: alice@example.org Bob Jones − Example Therapeutics, Boston 02210, United States; Email: bob@example.org"
    ]
    _recover_author_information_affiliations(texts, [], [], [props])
    names = [p for p in props.affiliation_parts if p.role == "preserve"]
    assert names[1].text.strip() == "Bob Jones −"
    assert names[1].break_before
