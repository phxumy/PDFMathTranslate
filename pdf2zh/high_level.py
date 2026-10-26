"""Functions that can be used for the most common use-cases for pdf2zh.six"""

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
from asyncio import CancelledError
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, BinaryIO, Dict, List, Optional

import numpy as np
import requests
import tqdm
from babeldoc.assets.assets import get_font_and_metadata
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfexceptions import PDFValueError
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pymupdf import Document, Font, Matrix

from pdf2zh.config import ConfigManager
from pdf2zh.converter import TranslateConverter
from pdf2zh.doclayout import OnnxModel
from pdf2zh.font_cmap import prepare_pdf_text_font
from pdf2zh.link_annotations import (
    restore_imported_named_links,
    restore_imported_uri_links,
    suppress_reflowed_link_borders,
)
from pdf2zh.pdfinterp import PDFPageInterpreterEx
from pdf2zh.scanned_pdf import prepare_scan_background
from pdf2zh.source_spelling import (
    coalesce_source_uri_annotations,
    collect_source_spelling,
)
from pdf2zh.translation_progress import PagePreview, TranslationProgressTracker

NOTO_NAME = "noto"
LATIN_NAME = "pdf2zh_latin"

logger = logging.getLogger(__name__)

_CAPTION_LAYOUT_CLASSES = frozenset(
    {"figure_caption", "table_caption", "table_footnote"}
)
_PRESERVED_LAYOUT_CLASSES = frozenset({"figure", "table"})
_FORMULA_LAYOUT_CLASSES = frozenset({"isolate_formula", "formula_caption"})
_NON_TEXT_LAYOUT_CLASSES = (
    frozenset({"abandon"}) | _PRESERVED_LAYOUT_CLASSES | _FORMULA_LAYOUT_CLASSES
)


@dataclass(frozen=True)
class _LayoutDetection:
    value: int
    class_name: str
    bounds: tuple[int, int, int, int]
    confidence: float


noto_list = [
    "am",  # Amharic
    "ar",  # Arabic
    "bn",  # Bengali
    "bg",  # Bulgarian
    "chr",  # Cherokee
    "el",  # Greek
    "gu",  # Gujarati
    "iw",  # Hebrew
    "hi",  # Hindi
    "kn",  # Kannada
    "ml",  # Malayalam
    "mr",  # Marathi
    "ru",  # Russian
    "sr",  # Serbian
    "ta",  # Tamil
    "te",  # Telugu
    "th",  # Thai
    "ur",  # Urdu
    "uk",  # Ukrainian
]


def check_files(files: List[str]) -> List[str]:
    files = [
        f for f in files if not f.startswith("http://")
    ]  # exclude online files, http
    files = [
        f for f in files if not f.startswith("https://")
    ]  # exclude online files, https
    missing_files = [file for file in files if not os.path.exists(file)]
    return missing_files


def _paint_layout_region(
    layout_box: np.ndarray,
    confidence_map: np.ndarray,
    bounds: tuple[int, int, int, int],
    value: float,
    confidence: float,
    *,
    force: bool = False,
) -> None:
    """Paint one detected region while retaining stronger overlapping labels."""
    x0, y0, x1, y1 = bounds
    box_region = layout_box[y0:y1, x0:x1]
    confidence_region = confidence_map[y0:y1, x0:x1]
    if not box_region.size:
        return
    if force:
        box_region[...] = value
        confidence_region[...] = confidence
        return
    stronger = confidence > confidence_region
    box_region[stronger] = value
    confidence_region[stronger] = confidence


