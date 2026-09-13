"""Replace OCR-backed scan text while retaining the original page artwork."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pdfminer.layout import LTChar


@dataclass(frozen=True)
class ScanBackground:
    image_name: str
    image_matrix: tuple[float, float, float, float, float, float]
    width: float
    height: float
    preview: np.ndarray | None = field(default=None, repr=False, compare=False)
    blend_state: str | None = None

    def fragment_ops(
        self,
        bounds: tuple[float, float, float, float],
        target_x: float,
        target_y: float,
        scale: float,
    ) -> str:
        """Replay a clipped piece of the existing image, without resampling it."""
        x0, y0, x1, y1 = bounds
        matrix = " ".join(f"{value:f}" for value in self.image_matrix)
        blend = f"/{self.blend_state} gs " if self.blend_state else ""
        return (
            f"ET q {blend}{scale:f} 0 0 {scale:f} "
            f"{target_x - x0 * scale:f} {target_y - y0 * scale:f} cm "
            f"{x0:f} {y0:f} {x1 - x0:f} {y1 - y0:f} re W n "
            f"{matrix} cm /{self.image_name} Do Q BT "
        )


def detect_scan_background(page: Any, rgb_image: np.ndarray) -> ScanBackground | None:
    """Accept only white-paper, axis-aligned scans with a hidden OCR layer.

    A normal PDF with a photograph, visible text, a coloured background, or an
    image nested inside a Form is deliberately left on the existing path.
    """
    traces = page.get_texttrace()
    hidden = sum(len(trace["chars"]) for trace in traces if trace["type"] == 3)
    total = sum(len(trace["chars"]) for trace in traces)
    if hidden < 10 or hidden < 0.9 * total or page.rotation:
        return None
    samples = rgb_image[::4, ::4, :3].astype(np.int16)
    if (
        np.mean(samples.max(axis=2) - samples.min(axis=2) > 12) > 0.01
        or np.mean(samples.min(axis=2) >= 245) < 0.70
    ):
        return None
    direct_images = {
        image[0]: image[7] for image in page.get_images(full=True) if image[9] == 0
    }
    for image in page.get_image_info(xrefs=True):
        if image["xref"] not in direct_images:
            continue
        x0, y0, x1, y1 = image["bbox"]
        a, b, c, d, e, f = image["transform"]
        if (
            (x1 - x0) * (y1 - y0) < 0.85 * page.rect.width * page.rect.height
            or abs(b) > 1e-5
            or abs(c) > 1e-5
            or a <= 0
            or d <= 0
        ):
            continue
        return ScanBackground(
            direct_images[image["xref"]],
            (a, 0.0, 0.0, d, e, page.rect.height - f - d),
            page.rect.width,
            page.rect.height,
            rgb_image,
        )
    return None


def prepare_scan_background(page: Any, rgb_image: np.ndarray) -> ScanBackground | None:
    """Register fragment-only Multiply blending after scan detection succeeds."""
    background = detect_scan_background(page, rgb_image)
    if background is None:
        return None
    doc = page.parent
    resource_xref = page.xref
    prefix = "Resources/"
    kind, value = doc.xref_get_key(page.xref, "Resources")
    if kind == "xref":
        resource_xref = int(value.split()[0])
        prefix = ""
    kind, value = doc.xref_get_key(resource_xref, prefix + "ExtGState")
    if kind == "xref":
        resource_xref = int(value.split()[0])
        prefix = ""
    else:
        prefix += "ExtGState/"
    name = "PDF2ZHScanMultiply"
    suffix = 0
    while doc.xref_get_key(resource_xref, prefix + name)[0] != "null":
        suffix += 1
        name = f"PDF2ZHScanMultiply{suffix}"
    doc.xref_set_key(resource_xref, prefix + name, "<< /BM /Multiply >>")
    return ScanBackground(
        background.image_name,
        background.image_matrix,
        background.width,
        background.height,
        background.preview,
        name,
    )


def is_hidden_ocr(char: Any) -> bool:
    return getattr(char, "_pdf2zh_source_render_mode", 0) == 3


def recover_scan_gap_fragments(
    ltpage: Any, background: ScanBackground, layout: np.ndarray
) -> None:
    """Retain tiny superscripts omitted entirely from a scan's OCR layer.

    Only an ink island high in a short gap between two same-region OCR glyphs
    qualifies. Ordinary spaces, column gaps, and image regions are untouched.
    The island becomes an opaque inline formula, retaining its exact pixels.
    """
    if background.preview is None or getattr(
        ltpage, "_pdf2zh_scan_gaps_checked", False
    ):
        return
    ltpage._pdf2zh_scan_gaps_checked = True
    preview = background.preview
    scale_x = preview.shape[1] / background.width
    scale_y = preview.shape[0] / background.height
    previous = None
    output = []
    for child in ltpage:
        if (
            not isinstance(child, LTChar)
            or not is_hidden_ocr(child)
            or not child.get_text().strip()
        ):
            output.append(child)
            continue
        if previous is not None:
            em = max(float(previous.size), float(child.size))
            gap = float(child.x0) - float(previous.x1)
            px, py = int(previous.x0), int(previous.y0)
            cx, cy = int(child.x0), int(child.y0)
            same_region = (
                0 <= px < layout.shape[1]
                and 0 <= cx < layout.shape[1]
                and 0 <= py < layout.shape[0]
                and 0 <= cy < layout.shape[0]
                and layout[py, px] != 0
                and layout[py, px] == layout[cy, cx]
            )
            if (
                same_region
                and 1.5 * em < gap < 5 * em
                and abs(child.y0 - previous.y0) < 0.2 * em
            ):
                left = max(0, int((previous.x1 + 0.2 * em) * scale_x))
                right = min(preview.shape[1], int((child.x0 - 0.2 * em) * scale_x))
                top = max(0, int((background.height - child.y0 - 1.1 * em) * scale_y))
                bottom = min(
                    preview.shape[0],
                    int((background.height - child.y0 + 0.1 * em) * scale_y),
                )
                ys, xs = np.where(preview[top:bottom, left:right, :3].min(axis=2) < 170)
                if len(xs) >= 4:
                    x0, x1 = (left + xs.min()) / scale_x, (
                        left + xs.max() + 1
                    ) / scale_x
                    y0 = background.height - (top + ys.max() + 1) / scale_y
                    y1 = background.height - (top + ys.min()) / scale_y
                    if (
                        y0 >= child.y0 + 0.35 * em
                        and y1 - y0 <= 0.65 * em
                        and x1 - x0 >= 0.4 * em
                    ):
                        fragment = copy(previous)
                        fragment.set_bbox((x0, y0, x1, y1))
                        fragment.size = y1 - y0
                        fragment.adv = x1 - x0
                        fragment._text = "†"
                        fragment._pdf2zh_scan_fragment = True
                        output.append(fragment)
        output.append(child)
        previous = child
    ltpage._objs = output


def scan_text_bounds(
    chars: list[Any], background: ScanBackground
) -> tuple[float, float, float, float]:
    """Pad OCR boxes just enough to cover scan ink outside the font metrics."""
    em = max(float(char.size) for char in chars)
    pad = max(0.75, 0.18 * em)
    return (
        max(0.0, min(float(char.x0) for char in chars) - pad),
        max(0.0, min(float(char.y0) for char in chars) - pad),
        min(background.width, max(float(char.x1) for char in chars) + pad),
        min(background.height, max(float(char.y1) for char in chars) + pad),
    )


def scan_line_mask_ops(chars: list[Any], background: ScanBackground) -> str:
    """Mask compact OCR lines, not the whole paragraph or its surrounding art."""
    lines: list[list[Any]] = []
    for char in sorted(chars, key=lambda item: (-float(item.y0), float(item.x0))):
        if not is_hidden_ocr(char) or not char.get_text().strip():
            continue
        for line in lines:
            em = max(float(char.size), float(line[0].size))
            if abs(float(char.y0) - float(line[0].y0)) <= 0.45 * em:
                line.append(char)
                break
        else:
            lines.append([char])
    bounds = []
    for line in lines:
        run: list[Any] = []
        for char in sorted(line, key=lambda item: float(item.x0)):
            if run and float(char.x0) - max(float(item.x1) for item in run) > (
                1.5 * max(float(char.size), float(run[-1].size))
            ):
                bounds.append(scan_text_bounds(run, background))
                run = []
            run.append(char)
        if run:
            bounds.append(scan_text_bounds(run, background))
    # LTChar deliberately has no font descent in pdf2zh. Follow connected ink
    # below each compact line until paper resumes, without sweeping across the
    # whitespace separating it from a caption rule or the following line.
    if background.preview is not None:
        image = background.preview
        sx, sy = image.shape[1] / background.width, image.shape[0] / background.height
        em = max((float(char.size) for char in chars), default=0.0)
        expanded = []
        for x0, y0, x1, y1 in bounds:
            left, right = max(0, int(x0 * sx)), min(
                image.shape[1], int(np.ceil(x1 * sx))
            )
            top = max(0, int((background.height - y1) * sy))
            bottom = min(image.shape[0], int(np.ceil((background.height - y0) * sy)))
            limit = min(
                image.shape[0], int(np.ceil((background.height - y0 + 0.5 * em) * sy))
            )
            dark = (image[top:limit, left:right, :3].min(axis=2) < 170).any(axis=1)
            initial_ink = np.flatnonzero(dark[: bottom - top])
            if len(initial_ink):
                last = int(initial_ink[-1])
                gap = 0
                for row in range(last + 1, len(dark)):
                    if dark[row]:
                        last, gap = row, 0
                    else:
                        gap += 1
                        if gap == 2:
                            break
                y0 = min(y0, background.height - (top + last + 1.5) / sy)
            expanded.append((x0, max(0.0, y0), x1, y1))
        bounds = expanded
    rectangles = " ".join(
        f"{x0:f} {y0:f} {x1 - x0:f} {y1 - y0:f} re f" for x0, y0, x1, y1 in bounds
    )
    return f"q 1 g {rectangles} Q " if rectangles else ""


def horizontal_fit_scale(
    *, source_left: float, source_right: float, target_right: float
) -> float:
    """Keep an expanded single-line translation inside its source width."""
    width = source_right - source_left
    extent = target_right - source_left
    return min(1.0, width / extent) if width > 0 and extent > 0 else 1.0
