from pathlib import Path

import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from pdf2zh.font_cmap import prepare_pdf_text_font


def _rectangle_glyph():
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((550, 0))
    pen.lineTo((550, 700))
    pen.lineTo((50, 700))
    pen.closePath()
    return pen.glyph()


def _build_duplicate_cmap_font(path: Path) -> None:
    glyph_names = [".notdef", "shared", "compatOnly"]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_names)
    builder.setupCharacterMap(
        {
            0x91CF: "shared",
            0xF97E: "shared",
            0xFA0C: "compatOnly",
        }
    )
    builder.setupGlyf({name: _rectangle_glyph() for name in glyph_names})
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyph_names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "PDF2ZH cmap test",
            "styleName": "Regular",
            "uniqueFontIdentifier": "PDF2ZH cmap test Regular",
            "fullName": "PDF2ZH cmap test Regular",
            "psName": "PDF2ZH-cmap-test-Regular",
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
    )
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


def _unicode_cmap(path: Path) -> dict[int, str]:
    font = TTFont(path)
    try:
        result: dict[int, str] = {}
        for table in font["cmap"].tables:
            if table.isUnicode():
                result.update(table.cmap)
        return result
    finally:
        font.close()


def _pdf_text_for(font_path: Path, text: str, output: Path) -> str:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        text,
        fontfile=str(font_path),
        fontname="target",
        fontsize=14,
    )
    document.subset_fonts(fallback=True)
    document.save(output)
    document.close()
    rendered = pymupdf.open(output)
    try:
        return rendered[0].get_text().strip()
    finally:
        rendered.close()


def test_duplicate_compatibility_mapping_is_removed_but_unique_one_is_kept(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttf"
    _build_duplicate_cmap_font(source)

    prepared = Path(prepare_pdf_text_font(source, cache_dir=tmp_path / "cache"))
    cmap = _unicode_cmap(prepared)

    assert prepared != source
    assert cmap[0x91CF] == "shared"
    assert 0xF97E not in cmap
    assert cmap[0xFA0C] == "compatOnly"


def test_prepared_font_produces_canonical_pdf_text_after_subsetting(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ttf"
    _build_duplicate_cmap_font(source)
    prepared = Path(prepare_pdf_text_font(source, cache_dir=tmp_path / "cache"))

    assert _pdf_text_for(source, "量", tmp_path / "source.pdf") == "\uf97e"
    assert _pdf_text_for(prepared, "量", tmp_path / "prepared.pdf") == "量"


def test_prepared_font_is_content_addressed_and_reused(tmp_path: Path) -> None:
    source = tmp_path / "source.ttf"
    _build_duplicate_cmap_font(source)
    cache = tmp_path / "cache"

    first = prepare_pdf_text_font(source, cache_dir=cache)
    second = prepare_pdf_text_font(source, cache_dir=cache)

    assert first == second
    assert len(list(cache.glob("*.ttf"))) == 1
