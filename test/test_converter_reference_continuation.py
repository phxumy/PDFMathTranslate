from __future__ import annotations

import unittest
from types import SimpleNamespace

from pdf2zh.converter import TranslateConverter
from pdf2zh.reading_flow import (
    FragmentSpan,
    ReferenceContinuationGroup,
    SegmentRef,
)
from pdf2zh.translation_policy import ExactReplacement


class _ReferenceTranslator:
    def translate_reference_continuation_fragments(
        self,
        left_entry: str,
        right_prefix: str,
    ) -> tuple[ExactReplacement, ExactReplacement]:
        return (
            ExactReplacement("title ending with low", "低"),
            ExactReplacement("anharmonicity qubits", "非谐量子比特标题"),
        )


class ConverterReferenceContinuationTests(unittest.TestCase):
    def test_only_title_spans_are_overridden_across_pages(self) -> None:
        left_ref = SegmentRef(8, 0)
        right_ref = SegmentRef(9, 0)
        left = "17. A. Smith, A useful title ending with low"
        right = "anharmonicity qubits. Nature 1 (2020). 18. B. Jones, Next title."
        next_marker = right.index("18.")
        group = ReferenceContinuationGroup(
            reference_number=17,
            reference_prefix="",
            fragments=(
                FragmentSpan(left_ref, 0, len(left)),
                FragmentSpan(right_ref, 0, next_marker),
            ),
            left_marker_end=len("17."),
            next_marker_start=next_marker,
        )
        converter = TranslateConverter.__new__(TranslateConverter)
        converter.translator = _ReferenceTranslator()
        drafts = {
            left_ref: SimpleNamespace(sstk=[left]),
            right_ref: SimpleNamespace(sstk=[right]),
        }

        overrides = converter._translate_reference_continuation_group(
            group,
            drafts,
        )

        self.assertIsNotNone(overrides)
        assert overrides is not None
        self.assertEqual(
            left[overrides[0].span.start : overrides[0].span.end],
            "title ending with low",
        )
        self.assertGreater(overrides[0].span.start, len("17."))
        self.assertEqual(overrides[0].translation, "低")
        self.assertEqual(
            right[overrides[1].span.start : overrides[1].span.end],
            "anharmonicity qubits",
        )
        self.assertLessEqual(overrides[1].span.end, next_marker)
        self.assertEqual(right[next_marker : next_marker + 3], "18.")


if __name__ == "__main__":
    unittest.main()
