from test_cm_prose_and_region_recovery import detection, recover, run


def test_reference_recovery_keeps_year_line_and_excludes_author_email():
    email = run("*author@example.org", x=30, y=150)
    first = run("[1] Y. Aharonov and D. Bohm, Significance of", y=135)
    second = run("electromagnetic potentials, Phys. Rev. 115, 485", x=32, y=124)
    year = run("(1959).", x=32, y=113)
    next_ref = run("[2] M. V. Berry, Quantal phase factors,", y=102)
    last = run("Proc. R. Soc. A 392, 45 (1984).", x=32, y=91)
    mapping, kinds = recover(
        email + first + second + year + next_ref + last,
        [detection("abandon", (0, 70, 599, 170))],
    )
    assert all(id(ch) in mapping for ch in first + second + year + next_ref + last)
    assert len({mapping[id(ch)] for ch in first + second + year}) == 1
    assert mapping[id(first[0])] != mapping[id(next_ref[0])]
    assert all(id(ch) not in mapping for ch in email)
    assert kinds[mapping[id(first[0])]] == "plain text"


def test_numbered_body_citation_and_page_furniture_are_not_recovered():
    chars = run("[1] We describe the quantum experiment here.", y=100)
    footer = run("Copyright 2020. All rights reserved.", y=87)
    mapping, _ = recover(chars + footer, [detection("abandon", (0, 70, 599, 120))])
    assert not mapping


def test_reference_recovery_keeps_parallel_columns_separate():
    left = run("[1] A. Smith, Quantum effects,", x=20, y=130)
    left_end = run("Phys. Rev. 100, 123 (2020).", x=32, y=119)
    right = run("[2] B. Jones, Quantum gates,", x=310, y=130)
    right_end = run("Phys. Rev. 101, 456 (2021).", x=322, y=119)
    mapping, _ = recover(
        left + right + left_end + right_end,
        [detection("abandon", (0, 90, 599, 150))],
    )
    assert mapping[id(left[0])] == mapping[id(left_end[0])]
    assert mapping[id(right[0])] == mapping[id(right_end[0])]
    assert mapping[id(left[0])] != mapping[id(right[0])]


def test_reference_with_trailing_doi_does_not_absorb_publication_footer():
    for text in (
        "Copyright 2026. All rights reserved.",
        "Downloaded from the publisher site.",
        "Unauthorized redistribution is prohibited.",
        "All rights reserved.",
    ):
        reference = run(
            "[1] A. Smith, Quantum effects, Phys. Rev. 100, 123 (2020), "
            "doi:10.1234/abcd.",
            x=20,
            y=130,
        )
        footer = run(text, x=20, y=119)
        mapping, kinds = recover(
            reference + footer, [detection("abandon", (0, 90, 599, 150))]
        )
        assert all(id(ch) in mapping for ch in reference), text
        assert all(id(ch) not in mapping for ch in footer), text
        assert kinds[mapping[id(reference[0])]] == "plain text"
