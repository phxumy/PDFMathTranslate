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
        "poll_codex_account_login",
        "codex_selection_changed",
        "refresh_codex_account",
        "logout_codex_account",
        "install_app_update",
        "_update_download_controls",
        "cancel_app_update_download",
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
        TaskRecorder=Mock(),
        cache_information=lambda: {"healthy": True, "entries": 0, "bytes": 0},
        current_task=Mock(),
        _preview_payload=lambda pages: dict(pages),
        stop_translate_file=Mock(),
        desktop_mode=True,
        logger=Mock(),
        poll_codex_login=Mock(),
        start_codex_login=Mock(),
        cancel_codex_login=Mock(),
        get_login_account_status=Mock(),
        get_codex_status=Mock(),
        logout_codex=Mock(),
    )
    ns["update_manager"].download_state.return_value = {
        "busy": False,
        "cancellable": False,
        "available": True,
    }
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
        assert preview[1]["visible"] is True
        assert preview[6]["visible"] is True
        path = preview[6]["value"][1]
        assert Path(path).read_bytes() == b"png bytes"
        ui["update_manager"].end_translation.assert_not_called()
        proceed.set()
        final = next(iterator)
        assert final[1] == {"visible": True}
        assert final[6]["visible"] is True
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


def test_history_write_failure_does_not_leave_ui_waiting_forever(ui):
    ui["translate_file"].side_effect = RuntimeError("provider unavailable")
    ui["TaskRecorder"].return_value.finish.side_effect = OSError("history full")
    iterator = stream(ui)
    next(iterator)
    assert "provider unavailable" in next(iterator)[7]
    with pytest.raises(RuntimeError, match="provider unavailable"):
        next(iterator)
    ui["update_manager"].end_translation.assert_called_once()


def test_success_waits_for_worker_cleanup_without_cancelling_next_queued_file(ui):
    result_posted, cleanup, closed = Event(), Event(), Event()

    class FinishingQueue(Queue):
        def put(self, item):
            super().put(item)
            if item[0] == "result":
                result_posted.set()
                assert cleanup.wait(5)

    ui["Queue"] = FinishingQueue
    ui["translate_file"].return_value = ("mono.pdf", "mono.pdf", "dual.pdf", {}, {}, {})
    iterator = stream(ui)
    next(iterator)
    assert result_posted.wait(1)
    assert "翻译完成" in next(iterator)[7]

    def finish():
        with pytest.raises(StopIteration):
            next(iterator)
        closed.set()

    draining = Thread(target=finish)
    draining.start()
    try:
        assert not closed.wait(0.05)
        ui["stop_translate_file"].assert_not_called()
        ui["update_manager"].end_translation.assert_not_called()
    finally:
        cleanup.set()
        draining.join(5)
    assert closed.is_set()
    ui["stop_translate_file"].assert_not_called()
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
    assert [row[0] for row in messages[1:]] == ["50%", "done"]
    assert messages[-1][1:] == (
        {"interactive": True},
        {"visible": False, "interactive": False},
        {"interactive": True},
    )


def test_update_cancel_button_is_enabled_only_during_download(ui):
    ui["update_manager"].download_state.return_value = {
        "busy": True,
        "cancellable": True,
        "available": True,
    }
    result = ui["_update_download_controls"]("downloading")
    assert result[1:] == (
        {"interactive": False},
        {"visible": True, "interactive": True},
        {"interactive": False},
    )
    ui["update_manager"].cancel_download.return_value = "正在取消"
    ui["update_manager"].download_state.return_value = {
        "busy": True,
        "cancellable": False,
        "available": True,
    }
    cancelled = ui["cancel_app_update_download"]()
    assert cancelled[0] == "正在取消" and not cancelled[2]["visible"]
    assert not cancelled[1]["interactive"]
    ui["update_manager"].cancel_download.assert_called_once()


def test_successful_login_refreshes_identity_and_quota_then_stops_polling(ui):
    ui["poll_codex_login"].return_value = {"status": "succeeded", "message": "done"}
    ui["get_login_account_status"].return_value = {
        "message": "account@example.com · 剩余 75%"
    }
    result = ui["poll_codex_account_login"]("private-login")
    assert result == ("account@example.com · 剩余 75%", None, {"active": False})
    ui["get_login_account_status"].assert_called_once_with("private-login")


def test_changing_cli_cancels_old_login_and_clears_stale_account(ui):
    result = ui["codex_selection_changed"]("old-login")
    ui["cancel_codex_login"].assert_called_once_with("old-login")
    assert "已更改" in result[0]
    assert result[1:] == (None, {"active": False})


def test_account_refresh_and_login_follow_selected_cli_and_profile(ui):
    ui["get_codex_status"].return_value = {"message": "selected account"}
    assert ui["refresh_codex_account"]("custom-cli", "work") == "selected account"
    ui["get_codex_status"].assert_called_once_with("custom-cli", "work")
    ui["start_codex_login"].return_value = {"status": "pending", "login_id": "new"}
    ui["begin_codex_account_login"]("custom-cli", "device", None, "work")
    ui["start_codex_login"].assert_called_once_with(
        mode="device", configured_bin="custom-cli", profile="work"
    )


def test_refresh_does_not_expose_exception_details_and_web_cannot_logout(ui):
    ui["get_codex_status"].side_effect = RuntimeError("private-token-sentinel")
    assert "private-token-sentinel" not in ui["refresh_codex_account"]("codex")
    ui["desktop_mode"] = False
    with pytest.raises(RuntimeError, match="桌面软件"):
        ui["logout_codex_account"]("codex")
    ui["logout_codex"].assert_not_called()