def _box_iou(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    left_x0, left_y0, left_x1, left_y1 = left
    right_x0, right_y0, right_x1, right_y1 = right
    intersection_width = max(0, min(left_x1, right_x1) - max(left_x0, right_x0))
    intersection_height = max(0, min(left_y1, right_y1) - max(left_y0, right_y0))
    intersection = intersection_width * intersection_height
    left_area = max(0, left_x1 - left_x0) * max(0, left_y1 - left_y0)
    right_area = max(0, right_x1 - right_x0) * max(0, right_y1 - right_y0)
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _abandon_matches_stronger_text_region(
    bounds: tuple[int, int, int, int],
    confidence: float,
    text_regions: list[tuple[tuple[int, int, int, int], float]],
    minimum_iou: float = 0.9,
) -> bool:
    """Return true only for a near-duplicate, stronger text detection."""
    return any(
        text_confidence > confidence and _box_iou(bounds, text_bounds) >= minimum_iou
        for text_bounds, text_confidence in text_regions
    )


def _layout_detection_bounds(
    detection: Any,
    height: int,
    width: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = detection.xyxy.squeeze()
    return (
        int(np.clip(int(x0 - 1), 0, width - 1)),
        int(np.clip(int(height - y1 - 1), 0, height - 1)),
        int(np.clip(int(x1 + 1), 0, width - 1)),
        int(np.clip(int(height - y0 + 1), 0, height - 1)),
    )


def _layout_detections(
    page_layout: Any,
    height: int,
    width: int,
    source_page: Any = None,
) -> list[_LayoutDetection]:
    detections = [
        _LayoutDetection(
            value=index + 2,
            class_name=page_layout.names[int(detection.cls)],
            bounds=_layout_detection_bounds(detection, height, width),
            confidence=float(detection.conf.squeeze()),
        )
        for index, detection in enumerate(page_layout.boxes)
    ]
    if source_page is None:
        return detections
    detections = _recover_publisher_contact_lines(
        detections, source_page, height, width
    )
    detections = _recover_caption_line_bounds(detections, source_page, height, width)
    detections = _recover_abandoned_prose(detections, source_page, height, width)
    detections = _recover_table_note_lines(detections, source_page, height, width)
    return _recover_publisher_cover_lines(detections, source_page, height, width)


def _recover_publisher_contact_lines(detections, page, height, width):
    """Keep a publisher's contact lead-in separate from the preceding author list."""
    sx, sy = width / page.rect.width, height / page.rect.height
    recovered = list(detections)
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line["spans"]).strip()
            if line.get("dir", (1, 0)) != (1, 0) or not re.fullmatch(
                r"Complete contact information is available at:\s*", text
            ):
                continue
            left, top, right, bottom = line["bbox"]
            bounds = (
                max(0, int(left * sx) - 1),
                max(0, int(height - bottom * sy) - 1),
                min(width - 1, int(right * sx) + 1),
                min(height - 1, int(height - top * sy) + 1),
            )
            recovered.append(
                _LayoutDetection(
                    max((x.value for x in recovered), default=1) + 1,
                    "publisher_text",
                    bounds,
                    1.0,
                )
            )
    return recovered


def _recover_caption_line_bounds(detections, page, height, width):
    """Extend clipped caption detections to complete, source-confirmed lines.

    A detector rectangle can end inside a word. Classifying its edge glyphs as
    unrelated text then sends fragments such as ``Ta`` / ``ble 4 ... t`` to the
    translator. Require an explicit numbered caption in the source block and
    substantial overlap with the detected caption; never expand to nearby body
    text, another column, or labels inside the figure/table.
    """
    sx, sy = width / page.rect.width, height / page.rect.height
    blocks = []
    for block in page.get_text("dict")["blocks"]:
        lines = block.get("lines", [])
        if not lines:
            continue
        first = "".join(s.get("text", "") for s in lines[0]["spans"]).strip()
        label = re.match(r"^(Table|Figure|Fig\.|Scheme)\s+[A-Z]?\d+[.:]?\s", first)
        if label:
            blocks.append((label[1], lines))
    recovered = []
    for detection in detections:
        if detection.class_name not in {"figure_caption", "table_caption"}:
            recovered.append(detection)
            continue
        x0, y0, x1, y1 = detection.bounds
        bounds = detection.bounds
        kind = detection.class_name
        for label, lines in blocks:
            for line in lines:
                if line.get("dir", (1, 0)) != (1, 0):
                    continue
                left, top, right, bottom = line["bbox"]
                left, right = left * sx, right * sx
                low, high = height - bottom * sy, height - top * sy
                if not (
                    min(x1, right) - max(x0, left) >= 0.5 * (right - left)
                    and min(y1, high) - max(y0, low) >= 0.5 * min(y1 - y0, high - low)
                    and max(x0 - left, right - x1) <= width * 0.25
                ):
                    continue
                bounds = (
                    max(0, min(bounds[0], int(left) - 1)),
                    max(0, min(bounds[1], int(low) - 1)),
                    min(width - 1, max(bounds[2], int(right) + 1)),
                    min(height - 1, max(bounds[3], int(high) + 1)),
                )
                kind = "table_caption" if label == "Table" else "figure_caption"
        recovered.append(
            _LayoutDetection(detection.value, kind, bounds, detection.confidence)
        )
    return recovered


def _recover_publisher_cover_lines(detections, page, height, width):
    """Recover publisher citation lines and linked recommendation titles.

    Only a first-page publisher cover with an explicit recommendations heading
    qualifies. Source hyperlinks identify the title lines; author names and
    download notices outside those rectangles remain untouched.
    """
    if getattr(page, "number", None) != 0:
        return detections
    headings = page.search_for("You may also like")
    if not headings:
        return detections
    links = [link["from"] for link in page.get_links() if link.get("from")]
    recovered = list(detections)
    for block in page.get_text("dict")["blocks"]:
        selected = []
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line["spans"]).strip()
            left, top, right, bottom = line["bbox"]
            is_citation = text.startswith("To cite this article:")
            recommended = (
                len(re.findall(r"[A-Za-z]+", text)) >= 1
                and any(
                    left >= head.x0
                    and top >= head.y1
                    and top - head.y1 < page.rect.height * 0.25
                    for head in headings
                )
                and any(
                    min(right, link.x1) - max(left, link.x0) >= 0.8 * (right - left)
                    and link.y0 <= (top + bottom) / 2 <= link.y1
                    for link in links
                )
            )
            if is_citation or recommended:
                selected.append(line["bbox"])
        if selected:
            sx, sy = width / page.rect.width, height / page.rect.height
            bounds = (
                max(0, int(min(b[0] for b in selected) * sx) - 1),
                max(0, int(height - max(b[3] for b in selected) * sy) - 1),
                min(width - 1, int(max(b[2] for b in selected) * sx) + 1),
                min(height - 1, int(height - min(b[1] for b in selected) * sy) + 1),
            )
            recovered.append(
                _LayoutDetection(
                    max((d.value for d in recovered), default=1) + 1,
                    "publisher_text",
                    bounds,
                    1.0,
                )
            )
            # Text following the linked title within the same source block is
            # the publisher's author list. Preserve its original glyphs, even
            # if PDF extraction omitted accents or initials punctuation.
            for line in block.get("lines", []):
                left, top, right, bottom = line["bbox"]
                if line["bbox"] in selected or not any(
                    left >= head.x0 for head in headings
                ):
                    continue
                if top < min(b[1] for b in selected):
                    continue
                bounds = (
                    max(0, int(left * sx) - 1),
                    max(0, int(height - bottom * sy) - 1),
                    min(width - 1, int(right * sx) + 1),
                    min(height - 1, int(height - top * sy) + 1),
                )
                recovered.append(
                    _LayoutDetection(
                        max(d.value for d in recovered) + 1,
                        "publisher_identity",
                        bounds,
                        1.0,
                    )
                )
    return recovered


