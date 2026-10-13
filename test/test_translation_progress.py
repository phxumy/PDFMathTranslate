from dataclasses import FrozenInstanceError

import pytest

from pdf2zh.translation_progress import PagePreview, TranslationProgressTracker


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_eta_waits_for_real_samples_and_excludes_cold_start():
    clock = Clock()
    tracker = TranslationProgressTracker(10, clock=clock)
    assert tracker.emit("preparing").eta_high_seconds is None
    clock.now = 400
    assert tracker.page_completed(1).eta_high_seconds is None
    for number in (2, 3):
        clock.now += 10
        assert tracker.page_completed(number).eta_high_seconds is None
    clock.now += 10
    event = tracker.page_completed(4)
    assert event.completed_pages == 4
    assert event.elapsed_seconds == 430
    assert event.eta_low_seconds == pytest.approx(42)
    assert event.eta_high_seconds == pytest.approx(78)
    assert tracker.emit("analyzing", 5).completed_pages == 4


def test_cached_prefix_and_stalled_current_page_do_not_claim_instant_finish():
    clock = Clock()
    tracker = TranslationProgressTracker(20, clock=clock)
    for page_number in range(1, 6):
        clock.now += 0.05
        assert tracker.page_completed(page_number).eta_low_seconds is None
    for page_number in range(6, 14):
        clock.now += 10
        tracker.page_completed(page_number)
    assert tracker.emit("translating", 14).eta_high_seconds is not None
    clock.now += 60
    assert tracker.emit("translating", 14).eta_high_seconds is None


def test_final_file_assembly_is_explicit_and_events_are_immutable():
    tracker = TranslationProgressTracker(1)
    page = tracker.page_completed(8)
    assert page.eta_high_seconds is None
    assert tracker.emit("finalizing").phase == "finalizing"
    assert tracker.emit("complete").eta_high_seconds == 0
    with pytest.raises(ValueError, match="twice"):
        tracker.page_completed(8)
    with pytest.raises(FrozenInstanceError):
        page.completed_pages = 5
    snapshot = PagePreview(8, 1, 1, b"png")
    with pytest.raises(FrozenInstanceError):
        snapshot.png_bytes = b"changed"
