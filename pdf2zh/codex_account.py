"""Local Codex account controls backed exclusively by the official CLI.

Credentials remain in the CLI's credential store. Only recognized login state,
official authorization URLs, and short-lived device codes leave this module;
raw CLI output is neither returned nor logged.
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass, field
import os
import re
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlsplit
import uuid

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_URL = re.compile(r"https://[^\s<>\"']+")
_DEVICE_CODE = re.compile(r"(?<![A-Z0-9-])([A-Z0-9]{4,5}-[A-Z0-9]{4,5})(?![A-Z0-9-])")
_LOGIN_TIMEOUT = 15 * 60
_TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


def _cli(configured_bin: str | None):
    # Keep this module cheap to import, including in desktop startup and tests.
    from pdf2zh.translator import find_codex_executable

    return find_codex_executable(configured_bin)


def _window_kwargs():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _result(status, message, **fields):
    return {"status": status, "message": message, **fields}


def get_codex_status(configured_bin: str | None = None) -> dict:
    """Read CLI authentication state without opening or changing credentials."""

    binary = _cli(configured_bin)
    if not binary:
        return _result(
            "unavailable", "未找到 Codex CLI，请检查安装或 CODEX_BIN。", logged_in=False
        )
    try:
        completed = subprocess.run(
            [binary, "login", "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            **_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return _result("timeout", "Codex 登录状态查询超时，请重试。", logged_in=False)
    except OSError:
        return _result("error", "无法启动 Codex CLI，请检查安装。", logged_in=False)

    output = (completed.stdout + "\n" + completed.stderr).casefold()
    if completed.returncode == 0:
        method = (
            "api_key"
            if "api key" in output or "api-key" in output
            else ("chatgpt" if "chatgpt" in output else "unknown")
        )
        label = {"api_key": "API Key", "chatgpt": "ChatGPT", "unknown": "Codex"}[method]
        return _result(
            "authenticated",
            f"已登录 Codex（{label}）。",
            logged_in=True,
            auth_method=method,
        )
    if "not logged in" in output or "not authenticated" in output:
        return _result("not_logged_in", "尚未登录 Codex。", logged_in=False)
    return _result(
        "error", "无法读取 Codex 登录状态，请检查 CLI 配置后重试。", logged_in=False
    )


def _terminate_process(process):
    """Stop only the CLI process tree owned by this login/request."""
    if process.poll() is not None:
        return
    try:
        # npm's .cmd wrapper can spawn node and a native CLI on Windows.
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                **_window_kwargs(),
            )
        else:
            process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass


@dataclass
class _Login:
    login_id: str
    mode: str
    process: subprocess.Popen
    started: float = field(default_factory=time.monotonic)
    status: str = "pending"
    message: str = "请在打开的浏览器中完成 Codex 登录。"
    auth_url: str = ""
    user_code: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self):
        with self.lock:
            return _result(
                self.status,
                self.message,
                login_id=self.login_id,
                mode=self.mode,
                auth_url=self.auth_url,
                user_code=self.user_code,
            )

    def consume(self, line):
        """Extract a narrow set of presentation fields; discard everything else."""
        line = _ANSI.sub("", line)
        with self.lock:
            if self.status in _TERMINAL:
                return
            for match in _URL.finditer(line):
                url = match.group().rstrip(".,;)")
                try:
                    parsed = urlsplit(url)
                    allowed = (
                        parsed.hostname == "auth.openai.com"
                        and not parsed.username
                        and not parsed.password
                        and parsed.port in (None, 443)
                        and parsed.path in ("/oauth/authorize", "/codex/device")
                        and not any(
                            key.casefold()
                            in {
                                "access_token",
                                "refresh_token",
                                "id_token",
                                "api_key",
                                "client_secret",
                                "password",
                            }
                            for key, _ in parse_qsl(parsed.query)
                        )
                    )
                except ValueError:
                    allowed = False
                if allowed:
                    self.auth_url = url
            if self.mode == "device":
                match = _DEVICE_CODE.search(line)
                if match:
                    self.user_code = match.group(1)
                    self.message = "请打开官方登录页面，输入设备验证码并完成登录。"

    def finish(self, status, message):
        with self.lock:
            if self.status in _TERMINAL:
                return
            self.status, self.message = status, message
            # Expired authorization links and codes should not remain visible.
            self.auth_url = self.user_code = ""


_sessions: dict[str, _Login] = {}
_sessions_lock = threading.RLock()


def _read_instructions(session):
    try:
        with session.process.stdout as stream:
            for line in iter(lambda: stream.readline(8192), ""):
                session.consume(line)
    except (OSError, ValueError):
        pass


def _watch_login(session):
    reader = threading.Thread(target=_read_instructions, args=(session,), daemon=True)
    reader.start()
    try:
        code = session.process.wait(timeout=_LOGIN_TIMEOUT)
        reader.join(timeout=1)
        if code == 0:
            session.finish("succeeded", "Codex 登录成功。")
        else:
            session.finish("failed", "Codex 登录未完成。请重试，或改用设备验证码登录。")
    except subprocess.TimeoutExpired:
        session.finish("expired", "登录等待已超时，请重新发起登录。")
        _terminate_process(session.process)
    except OSError:
        session.finish("failed", "Codex 登录进程已结束，请重试。")


def start_codex_login(mode: str = "browser", configured_bin: str | None = None) -> dict:
    """Start a hidden CLI login; its browser/device approval remains user-driven."""
    if mode not in {"browser", "device"}:
        return _result("failed", "不支持的登录方式。")
    binary = _cli(configured_bin)
    if not binary:
        return _result("unavailable", "未找到 Codex CLI，请检查安装或 CODEX_BIN。")
    with _sessions_lock:
        for key, session in list(_sessions.items()):
            if (
                session.status in _TERMINAL
                and time.monotonic() - session.started > _LOGIN_TIMEOUT
            ):
                del _sessions[key]
            elif session.status not in _TERMINAL:
                return _result("busy", "已有 Codex 登录正在进行，请先完成或取消。")
        command = [binary, "login"]
        if mode == "device":
            command.append("--device-auth")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_window_kwargs(),
            )
        except OSError:
            return _result("failed", "无法启动 Codex 登录，请检查 CLI 安装。")
        session = _Login(uuid.uuid4().hex, mode, process)
        if mode == "device":
            session.message = "正在获取设备验证码…"
        _sessions[session.login_id] = session
        threading.Thread(target=_watch_login, args=(session,), daemon=True).start()
        return session.snapshot()


def poll_codex_login(login_id: str | None) -> dict:
    """Poll the opaque login handle returned by start_codex_login."""
    with _sessions_lock:
        session = _sessions.get(login_id or "")
    if not session:
        return _result(
            "idle", "请点击登录 Codex。", login_id="", auth_url="", user_code=""
        )
    return session.snapshot()


def cancel_codex_login(login_id: str | None) -> dict:
    with _sessions_lock:
        session = _sessions.get(login_id or "")
    if not session:
        return poll_codex_login(login_id)
    session.finish("cancelled", "已取消本次 Codex 登录。")
    _terminate_process(session.process)
    return session.snapshot()


def _close_logins():
    with _sessions_lock:
        sessions = list(_sessions.values())
    for session in sessions:
        if session.status not in _TERMINAL:
            cancel_codex_login(session.login_id)


atexit.register(_close_logins)
