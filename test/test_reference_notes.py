from dataclasses import replace

from pdf2zh.reading_flow import FragmentSpan, detect_reference_note_continuations
from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    ROLE_PRESERVE,
    ROLE_REFERENCE,
    ROLE_TRANSLATE,
    SourceSegment,
)
from test_converter_continuation import (
    FakeContinuationTranslator,
    converter_with,
    draft,
    paragraph,
)
from test_reference_continuation import segment

PRIOR = "[64] A. Smith, Quantum control, Phys. Rev. 100, 123 (2020). "
NOTE = (
    "See Supplemental Material at https://example.org/supplement/10.1234/paper "
    "for a discussion of the experimental setup, quantum process"
)
CONTINUED = (
    "tomography, randomized benchmarking, and geometric gates, "
    "which includes Refs. [11,57,64,66–69]. "
)
NEXT = "[66] B. Jones, Quantum gates, Phys. Rev. 101, 456 (2021)."


def columns():
    return [
        segment(6, 0, PRIOR + "[65] " + NOTE, column=0, y0=282, y1=743),
        segment(6, 1, CONTINUED + NEXT, column=1, y0=282, y1=743),
    ]


def test_supplemental_note_uses_prose_translation_and_preserves_number():
    text = PRIOR + "[65] " + NOTE + " tomography. " + NEXT
    source = SourceSegment(0, text, 6, 52, 297, 282, 743, 9.5, "plain text")
    plan = DocumentTranslationPolicy().plan_segment(source)
    note_part = next(part for part in plan.parts if NOTE in part.text)
    assert note_part.role == ROLE_TRANSLATE
    assert any(
        part.role == ROLE_PRESERVE and part.text == "[65]" for part in plan.parts
    )
    references = [part.text for part in plan.parts if part.role == ROLE_REFERENCE]
    assert references == [PRIOR, NEXT]


def test_column_note_continuation_uses_exact_prose_spans_without_markers():
    values = columns()
    groups = detect_reference_note_continuations(values)
    assert len(groups) == 1
    group = groups[0]
    assert group.edge.kind == "column"
    assert (
        values[0].text[group.fragments[0].start : group.fragments[0].end] == " " + NOTE
    )
    assert (
        values[1].text[group.fragments[1].start : group.fragments[1].end] == CONTINUED
    )


def test_note_continuation_rejects_completed_note_wrong_number_or_geometry():
    left, right = columns()
    for modified_left, modified_right in (
        (replace(left, text=left.text + "."), right),
        (left, replace(right, text=right.text.replace("[66]", "[67]"))),
        (left, replace(right, size=right.size * 2)),
        (left, replace(right, y1=right.y1 - 100)),
        (left, replace(right, ref=replace(right.ref, page_id=7))),
        (replace(left, text=PRIOR + "[65] A. Smith, Title ends with low"), right),
    ):
        assert not detect_reference_note_continuations([modified_left, modified_right])
    assert not detect_reference_note_continuations(
        [left, right], occupied=[FragmentSpan(left.ref, len(PRIOR), len(left.text))]
    )


def test_converter_translates_both_note_halves_together_and_keeps_reference_spans():
    class LiteralPreservingTranslator(FakeContinuationTranslator):
        def translate_continuation_fragments(self, texts, contexts, *, join_kind):
            self.calls.append((list(texts), list(contexts), join_kind))
            return ["译文 " + text for text in texts]

    values = columns()
    page = draft(
        6,
        [value.text for value in values],
        [paragraph(6, column=i, y0=282, y1=743) for i in range(2)],
    )
    translator = LiteralPreservingTranslator()
    converter = converter_with(translator)
    converter._prepare_continuation_overrides(page, None)
    assert len(translator.calls) == 1
    assert len(page.overrides) == 2
    for value, override in zip(values, page.overrides):
        text = value.text[override.span.start : override.span.end]
        assert "[65]" not in text and "[66]" not in text
    assert "https://example.org" not in translator.calls[0][0][0]
    assert "https://example.org" in page.overrides[0].translation
