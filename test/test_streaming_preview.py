"""Exercise real PDF patch/render paths with deterministic translated operators."""

import asyncio
from types import SimpleNamespace

import pymupdf
import pytest

from pdf2zh import converter, high_level


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    finished = []
    seen_source_images = []
    devices = []
    documents = []
    font = tmp_path / "test.ttf"
    font.write_bytes(pymupdf.Font("tiro").buffer)
    monkeypatch.setattr(high_level, "download_remote_fonts", lambda _: str(font))
    monkeypatch.setattr(high_level, "prepare_pdf_text_font", lambda path: path)

    class Translator:
        name = "codex"
        prompttext = None

        def __init__(self, *args, **kwargs):
            pass

        def set_concurrency(self, thread):
            pass

        def translate_continuation_fragments(self):
            pass

    class Converter(converter.TranslateConverter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            devices.append(self)
            self.was_closed = False

        def _prepare_continuation_overrides(self, pending, following):
            pass

        def receive_layout(self, ltpage, *, preview_only=False, page_xref=-1, **kwargs):
            if preview_only:
                return SimpleNamespace(
                    page_id=ltpage.pageid,
                    page_xref=page_xref,
                    ltpage=ltpage,
                    fontid=dict(self.fontid),
                    fontmap=dict(self.fontmap),
                    overrides=[],
                )
            finished.append(ltpage.pageid + 1)
            return f"1 0 0 rg {20 + ltpage.pageid * 10} 20 80 80 re f\n"

        def close(self):
            self.was_closed = True
            super().close()

    class Model:
        def predict(self, image, **kwargs):
            seen_source_images.append(image.tobytes())
            return [SimpleNamespace(boxes=[], names={})]

    monkeypatch.setattr(converter, "CodexTranslator", Translator)
    monkeypatch.setattr(high_level, "TranslateConverter", Converter)

    def open_document(*args, **kwargs):
        document = pymupdf.Document(*args, **kwargs)
        documents.append(document)
        return document

    monkeypatch.setattr(high_level, "Document", open_document)
    with pymupdf.open() as document:
        for index in range(3):
            page = document.new_page(width=300, height=300)
            page.insert_text((20, 180), f"Original page {index + 1}")
        original = document.tobytes()

    def run(**kwargs):
        options = {
            "service": "codex",
            "lang_out": "en",
            "model": Model(),
            "skip_subset_fonts": True,
            "preview_max_dimension": 300,
        }
        options.update(kwargs)
        return high_level.translate_stream(original, **options)

    return SimpleNamespace(
        run=run,
        finished=finished,
        devices=devices,
        documents=documents,
        original=original,
        seen_source_images=seen_source_images,
    )


def test_previews_are_completed_pages_and_match_the_final_pdf(pipeline):
    snapshots = []
    progress = []
    legacy_counts = []

    def receive_preview(event):
        snapshots.append(event)
        # A preview arrives before translation of subsequent pages, including
        # when the converter uses a one-page lookahead for sentence joins.
        assert pipeline.finished == list(range(1, event.page_number + 1))
        assert event.completed_pages == len(snapshots)

    mono, dual = pipeline.run(
        preview_callback=receive_preview,
        progress_callback=progress.append,
        callback=lambda bar: legacy_counts.append(bar.n),
    )
    assert [item.page_number for item in snapshots] == [1, 2, 3]
    assert legacy_counts == [1, 2, 3]
    assert [event.phase for event in progress][-2:] == ["finalizing", "complete"]
    assert (
        next(
            event for event in progress if event.phase == "translating"
        ).completed_pages
        == 0
    )
    with pymupdf.open(stream=mono) as document:
        for event in snapshots:
            actual = pymupdf.Pixmap(event.png_bytes)
            expected = document[event.page_number - 1].get_pixmap(alpha=False)
            assert actual.samples == expected.samples
            assert actual.pixel(50, 250)[:3] == (255, 0, 0)
    with pymupdf.open(stream=dual) as document:
        assert document.page_count == 6
    with pymupdf.open(stream=pipeline.original) as original:
        for number, source_image in enumerate(pipeline.seen_source_images):
            # Preview patches may not alter later source/layout input.
            import numpy as np

            pixmap = original[number].get_pixmap()
            source = np.frombuffer(pixmap.samples, np.uint8).reshape(300, 300, 3)
            assert source[:, :, ::-1].tobytes() == source_image
    assert pipeline.devices[0].was_closed
    assert all(document.is_closed for document in pipeline.documents)


def test_selected_pages_keep_physical_numbers_and_count_unique_valid_pages(pipeline):
    snapshots = []
    progress = []
    pipeline.run(
        pages=[2, 0, 2, 20],
        preview_callback=snapshots.append,
        progress_callback=progress.append,
    )
    assert [item.page_number for item in snapshots] == [1, 3]
    assert all(item.total_pages == 2 for item in snapshots)
    assert progress[-1].completed_pages == progress[-1].total_pages == 2


def test_cancel_after_first_preview_never_publishes_untranslated_pages(pipeline):
    cancelled = asyncio.Event()
    snapshots = []
    progress = []

    def receive_preview(event):
        snapshots.append(event)
        cancelled.set()

    with pytest.raises(asyncio.CancelledError):
        pipeline.run(
            cancellation_event=cancelled,
            preview_callback=receive_preview,
            progress_callback=progress.append,
        )
    assert pipeline.finished == [1]
    assert [item.page_number for item in snapshots] == [1]
    assert "complete" not in [event.phase for event in progress]
    assert pipeline.devices[0].was_closed
    # Retain references to every native document: passing this assertion does
    # not depend on GC or on pytest discarding an exception's traceback.
    assert len(pipeline.documents) == 3
    assert all(document.is_closed for document in pipeline.documents)


@pytest.mark.parametrize(
    "failure_phase", ["preparing", "preview", "finalizing", "subset", "save"]
)
def test_native_documents_close_when_a_stage_fails(
    pipeline, monkeypatch, failure_phase
):
    progress = []

    def fail(*args, **kwargs):
        raise RuntimeError("injected stage failure")

    def on_progress(event):
        progress.append(event)
        if event.phase == failure_phase:
            fail()

    if failure_phase == "subset":
        monkeypatch.setattr(pymupdf.Document, "subset_fonts", fail)
    elif failure_phase == "save":
        original_write = pymupdf.Document.write

        def write(document, *args, **kwargs):
            if kwargs.get("deflate"):
                fail()
            return original_write(document, *args, **kwargs)

        monkeypatch.setattr(pymupdf.Document, "write", write)

    with pytest.raises(RuntimeError, match="injected stage failure"):
        pipeline.run(
            progress_callback=on_progress,
            preview_callback=fail if failure_phase == "preview" else lambda _: None,
            skip_subset_fonts=failure_phase != "subset",
        )
    assert pipeline.documents
    assert all(document.is_closed for document in pipeline.documents)
    assert not any(event.phase == "complete" for event in progress)


def test_converter_cancellation_is_not_swallowed_by_styled_fallback():
    device = converter.TranslateConverter.__new__(converter.TranslateConverter)
    device._flow_token_counter = 0

    def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    device.translator = SimpleNamespace(translate_styled_batch=cancel)
    with pytest.raises(asyncio.CancelledError):
        device._translate_styled_segments(["Sentence"])