def _recover_table_note_lines(detections, source_page, height: int, width: int):
    """Recover source-confirmed statistical footnotes inside a figure container.

    A table and the figure below it can share one detector box. Require a
    detected table caption, a genuinely raised smaller footnote marker and a
    complete statistical note; ordinary table cells and plot labels stay intact.
    """
    captions = [item for item in detections if item.class_name == "table_caption"]
    if not captions:
        return detections
    recovered = list(detections)
    scale_x = width / source_page.rect.width
    scale_y = height / source_page.rect.height
    for block in source_page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [span for span in line["spans"] if span.get("text", "").strip()]
            if len(spans) < 2 or line.get("dir", (1, 0)) != (1, 0):
                continue
            marker, body = spans[0], spans[1:]
            text = "".join(span["text"] for span in body).strip()
            if not (
                re.fullmatch(r"[a-z*†‡]", marker["text"].strip())
                and marker["size"] <= 0.85 * body[0]["size"]
                and marker["origin"][1] <= body[0]["origin"][1] - body[0]["size"] * 0.15
                and re.match(
                    r"(?:Data|Values|Results)\s+(?:represent|are|were)\b", text
                )
                and re.search(
                    r"\b(?:mean|median|replicates?|experiments?|deviation|error)\b",
                    text,
                    re.I,
                )
                and len(re.findall(r"[A-Za-z]+", text)) >= 6
            ):
                continue
            left, top, right, bottom = line["bbox"]
            bounds = (
                max(0, int(left * scale_x) - 1),
                max(0, int(height - bottom * scale_y) - 1),
                min(width - 1, int(right * scale_x) + 1),
                min(height - 1, int(height - top * scale_y) + 1),
            )
            x0, y0, x1, y1 = bounds
            if not any(
                0 <= caption.bounds[1] - y1 <= height * 0.16
                and min(x1, caption.bounds[2]) - max(x0, caption.bounds[0])
                >= (x1 - x0) * 0.8
                for caption in captions
            ):
                continue
            if any(
                item.class_name == "table_footnote"
                and _box_iou(bounds, item.bounds) > 0.5
                for item in recovered
            ):
                continue
            recovered.append(
                _LayoutDetection(
                    max(item.value for item in recovered) + 1,
                    "table_footnote",
                    bounds,
                    1.0,
                )
            )
    return recovered


def _recover_abandoned_prose(
    detections: list[_LayoutDetection],
    source_page: Any,
    height: int,
    width: int,
) -> list[_LayoutDetection]:
    """Recover narrowly identifiable prose misclassified as page furniture.

    The layout model can label a supplementary-section heading or the short
    continuation before a displayed equation as ``abandon``.  Confidence alone
    cannot distinguish those from real running heads.  Require visible source
    words below the running-head margin and, for an equation lead-in, a nearby
    displayed formula in the same column.  Figure/table labels remain protected.
    """
    words = source_page.get_text("words")
    scale_x = width / source_page.rect.width
    scale_y = height / source_page.rect.height
    recovered = []
    for detection in detections:
        x0, y0, x1, y1 = detection.bounds
        if detection.class_name != "abandon" or y1 > height * 0.94:
            recovered.append(detection)
            continue
        selected = [
            word
            for word in words
            if x0 <= (word[0] + word[2]) * scale_x / 2 <= x1
            and y0 <= height - (word[1] + word[3]) * scale_y / 2 <= y1
        ]
        text = " ".join(word[4] for word in selected).strip()
        section_heading = re.fullmatch(
            r"(?:Supplementary|Supplemental|Supporting)\s+"
            r"(?:Information|Materials?|Figures?|Tables?|Methods|Notes|"
            r"Results|Discussion|References)(?:\s+[A-Z]?\d+)?\s*[:.]?",
            text,
            re.IGNORECASE,
        )
        equation_lead_in = (
            3 <= len(selected) <= 18
            and re.search(
                r"\b(?:is|are|can\s+be|may\s+be)\s+"
                r"(?:given|defined|written|expressed|obtained)\s+"
                r"(?:by|as|in\s+the\s+form)\s*[:.]?$",
                text,
                re.IGNORECASE,
            )
            and any(
                other.class_name == "isolate_formula"
                and 0 <= y0 - other.bounds[3] <= height * 0.045
                and min(x1, other.bounds[2]) > max(x0, other.bounds[0])
                for other in detections
            )
        )
        overlaps_figure = any(
            other.class_name in _PRESERVED_LAYOUT_CLASSES
            and min(x1, other.bounds[2]) > max(x0, other.bounds[0])
            and min(y1, other.bounds[3]) > max(y0, other.bounds[1])
            for other in detections
        )
        if (section_heading or equation_lead_in) and not overlaps_figure:
            recovered.append(
                _LayoutDetection(
                    detection.value,
                    "title" if section_heading else "plain text",
                    detection.bounds,
                    detection.confidence,
                )
            )
        else:
            recovered.append(detection)
    return recovered


