"""Prepare translation fonts with an unambiguous Unicode character map.

Some CJK fonts map both a unified ideograph (for example U+91CF) and its
compatibility equivalent (U+F97E) to the same glyph.  MuPDF builds a PDF
``ToUnicode`` map by reversing that cmap and may choose the compatibility
codepoint.  The page then looks correct, but copied or extracted text contains
compatibility ideographs.

Only duplicate compatibility mappings are removed here.  Glyph outlines and
all canonical Unicode mappings stay untouched, and source-PDF fonts used for
formula preservation never pass through this module.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

_CACHE_FORMAT_VERSION = "v1"


def _is_cjk_compatibility_ideograph(codepoint: int) -> bool:
    return 0xF900 <= codepoint <= 0xFAFF or 0x2F800 <= codepoint <= 0x2FA1F


def _font_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as font_file:
        for block in iter(lambda: font_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _default_cache_dir() -> Path:
    return Path.home() / ".cache" / "pdf2zh" / "fonts"


def prepare_pdf_text_font(
    font_path: str | os.PathLike[str],
    *,
    cache_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Return a font whose reverse cmap prefers unified CJK codepoints.

    A content-addressed copy is produced only when the source font contains a
    compatibility mapping that aliases a glyph already reachable through a
    non-compatibility Unicode codepoint.  Unique compatibility-only glyphs are
    retained so this operation cannot make an otherwise reachable glyph
    disappear.
    """

    source = Path(font_path).resolve()
    font = TTFont(source, recalcBBoxes=False, recalcTimestamp=False)
    try:
        unicode_tables = [table for table in font["cmap"].tables if table.isUnicode()]
        canonical_glyphs = {
            glyph_name
            for table in unicode_tables
            for codepoint, glyph_name in table.cmap.items()
            if isinstance(glyph_name, str)
            and not _is_cjk_compatibility_ideograph(codepoint)
        }
        duplicate_codepoints = {
            codepoint
            for table in unicode_tables
            for codepoint, glyph_name in table.cmap.items()
            if _is_cjk_compatibility_ideograph(codepoint)
            and isinstance(glyph_name, str)
            and glyph_name in canonical_glyphs
        }
        if not duplicate_codepoints:
            return str(source)

        digest = _font_digest(source)
        output_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / (
            f"{source.stem}.pdf2zh-canonical-cjk-{_CACHE_FORMAT_VERSION}-"
            f"{digest[:16]}{source.suffix}"
        )
        if output.is_file():
            return str(output)

        for table in unicode_tables:
            for codepoint in duplicate_codepoints:
                table.cmap.pop(codepoint, None)

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output_dir,
                prefix=f".{output.stem}-",
                suffix=source.suffix,
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
            font.save(temporary_name)
            if not output.exists():
                os.replace(temporary_name, output)
                temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

        return str(output)
    finally:
        font.close()
