"""Exercise streamed UI results without loading models or contacting a provider."""

import ast
from dataclasses import replace
import asyncio
import shutil
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
import time
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from pdf2zh.translation_progress import PagePreview, TranslationProgress


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = Path(__file__).parents[1] / "pdf2zh" / "gui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {
        "_codex_settings",
        "_duration",
        "_translation_status",
        "translate_file_stream",
        "_codex_login_updates",
        "begin_codex_account_login",
        "install_app_update",
    }
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    backend = SimpleNamespace(
        name="codex",
        envs={
            "CODEX_BIN": "codex",
            "CODEX_PROFILE": None,
            "CODEX_MODEL": None,
            "CODEX_REASONING_EFFORT": "none",
            "CODEX_TIMEOUT": "120",
            "CODEX_FAST_MODE": "false",
            "CODEX_MAX_CONCURRENCY": "",
        },
    )
    ns = dict(
        replace=replace,
        Path=Path,
        Empty=Empty,
        Queue=Queue,
        Thread=Thread,
        Event=Event,
        time=time,
        shutil=shutil,
        CancelledError=asyncio.CancelledError,
        cancellation_event_map={},
        uuid=uuid,
        gr=SimpleNamespace(update=lambda **kw: kw, Progress=Mock, Error=RuntimeError),
        ConfigManager=Mock(),
        CodexTranslator=backend,
        service_map={"Codex": backend},
        update_manager=Mock(),
        translate_file=Mock(),
        stop_translate_file=Mock(),
        desktop_mode=True,
        poll_codex_login=Mock(),
        start_codex_login=Mock(),
    )
    exec(compile(tree, str(source), "exec"), ns)
    return ns


def stream(ui):
    return ui["translate_file_stream"](
        "File",
        "source.pdf",
        "",
        "Codex",
        "English",
        "Simplified Chinese",
        "All",
        "",
        "",
        "4",
        False,
        False,
        "",
        False,
        "",
        {},
        None,
        None,
        False,
        "double",
        4,
    )


def test_double_parallel_keeps_fast_mode_independent(ui):
    values, workers = ui["_codex_settings"]([], False, "double", 4)
    assert workers == 8
    assert values[-2:] == ["false", "8"]
    values, workers = ui["_codex_settings"]([], True, "standard", 4)
    assert workers == 4
    assert values[-2:] == ["true", "4"]


def test_page_preview_arrives_before_final_output(ui):
    proceed = Event()

    def translate(*args, **kwargs):
        kwargs["progress_callback"](TranslationProgress("translating", 1, 2, 1, 3))
        kwargs["preview_callback"](PagePreview(1, 1, 2, b"png bytes"))
        assert proceed.wait(5)
        return ("mono.pdf", "mono.pdf", "dual.pdf", {}, {}, {})

    ui["translate_file"].side_effect = translate
    iterator = stream(ui)
    try:
        initial = next(iterator)
        assert initial[1]["visible"] is True
        progress = next(iterator)
        assert "1/2" in progress[7]
        preview = next(iterator)
        assert preview[1]["visible"] is False
        assert preview[6]["visible"] is True
        path, caption = preview[6]["value"][0]
        assert Path(path).read_bytes() == b"png bytes"
        assert "1" in caption
        ui["update_manager"].end_translation.assert_not_called()
        proceed.set()
        final = next(iterator)
        assert final[1] == {"value": "mono.pdf", "visible": True}
        assert final[6]["visible"] is False
        with pytest.raises(StopIteration):
            next(iterator)
        ui["update_manager"].end_translation.assert_called_once()
    finally:
        proceed.set()
        iterator.close()


def test_stream_failure_releases_update_guard_and_exposes_message(ui):
    ui["translate_file"].side_effect = RuntimeError("account unavailable")
    iterator = stream(ui)
    next(iterator)
    failed = next(iterator)
    assert "account unavailable" in failed[7]
    with pytest.raises(RuntimeError, match="account unavailable"):
        next(iterator)
    ui["update_manager"].end_translation.assert_called_once()


def test_closing_stream_cancels_worker_and_retains_guard_until_stopped(ui):
    proceed, entered = Event(), Event()

    def translate(*args, **kwargs):
        entered.set()
        proceed.wait(5)
        return ("mono.pdf", "mono.pdf", "dual.pdf", {}, {}, {})

    ui["translate_file"].side_effect = translate
    iterator = stream(ui)
    next(iterator)
    assert entered.wait(1)
    iterator.close()
    ui["stop_translate_file"].assert_called_once()
    ui["update_manager"].end_translation.assert_not_called()
    proceed.set()
    deadline = time.monotonic() + 2
    while (
        not ui["update_manager"].end_translation.called and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    ui["update_manager"].end_translation.assert_called_once()


def test_eta_explicitly_waits_for_samples_and_separates_finalization(ui):
    status = ui["_translation_status"](TranslationProgress("translating", 1, 20, 1, 13))
    assert "估算中" in status
    status = ui["_translation_status"](
        TranslationProgress("finalizing", 20, 20, None, 300, 0, 0)
    )
    assert "合并" in status
    assert "预计还需" not in status


def test_login_double_click_keeps_existing_pending_session(ui):
    ui["poll_codex_login"].return_value = {
        "status": "pending",
        "message": "waiting",
        "login_id": "mine",
    }
    result = ui["begin_codex_account_login"]("codex", "browser", "mine")
    assert result[1:] == ("mine", {"active": True})
    ui["start_codex_login"].assert_not_called()


def test_update_download_yields_progress(ui):
    def install(progress_callback):
        progress_callback("50%")
        return "done"

    ui["update_manager"].install.side_effect = install
    messages = list(ui["install_app_update"]())
    assert messages[1:] == ["50%", "done"]