def _build_layout_mask(
    page_layout: Any,
    height: int,
    width: int,
    source_page: Any = None,
) -> tuple[np.ndarray, dict[int, str]]:
    """Build a mask without letting figure/table boxes erase exact captions."""
    layout_box = np.ones((height, width))
    confidence_map = np.full(layout_box.shape, -np.inf, dtype=np.float32)
    detections = _layout_detections(page_layout, height, width, source_page)
    region_types = {detection.value: detection.class_name for detection in detections}
    text_regions: list[tuple[tuple[int, int, int, int], float]] = []
    semantic_text_regions: list[tuple[tuple[int, int, int, int], float]] = []

    for detection in detections:
        if detection.class_name in _NON_TEXT_LAYOUT_CLASSES:
            continue
        text_regions.append((detection.bounds, detection.confidence))
        if detection.class_name == "title" or detection.class_name in (
            _CAPTION_LAYOUT_CLASSES
        ):
            semantic_text_regions.append((detection.bounds, detection.confidence))
        _paint_layout_region(
            layout_box,
            confidence_map,
            detection.bounds,
            detection.value,
            detection.confidence,
        )

    for detection in detections:
        if detection.class_name not in {"abandon"} | _PRESERVED_LAYOUT_CLASSES:
            continue
        defer_abandon = (
            detection.class_name == "abandon"
            and _abandon_matches_stronger_text_region(
                detection.bounds,
                detection.confidence,
                text_regions,
            )
        )
        _paint_layout_region(
            layout_box,
            confidence_map,
            detection.bounds,
            0,
            detection.confidence,
            force=detection.class_name != "abandon" or not defer_abandon,
        )

    # Captions are semantically translatable even when their detector box overlaps
    # the surrounding figure/table.  Repaint only the exact detected caption box;
    # ordinary text inside figures remains protected.
    for detection in detections:
        if detection.class_name in _CAPTION_LAYOUT_CLASSES:
            _paint_layout_region(
                layout_box,
                confidence_map,
                detection.bounds,
                detection.value,
                detection.confidence,
                force=True,
            )

    # Formula boxes take final precedence inside a caption so their original PDF
    # glyphs and equation numbers are never sent through translation.  A weak
    # formula box that is a near-exact duplicate of a stronger semantic title or
    # caption is a detector conflict, however, rather than an inline formula;
    # retain the stronger semantic label in that narrow case.
    for detection in detections:
        if detection.class_name in _FORMULA_LAYOUT_CLASSES:
            defer_formula = _abandon_matches_stronger_text_region(
                detection.bounds,
                detection.confidence,
                semantic_text_regions,
            )
            _paint_layout_region(
                layout_box,
                confidence_map,
                detection.bounds,
                0,
                detection.confidence,
                force=not defer_formula,
            )

    for detection in detections:
        if detection.class_name == "publisher_identity":
            _paint_layout_region(
                layout_box,
                confidence_map,
                detection.bounds,
                0,
                detection.confidence,
                force=True,
            )
    # Source font ascenders/descenders overlap adjacent rows. Verified linked
    # title glyphs take priority over the neighbouring author-row rectangle.
    for detection in detections:
        if detection.class_name == "publisher_text":
            _paint_layout_region(
                layout_box,
                confidence_map,
                detection.bounds,
                detection.value,
                detection.confidence,
                force=True,
            )
    return layout_box, region_types


def _chatgpt_document_task_id(
    stream: bytes,
    *,
    pages=None,
    lang_in="",
    lang_out="",
    prompt=None,
    vfont="",
    vchar="",
    model=None,
    compatible=False,
) -> str:
    """Identify original document/settings, independently of temporary PDF paths."""
    layout_model = None
    if model is not None:
        layout_model = {"type": f"{type(model).__module__}.{type(model).__qualname__}"}
        model_path = getattr(model, "model_path", None)
        if model_path and Path(model_path).is_file():
            digest = hashlib.sha256()
            with Path(model_path).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            layout_model["sha256"] = digest.hexdigest()
    identity = {
        "version": 1,
        "source_sha256": hashlib.sha256(stream).hexdigest(),
        "pages": sorted(set(pages)) if pages else None,
        "lang_in": lang_in.lower(),
        "lang_out": lang_out.lower(),
        "prompt": getattr(prompt, "template", prompt),
        "vfont": vfont or "",
        "vchar": vchar or "",
        "layout_model": layout_model,
        "compatible": bool(compatible),
    }
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _collect_source_uri_links(document):
    """Read URI actions directly, including relative URIs reported as Launch."""
    return {
        page.number: coalesce_source_uri_annotations(
            [
                (
                    document.xref_get_key(link["xref"], "A/URI")[1],
                    (
                        link["from"].x0,
                        page.rect.height - link["from"].y1,
                        link["from"].x1,
                        page.rect.height - link["from"].y0,
                    ),
                )
                for link in page.get_links()
                if link.get("xref")
                and link.get("from")
                and document.xref_get_key(link["xref"], "A/S") == ("name", "/URI")
                and document.xref_get_key(link["xref"], "A/URI")[0] == "string"
            ]
        )
        for page in document
    }


