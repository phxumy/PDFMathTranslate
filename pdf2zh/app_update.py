"""Explicit, verified updates from this fork's public GitHub releases."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
from typing import Callable
from urllib.parse import urlparse

import requests

from pdf2zh.version import APP_VERSION, REPOSITORY, REPOSITORY_URL

INSTALLER_NAME = "PDFMathTranslate-Codex-Setup-win64.exe"
CHECKSUM_NAME = "SHA256SUMS.txt"
MAX_INSTALLER_BYTES = 2 * 1024**3
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)-codex\.(\d+)(?:\.(\d+))?$")


class UpdateError(RuntimeError):
    """An update was refused or could not be verified."""


class UpdateCancelled(UpdateError):
    """The user cancelled before the installer handoff."""


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelled("更新下载已取消。")


def version_key(version: str) -> tuple[int, ...]:
    match = _VERSION.fullmatch(version)
    if not match:
        raise UpdateError("发行版本号格式不受支持。")
    return tuple(int(part or 0) for part in match.groups())


def _release_url(url: str, tag: str, name: str) -> bool:
    return url == f"{REPOSITORY_URL}/releases/download/{tag}/{name}"


def _verified_response(url: str, *, stream: bool = False, cancel_event=None):
    """Follow HTTPS redirects only to GitHub's release download infrastructure."""
    allowed = {
        "api.github.com",
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
    for _ in range(6):
        _check_cancelled(cancel_event)
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise UpdateError("更新服务器返回了不受信任的下载地址。")
        response = requests.get(
            url,
            headers={"User-Agent": f"PDFMathTranslate/{APP_VERSION}"},
            timeout=(10, 5) if cancel_event is not None else (15, 60),
            allow_redirects=False,
            stream=stream,
        )
        if cancel_event is not None and cancel_event.is_set():
            response.close()
            raise UpdateCancelled("更新下载已取消。")
        if response.is_redirect:
            url = response.headers.get("Location", "")
            response.close()
            continue
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        return response
    raise UpdateError("更新下载重定向次数过多。")


@dataclass(frozen=True)
class Release:
    tag: str
    installer_url: str
    checksum_url: str
    size: int
    digest: str | None = None


def _parse_release(data: dict) -> Release:
    tag = data.get("tag_name", "")
    version_key(tag)
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("该发行版尚未正式发布。")
    assets = {asset.get("name"): asset for asset in data.get("assets", [])}
    installer, checksum = assets.get(INSTALLER_NAME), assets.get(CHECKSUM_NAME)
    if not installer or not checksum:
        raise UpdateError("该版本尚未提供完整的 EXE 安装程序与校验文件，请稍后重试。")
    for asset, name in ((installer, INSTALLER_NAME), (checksum, CHECKSUM_NAME)):
        if not _release_url(asset.get("browser_download_url", ""), tag, name):
            raise UpdateError("发行附件地址与本项目仓库不一致，已停止更新。")
    size = installer.get("size", 0)
    if not isinstance(size, int) or not 0 < size < MAX_INSTALLER_BYTES:
        raise UpdateError("安装程序大小无效。")
    return Release(
        tag,
        installer["browser_download_url"],
        checksum["browser_download_url"],
        size,
        installer.get("digest"),
    )


def _checksum(text: str) -> str:
    matches = []
    for line in text.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2) == INSTALLER_NAME:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise UpdateError("校验文件缺少唯一的安装程序 SHA-256。")
    return matches[0]


