from __future__ import annotations

import unittest

from pdf2zh.converter import (
    FLOW_TOKEN_PREFIX,
    PageLayoutDraft,
    Paragraph,
    TranslateConverter,
    _classify_running_header_regions,
)
from pdf2zh.reading_flow import detect_cross_page_edge
from pdf2zh.translation_policy import RUNNING_HEADER_REGION_KIND


class FakeContinuationTranslator:
    name = "codex"
    lang_out = "zh-cn"
    prompttext = None

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[list[str], list[dict[str, str]], str]] = []

    def translate_continuation_fragments(
        self,
        texts: list[str],
        contexts: list[dict[str, str]],
        *,
        join_kind: str,
    ) -> list[str] | None:
        self.calls.append((list(texts), list(contexts), join_kind))
        if self.fail:
            return None
        call_number = len(self.calls)
        return [
            f"联合{call_number}.{index}"
            for index in range(1, len(texts) + 1)
        ]


def paragraph(
    page_id: int,
    *,
    column: int,
    y0: float,
    y1: float,
    kind: str = "plain text",
) -> Paragraph:
    x0, x1 = ((48.0, 286.0) if column == 0 else (326.0, 564.0))
    return Paragraph(
        y1,
        x0,
        x0,
        x1,
        y0,
        y1,
        9.5,
        True,
        page_id=page_id,
        layout_class=2,
        region_kind=kind,
    )


def draft(
    page_id: int,
    texts: list[str],
    paragraphs: list[Paragraph],
) -> PageLayoutDraft:
    return PageLayoutDraft(
        ltpage=object(),
        page_id=page_id,
        page_xref=100 + page_id,
        width=612.0,
        height=792.0,
        sstk=texts,
        pstk=paragraphs,
        var=[],
        varl=[],
        varf=[],
        varp=[],
        vlen=[],
        lstk=[],
        formula_texts=[],
        italic_candidates={},
        readonly_formula_contexts={},
        fontid={},
        fontmap={},
        overrides=[],
    )


def converter_with(translator: FakeContinuationTranslator) -> TranslateConverter:
    converter = TranslateConverter.__new__(TranslateConverter)
    converter.translator = translator
    converter._flow_token_counter = 0
    return converter


def yan_chain() -> tuple[PageLayoutDraft, PageLayoutDraft, PageLayoutDraft]:
    page3 = draft(
        2,
        [
            "The dominant error is determined by the coupling strength of (1) "
            "the virtual"
        ],
        [paragraph(2, column=1, y0=58.0, y1=208.0)],
    )
    page4 = draft(
        3,
        [
            "exchange interaction via the state {v0}. Several ordinary sentences "
            "follow before we discuss their individual contribution",
            "separately. First, another discussion follows. By checking the final "
            "state at the end of the gate operation, we",
        ],
        [
            paragraph(3, column=0, y0=55.0, y1=738.0),
            paragraph(3, column=1, y0=56.0, y1=739.0),
        ],
    )
    page5 = draft(
        4,
        [
            "FIG. 3. Relation between error per gate and gate length.",
            "find that the remaining errors are largely due to leakage to the "
            "excited states of the coupler.",
        ],
        [
            paragraph(
                4,
                column=0,
                y0=430.0,
                y1=580.0,
                kind="figure_caption",
            ),
            paragraph(4, column=0, y0=248.0, y1=405.0),
        ],
    )
    return page3, page4, page5


class ConverterContinuationTests(unittest.TestCase):
    def test_running_header_is_preserved_and_excluded_from_page_flow(self) -> None:
        previous = draft(
            0,
            ["The preceding discussion continues on the next physical page"],
            [paragraph(0, column=1, y0=74.0, y1=111.0)],
        )
        header = Paragraph(
            760.15,
            38.0,
            38.0,
            556.0,
            753.18,
            760.15,
            6.97,
            False,
            page_id=1,
            layout_class=1,
        )
        following = draft(
            1,
            [
                "IEEE journal running header for this page",
                "and is completed by this ordinary body paragraph.",
            ],
            [header, paragraph(1, column=0, y0=488.0, y1=742.0)],
        )

        classified = _classify_running_header_regions(
            following.sstk,
            following.pstk,
            page_width=following.width,
            page_height=following.height,
        )
        previous_flow = TranslateConverter._draft_flow_segments(previous)
        following_flow = TranslateConverter._draft_flow_segments(following)
        edge = detect_cross_page_edge(previous_flow, following_flow)

        self.assertEqual(classified, (0,))
        self.assertEqual(
            following.pstk[0].region_kind,
            RUNNING_HEADER_REGION_KIND,
        )
        self.assertIsNotNone(edge)
        assert edge is not None
        self.assertEqual(edge.right.segment_index, 1)

    def test_yan_long_chain_uses_three_nonoverlapping_boundary_groups(self) -> None:
        translator = FakeContinuationTranslator()
        converter = converter_with(translator)
        page3, page4, page5 = yan_chain()

        converter._prepare_continuation_overrides(page3, page4)
        converter._prepare_continuation_overrides(page4, page5)

        self.assertEqual(len(translator.calls), 3)
        self.assertEqual(
            [call[2] for call in translator.calls],
            ["page:normal", "column:normal", "page:normal"],
        )
        self.assertEqual(len(page3.overrides), 1)
        self.assertEqual(len(page4.overrides), 4)
        self.assertEqual(len(page5.overrides), 1)
        page4_spans = [item.span for item in page4.overrides]
        self.assertEqual(len(page4_spans), len(set(page4_spans)))
        for left_index, left in enumerate(page4_spans):
            for right in page4_spans[left_index + 1 :]:
                if left.ref != right.ref:
                    continue
                self.assertTrue(left.end <= right.start or right.end <= left.start)
        translated_sources = " ".join(
            fragment
            for call in translator.calls
            for fragment in call[0]
        )
        self.assertNotIn("FIG. 3", translated_sources)

    def test_joint_translation_failure_leaves_old_path_unmodified(self) -> None:
        translator = FakeContinuationTranslator(fail=True)
        converter = converter_with(translator)
        page3, page4, _ = yan_chain()

        converter._prepare_continuation_overrides(page3, page4)

        self.assertEqual(len(translator.calls), 1)
        self.assertEqual(page3.overrides, [])
        self.assertEqual(page4.overrides, [])

    def test_multiple_overrides_restore_to_their_original_segment(self) -> None:
        translator = FakeContinuationTranslator()
        converter = converter_with(translator)
        _, page4, _ = yan_chain()
        source = list(page4.sstk)
        from pdf2zh.converter import FragmentOverride  # noqa: PLC0415
        from pdf2zh.reading_flow import FragmentSpan, SegmentRef  # noqa: PLC0415

        page4.overrides = [
            FragmentOverride(FragmentSpan(SegmentRef(3, 0), 0, 8), "开头"),
            FragmentOverride(
                FragmentSpan(SegmentRef(3, 0), len(source[0]) - 12, len(source[0])),
                "结尾",
            ),
        ]
        masked, replacements = converter._mask_fragment_overrides(
            source,
            page4.overrides,
            page4.page_id,
        )

        self.assertEqual(masked[0].count(FLOW_TOKEN_PREFIX), 2)
        restored = converter._restore_fragment_overrides(masked, replacements)
        self.assertTrue(restored[0].startswith("开头"))
        self.assertTrue(restored[0].endswith("结尾"))
        self.assertNotIn(FLOW_TOKEN_PREFIX, restored[0])


if __name__ == "__main__":
    unittest.main()
