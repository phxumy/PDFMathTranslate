"""Immutable UI events and conservative estimates from finished PDF pages.

Parsing a page is not completion: its translation, continuation handling and
typesetting must all finish before ``page_completed`` is called. Wall-clock
samples therefore include actual API concurrency, retries and cache effects.
"""

from collections import deque
from dataclasses import dataclass
from statistics import mean, pstdev
from time import monotonic
from typing import Callable


@dataclass(frozen=True)
class TranslationProgress:
    phase: str
    completed_pages: int
    total_pages: int
    current_page: int | None
    elapsed_seconds: float
    eta_low_seconds: float | None = None
    eta_high_seconds: float | None = None


@dataclass(frozen=True)
class PagePreview:
    """A completed page snapshot; page_number is one-based in the source PDF."""

    page_number: int
    completed_pages: int
    total_pages: int
    png_bytes: bytes


class TranslationProgressTracker:
    """Track real completions without extrapolating the first cold-start page.

    Three subsequent samples are required before publishing a range. A moving
    window adapts to cached/uncached sections and observed parallel throughput;
    worker counts and advertised service tiers are deliberately not multipliers.
    Estimates cover remaining page work, not font subsetting/final file writing.
    """

    def __init__(
        self,
        total_pages: int,
        callback: Callable[[TranslationProgress], None] | None = None,
        *,
        clock: Callable[[], float] = monotonic,
    ):
        self.total_pages = total_pages
        self.completed_pages = 0
        self.callback = callback
        self.clock = clock
        self.started_at = clock()
        self.last_completion = self.started_at
        self.samples: deque[float] = deque(maxlen=8)
        self.completed_page_numbers: set[int] = set()

    def emit(self, phase: str, current_page: int | None = None):
        now = self.clock()
        low = high = None
        remaining = self.total_pages - self.completed_pages
        if phase == "complete":
            low = high = 0.0
        elif remaining > 0 and len(self.samples) >= 3:
            average = mean(self.samples)
            spread = max(average * 0.30, pstdev(self.samples) * 1.5)
            # A cache-only prefix is not evidence that remote work is instant.
            # Likewise, an unusually slow current page invalidates old samples.
            in_flight = now - self.last_completion
            if average >= 1.0 and in_flight <= max(self.samples) * 2.0:
                low = max(0.0, (average - spread) * remaining - in_flight)
                high = max(0.0, (average + spread) * remaining - in_flight)
        event = TranslationProgress(
            phase=phase,
            completed_pages=self.completed_pages,
            total_pages=self.total_pages,
            current_page=current_page,
            elapsed_seconds=max(0.0, now - self.started_at),
            eta_low_seconds=low,
            eta_high_seconds=high,
        )
        if self.callback:
            self.callback(event)
        return event

    def page_completed(self, page_number: int):
        if page_number in self.completed_page_numbers:
            raise ValueError(f"page {page_number} completed twice")
        if self.completed_pages >= self.total_pages:
            raise ValueError("more completed pages than selected pages")
        now = self.clock()
        if self.completed_pages:
            self.samples.append(max(0.0, now - self.last_completion))
        self.last_completion = now
        self.completed_page_numbers.add(page_number)
        self.completed_pages += 1
        return self.emit("translating", page_number)
