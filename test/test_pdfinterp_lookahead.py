from __future__ import annotations

import types
import unittest
from dataclasses import dataclass

from pdfminer.layout import LTFigure, LTPage
from pdfminer.pdfinterp import PDFResourceManager

from pdf2zh.converter import (
    PageLayoutDraft,
    Paragraph,
    TranslateConverter,
)
from pdf2zh.pdfinterp import PDFPageInterpreterEx


class FakeCodexTranslator:
    name = "codex"
    prompttext = None

    def __init__(self) -> None:
        self.continuation_calls: list[tuple[list[str], str]] = []

    def translate_continuation_fragments(
        self,
        sources: list[str],
        formula_contexts: list[dict[str, str]],
        *,
        join_kind: str,
    ) -> list[str]:
        self.continuation_calls.append((list(sources), join_kind))
        return [f"translated:{source}" for source in sources]


@dataclass
class ConverterPage:
    page_xref: int


def paragraph_for(
    page_id: int,
    *,
    column: int,
    y0: float,
    y1: float,
) -> Paragraph:
    x0, x1 = ((52.0, 297.0) if column == 0 else (315.0, 560.0))
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
        region_kind="plain text",
    )


def make_draft(
    page_id: int,
    page_xref: int,
    *,
    text: str | None = None,
    column: int = 0,
    y0: float = 80.0,
    y1: float = 720.0,
    font_marker: str | None = None,
) -> PageLayoutDraft:
    ltpage = LTPage(page_id, (0.0, 0.0, 612.0, 792.0))
    sstk = [] if text is None else [text]
    pstk = (
        []
        if text is None
        else [paragraph_for(page_id, column=column, y0=y0, y1=y1)]
    )
    marker = font_marker or f"font-page-{page_id}"
    return PageLayoutDraft(
        ltpage=ltpage,
        page_id=page_id,
        page_xref=page_xref,
        width=612.0,
        height=792.0,
        sstk=sstk,
        pstk=pstk,
        var=[],
        varl=[],
        varf=[],
        varp=[],
        vlen=[],
        lstk=[],
        formula_texts=[],
        italic_candidates={},
        readonly_formula_contexts={},
        fontid={marker: f"id-{marker}"},
        fontmap={f"id-{marker}": marker},
        overrides=[],
    )


def make_converter(
    drafts: dict[int, PageLayoutDraft],
) -> tuple[TranslateConverter, FakeCodexTranslator, list[dict]]:
    converter = TranslateConverter.__new__(TranslateConverter)
    translator = FakeCodexTranslator()
    converter.translator = translator
    converter._pending_page = None
    converter._flow_token_counter = 0
    converter.fontid = {}
    converter.fontmap = {}
    converter.layout_region_types = {}
    final_calls: list[dict] = []

    def fake_receive_layout(
        self,
        ltpage,
        *,
        preview_only: bool = False,
        page_xref: int = -1,
        overrides=None,
    ):
        if preview_only:
            draft = drafts[int(ltpage.pageid)]
            if draft.page_xref != page_xref:
                raise AssertionError(
                    f"draft xref {draft.page_xref} != requested {page_xref}"
                )
            return draft
        final_calls.append(
            {
                "page_id": int(ltpage.pageid),
                "fontid": dict(self.fontid),
                "fontmap": dict(self.fontmap),
                "overrides": list(overrides or []),
            }
        )
        return f"PAGE-OPS-{int(ltpage.pageid)}"

    converter.receive_layout = types.MethodType(fake_receive_layout, converter)
    return converter, translator, final_calls


