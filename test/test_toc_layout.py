from __future__ import annotations

import re
import html
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from pdfminer.layout import LTPage
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager

from pdf2zh.converter import PDFConverterEx, TranslateConverter
from pdf2zh.toc_layout import detect_toc_layout
from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    ROLE_TRANSLATE,
    SourceSegment,
)
from pdf2zh.translator import BingTranslator, GoogleTranslator


class Font:
    fontname = "Times-Roman"
    to_unichr = staticmethod(chr)
    char_width = staticmethod(lambda _cid: 0.5)
    char_disp = staticmethod(lambda _cid: 0)
    is_vertical = staticmethod(lambda: False)
    get_descent = staticmethod(lambda: 0.0)


def converter():
    value = TranslateConverter.__new__(TranslateConverter)
    PDFConverterEx.__init__(value, PDFResourceManager())
    value.cur_item = LTPage(0, (0.0, 0.0, 600.0, 800.0))
    value.layout = {0: np.ones((800, 600))}
    value.layout_region_types = {0: {1: "plain text"}}
    value.vfont = value.vchar = ""
    value.translator = SimpleNamespace(name="google", lang_out="en")
    value.noto_name = "noto"
    value.noto = None
    font = Font()
    value.fontmap = {"F1": font, "tiro": font}
    value.fontid = {font: "F1"}
    return value


def text(value, text, x, y, size=10):
    for char in text:
        if not char.isspace():
            value.render_char(
                (1, 0, 0, 1, x, y),
                value.fontmap["F1"],
                size,
                1,
                0,
                ord(char),
                None,
                PDFGraphicState(),
            )
        x += size * 0.5
    return x


def entry(value, label, number, y, x=60):
    end = text(value, label, x, y)
    for dot_x in np.arange(end + 5, 530 - len(number) * 5, 5):
        text(value, ".", dot_x, y)
    text(value, number, 530 - len(number) * 5, y)


def seed(value):
    text(value, "Table of contents", 60, 740, 20)
    entry(value, "Abstract", "2", 700)
    entry(value, "1 Introduction", "11", 680)
    entry(value, "2 Methods", "20", 660)


def joined(chars):
    return "".join(char.get_text() for char in chars)


def test_single_detector_block_becomes_independent_entries_and_protected_numbers():
    value = converter()
    seed(value)
    entry(value, "References", "53", 640)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    labels = [
        source
        for source, paragraph in zip(draft.sstk, draft.pstk)
        if paragraph.region_kind == "toc_entry"
    ]
    assert labels == ["Abstract", "1 Introduction", "2 Methods", "References"]
    assert draft.formula_texts == ["2", "11", "20", "53"]
    assert all("..." not in source for source in draft.sstk)


def test_multiline_entry_keeps_formula_scripts_and_indentation():
    value = converter()
    seed(value)
    text(value, "2.1 A very long material label", 90, 640)
    end = text(value, "Cs", 90, 625)
    text(value, "3", end, 623, 6)
    end = text(value, "Bi", end + 3, 625)
    text(value, "2", end, 623, 6)
    entry(value, "properties", "21", 625, x=end + 8)
    entry(value, "3 Results", "30", 605)
    layout = detect_toc_layout(value.cur_item)
    assert len(layout.entries) == 5
    assert (
        joined(layout.entries[3].chars) == "2.1AverylongmateriallabelCs3Bi2properties"
    )
    assert layout.entries[3].x0 == 90
    assert joined(layout.entries[3].page_number) == "21"


def test_row_without_leaders_is_separate_and_word_suffix_is_not_roman_page():
    value = converter()
    seed(value)
    text(value, "3.5 Scanning microscopy (EDS)", 90, 640)
    text(value, "26", 520, 640)
    # A title line ending near the page-number column with "spectrum" must
    # not lose its final m to the Roman-page-number detector.
    text(value, "Figure 3.6 A reflectance spectrum", 370, 620)
    entry(value, "and its composition", "27", 600)
    layout = detect_toc_layout(value.cur_item)
    assert len(layout.entries) == 5
    assert joined(layout.entries[3].page_number) == "26"
    assert joined(layout.entries[4].page_number) == "27"
    assert "spectrum" in joined(layout.entries[4].chars)


def test_wrapped_destination_number_returns_to_right_column():
    value = converter()
    seed(value)
    text(value, "Figure 4.4 Bandgap comparison....", 60, 640)
    text(value, "35", 60, 625)
    layout = detect_toc_layout(value.cur_item)
    assert len(layout.entries) == 4
    assert joined(layout.entries[-1].page_number) == "35"
    assert "..." not in joined(layout.entries[-1].chars)
    draft = value.receive_layout(value.cur_item, preview_only=True)
    number_id = draft.formula_texts.index("35")
    paragraph = draft.pstk[draft.varp[number_id]]
    assert paragraph.x0 == 520
    assert paragraph.x1 == 530