def _collect_closed_box_strokes(page):
    """Find small outlines made of four strokes, such as a proof-end box.

    TeX emits these four sides separately. Extracting just their horizontal
    sides as fraction rules detaches them from the vertical sides on reflow.
    Require a complete outline with matching stroke style and touching painted
    endpoints; two fraction bars alone are never sufficient evidence.
    """
    horizontal = []
    vertical = []
    for drawing in page.get_drawings():
        items = drawing["items"]
        width = drawing.get("width") or 0
        if (
            drawing.get("type") != "s"
            or len(items) != 1
            or items[0][0] != "l"
            or not 0 < width <= 1.5
            or drawing.get("dashes") != "[] 0"
        ):
            continue
        _, start, end = items[0]
        # PyMuPDF drawings use unrotated page coordinates; layout and pdfminer
        # use the displayed page with a bottom-left origin.
        start = start * page.rotation_matrix
        end = end * page.rotation_matrix
        points = sorted(
            (
                (float(start.x), float(page.rect.height - start.y)),
                (float(end.x), float(page.rect.height - end.y)),
            )
        )
        (x0, y0), (x1, y1) = points
        style = (
            width,
            drawing.get("color"),
            drawing.get("stroke_opacity"),
            drawing.get("lineCap"),
        )
        if abs(y0 - y1) <= 0.005 and 2 <= x1 - x0 <= 14:
            horizontal.append((x0, y0, x1, y1, style))
        elif abs(x0 - x1) <= 0.005 and 2 <= abs(y1 - y0) <= 14:
            vertical.append((x0, min(y0, y1), x1, max(y0, y1), style))

    protected = set()
    for index, top in enumerate(horizontal):
        x0, y0, x1, _, style = top
        for bottom in horizontal[index + 1 :]:
            a, y1, b, _, other_style = bottom
            height = abs(y1 - y0)
            if (
                style != other_style
                or abs(a - x0) > 0.01
                or abs(b - x1) > 0.01
                or not 2 <= height <= 14
                or not 0.4 <= (x1 - x0) / height <= 2.5
            ):
                continue
            low, high = sorted((y0, y1))
            tolerance = style[0] / 2 + 0.02

            def has_side(x):
                return any(
                    vstyle == style
                    and abs(vx - x) <= tolerance
                    and abs(vlow - low) <= tolerance
                    and abs(vhigh - high) <= tolerance
                    for vx, vlow, _, vhigh, vstyle in vertical
                )

            if has_side(x0) and has_side(x1):
                protected.add(tuple(top[:4]))
                protected.add(tuple(bottom[:4]))
    return tuple(sorted(protected))


class _PagePreviewRenderer:
    """Patch a private PDF copy and render one page without full-document saves.

    The source used for layout detection must never see partially translated
    shared XObjects. This private document retains the original xref numbering
    and is created once; only new/changed streams are applied for each snapshot.
    """

    def __init__(self, document: Document, max_dimension: int):
        self.max_dimension = max(256, min(int(max_dimension), 2400))
        self.document = Document(stream=document.tobytes())
        self.applied: dict[int, str] = {}

    def render(self, page_number: int, page_xref: int, patches, resolve_markers):
        for xref, operations in patches.items():
            if self.applied.get(xref) == operations:
                continue
            while self.document.xref_length() <= xref:
                new_xref = self.document.get_new_xref()
                self.document.update_object(new_xref, "<<>>")
            self.document.update_stream(xref, resolve_markers(operations).encode())
            self.applied[xref] = operations
        page = self.document[page_number - 1]
        page.set_contents(page_xref)
        suppress_reflowed_link_borders(page)
        page = self.document.reload_page(page)
        scale = self.max_dimension / max(page.rect.width, page.rect.height)
        return page.get_pixmap(matrix=Matrix(scale, scale), alpha=False).tobytes("png")

    def close(self):
        self.document.close()


def _check_translation_cancelled(cancellation_event):
    if cancellation_event is not None and cancellation_event.is_set():
        raise CancelledError("task cancelled")


def _selected_page_numbers(document: Document, pages):
    selected = set(pages) if pages else None
    return [
        page_number
        for page_number in range(document.page_count)
        if selected is None or page_number in selected
    ]