class ConverterLookaheadLifecycleTests(unittest.TestCase):
    def test_second_page_commits_first_page_and_restores_its_font_snapshot(self) -> None:
        drafts = {
            0: make_draft(0, 101, font_marker="first-font"),
            1: make_draft(1, 102, font_marker="second-font"),
        }
        converter, _, final_calls = make_converter(drafts)

        converter.cur_item = drafts[0].ltpage
        converter.fontid = dict(drafts[0].fontid)
        converter.fontmap = dict(drafts[0].fontmap)
        first_result = converter.end_page(ConverterPage(101))

        self.assertEqual(first_result, {})
        self.assertIs(converter._pending_page, drafts[0])
        self.assertEqual(final_calls, [])

        converter.cur_item = drafts[1].ltpage
        converter.fontid = dict(drafts[1].fontid)
        converter.fontmap = dict(drafts[1].fontmap)
        second_result = converter.end_page(ConverterPage(102))

        self.assertEqual(second_result, {101: "PAGE-OPS-0"})
        self.assertIs(converter._pending_page, drafts[1])
        self.assertEqual([call["page_id"] for call in final_calls], [0])
        self.assertEqual(final_calls[0]["fontid"], drafts[0].fontid)
        self.assertEqual(final_calls[0]["fontmap"], drafts[0].fontmap)

    def test_eof_flush_commits_last_pending_page_once(self) -> None:
        draft = make_draft(4, 404)
        converter, _, final_calls = make_converter({4: draft})
        converter.cur_item = draft.ltpage

        self.assertEqual(converter.end_page(ConverterPage(404)), {})
        self.assertEqual(converter.flush_deferred_pages(), {404: "PAGE-OPS-4"})
        self.assertIsNone(converter._pending_page)
        self.assertEqual(converter.flush_deferred_pages(), {})
        self.assertEqual([call["page_id"] for call in final_calls], [4])

    def test_figure_is_finalized_immediately_without_touching_pending_page(self) -> None:
        pending = make_draft(5, 505)
        converter, _, final_calls = make_converter({5: pending})
        converter._pending_page = pending
        parent = LTPage(5, (0.0, 0.0, 612.0, 792.0))
        figure = LTFigure(
            "Figure-XObject",
            (0.0, 0.0, 100.0, 100.0),
            (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        )
        figure.pageid = 5
        converter._stack = [parent]
        converter.cur_item = figure

        result = converter.end_figure("Figure-XObject")

        self.assertEqual(result, "PAGE-OPS-5")
        self.assertIs(converter._pending_page, pending)
        self.assertIs(converter.cur_item, parent)
        self.assertIn(figure, list(parent))
        self.assertEqual([call["page_id"] for call in final_calls], [5])

    def test_nonconsecutive_physical_pages_do_not_create_cross_page_edge(self) -> None:
        previous = make_draft(
            2,
            202,
            text=(
                "The four terms represent the coupling strength of the virtual"
            ),
            column=1,
            y0=74.0,
            y1=111.0,
        )
        following = make_draft(
            4,
            404,
            text=(
                "exchange interaction via the intermediate state and the coupler"
            ),
            column=0,
            y0=488.0,
            y1=742.0,
        )
        converter, translator, _ = make_converter({2: previous, 4: following})

        converter.cur_item = previous.ltpage
        self.assertEqual(converter.end_page(ConverterPage(202)), {})
        converter.cur_item = following.ltpage
        self.assertEqual(
            converter.end_page(ConverterPage(404)),
            {202: "PAGE-OPS-2"},
        )

        self.assertEqual(translator.continuation_calls, [])
        self.assertEqual(previous.overrides, [])
        self.assertEqual(following.overrides, [])


@dataclass
class ContentStream:
    objid: int


@dataclass
class InterpreterPage:
    pageno: int
    page_xref: int
    cropbox: tuple[float, float, float, float]
    contents: tuple[ContentStream, ...]
    rotate: int = 0
    resources: dict | None = None

    def __post_init__(self) -> None:
        if self.resources is None:
            self.resources = {}


class DeferredDevice:
    def __init__(self) -> None:
        self.current_page: InterpreterPage | None = None
        self.fontid = {}
        self.fontmap = {}

    def begin_page(self, page: InterpreterPage, ctm) -> None:
        self.current_page = page

    def end_page(self, page: InterpreterPage):
        if page.pageno == 0:
            return {}
        return {101: "TRANSLATED-OPS-FOR-PAGE-0"}


class PDFInterpreterDeferredPatchTests(unittest.TestCase):
    def test_deferred_ops_are_appended_to_the_referenced_old_page_patch(self) -> None:
        device = DeferredDevice()
        obj_patch: dict[int, str] = {}
        interpreter = PDFPageInterpreterEx(
            PDFResourceManager(),
            device,
            obj_patch,
        )

        def fake_render_contents(self, resources, streams, ctm):
            self.fontid = {}
            self.fontmap = {}
            assert device.current_page is not None
            return f"BASE-OPS-PAGE-{device.current_page.pageno} "

        interpreter.render_contents = types.MethodType(
            fake_render_contents,
            interpreter,
        )
        first = InterpreterPage(
            0,
            101,
            (11.0, 22.0, 611.0, 822.0),
            (ContentStream(201),),
        )
        second = InterpreterPage(
            1,
            102,
            (33.0, 44.0, 633.0, 844.0),
            (ContentStream(202),),
        )

        interpreter.process_page(first)
        first_base_patch = (
            "q BASE-OPS-PAGE-0 Q 1 0 0 1 11.0 22.0 cm "
        )
        self.assertEqual(obj_patch[101], first_base_patch)

        interpreter.process_page(second)

        self.assertEqual(
            obj_patch[101],
            first_base_patch + "TRANSLATED-OPS-FOR-PAGE-0",
        )
        self.assertEqual(
            obj_patch[102],
            "q BASE-OPS-PAGE-1 Q 1 0 0 1 33.0 44.0 cm ",
        )
        self.assertNotIn("TRANSLATED-OPS-FOR-PAGE-0", obj_patch[102])
        self.assertEqual(obj_patch[201], "")
        self.assertEqual(obj_patch[202], "")


if __name__ == "__main__":
    unittest.main()