def test_single_dotted_sentence_does_not_change_body_layout():
    value = converter()
    text(value, "An ordinary sentence continuing", 60, 700)
    entry(value, "after an ellipsis", "42", 685)
    assert not detect_toc_layout(value.cur_item).entries


def test_three_unaligned_dotted_rows_do_not_establish_page_column():
    value = converter()
    for offset, y in enumerate([700, 680, 660]):
        text(value, "Label......23", 60 + offset * 90, y)
    assert not detect_toc_layout(value.cur_item).entries


def test_references_in_toc_do_not_change_following_entries_to_bibliography():
    policy = DocumentTranslationPolicy()
    for label in ["References", "2.1 Materials", "J. Smith and A. Jones"]:
        segment = SourceSegment(
            0, label, 60, 530, 620, 630, 10, False, 600, region_kind="toc_entry"
        )
        plan = policy.plan_segment(segment)
        assert [part.role for part in plan.parts] == [ROLE_TRANSLATE]
    assert policy.pending_reference_heading == 0


def test_render_short_labels_with_fixed_destinations_and_regenerated_leaders():
    value = converter()
    seed(value)
    value._translate_planned_segments = lambda sources, paragraphs, *args, **kwargs: [
        "Short" if p.region_kind == "toc_entry" else source
        for source, p in zip(sources, paragraphs)
    ]
    operations = value.receive_layout(value.cur_item)
    assert operations.count("[0 2.500000] 0 d") == 3
    assert "/F1 10.000000 Tf 1 0 0 1 525.000000 700.000000 Tm" in operations
    assert "/F1 10.000000 Tf 1 0 0 1 520.000000 680.000000 Tm" in operations
    assert "/F1 10.000000 Tf 1 0 0 1 520.000000 660.000000 Tm" in operations
    leader_starts = re.findall(r"\[0 2\.500000\] 0 d ([\d.]+) [\d.]+ m", operations)
    assert [float(start) for start in leader_starts] == [87.5, 87.5, 87.5]


@pytest.mark.parametrize("backend", [GoogleTranslator, BingTranslator])
def test_generic_translator_http_path_keeps_contents_rows_and_formula_geometry(backend):
    # Exercise the complete shared converter -> planner -> translate() ->
    # provider do_translate() -> renderer path. Only HTTP and cache I/O are
    # stubbed; this must never depend on a Codex-specific batch implementation.
    value = converter()
    seed(value)
    entry(value, "3 Energy x", "30", 640)
    for char in value.cur_item:
        if char.get_text() == "x":
            char.fontname = "CMI10"

    translator = backend.__new__(backend)
    translator.lang_in = "en"
    translator.lang_out = "en"
    translator.ignore_cache = True
    translator.cache = Mock()
    translator.headers = {}
    translator.endpoint = "https://stub.invalid/translator"
    translator.session = Mock()
    translator.translate_batch = Mock(
        side_effect=AssertionError("generic path must use translate")
    )
    requests = []

    def translated(source):
        requests.append(source)
        formulas = re.findall(r"\{v\d+\}", source)
        return "Short" + (" " + "".join(formulas) if formulas else "")

    def get(_url, **kwargs):
        if backend is GoogleTranslator:
            result = translated(kwargs["params"]["q"])
            return SimpleNamespace(
                status_code=200,
                text=f'class="result-container">{html.escape(result)}<',
                raise_for_status=lambda: None,
            )
        return SimpleNamespace(
            url=translator.endpoint,
            text='"ig":"stub-ig" data-iid="stub-iid" params_AbusePreventionHelper = [123,"stub-token",',
            raise_for_status=lambda: None,
        )

    def post(_url, **kwargs):
        result = translated(kwargs["data"]["text"])
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: [{"translations": [{"text": result}]}],
        )

    translator.session.get.side_effect = get
    translator.session.post.side_effect = post
    value.translator = translator
    value.thread = 4
    value.translation_policy = DocumentTranslationPolicy()
    operations = value.receive_layout(value.cur_item)

    assert len(requests) == 5  # Heading plus four independent entries.
    assert {source for source in requests if not source.startswith("3 Energy")} == {
        "Table of contents",
        "Abstract",
        "1 Introduction",
        "2 Methods",
    }
    formula_source = next(
        source for source in requests if source.startswith("3 Energy")
    )
    assert re.fullmatch(r"3 Energy \{v\d+\}", formula_source)
    assert all("..." not in source for source in requests)
    assert not {"2", "11", "20", "30"}.intersection(requests)
    translator.translate_batch.assert_not_called()
    assert operations.count("[0 2.500000] 0 d") == 4
    # Original page digits and the protected x glyph remain source-font
    # operations; the destination remains in its original right column.
    assert "/F1 10.000000 Tf 1 0 0 1 520.000000 640.000000 Tm [<33>]" in operations
    assert "/F1 10.000000 Tf 1 0 0 1 525.000000 640.000000 Tm [<30>]" in operations
    assert re.search(
        r"/F1 10\.000000 Tf 1 0 0 1 [\d.]+ 640\.000000 Tm \[<78>\]", operations
    )