def translate_patch(
    inf: BinaryIO,
    pages: Optional[list[int]] = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    noto_name: str = "",
    noto: Font = None,
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    ignore_cache: bool = False,
    latin: Font = None,
    latin_name: str = LATIN_NAME,
    chatgpt_task_id: str = None,
    progress_callback: object = None,
    preview_callback: object = None,
    preview_max_dimension: int = 1400,
    _progress_tracker: TranslationProgressTracker = None,
    **kwarg: Any,
) -> None:
    rsrcmgr = PDFResourceManager()
    layout = {}
    layout_region_types: dict[int, dict[int, str]] = {}
    device = TranslateConverter(
        rsrcmgr,
        vfont,
        vchar,
        thread,
        layout,
        lang_in,
        lang_out,
        service,
        noto_name,
        noto,
        envs,
        prompt,
        ignore_cache,
        chatgpt_task_id=chatgpt_task_id,
    )
    device.layout_region_types = layout_region_types
    device.latin = latin
    device.latin_name = latin_name
    device.scan_backgrounds = {}
    device.source_spelling = collect_source_spelling(doc_zh)
    device.source_uri_links = _collect_source_uri_links(doc_zh)
    device.source_closed_box_strokes = {}
    device.cancellation_event = cancellation_event
    device.translator.cancellation_event = cancellation_event

    assert device is not None
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch)
    selected_pages = set(_selected_page_numbers(doc_zh, pages))
    total_pages = len(selected_pages)
    tracker = _progress_tracker or TranslationProgressTracker(
        total_pages, progress_callback
    )
    device.page_translation_callback = lambda number: tracker.emit(
        "translating", number
    )
    preview_renderer = None
    page_numbers_by_xref: dict[int, int] = {}

    try:
        if preview_callback:
            preview_renderer = _PagePreviewRenderer(doc_zh, preview_max_dimension)
        parser = PDFParser(inf)
        doc = PDFDocument(parser)
        with tqdm.tqdm(total=total_pages) as progress:

            def publish_completed_pages():
                for page_xref in device.take_completed_page_xrefs():
                    _check_translation_cancelled(cancellation_event)
                    page_number = page_numbers_by_xref[page_xref]
                    tracker.page_completed(page_number)
                    progress.update()
                    if callback:
                        callback(progress)
                    if preview_renderer is not None:
                        png = preview_renderer.render(
                            page_number,
                            page_xref,
                            obj_patch,
                            device.resolve_uri_underline_markers,
                        )
                        _check_translation_cancelled(cancellation_event)
                        preview_callback(
                            PagePreview(
                                page_number=page_number,
                                completed_pages=tracker.completed_pages,
                                total_pages=total_pages,
                                png_bytes=png,
                            )
                        )

            for pageno, page in enumerate(PDFPage.create_pages(doc)):
                _check_translation_cancelled(cancellation_event)
                if pageno not in selected_pages:
                    continue
                tracker.emit("analyzing", pageno + 1)
                page.pageno = pageno
                pix = doc_zh[page.pageno].get_pixmap()
                image = np.frombuffer(pix.samples, np.uint8).reshape(
                    pix.height, pix.width, 3
                )[:, :, ::-1]
                scan_background = prepare_scan_background(doc_zh[page.pageno], image)
                if scan_background is not None:
                    device.scan_backgrounds[page.pageno] = scan_background
                page_layout = model.predict(image, imgsz=int(pix.height / 32) * 32)[0]
                # kdtree 是不可能 kdtree 的，不如直接渲染成图片，用空间换时间
                box, page_region_types = _build_layout_mask(
                    page_layout,
                    pix.height,
                    pix.width,
                    doc_zh[page.pageno],
                )
                if not hasattr(device, "layout_detections"):
                    device.layout_detections = {}
                device.layout_detections[page.pageno] = _layout_detections(
                    page_layout, pix.height, pix.width, doc_zh[page.pageno]
                )
                layout_region_types[page.pageno] = page_region_types
                layout[page.pageno] = box
                device.source_closed_box_strokes[page.pageno] = (
                    _collect_closed_box_strokes(doc_zh[page.pageno])
                )
                # 新建一个 xref 存放新指令流
                page.page_xref = doc_zh.get_new_xref()
                doc_zh.update_object(page.page_xref, "<<>>")
                doc_zh.update_stream(page.page_xref, b"")
                doc_zh[page.pageno].set_contents(page.page_xref)
                page_numbers_by_xref[page.page_xref] = pageno + 1
                _check_translation_cancelled(cancellation_event)
                tracker.emit("translating", pageno + 1)
                interpreter.process_page(page)
                publish_completed_pages()

            _check_translation_cancelled(cancellation_event)
            for page_xref, translated_ops in device.flush_deferred_pages().items():
                if page_xref not in obj_patch:
                    raise ValueError(
                        f"deferred translation refers to unknown page xref {page_xref}"
                    )
                obj_patch[page_xref] += translated_ops
            publish_completed_pages()
        for xref, operations in obj_patch.items():
            obj_patch[xref] = device.resolve_uri_underline_markers(operations)
        return obj_patch
    finally:
        if preview_renderer is not None:
            preview_renderer.close()
        device.close()


