"""Credential ownership across every Codex entry point, using fake accounts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest import mock

import pytest

from pdf2zh import codex_account as account
from pdf2zh import codex_models, codex_runtime
from pdf2zh.codex_rpc import CodexAppServer
from pdf2zh.codex_runtime import CodexRuntime
from pdf2zh.translator import CodexTranslator, find_codex_executable


@pytest.fixture
def scopes(tmp_path, monkeypatch):
    bundle = tmp_path / "software"
    binary = bundle / "codex-cli/x86_64-pc-windows-msvc/bin/codex.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"stand-in")
    (bundle / "PDFMathTranslate-Codex.exe").touch()
    (bundle / "site-packages/pdf2zh").mkdir(parents=True)
    personal = tmp_path / "personal-codex"
    personal.mkdir()
    (personal / "auth.json").write_text("personal-account-sentinel")
    private = tmp_path / "software-account"
    monkeypatch.setenv("PYSTAND", str(bundle / "PDFMathTranslate-Codex.exe"))
    monkeypatch.setenv("CODEX_HOME", str(personal))
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(personal))
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
    ):
        monkeypatch.setenv(name, "personal-secret-sentinel")
    monkeypatch.setattr(codex_runtime, "application_codex_home", lambda: private)
    monkeypatch.setattr(account, "_sessions", {})
    yield binary, personal, private
    account._close_logins()
    assert (personal / "auth.json").read_text() == "personal-account-sentinel"


def test_bundled_auto_and_saved_absolute_path_use_private_scope(scopes):
    binary, personal, private = scopes
    for configured in ("codex", str(binary)):
        runtime = CodexRuntime.for_binary(find_codex_executable(configured))
        assert runtime.isolated
        assert runtime.command("login") == [
            str(binary),
            "-c",
            'cli_auth_credentials_store="file"',
            "login",
        ]
        options = runtime.subprocess_kwargs(account_operation=True)
        assert options["cwd"] == options["env"]["CODEX_HOME"] == str(private)
        assert private.is_dir() and not (private / "auth.json").exists()
        assert not (codex_runtime._AUTH_ENV & options["env"].keys())
    assert os.environ["CODEX_HOME"] == str(personal)
    assert os.environ["CODEX_API_KEY"] == "personal-secret-sentinel"


def test_saved_binary_from_another_install_is_still_isolated(scopes, monkeypatch):
    binary, _, _ = scopes
    monkeypatch.setattr(codex_runtime, "bundled_codex_candidates", lambda: [])
    assert CodexRuntime.for_binary(str(binary)).isolated


def test_external_cli_keeps_its_environment_and_configuration(scopes, tmp_path):
    external = tmp_path / "personal-tools/codex.exe"
    external.parent.mkdir()
    external.touch()
    runtime = CodexRuntime.for_binary(str(external))
    assert not runtime.isolated
    assert runtime.command("--profile", "work", "login") == [
        str(external),
        "--profile",
        "work",
        "login",
    ]
    assert runtime.subprocess_kwargs(account_operation=True) == {}
    assert "沿用用户登录" in runtime.scope_message


def test_matching_filename_or_directory_alone_does_not_mean_bundled(scopes, tmp_path):
    binary = tmp_path / "other/codex-cli/x86_64-pc-windows-msvc/bin/codex.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    assert not CodexRuntime.for_binary(str(binary)).isolated


def _standin(monkeypatch, binary, script):
    native = subprocess.Popen
    calls = []

    def popen(command, **kwargs):
        if command[0] == str(binary):
            calls.append((command, kwargs.copy()))
            return native([sys.executable, "-u", "-c", script, *command[1:]], **kwargs)
        return native(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(account, "_cli", lambda _: str(binary))
    return calls


def test_login_switch_status_and_logout_never_touch_personal_account(
    scopes, monkeypatch
):
    binary, _, private = scopes
    calls = _standin(
        monkeypatch,
        binary,
        """
import os, pathlib, sys
p = pathlib.Path(os.environ['CODEX_HOME']) / 'auth.json'
if 'logout' in sys.argv:
    p.unlink(missing_ok=True)
elif 'status' in sys.argv:
    print('Logged in using ChatGPT' if p.exists() else 'Not logged in')
    sys.exit(0 if p.exists() else 1)
else:
    p.write_text('software-account-' + ('B' if p.exists() else 'A'))
""",
    )
    monkeypatch.setattr(account, "_account_details", lambda *args: {})
    assert account.get_codex_status()["status"] == "not_logged_in"
    for expected in ("A", "B"):
        login = account.start_codex_login()
        deadline = time.monotonic() + 5
        while account.poll_codex_login(login["login_id"])["status"] == "pending":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert (private / "auth.json").read_text() == "software-account-" + expected
        assert account.get_login_account_status(login["login_id"])["logged_in"]
    assert account.logout_codex()["status"] == "not_logged_in"
    assert not (private / "auth.json").exists()
    assert calls and all(call[1]["env"]["CODEX_HOME"] == str(private) for call in calls)
    assert all('cli_auth_credentials_store="file"' in call[0] for call in calls)


def test_model_catalog_reads_same_private_account(scopes, monkeypatch):
    binary, _, private = scopes
    _standin(
        monkeypatch,
        binary,
        """
