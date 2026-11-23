"""Stage verified Next and OCR assets for the Windows desktop distribution."""

import argparse
import hashlib
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile
import urllib.request
from zipfile import ZipFile

NEXT_ARCHIVE = "pdf2zh-v2.10.2-BabelDOC-v0.7.1-with-assets-win64.zip"
NEXT_URL = (
    "https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/releases/download/v2.10.2/"
    + NEXT_ARCHIVE
)
NEXT_SHA256 = "7ac1811b0f82078a86bcce88e185efff75fc55a3752c7f9c015186e5214b3c0b"
TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/"
TESSDATA = {
    "eng.traineddata": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "chi_sim.traineddata": "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
    "chi_tra.traineddata": "529c5b5797d64b126065cd55f2bb4c7fd7b15790798091b1ff259941a829330b",
    "LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


def digest(path):
    with path.open("rb") as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
        return h.hexdigest()


def verified(url, path, expected):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and digest(path) == expected:
        return path
    with NamedTemporaryFile(dir=path.parent, delete=False) as f:
        partial = Path(f.name)
    try:
        with (
            urllib.request.urlopen(url, timeout=90) as response,
            partial.open("wb") as f,
        ):
            shutil.copyfileobj(response, f, 1024 * 1024)
        if digest(partial) != expected:
            raise ValueError(f"SHA-256 mismatch for {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def stage(release_root, downloads):
    archive = verified(NEXT_URL, downloads / NEXT_ARCHIVE, NEXT_SHA256)
    target = (release_root / "engines" / "precise").resolve()
    with ZipFile(archive) as package:
        for item in package.infolist():
            parts = Path(item.filename).parts
            if item.is_dir() or len(parts) < 2 or parts[0] != "pdf2zh":
                continue
            if parts[1] not in ("runtime", "site-packages") and not (
                len(parts) == 2
                and parts[1].startswith("offline_assets_")
                and parts[1].endswith(".zip")
            ):
                continue
            if "__pycache__" in parts or item.filename.endswith((".pyc", ".pyo")):
                continue
            destination = target.joinpath(*parts[1:]).resolve()
            if not destination.is_relative_to(target):
                raise ValueError("Unexpected path in Next archive")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    ocr = release_root / "tessdata"
    ocr.mkdir(parents=True, exist_ok=True)
    for name, expected in TESSDATA.items():
        path = verified(TESSDATA_URL + name, downloads / "tessdata" / name, expected)
        shutil.copyfile(path, ocr / name)
    for required in (
        "runtime/python.exe",
        "site-packages/pdf2zh_next/main.py",
        "site-packages/babeldoc/format/pdf/high_level.py",
    ):
        if not (target / required).is_file():
            raise RuntimeError(f"Missing Next runtime component {required}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--downloads", required=True, type=Path)
    args = parser.parse_args()
    stage(args.release_root, args.downloads)