def translate_stream(
    stream: bytes,
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    ignore_cache: bool = False,
    chatgpt_task_id: str = None,
    progress_callback: object = None,
    preview_callback: object = None,
    preview_max_dimension: int = 1400,
    **kwarg: Any,
):
    _check_translation_cancelled(cancellation_event)
    doc_en = doc_zh = None
    try:
        doc_en = Document(stream=stream)
        _progress_tracker = TranslationProgressTracker(
            len(_selected_page_numbers(doc_en, pages)), progress_callback
        )
        _progress_tracker.emit("preparing")
        if service.split(":", 1)[0] == "chatgpt-browser" and chatgpt_task_id is None:
            # Compute before MuPDF rewrites bytes or injects fonts/object IDs.
            chatgpt_task_id = _chatgpt_document_task_id(
                stream,
                pages=pages,
                lang_in=lang_in,
                lang_out=lang_out,
                prompt=prompt,
                vfont=vfont,
                vchar=vchar,
                model=model,
                compatible=kwarg.get("compatible", False),
            )
        font_list = [("tiro", None, None)]

        font_path = prepare_pdf_text_font(download_remote_fonts(lang_out.lower()))
        noto_name = NOTO_NAME
        noto = Font(noto_name, font_path)
        font_list.append((noto_name, font_path, None))
        # CJK target fonts do not cover every author-name character (e.g. Ł/ł).
        # Embed the bundled Roman font as Unicode rather than writing glyph zero.
        latin_name = LATIN_NAME
        latin = Font("tiro")
        font_list.append((latin_name, None, latin.buffer))

        stream = io.BytesIO()
        doc_en.save(stream)
        doc_zh = Document(stream=stream)
        page_count = doc_zh.page_count
        # font_list = [("GoNotoKurrent-Regular.ttf", font_path), ("tiro", None)]
        font_id = {}
        for page in doc_zh:
            for font in font_list:
                font_id[font[0]] = page.insert_font(
                    font[0], font[1], fontbuffer=font[2]
                )
        xreflen = doc_zh.xref_length()
        for xref in range(1, xreflen):
            for label in ["Resources/", ""]:  # 可能是基于 xobj 的 res
                try:  # xref 读写可能出错
                    font_res = doc_zh.xref_get_key(xref, f"{label}Font")
                    target_key_prefix = f"{label}Font/"
                    if font_res[0] == "xref":
                        resource_xref_id = re.search("(\\d+) 0 R", font_res[1]).group(1)
                        xref = int(resource_xref_id)
                        font_res = ("dict", doc_zh.xref_object(xref))
                        target_key_prefix = ""

                    if font_res[0] == "dict":
                        for font in font_list:
                            target_key = f"{target_key_prefix}{font[0]}"
                            font_exist = doc_zh.xref_get_key(xref, target_key)
                            if font_exist[0] == "null":
                                doc_zh.xref_set_key(
                                    xref,
                                    target_key,
                                    f"{font_id[font[0]]} 0 R",
                                )
                except Exception:
                    pass

        fp = io.BytesIO()

        doc_zh.save(fp)
        obj_patch: dict = translate_patch(fp, **locals())
        _check_translation_cancelled(cancellation_event)
        _progress_tracker.emit("finalizing")

        for obj_id, ops_new in obj_patch.items():
            # ops_old=doc_en.xref_stream(obj_id)
            # print(obj_id)
            # print(ops_old)
            # print(ops_new.encode())
            doc_zh.update_stream(obj_id, ops_new.encode())

        for page in doc_zh:
            if any(xref in obj_patch for xref in page.get_contents()):
                suppress_reflowed_link_borders(page)

        doc_en.insert_file(doc_zh)
        restore_imported_named_links(doc_zh, doc_en, start_at=page_count)
        restore_imported_uri_links(doc_zh, doc_en, start_at=page_count)
        for id in range(page_count):
            doc_en.move_page(page_count + id, id * 2 + 1)
        if not skip_subset_fonts:
            # Rebuild MuPDF's document/font state after replacing content streams
            # and grafting pages. Subsetting those live edited objects can leave
            # stale native font references and crash during tracing or final save.
            # Snapshot both documents before either one's fonts are mutated.
            serialized_zh, serialized_en = doc_zh.write(), doc_en.write()
            doc_zh.close()
            doc_en.close()
            doc_zh = Document(stream=serialized_zh)
            doc_en = Document(stream=serialized_en)
            doc_zh.subset_fonts(fallback=True)
            doc_en.subset_fonts(fallback=True)
        _check_translation_cancelled(cancellation_event)
        results = (
            doc_zh.write(deflate=True, garbage=3, use_objstms=1),
            doc_en.write(deflate=True, garbage=3, use_objstms=1),
        )
        _progress_tracker.emit("complete")
        return results
    finally:
        for document in (doc_zh, doc_en):
            if document is not None and not document.is_closed:
                document.close()


