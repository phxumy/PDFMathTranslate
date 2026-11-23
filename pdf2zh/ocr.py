"""Paragraph reconstruction and bounded typesetting for OCR pages."""

import asyncio
import logging
import os
import re
from pathlib import Path

import requests
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from html import escape
from statistics import median

from pymupdf import Archive, Document, Rect, TEXTFLAGS_DICT, TEXT_PRESERVE_IMAGES
from tenacity import retry, wait_fixed, stop_after_attempt, retry_if_exception

from pdf2zh.text_spacing import add_cjk_latin_spacing

logger = logging.getLogger(__name__)


def ocr_paragraphs(page, detection):
    """Group OCR words by layout region, then join lines in reading order."""
    regions = [
        (Rect(*box.xyxy), detection.names[int(box.cls)], box.conf)
        for box in detection.boxes
    ]
    line_sizes = {
        (block["number"], i): median(span["size"] for span in line["spans"])
        for block in page.get_text(
            "dict", flags=TEXTFLAGS_DICT & ~TEXT_PRESERVE_IMAGES
        )["blocks"]
        for i, line in enumerate(block.get("lines", []))
        if line["spans"]
    }
    groups = defaultdict(list)
    protected = {"figure", "table", "isolate_formula", "formula_caption"}
    for word in page.get_text("words"):
        rect = Rect(word[:4]) * page.rotation_matrix
        if rect.is_empty or not word[4].strip():
            continue
        center = (rect.tl + rect.br) / 2
        if any(center in box and name in protected for box, name, _ in regions):
            continue
        matches = [
            ((Rect(rect) & box).get_area(), confidence, i)
            for i, (box, _, confidence) in enumerate(regions)
        ]
        overlap, _, region = max(matches, default=(0, 0, -1))
        if overlap and regions[region][1] == "abandon":
            continue
        key = region if overlap else ("unassigned", word[5])
        groups[key].append((rect, word[4], word[5:7]))

    paragraphs = []
    for words in groups.values():
        line_height = median(w[0].height for w in words)
        ordered = []
        for word in sorted(words, key=lambda w: (w[0].y0 + w[0].y1, w[0].x0)):
            center = (word[0].y0 + word[0].y1) / 2
            if (
                not ordered
                or abs(center - median((w[0].y0 + w[0].y1) / 2 for w in ordered[-1]))
                > line_height * 0.5
            ):
                ordered.append([])
            ordered[-1].append(word)
        left = min(w[0].x0 for w in words)
        chunks = []
        for line in ordered:
            line.sort(key=lambda w: w[0].x0)
            bounds = Rect(line[0][0])
            for word in line[1:]:
                bounds |= word[0]
            if chunks:
                previous = chunks[-1][-1][0]
                if (
                    bounds.y0 - previous.y1 > line_height * 0.8
                    or bounds.x0 > left + line_height * 0.8
                    or bounds.x0 >= previous.x1
                    or bounds.x1 <= previous.x0
                ):
                    chunks.append([])
            else:
                chunks.append([])
            chunks[-1].append((bounds, " ".join(w[1] for w in line), line))
        for chunk in chunks:
            bounds = Rect(chunk[0][0])
            text = ""
            source = []
            sizes = []
            for rect, line, words in chunk:
                bounds |= rect
                # ponytail: a lowercase continuation treats an end-of-line hyphen as soft.
                if text.endswith("-") and line[:1].islower():
                    text = text[:-1] + line
                else:
                    text += (" " if text else "") + line
                source.extend(w[0] for w in words)
                sizes.extend(
                    line_sizes.get(tuple(w[2]), w[0].height * 0.85) for w in words
                )
            size = median(sizes)
            bounds.y1 = min(page.rect.y1, max(bounds.y1, bounds.y0 + size * 1.25))
            paragraphs.append((bounds, text, size, source))
    # OCR glyph boxes can overlap the next paragraph by a few points.
    for i, (above, _, _, _) in enumerate(paragraphs):
        for below, _, _, _ in paragraphs[i + 1 :]:
            a, b = sorted((above, below), key=lambda r: r.y0)
            overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
            if a.y0 < b.y0 < a.y1 and overlap > min(a.width, b.width) / 2:
                boundary = (a.y1 + b.y0) / 2
                a.y1, b.y0 = boundary - 0.5, boundary + 0.5
    return paragraphs


