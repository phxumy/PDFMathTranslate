"""Local Codex account controls backed exclusively by the official CLI.

Credentials remain in the CLI's credential store. Only recognized login state,
official authorization URLs, and short-lived device codes leave this module;
raw CLI output is neither returned nor logged.
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass, field
from datetime import datetime
import math
import os
import re
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlsplit
import uuid

from pdf2zh.codex_rpc import CodexAppServer, CodexRpcError
from pdf2zh.codex_runtime import CodexRuntime

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


def _quota_lines(result):
    """Use only documented usage fields; never expose raw RPC results."""
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        buckets = {"codex": result.get("rateLimits")}
    lines = []
    for key, bucket in list(buckets.items())[:8]:
        if not isinstance(bucket, dict):
            continue
        label = bucket.get("limitName") or key
        if not isinstance(label, str) or not re.fullmatch(r"[\w .:/-]{1,80}", label):
            label = "Codex"
        for field_name, fallback in (("primary", "主窗口"), ("secondary", "次窗口")):
            window = bucket.get(field_name)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            if type(used) not in (int, float) or not math.isfinite(used):
                continue
            duration = window.get("windowDurationMins")
            period = fallback
            if type(duration) in (int, float) and 0 < duration < 527040:
                if duration % 1440 == 0:
                    period = f"{duration / 1440:g} 天"
                elif duration % 60 == 0:
                    period = f"{duration / 60:g} 小时"
                else:
                    period = f"{duration:g} 分钟"
            line = f"{label} · {period}额度剩余 {max(0, min(100, 100 - used)):g}%"
            reset = window.get("resetsAt")
            if type(reset) in (int, float) and math.isfinite(reset):
                try:
                    when = datetime.fromtimestamp(reset).astimezone()
                    line += f" · 重置 {when:%m-%d %H:%M %z}"
                except (ValueError, OSError, OverflowError):
                    pass
            lines.append(line)
        credits = bucket.get("credits")
        if isinstance(credits, dict) and credits.get("hasCredits") is True:
            if credits.get("unlimited") is True:
                lines.append(f"{label} · 额外额度：无限制")
            elif re.fullmatch(r"\d+(?:\.\d+)?", str(credits.get("balance", ""))):
                lines.append(f"{label} · 额外额度余额：{credits['balance']}")
    return lines


def _account_details(runtime, profile):
    details = {}
    with CodexAppServer(runtime, profile) as server:
        response = server.request("account/read", {"refreshToken": False})
        current = response.get("account")
        if not isinstance(current, dict):
            return details
        if current.get("type") == "apiKey":
            return {"details_message": "API Key 登录；官方接口不提供此方式的账户余额。"}
        if current.get("type") != "chatgpt":
            return details
        email = current.get("email")
        if isinstance(email, str) and re.fullmatch(
            r"[^\s@]{1,128}@[^\s@]{1,190}", email
        ):
            details["email"] = email
        plan = current.get("planType")
        if isinstance(plan, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", plan):
            details["plan"] = plan
        try:
            details["quota_lines"] = _quota_lines(
                server.request("account/rateLimits/read")
            )
        except (OSError, ValueError, TimeoutError, CodexRpcError):
            details["quota_lines"] = []
        return details


def get_codex_status(configured_bin: str | None = None, profile=None) -> dict:
    """Query the selected CLI; credential files are never opened by the app."""

    binary = _cli(configured_bin)
    if not binary:
        return _result(
            "unavailable", "未找到 Codex CLI，请检查安装或 CODEX_BIN。", logged_in=False
        )
    runtime = CodexRuntime.for_binary(binary)
    scope = runtime.scope_message
    profile_args = ["--profile", str(profile)] if profile else []
    try:
        completed = subprocess.run(
            runtime.command(*profile_args, "login", "status"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            **runtime.subprocess_kwargs(account_operation=True),
            **_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return _result(
            "timeout", scope + "\nCodex 登录状态查询超时，请重试。", logged_in=False
        )
    except OSError:
        return _result(
            "error", scope + "\n无法启动 Codex CLI，请检查安装。", logged_in=False
        )

    output = (completed.stdout + "\n" + completed.stderr).casefold()
    if completed.returncode == 0:
        method = (
            "api_key"
            if "api key" in output or "api-key" in output
            else ("chatgpt" if "chatgpt" in output else "unknown")
        )
        label = {"api_key": "API Key", "chatgpt": "ChatGPT", "unknown": "Codex"}[method]
        try:
            details = _account_details(runtime, profile)
        except (OSError, ValueError, TypeError, TimeoutError, CodexRpcError):
            details = {}
        lines = [scope, f"已登录 Codex（{label}）。"]
        if details.get("email"):
            lines.append("账号：" + details["email"])
        if details.get("plan"):
            lines.append("套餐：" + details["plan"])
        if details.get("details_message"):
            lines.append(details["details_message"])
        else:
            if not details.get("email"):
                lines.append("账号信息暂不可用。")
            lines.extend(
                details.get("quota_lines") or ["剩余额度暂不可用，请刷新重试。"]
            )
        lines.append(
            "查询时间：" + datetime.now().astimezone().strftime("%m-%d %H:%M:%S %z")
        )
        return _result(
            "authenticated",
            "\n".join(lines),
            logged_in=True,
            auth_method=method,
            isolated=runtime.isolated,
            **details,
        )
    if "not logged in" in output or "not authenticated" in output:
        return _result(
            "not_logged_in",
            scope
            + "\n尚未登录 Codex。"
            + ("首次使用独立账号需要在本软件中登录。" if runtime.isolated else ""),
            logged_in=False,
            isolated=runtime.isolated,
        )
    return _result(
        "error",
        scope + "\n无法读取 Codex 登录状态，请检查 CLI 配置后重试。",
        logged_in=False,
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
    binary: str | None = None
    profile: str | None = None
    scope: str = ""
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
                (self.scope + "\n" if self.scope else "") + self.message,
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


def start_codex_login(
    mode: str = "browser", configured_bin: str | None = None, profile=None
) -> dict:
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
        runtime = CodexRuntime.for_binary(binary)
        profile_args = ["--profile", str(profile)] if profile else []
        command = runtime.command(*profile_args, "login")
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
                **runtime.subprocess_kwargs(account_operation=True),
                **_window_kwargs(),
            )
        except OSError:
            return _result("failed", "无法启动 Codex 登录，请检查 CLI 安装。")
        session = _Login(
            uuid.uuid4().hex,
            mode,
            process,
            binary=binary,
            profile=profile,
            scope=runtime.scope_message,
        )
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


def get_login_account_status(login_id):
    """Refresh the exact CLI used to log in, even if UI settings have changed."""
    with _sessions_lock:
        session = _sessions.get(login_id or "")
    if session is None or session.status != "succeeded":
        return None
    return get_codex_status(session.binary, session.profile)


def logout_codex(configured_bin=None, profile=None):
    binary = _cli(configured_bin)
    if not binary:
        return _result("unavailable", "未找到 Codex CLI，请检查安装或 CODEX_BIN。")
    runtime = CodexRuntime.for_binary(binary)
    with _sessions_lock:
        if any(s.status not in _TERMINAL for s in _sessions.values()):
            return _result("busy", "请先完成或取消当前登录。")
        try:
            args = ["--profile", str(profile)] if profile else []
            result = subprocess.run(
                runtime.command(*args, "logout"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
                **runtime.subprocess_kwargs(account_operation=True),
                **_window_kwargs(),
            )
            if result.returncode == 0:
                return _result(
                    "not_logged_in",
                    runtime.scope_message + "\n已退出当前账号，可以登录其他账号。",
                    logged_in=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return _result(
        "error", runtime.scope_message + "\n退出未完成，请刷新账号状态后重试。"
    )


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