def convert_to_pdfa(input_path, output_path):
    """
    Convert PDF to PDF/A format

    Args:
        input_path: Path to source PDF file
        output_path: Path to save PDF/A file
    """
    from pikepdf import Dictionary, Name, Pdf

    # Open the PDF file
    pdf = Pdf.open(input_path)

    # Add PDF/A conformance metadata
    metadata = {
        "pdfa_part": "2",
        "pdfa_conformance": "B",
        "title": pdf.docinfo.get("/Title", ""),
        "author": pdf.docinfo.get("/Author", ""),
        "creator": "PDF Math Translate",
    }

    with pdf.open_metadata() as meta:
        meta.load_from_docinfo(pdf.docinfo)
        meta["pdfaid:part"] = metadata["pdfa_part"]
        meta["pdfaid:conformance"] = metadata["pdfa_conformance"]

    # Create OutputIntent dictionary
    output_intent = Dictionary(
        {
            "/Type": Name("/OutputIntent"),
            "/S": Name("/GTS_PDFA1"),
            "/OutputConditionIdentifier": "sRGB IEC61966-2.1",
            "/RegistryName": "http://www.color.org",
            "/Info": "sRGB IEC61966-2.1",
        }
    )

    # Add output intent to PDF root
    if "/OutputIntents" not in pdf.Root:
        pdf.Root.OutputIntents = [output_intent]
    else:
        pdf.Root.OutputIntents.append(output_intent)

    # Save as PDF/A
    pdf.save(output_path, linearize=True)
    pdf.close()


def translate(
    files: list[str],
    output: str = "",
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    compatible: bool = False,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    ignore_cache: bool = False,
    progress_callback: object = None,
    preview_callback: object = None,
    preview_max_dimension: int = 1400,
    **kwarg: Any,
):
    if not files:
        raise PDFValueError("No files to process.")

    missing_files = check_files(files)

    if missing_files:
        print("The following files do not exist:", file=sys.stderr)
        for file in missing_files:
            print(f"  {file}", file=sys.stderr)
        raise PDFValueError("Some files do not exist.")

    output_dir = Path(output or ".")
    output_dir.mkdir(parents=True, exist_ok=True)
    result_files = []

    for file in files:
        if type(file) is str and (
            file.startswith("http://") or file.startswith("https://")
        ):
            print("Online files detected, downloading...")
            try:
                r = requests.get(file, allow_redirects=True)
                if r.status_code == 200:
                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False
                    ) as tmp_file:
                        print(f"Writing the file: {file}...")
                        tmp_file.write(r.content)
                        file = tmp_file.name
                else:
                    r.raise_for_status()
            except Exception as e:
                raise PDFValueError(
                    f"Errors occur in downloading the PDF file. Please check the link(s).\nError:\n{e}"
                )
        filename = os.path.splitext(os.path.basename(file))[0]

        # PDF/A conversion can rewrite metadata. Preserve the original identity
        # across reruns even when that optional conversion is enabled.
        chatgpt_task_id = None
        if compatible and service.split(":", 1)[0] == "chatgpt-browser":
            chatgpt_task_id = _chatgpt_document_task_id(
                Path(file).read_bytes(),
                pages=pages,
                lang_in=lang_in,
                lang_out=lang_out,
                prompt=prompt,
                vfont=vfont,
                vchar=vchar,
                model=model,
                compatible=True,
            )

        # If the commandline has specified converting to PDF/A format
        # --compatible / -cp
        if compatible:
            with tempfile.NamedTemporaryFile(
                suffix="-pdfa.pdf", delete=False
            ) as tmp_pdfa:
                print(f"Converting {file} to PDF/A format...")
                convert_to_pdfa(file, tmp_pdfa.name)
                doc_raw = open(tmp_pdfa.name, "rb")
                os.unlink(tmp_pdfa.name)
        else:
            doc_raw = open(file, "rb")
        s_raw = doc_raw.read()
        doc_raw.close()

        temp_dir = Path(tempfile.gettempdir())
        file_path = Path(file)
        try:
            if file_path.exists() and file_path.resolve().is_relative_to(
                temp_dir.resolve()
            ):
                file_path.unlink(missing_ok=True)
                logger.debug(f"Cleaned temp file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to clean temp file {file_path}", exc_info=True)

        s_mono, s_dual = translate_stream(
            s_raw,
            **locals(),
        )
        file_mono = output_dir / f"{filename}-mono.pdf"
        file_dual = output_dir / f"{filename}-dual.pdf"
        doc_mono = open(file_mono, "wb")
        doc_dual = open(file_dual, "wb")
        doc_mono.write(s_mono)
        doc_dual.write(s_dual)
        doc_mono.close()
        doc_dual.close()
        result_files.append((str(file_mono), str(file_dual)))

    return result_files


def download_remote_fonts(lang: str):
    lang = lang.lower()
    LANG_NAME_MAP = {
        **{la: "GoNotoKurrent-Regular.ttf" for la in noto_list},
        **{
            la: f"SourceHanSerif{region}-Regular.ttf"
            for region, langs in {
                "CN": ["zh-cn", "zh-hans", "zh"],
                "TW": ["zh-tw", "zh-hant"],
                "JP": ["ja"],
                "KR": ["ko"],
            }.items()
            for la in langs
        },
    }
    font_name = LANG_NAME_MAP.get(lang, "GoNotoKurrent-Regular.ttf")

    # docker
    font_path = ConfigManager.get("NOTO_FONT_PATH", Path("/app", font_name).as_posix())
    if not Path(font_path).exists():
        font_path, _ = get_font_and_metadata(font_name)
        font_path = font_path.as_posix()

    logger.info(f"use font: {font_path}")

    return font_path