def translate_ocr_page(
    page, detection, translator, font, thread, cancellation_event=None
):
    paragraphs = ocr_paragraphs(page, detection)
    if not paragraphs:
        return

    from pdf2zh.converter import _retryable_translation_error

    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_retryable_translation_error),
        reraise=True,
    )
    def translate(paragraph):
        if cancellation_event and cancellation_event.is_set():
            raise asyncio.CancelledError("task cancelled")
        return translator.translate(paragraph[1])

    translations = [""] * len(paragraphs)
    with ThreadPoolExecutor(max_workers=max(1, thread)) as executor:
        futures = {
            executor.submit(copy_context().run, translate, p): i
            for i, p in enumerate(paragraphs)
        }
        try:
            for future in as_completed(futures):
                translations[futures[future]] = future.result()
        finally:
            for future in futures:
                future.cancel()
    if not any(text.strip() for text in translations):
        return
    archive = Archive((font.buffer, "ocr.ttf"))
    with Document() as overlay:
        target = overlay.new_page(width=page.rect.width, height=page.rect.height)
        for (_, _, _, words), text in zip(paragraphs, translations):
            if text.strip():
                for rect in words:
                    target.draw_rect(
                        rect + (-0.5, -0.5, 0.5, 0.5), color=None, fill=(1, 1, 1)
                    )
        for (bounds, _, size, _), text in zip(paragraphs, translations):
            if not text.strip():
                continue
            centered = any(
                detection.names[int(box.cls)] == "title"
                and (Rect(*box.xyxy) & bounds).get_area() > bounds.get_area() / 2
                for box in detection.boxes
            )
            align = "center" if centered else "justify"
            css = (
                "@font-face {font-family:ocr;src:url(ocr.ttf);}"
                f"body {{font-family:ocr;font-size:{size}pt;line-height:1.2;margin:0;}}"
                f"p {{margin:0;overflow-wrap:break-word;text-align:{align};}}"
            )
            spare, _ = target.insert_htmlbox(
                bounds,
                f"<p>{escape(add_cjk_latin_spacing(text))}</p>",
                css=css,
                archive=archive,
                scale_low=0,
            )
            if spare < 0:
                raise RuntimeError("OCR translation could not fit its paragraph box")
        # Remove the invisible OCR layer, keeping source pixels and annotations.
        links = page.get_links()
        page.add_redact_annot(page.rect * page.derotation_matrix, fill=False)
        page.apply_redactions(images=0, graphics=0, text=0)
        for link in links:
            page.insert_link(link)
        page.show_pdf_page(
            page.rect * page.derotation_matrix, overlay, 0, rotate=page.rotation
        )


def _ocr_tessdata(language):
    """Use explicit local data, or download requested languages once on demand."""
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ValueError("Invalid OCR language; use Tesseract codes such as eng+deu")
    if tessdata := os.environ.get("TESSDATA_PREFIX"):
        return tessdata
    bundled = Path(__file__).resolve().parents[2] / "tessdata"
    if all((bundled / f"{code}.traineddata").is_file() for code in language.split("+")):
        return str(bundled)
    try:
        import pooch
    except ImportError as exc:
        raise RuntimeError(
            "Automatic OCR downloads require pip install 'pdf2zh[ocr]'. "
            "Alternatively, set TESSDATA_PREFIX to existing language data."
        ) from exc
    cache = Path.home() / ".cache/pdf2zh/tessdata/4.1.0"
    for code in dict.fromkeys(language.split("+")):
        filename = f"{code}.traineddata"
        try:
            pooch.retrieve(
                url=f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{filename}",
                known_hash=None,
                fname=filename,
                path=cache,
                downloader=pooch.HTTPDownloader(timeout=60),
            )
        except (requests.RequestException, OSError) as exc:
            raise RuntimeError(
                f"Could not download OCR data for '{code}'. Check the language code "
                "and network connection, or set TESSDATA_PREFIX to local data."
            ) from exc
    return str(cache)


def _ocr_pages(doc, pages, lang_in, cancellation_event):
    """Add a text layer only to selected image-only pages in the working copy."""
    language = os.environ.get("PDF2ZH_OCR_LANGUAGE") or {
        "en": "eng",
        "zh": "chi_sim",
        "zh-cn": "chi_sim",
        "zh-hans": "chi_sim",
        "zh-hant": "chi_tra",
        "en-us": "eng",
        "en-gb": "eng",
        "zh-tw": "chi_tra",
        "ja": "jpn",
        "ko": "kor",
        "de": "deu",
        "fr": "fra",
        "es": "spa",
        "it": "ita",
        "pt": "por",
        "ru": "rus",
    }.get(lang_in.lower(), lang_in or "eng")
    ocr_pages = set()
    tessdata = None
    for pageno in range(len(doc)):
        if cancellation_event and cancellation_event.is_set():
            raise asyncio.CancelledError("task cancelled")
        if pages and pageno not in pages:
            continue
        page = doc[pageno]
        # Preserve native text and existing OCR layers.
        if page.get_text().strip() or not page.get_image_info():
            continue
        logger.info("OCR page %d (%s)", pageno + 1, language)
        if tessdata is None:
            tessdata = _ocr_tessdata(language)
        try:
            data = page.get_pixmap(dpi=300, alpha=False, annots=False).pdfocr_tobytes(
                language=language,
                tessdata=tessdata,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"OCR failed on page {pageno + 1} using '{language}' data in {tessdata}. "
                "Check the language data and any TESSDATA_PREFIX override. "
                "Use PDF2ZH_OCR_LANGUAGE to override the OCR language."
            ) from exc
        with Document(stream=data) as recognized:
            if not recognized[0].get_text().strip():
                logger.warning("OCR found no text on page %d", pageno + 1)
                continue
            contents = doc.get_new_xref()
            doc.update_object(contents, "<<>>")
            doc.update_stream(contents, b"")
            page.set_contents(contents)
            page.show_pdf_page(
                page.rect * page.derotation_matrix,
                recognized,
                0,
                rotate=page.rotation,
            )
        ocr_pages.add(pageno)
    return ocr_pages
