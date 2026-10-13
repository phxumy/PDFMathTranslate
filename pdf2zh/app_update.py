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
from threading import Lock
from typing import Callable
from urllib.parse import urlparse

import requests

from pdf2zh.version import APP_VERSION, REPOSITORY, REPOSITORY_URL

INSTALLER_NAME = "PDFMathTranslate-Codex-Setup-win64.exe"
CHECKSUM_NAME = "SHA256SUMS.txt"
MAX_INSTALLER_BYTES = 2 * 1024**3
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)-codex\.(\d+)$")


class UpdateError(RuntimeError):
    """An update was refused or could not be verified."""


def version_key(version: str) -> tuple[int, ...]:
    match = _VERSION.fullmatch(version)
    if not match:
        raise UpdateError("发行版本号格式不受支持。")
    return tuple(map(int, match.groups()))


def _release_url(url: str, tag: str, name: str) -> bool:
    return url == f"{REPOSITORY_URL}/releases/download/{tag}/{name}"


def _verified_response(url: str, *, stream: bool = False):
    """Follow HTTPS redirects only to GitHub's release download infrastructure."""
    allowed = {
        "api.github.com",
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    }
    for _ in range(6):
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
            timeout=(15, 60),
            allow_redirects=False,
            stream=stream,
        )
        if response.is_redirect:
            url = response.headers.get("Location", "")
            response.close()
            continue
        response.raise_for_status()
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

    def status(self) -> str:
        with self._lock:
            return self._status

    def _report(self, message: str, callback=None):
        with self._lock:
            self._status = message
        if callback:
            callback(message)

    def check(self) -> dict:
        """Check only; never download or launch an installer automatically."""
        with self._lock:
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

        launched = False
        try:
            self._report("正在下载 SHA-256 校验文件…", progress_callback)
            with _verified_response(release.checksum_url) as response:
                if len(response.content) > 1024 * 1024:
                    raise UpdateError("更新校验文件大小异常。")
                expected = _checksum(response.text)
            if release.digest and release.digest != f"sha256:{expected}":
                raise UpdateError("GitHub 记录的附件摘要与校验文件不一致。")

            cache = Path(tempfile.mkdtemp(prefix="PDFMathTranslate-update-"))
            partial = cache / (INSTALLER_NAME + ".part")
            installer = cache / INSTALLER_NAME
            digest = hashlib.sha256()
            received = 0
            last_percent = -1
            try:
                with _verified_response(release.installer_url, stream=True) as response:
                    with partial.open("xb") as target:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
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
                if received != release.size or digest.hexdigest() != expected:
                    raise UpdateError("安装程序 SHA-256 或大小校验失败，未执行更新。")
                partial.rename(installer)
            finally:
                partial.unlink(missing_ok=True)
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
        except UpdateError:
            raise
        except (requests.RequestException, OSError) as exc:
            raise UpdateError("更新下载或启动失败，当前程序未被替换，请重试。") from exc
        finally:
            with self._lock:
                self._installing = launched


update_manager = UpdateManager()