class UpdateManager:
    def __init__(self):
        self._lock = Lock()
        self._translations = 0
        self._installing = False
        self._download_cancel: Event | None = None
        self._download_response = None
        self._release: Release | None = None
        self._close_desktop: Callable[[], None] | None = None
        self._status = "尚未检查更新。"

    def set_desktop_close(self, callback: Callable[[], None] | None):
        with self._lock:
            self._close_desktop = callback

    def begin_translation(self):
        with self._lock:
            if self._installing:
                raise UpdateError("正在下载并安装更新，请稍后再开始翻译。")
            self._translations += 1

    def end_translation(self):
        with self._lock:
            self._translations = max(0, self._translations - 1)

    def maintain_storage(self, action):
        """Serialize deletion with translation and installation startup."""
        with self._lock:
            if self._translations or self._installing:
                raise UpdateError("翻译或更新正在进行，请结束后再清理本地文件或缓存。")
            return action()

    def status(self) -> str:
        with self._lock:
            return self._status

    def download_state(self) -> dict:
        with self._lock:
            return {
                "busy": self._installing,
                "cancellable": self._download_cancel is not None
                and not self._download_cancel.is_set(),
                "available": self._release is not None,
            }

    def cancel_download(self) -> str:
        """Signal cancellation without waiting on a blocked network reader."""
        with self._lock:
            if self._download_cancel is None:
                return (
                    "安装程序即将或已经启动，下载阶段已结束。"
                    if self._installing
                    else self._status
                )
            if self._download_cancel.is_set():
                return self._status
            self._download_cancel.set()
            self._status = "正在取消更新下载…"
            response = self._download_response
            message = self._status
        if response is not None:

            def close_response():
                try:
                    response.close()
                except (OSError, ValueError, requests.RequestException):
                    pass

            Thread(
                target=close_response, name="pdf2zh-update-cancel", daemon=True
            ).start()
        return message

    def _report(self, message: str, callback=None):
        with self._lock:
            if self._download_cancel is not None and self._download_cancel.is_set():
                message = "正在取消更新下载…"
            self._status = message
        if callback:
            callback(message)

    def check(self) -> dict:
        """Check only; never download or launch an installer automatically."""
        with self._lock:
            if self._installing:
                raise UpdateError("更新正在进行，请完成或取消下载后再检查更新。")
            self._release = None
        try:
            with _verified_response(
                f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
            ) as response:
                data = response.json()
            tag = data.get("tag_name", "")
            newer = version_key(tag) > version_key(APP_VERSION)
            release = _parse_release(data) if newer else None
            with self._lock:
                self._release = release
            message = (
                f"发现新版本 {tag}。点击下载并安装后，工作台将关闭并完成升级。"
                if newer
                else f"当前版本 {APP_VERSION} 已是最新版本。"
            )
            self._report(message)
            return {
                "current_version": APP_VERSION,
                "latest_version": tag,
                "available": newer,
                "release_url": f"{REPOSITORY_URL}/releases/tag/{tag}",
                "message": message,
            }
        except UpdateError:
            raise
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise UpdateError("无法检查 GitHub 更新，请检查网络连接后重试。") from exc

    def install(self, progress_callback: Callable[[str], None] | None = None) -> str:
        """Download, verify and launch after the user explicitly clicks Install."""
        with self._lock:
            if self._translations:
                raise UpdateError("翻译正在进行，请等待完成或取消翻译后再安装更新。")
            if self._installing:
                raise UpdateError("更新正在进行，请勿重复点击。")
            if sys.platform != "win32" or self._close_desktop is None:
                raise UpdateError(
                    "应用内安装更新需要 Windows 桌面窗口，请前往发行页面下载安装。"
                )
            if self._release is None:
                raise UpdateError("请先检查更新，确认有可用的新版本。")
            release = self._release
            close_desktop = self._close_desktop
            self._installing = True
            cancel_event = Event()
            self._download_cancel = cancel_event

        launched = False
        cache = None
        try:
            _check_cancelled(cancel_event)
            self._report("正在下载 SHA-256 校验文件…", progress_callback)
            with _verified_response(
                release.checksum_url, cancel_event=cancel_event
            ) as response:
                _check_cancelled(cancel_event)
                if len(response.content) > 1024 * 1024:
                    raise UpdateError("更新校验文件大小异常。")
                expected = _checksum(response.text)
            if release.digest and release.digest != f"sha256:{expected}":
                raise UpdateError("GitHub 记录的附件摘要与校验文件不一致。")
            _check_cancelled(cancel_event)

            cache = Path(tempfile.mkdtemp(prefix="PDFMathTranslate-update-"))
            partial = cache / (INSTALLER_NAME + ".part")
            installer = cache / INSTALLER_NAME
            digest = hashlib.sha256()
            received = 0
            last_percent = -1
            try:
                with _verified_response(
                    release.installer_url, stream=True, cancel_event=cancel_event
                ) as response:
                    with self._lock:
                        self._download_response = response
                    _check_cancelled(cancel_event)
                    with partial.open("xb") as target:
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            _check_cancelled(cancel_event)
                            if not chunk:
                                continue
                            received += len(chunk)
                            if received > release.size:
                                raise UpdateError(
                                    "下载大小超过发行附件记录，已停止更新。"
                                )
                            target.write(chunk)
                            digest.update(chunk)
                            percent = int(received * 100 / release.size)
                            if percent != last_percent:
                                self._report(
                                    f"正在下载 {release.tag}：{percent}% "
                                    f"({received / 1024**2:.0f}/{release.size / 1024**2:.0f} MB)",
                                    progress_callback,
                                )
                                last_percent = percent
                _check_cancelled(cancel_event)
                if received != release.size or digest.hexdigest() != expected:
                    raise UpdateError("安装程序 SHA-256 或大小校验失败，未执行更新。")
                partial.rename(installer)
            finally:
                partial.unlink(missing_ok=True)
            # Serialize the last cancellation check with committing the handoff.
            # After this point a cancellation request must not claim success.
            with self._lock:
                _check_cancelled(cancel_event)
                self._download_cancel = None
                self._download_response = None
            self._report("下载及校验完成，正在启动安装程序…", progress_callback)
            command = [
                str(installer),
                "/SILENT",
                "/NORESTART",
                "/SP-",
                "/RELAUNCH=1",
                f"/WAITFORPID={os.getpid()}",
            ]
            launcher = Path(os.environ.get("PYSTAND", sys.executable)).resolve()
            if (launcher.parent / "unins000.exe").is_file():
                command.append(f"/DIR={launcher.parent}")
            subprocess.Popen(
                command,
                cwd=str(cache),
                close_fds=True,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            launched = True
            message = f"{release.tag} 已通过校验，正在关闭工作台并启动安装。"
            self._report(message, progress_callback)
            close_desktop()
            return message
        except (UpdateError, requests.RequestException, OSError, ValueError) as exc:
            if not launched and cancel_event.is_set():
                cleaned = self._clean_download(cache)
                with self._lock:
                    self._download_cancel = None
                message = "已取消更新下载，未启动安装，可以重新下载。"
                if not cleaned:
                    message += f" 临时文件未能清理，可手动删除：{cache}"
                self._report(message, progress_callback)
                return message
            if isinstance(exc, UpdateError):
                raise
            raise UpdateError("更新下载或启动失败，当前程序未被替换，请重试。") from exc
        finally:
            if not launched:
                self._clean_download(cache)
            with self._lock:
                self._download_cancel = None
                self._download_response = None
                self._installing = launched

    @staticmethod
    def _clean_download(cache):
        if cache is None:
            return True
        try:
            (cache / (INSTALLER_NAME + ".part")).unlink(missing_ok=True)
            (cache / INSTALLER_NAME).unlink(missing_ok=True)
            if cache.exists():
                cache.rmdir()
            return True
        except OSError:
            return False


update_manager = UpdateManager()
