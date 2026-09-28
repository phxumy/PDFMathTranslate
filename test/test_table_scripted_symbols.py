from test_cm_prose_and_region_recovery import detection, recover, run


def cell(base_font="TimesNewRoman-Italic", script_y=498.578, script_size=6.65, gap=0):
    base = run("Q", x=20, y=500, font=base_font, size=9.5)
    script = run("high", x=base[-1].x1 + gap, y=script_y, font="Times-Roman", size=script_size)
    return base + script


def test_textual_math_subscript_stays_protected_without_changing_glyphs():
    for font in ("TimesNewRoman-Italic", "CMMI10"):
        chars = cell(base_font=font)
        geometry = [(ch.get_text(), ch.x0, ch.y0, ch.x1, ch.y1, ch.size) for ch in chars]
        mapping, _ = recover(chars, [detection("table", (0, 450, 599, 550))])
        assert not mapping
        assert [(ch.get_text(), ch.x0, ch.y0, ch.x1, ch.y1, ch.size) for ch in chars] == geometry


def test_ordinary_high_header_and_nonscript_prose_still_translate():
    variants = [
        run("high", y=500, font="Times-Roman", size=9.5),
        cell(script_y=500, script_size=9.5),
        cell(base_font="Times-Roman"),
        cell(script_y=501.425),  # Smaller but vertically centered; no script evidence.
        cell(gap=5),  # Separate word, not a touching script.
    ]
    for chars in variants:
        mapping, kinds = recover(chars, [detection("table", (0, 450, 599, 550))])
        assert mapping
        assert {kinds[value] for value in mapping.values()} == {"table_cell"}


def test_neighboring_header_releases_while_scripted_symbol_is_preserved():
    symbol = cell()
    header = run("Quality factor", x=150, y=500, font="Times-Roman", size=9.5)
    mapping, kinds = recover(symbol + header, [detection("table", (0, 450, 599, 550))])
    assert set(mapping) == {id(ch) for ch in header}
    assert {kinds[value] for value in mapping.values()} == {"table_cell"}
