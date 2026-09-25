"""Exercise browser UI callbacks without loading Gradio or downloading models."""

import ast
import asyncio
import os
from pathlib import Path
import shutil
from string import Template
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest


@pytest.fixture
def gui():
    source = Path(__file__).parents[1] / "pdf2zh" / "gui.py"
    names = {
        "_chatgpt_control_updates",
        "_chatgpt_action",
        "open_chatgpt_login",
        "refresh_chatgpt_controls",
        "select_chatgpt_model",
        "stop_translate_file",
        "translate_file",
    }
    tree = ast.parse(source.read_text(encoding="utf-8"))
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]

    class GuiError(Exception):
        pass

    class TranslationAbortedError(RuntimeError):
        pass

    browser = SimpleNamespace(
        name="chatgpt-browser",
        envs={"CHATGPT_MODEL": "", "CHATGPT_EFFORT": "", "CHATGPT_TIMEOUT": "900"},
    )
    namespace = {
        "gr": SimpleNamespace(
            update=lambda **kwargs: kwargs, Error=GuiError, Progress=Mock
        ),
        "ChatGPTTranslator": browser,
        "service_map": {"ChatGPT 聊天": browser},
        "ConfigManager": Mock(),
        "logger": Mock(),
        "cancel_chatgpt_wait": Mock(),
        "login_chatgpt": Mock(),
        "refresh_chatgpt_options": Mock(),
        "uuid": uuid,
        "asyncio": asyncio,
        "CancelledError": asyncio.CancelledError,
        "cancellation_event_map": {},
        "flag_demo": False,
        "Path": Path,
        "shutil": shutil,
        "os": os,
        "lang_map": {"English": "en", "Simplified Chinese": "zh"},
        "page_map": {"All": None},
        "tqdm": SimpleNamespace(tqdm=object),
        "ModelInstance": SimpleNamespace(value=None),
        "Template": Template,
        "TranslationAbortedError": TranslationAbortedError,
        "translate": Mock(),
    }
    exec(compile(tree, str(source), "exec"), namespace)
    return SimpleNamespace(**namespace)


def test_account_refresh_removes_model_and_effort_no_longer_available(gui):
    status, model, effort = gui._chatgpt_control_updates(
        {
            "ok": True,
            "message": "已连接",
            "models": [{"value": "Instant", "label": "即时"}],
            "efforts": [],
            "model": "Thinking",
            "effort": "Extended",
        }
    )
    assert status == "已连接"
    assert model == {
        "choices": [("即时", "Instant")],
        "value": None,
        "interactive": True,
    }
    assert effort == {"choices": [], "value": None, "interactive": False}


def test_switch_model_rediscovers_effort_instead_of_reusing_old_effort(gui):
    gui.refresh_chatgpt_options.return_value = {
        "models": [{"value": "Thinking", "label": "Thinking"}],
        "efforts": [{"value": "Standard", "label": "标准"}],
        "model": "Thinking",
        "effort": "Standard",
    }
    _, model, effort = gui.select_chatgpt_model("Thinking")
    gui.refresh_chatgpt_options.assert_called_once_with("Thinking", None)
    assert model["value"] == "Thinking"
    assert effort["value"] == "Standard"


def test_browser_disconnect_is_shown_and_does_not_leave_stale_choices(gui):
    gui.refresh_chatgpt_options.side_effect = RuntimeError("浏览器已关闭，请重新连接。")
    status, model, effort = gui.refresh_chatgpt_controls("Thinking", "Extended")
    assert "重新连接" in status
    assert model["value"] is None
    assert effort["choices"] == []


def test_cancel_browser_signals_wait_without_affecting_other_services(gui):
    event = asyncio.Event()
    gui.cancellation_event_map["session"] = event
    gui.stop_translate_file({"session_id": "session", "service": "codex"})
    assert event.is_set()
    gui.cancel_chatgpt_wait.assert_not_called()
    gui.stop_translate_file({"session_id": "session", "service": "chatgpt-browser"})
    gui.cancel_chatgpt_wait.assert_called_once_with()


def _translate_kwargs(tmp_path):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"test source PDF")
    return dict(
        file_type="File",
        file_input=str(source),
        link_input=None,
        service="ChatGPT 聊天",
        lang_from="English",
        lang_to="Simplified Chinese",
        page_range="All",
        page_input="",
        prompt="Translate $text",
        threads="12",
        skip_subset_fonts=False,
        ignore_cache=False,
        vfont="",
        use_babeldoc=False,
        recaptcha_response=None,
        state={},
        chatgpt_model="Thinking",
        chatgpt_effort="Extended",
        progress=Mock(),
    )


def test_browser_translation_passes_selected_options_and_forces_serial_execution(
    gui, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    kwargs = _translate_kwargs(tmp_path)

    def write_outputs(**params):
        for suffix in ("mono", "dual"):
            (params["output"] / f"input-{suffix}.pdf").write_bytes(b"translated PDF")

    gui.translate.side_effect = write_outputs
    result = gui.translate_file(**kwargs)
    params = gui.translate.call_args.kwargs
    assert params["service"] == "chatgpt-browser"
    assert params["thread"] == 1
    assert params["envs"]["CHATGPT_MODEL"] == "Thinking"
    assert params["envs"]["CHATGPT_EFFORT"] == "Extended"
    assert params["prompt"].template == "Translate $text"
    gui.ConfigManager.set_translator_by_name.assert_called_once_with(
        "chatgpt-browser", params["envs"]
    )
    assert result[0].endswith("input-mono.pdf")
    assert not gui.cancellation_event_map


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("use_babeldoc", True, "BabelDOC"),
        ("chatgpt_model", None, "刷新模型"),
    ],
)
def test_browser_preflight_rejects_unsupported_or_unconnected_requests(
    gui, tmp_path, monkeypatch, key, value, message
):
    monkeypatch.chdir(tmp_path)
    kwargs = _translate_kwargs(tmp_path)
    kwargs[key] = value
    with pytest.raises(gui.gr.Error, match=message):
        gui.translate_file(**kwargs)
    gui.translate.assert_not_called()
    assert not (tmp_path / "pdf2zh_files").exists()


def test_paused_translation_reports_actionable_reason_and_clears_session(
    gui, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    kwargs = _translate_kwargs(tmp_path)
    gui.translate.side_effect = gui.TranslationAbortedError(
        "已暂停。重新连接后，对同一 PDF 再次点击翻译。"
    )
    with pytest.raises(gui.gr.Error, match="已暂停"):
        gui.translate_file(**kwargs)
    assert not gui.cancellation_event_map
