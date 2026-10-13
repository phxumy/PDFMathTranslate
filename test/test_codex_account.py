"""Login lifecycle tests use a local stand-in process, never real credentials."""

import json
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

from pdf2zh import codex_account as account
from pdf2zh.translator import CodexTranslator, TranslationAbortedError


@pytest.fixture
def sessions(monkeypatch):
    monkeypatch.setattr(account, "_sessions", {})
    monkeypatch.setattr(account, "_cli", lambda configured: "codex.exe")
    yield
    account._close_logins()


def wait_state(login_id, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = account.poll_codex_login(login_id)
        if predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError(f"Login state did not converge: {state['status']}")


@pytest.mark.parametrize(
    "output,code,status",
    [
        ("Logged in using ChatGPT", 0, "authenticated"),
        ("Logged in using an API key - sk-private-example", 0, "authenticated"),
        ("Not logged in", 1, "not_logged_in"),
        ("invalid config: secret=private-example", 1, "error"),
    ],
)
def test_status_is_read_only_and_never_returns_raw_cli_output(
    sessions, output, code, status
):
    result = subprocess.CompletedProcess([], code, "", output)
    with mock.patch.object(account.subprocess, "run", return_value=result) as run:
        state = account.get_codex_status()
    assert state["status"] == status
    assert "private-example" not in json.dumps(state)
    assert run.call_args.args[0] == ["codex.exe", "login", "status"]
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL


def test_missing_cli_and_timeout_return_actionable_status(sessions, monkeypatch):
    monkeypatch.setattr(account, "_cli", lambda configured: None)
    assert account.get_codex_status()["status"] == "unavailable"
    monkeypatch.setattr(account, "_cli", lambda configured: "codex.exe")
    with mock.patch.object(
        account.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 8)
    ):
        assert account.get_codex_status()["status"] == "timeout"


def spawn_standin(monkeypatch, script):
    native_popen = subprocess.Popen
    commands = []

    def start(command, **kwargs):
        if command[0] != "codex.exe":
            return native_popen(command, **kwargs)
        commands.append(command)
        return native_popen([sys.executable, "-u", "-c", script], **kwargs)

    monkeypatch.setattr(account.subprocess, "Popen", start)
    return commands


def test_device_login_reports_code_and_cancel_stops_real_process(sessions, monkeypatch):
    commands = spawn_standin(
        monkeypatch,
        "import time; print('https://auth.openai.com/codex/device'); "
        "print('ABCD-EFGH'); print('refresh_token=do-not-display'); time.sleep(30)",
    )
    started = account.start_codex_login("device")
    state = wait_state(started["login_id"], lambda state: bool(state["user_code"]))
    assert state["auth_url"] == "https://auth.openai.com/codex/device"
    assert state["user_code"] == "ABCD-EFGH"
    assert "do-not-display" not in json.dumps(state)
    assert commands == [["codex.exe", "login", "--device-auth"]]
    assert account.start_codex_login()["status"] == "busy"
    process = account._sessions[started["login_id"]].process
    cancelled = account.cancel_codex_login(started["login_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["auth_url"] == cancelled["user_code"] == ""
    assert process.poll() is not None


def test_browser_login_can_complete_and_clears_temporary_authorization(
    sessions, monkeypatch
):
    commands = spawn_standin(
        monkeypatch, "print('https://auth.openai.com/oauth/authorize?state=temporary')"
    )
    started = account.start_codex_login()
    state = wait_state(started["login_id"], lambda state: state["status"] != "pending")
    assert state["status"] == "succeeded"
    assert state["auth_url"] == state["user_code"] == ""
    assert commands == [["codex.exe", "login"]]


def test_abandoned_login_expires_and_stops_its_process(sessions, monkeypatch):
    monkeypatch.setattr(account, "_LOGIN_TIMEOUT", 0.2)
    spawn_standin(monkeypatch, "import time; time.sleep(30)")
    started = account.start_codex_login()
    wait_state(started["login_id"], lambda state: state["status"] == "expired")
    process = account._sessions[started["login_id"]].process
    process.wait(timeout=5)
    assert process.poll() is not None


def test_instruction_parser_ignores_external_or_credential_urls():
    session = account._Login("test", "browser", mock.Mock())
    for url in (
        "https://evil.example/oauth/authorize",
        "https://auth.openai.com.evil.example/oauth/authorize",
        "https://secret@auth.openai.com/oauth/authorize",
        "https://auth.openai.com:notaport/oauth/authorize",
        "https://auth.openai.com/oauth/authorize?access_token=secret",
    ):
        session.consume(url)
        assert session.auth_url == ""
    session.consume("https://auth.openai.com/oauth/authorize?state=ok")
    assert session.auth_url.endswith("state=ok")


def test_translation_cancel_terminates_an_inflight_cli_process(tmp_path):
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.cancellation_event = threading.Event()
    translator.timeout = 30
    timer = threading.Timer(0.25, translator.cancellation_event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(TranslationAbortedError):
            translator._run_translation_command(
                [sys.executable, "-c", "import time; time.sleep(30)"], str(tmp_path)
            )
    finally:
        timer.cancel()
    assert time.monotonic() - started < 5