import json, os, sys
for line in sys.stdin:
    r = json.loads(line)
    if r['method'] == 'initialize':
        result = {}
    elif r['method'] == 'model/list':
        assert 'CODEX_API_KEY' not in os.environ
        result = {'data': [{'id': 'private-account-model'}], 'nextCursor': None}
    else:
        continue
    print(json.dumps({'id': r['id'], 'result': result}), flush=True)
""",
    )
    monkeypatch.setattr(codex_models, "_cli", lambda _: str(binary))
    result = codex_models.get_codex_models(str(binary))
    assert result["ok"] and result["models"][0]["id"] == "private-account-model"
    assert private.is_dir()


@pytest.mark.parametrize("cancellable", [False, True])
def test_actual_translation_process_uses_private_scope(
    scopes, monkeypatch, cancellable
):
    import threading

    binary, _, private = scopes
    calls = _standin(
        monkeypatch,
        binary,
        """
import json, os
print(json.dumps({'home': os.environ['CODEX_HOME'], 'hasKey': 'CODEX_API_KEY' in os.environ}))
""",
    )
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.codex_runtime = CodexRuntime.for_binary(str(binary))
    translator.timeout = 5
    translator.cancellation_event = threading.Event() if cancellable else None
    result = translator._run_translation_command(
        translator.codex_runtime.command("exec"), str(binary.parent)
    )
    assert json.loads(result.stdout) == {"home": str(private), "hasKey": False}
    assert result.returncode == 0 and calls


def test_compatibility_login_script_uses_same_private_scope(scopes, monkeypatch):
    binary, _, private = scopes
    with mock.patch.object(subprocess, "call", return_value=0) as call:
        assert codex_runtime.login_bundled_cli(str(binary)) == 0
    assert call.call_args.kwargs["env"]["CODEX_HOME"] == str(private)
    assert 'cli_auth_credentials_store="file"' in call.call_args.args[0]


@pytest.mark.parametrize("quota_failure", [False, True])
def test_account_identity_and_quota_rpc_are_allowlisted(
    scopes, monkeypatch, quota_failure
):
    binary, _, _ = scopes
    quota = (
        {"error": {"message": "private-token-sentinel"}}
        if quota_failure
        else {
            "result": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 23,
                        "windowDurationMins": 300,
                        "resetsAt": 1800000000,
                    },
                    "secondary": {"usedPercent": 54, "windowDurationMins": 10080},
                    "private": "private-token-sentinel",
                }
            }
        }
    )
    script = """
import json, sys
for line in sys.stdin:
    r = json.loads(line)
    if r['method'] == 'initialize': result = {'result': {}}
    elif r['method'] == 'account/read':
        assert r['params']['refreshToken'] is False
        result = {'result': {'account': {'type': 'chatgpt', 'email': 'test@example.com',
                  'planType': 'plus', 'accessToken': 'private-token-sentinel'}}}
    elif r['method'] == 'account/rateLimits/read': result = QUOTA
    else: continue
    print(json.dumps({'id': r['id'], **result}), flush=True)
""".replace("QUOTA", repr(quota))
    _standin(monkeypatch, binary, script)
    details = account._account_details(CodexRuntime.for_binary(str(binary)), None)
    assert details["email"] == "test@example.com" and details["plan"] == "plus"
    assert "private-token-sentinel" not in json.dumps(details)
    if quota_failure:
        assert details["quota_lines"] == []
    else:
        assert "5 小时额度剩余 77%" in details["quota_lines"][0]
        assert "重置" in details["quota_lines"][0]
        assert "7 天额度剩余 46%" in details["quota_lines"][1]


def test_api_key_account_does_not_query_subscription_quota():
    with mock.patch.object(account, "CodexAppServer") as factory:
        server = factory.return_value.__enter__.return_value
        server.request.return_value = {"account": {"type": "apiKey"}}
        result = account._account_details(CodexRuntime("codex", False), None)
    assert "不提供" in result["details_message"]
    server.request.assert_called_once_with("account/read", {"refreshToken": False})


def test_missing_or_malformed_quota_is_never_reported_as_zero_or_full():
    assert account._quota_lines({}) == []
    assert account._quota_lines({"rateLimits": {"primary": {}}}) == []
    assert (
        account._quota_lines({"rateLimits": {"primary": {"usedPercent": float("nan")}}})
        == []
    )
    assert (
        account._quota_lines({"rateLimits": {"primary": {"usedPercent": True}}}) == []
    )
    lines = account._quota_lines(
        {
            "rateLimitsByLimitId": {
                "codex": {"primary": {"usedPercent": 100}},
                "other": {"primary": {"usedPercent": 0}},
            }
        }
    )
    assert "剩余 0%" in lines[0] and "剩余 100%" in lines[1]


def test_rpc_timeout_terminates_only_the_owned_child(scopes, monkeypatch):
    binary, _, _ = scopes
    _standin(monkeypatch, binary, "import time; time.sleep(30)")
    server = CodexAppServer(CodexRuntime.for_binary(str(binary)), timeout=0.15)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        with server:
            pass
    assert server.process is None and time.monotonic() - started < 5
