from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pdf2zh import high_level


def test_translate_creates_missing_output_directory(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"source-pdf")
    output = tmp_path / "nested" / "results"

    with patch.object(
        high_level,
        "translate_stream",
        return_value=(b"mono-pdf", b"dual-pdf"),
    ):
        results = high_level.translate(
            [str(source)],
            output=str(output),
        )

    mono = output / "paper-mono.pdf"
    dual = output / "paper-dual.pdf"
    assert results == [(str(mono), str(dual))]
    assert mono.read_bytes() == b"mono-pdf"
    assert dual.read_bytes() == b"dual-pdf"
