"""Document/task boundaries and explicit propagation, without browser access."""

import io
from string import Template
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest
from pdfminer.pdfinterp import PDFResourceManager

from pdf2zh import chatgpt, high_level
from pdf2zh.converter import TranslateConverter


def test_document_identity_is_stable_and_page_selection_is_canonical():
    identify = high_level._chatgpt_document_task_id
    original = identify(b"original PDF", pages=[2, 0], lang_in="EN", lang_out="ZH")
    assert original == identify(
        b"original PDF", pages=[0, 2, 0], lang_in="en", lang_out="zh"
    )
    assert identify(b"original PDF", pages=[]) == identify(b"original PDF", pages=None)
    assert identify(b"original PDF", prompt=Template("Translate $text")) == identify(
        b"original PDF", prompt="Translate $text"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"stream": b"another PDF"},
        {"pages": [1]},
        {"lang_in": "de"},
        {"lang_out": "ja"},
        {"vfont": "Math"},
        {"vchar": "[0-9]"},
        {"prompt": Template("Another prompt $text")},
        {"compatible": True},
    ],
)
def test_document_or_translation_setting_change_separates_tasks(change):
    original = {
        "stream": b"original PDF",
        "pages": [0],
        "lang_in": "en",
        "lang_out": "zh",
    }
    assert high_level._chatgpt_document_task_id(
        **original
    ) != high_level._chatgpt_document_task_id(**(original | change))


def test_layout_model_identity_uses_content_not_install_or_temporary_path(tmp_path):
    first = tmp_path / "first.onnx"
    second = tmp_path / "other.onnx"
    first.write_bytes(b"same layout model")
    second.write_bytes(first.read_bytes())
    identify = high_level._chatgpt_document_task_id
    one = identify(b"PDF", model=SimpleNamespace(model_path=str(first)))
    assert one == identify(b"PDF", model=SimpleNamespace(model_path=str(second)))
    second.write_bytes(b"changed layout model")
    assert one != identify(b"PDF", model=SimpleNamespace(model_path=str(second)))


def test_stream_task_uses_original_bytes_and_ignores_operational_settings(
    tmp_path, monkeypatch
):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((30, 40), "Task identity fixture")
        original = doc.tobytes()
    font = tmp_path / "font.ttf"
    font.write_bytes(pymupdf.Font("tiro").buffer)
    monkeypatch.setattr(high_level, "download_remote_fonts", lambda lang: str(font))
    monkeypatch.setattr(high_level, "prepare_pdf_text_font", lambda path: path)
    captured = []

    def patch_pdf(inf, **kwargs):
        assert inf.getvalue() != original, "fixture must observe post-rewrite bytes"
        captured.append(kwargs["chatgpt_task_id"])
        return {}

    monkeypatch.setattr(high_level, "translate_patch", patch_pdf)
    for thread, timeout, source in [
        (1, "100", "first-temp.pdf"),
        (8, "900", "second-temp.pdf"),
    ]:
        high_level.translate_stream(
            original,
            lang_in="en",
            lang_out="zh",
            service="chatgpt-browser:web",
            thread=thread,
            envs={"CHATGPT_TIMEOUT": timeout},
            file=source,
            skip_subset_fonts=True,
        )
    assert (
        captured
        == [high_level._chatgpt_document_task_id(original, lang_in="en", lang_out="zh")]
        * 2
    )


def test_patch_passes_task_id_to_converter_before_processing_pages(monkeypatch):
    class StopAtConverter(Exception):
        pass

    converter = Mock(side_effect=StopAtConverter)
    monkeypatch.setattr(high_level, "TranslateConverter", converter)
    with pytest.raises(StopAtConverter):
        high_level.translate_patch(io.BytesIO(), chatgpt_task_id="document-key")
    assert converter.call_args.kwargs["chatgpt_task_id"] == "document-key"


def test_converter_sends_task_id_only_to_chatgpt(monkeypatch):
    class FakeChatGPT:
        name = "chatgpt-browser"

        def __init__(self, *args, **kwargs):
            self.options = kwargs

    monkeypatch.setattr(chatgpt, "ChatGPTTranslator", FakeChatGPT)
    converter = TranslateConverter(
        PDFResourceManager(),
        service="chatgpt-browser:web",
        chatgpt_task_id="document-key",
    )
    assert converter.translator.options["task_id"] == "document-key"

    from pdf2zh import converter as converter_module

    class FakeGoogle:
        name = "google"

        def __init__(self, *args, **kwargs):
            self.options = kwargs

    monkeypatch.setattr(converter_module, "GoogleTranslator", FakeGoogle)
    converter = TranslateConverter(
        PDFResourceManager(), service="google", chatgpt_task_id="document-key"
    )
    assert "task_id" not in converter.translator.options
