import hashlib
import threading
import time
from unittest.mock import Mock, patch

import pytest

from pdf2zh.app_update import (
    CHECKSUM_NAME,
    INSTALLER_NAME,
    REPOSITORY_URL,
    Release,
    UpdateError,
    UpdateCancelled,
    UpdateManager,
    _parse_release,
    _verified_response,
    version_key,
)


def release_data(tag="v1.9.11-codex.6"):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": name,
                "size": 7,
                "browser_download_url": f"{REPOSITORY_URL}/releases/download/{tag}/{name}",
            }
            for name in (INSTALLER_NAME, CHECKSUM_NAME)
        ],
    }


class Response:
    def __init__(self, content=b"", data=None):
        self.content = content
        self.text = content.decode()
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def close(self):
        pass

    def json(self):
        return self.data

    def iter_content(self, chunk_size):
        yield self.content


def test_version_order_is_numeric_and_rejects_other_projects():
    assert version_key("v1.9.11-codex.10") > version_key("v1.9.11-codex.9")
    assert version_key("v1.9.11-codex.5.1") > version_key("v1.9.11-codex.5")
    assert version_key("v1.9.11-codex.5.10") > version_key("v1.9.11-codex.5.9")
    assert version_key("v1.9.11-codex.6") > version_key("v1.9.11-codex.5.99")
    assert version_key("v1.9.11-codex.5.0") == version_key("v1.9.11-codex.5")
    with pytest.raises(UpdateError):
        version_key("../../other.exe")
    with pytest.raises(UpdateError):
        version_key("v1.9.11-codex.5.1.2")


@pytest.mark.parametrize("base", ["v1.9.11-codex.5", "v1.9.11-codex.6"])
def test_patch_release_is_detected_from_a_base_release_without_downloading(base):
    manager = UpdateManager()
    latest = base + ".1"
    with (
        patch("pdf2zh.app_update.APP_VERSION", base),
        patch(
            "pdf2zh.app_update._verified_response",
            return_value=Response(data=release_data(latest)),
        ) as get,
        patch("pdf2zh.app_update.subprocess.Popen") as popen,
    ):
        result = manager.check()
    assert result["available"] is True
    assert result["latest_version"] == latest
    get.assert_called_once()
    popen.assert_not_called()


def test_release_requires_installer_and_checksums_from_exact_repository():
    data = release_data()
    assert _parse_release(data).tag == "v1.9.11-codex.6"
    data["assets"][0]["browser_download_url"] = "https://github.com/other/setup.exe"
    with pytest.raises(UpdateError, match="仓库"):
        _parse_release(data)
    data["assets"] = []
    with pytest.raises(UpdateError, match="完整"):
        _parse_release(data)


def test_external_redirect_is_rejected_before_request():
    response = Mock(is_redirect=True, headers={"Location": "https://example.com/x"})
    with patch("pdf2zh.app_update.requests.get", return_value=response) as get:
        with pytest.raises(UpdateError, match="不受信任"):
            _verified_response("https://github.com/test")
    assert get.call_count == 1


def test_check_never_downloads_or_launches_installer():
    manager = UpdateManager()
    with (
        patch("pdf2zh.app_update.APP_VERSION", "v1.9.11-codex.5.4"),
        patch(
            "pdf2zh.app_update._verified_response",
            return_value=Response(data=release_data()),
        ) as get,
        patch("pdf2zh.app_update.subprocess.Popen") as popen,
    ):
        assert manager.check()["available"] is True
    assert get.call_count == 1
    popen.assert_not_called()


def test_old_release_without_installer_is_already_current():
    manager = UpdateManager()
    data = release_data("v1.9.11-codex.4")
    data["assets"] = []
    with patch(
        "pdf2zh.app_update._verified_response", return_value=Response(data=data)
    ):
        assert manager.check()["available"] is False


def test_translation_blocks_install_before_any_network_access():
    manager = UpdateManager()
    manager.begin_translation()
    with patch("pdf2zh.app_update._verified_response") as get:
        with pytest.raises(UpdateError, match="翻译正在进行"):
            manager.install()
    get.assert_not_called()
    manager.end_translation()


@pytest.mark.parametrize("valid", [True, False])
def test_only_verified_installer_launches_and_closes_desktop(tmp_path, valid):
    content = b"testexe"
    digest = hashlib.sha256(content if valid else b"tampered").hexdigest()
    manager = UpdateManager()
    close = Mock()
    manager.set_desktop_close(close)
    manager._release = Release(
        "v1.9.11-codex.6", "installer", "checksums", len(content)
    )
    callbacks = []
    with (
        patch("pdf2zh.app_update.sys.platform", "win32"),
        patch("pdf2zh.app_update.tempfile.mkdtemp", return_value=str(tmp_path)),
        patch(
            "pdf2zh.app_update._verified_response",
            side_effect=[
                Response(f"{digest}  {INSTALLER_NAME}".encode()),
                Response(content),
            ],
        ),
        patch("pdf2zh.app_update.subprocess.Popen") as popen,
    ):
        if valid:
            assert "已通过校验" in manager.install(callbacks.append)
            assert popen.call_args.args[0][0] == str(tmp_path / INSTALLER_NAME)
            assert "/RELAUNCH=1" in popen.call_args.args[0]
            close.assert_called_once()
            assert any("100%" in message for message in callbacks)
            with pytest.raises(UpdateError, match="正在下载"):
                manager.begin_translation()
        else:
            with pytest.raises(UpdateError, match="校验失败"):
                manager.install()
            popen.assert_not_called()
            close.assert_not_called()
            assert not manager._installing
            assert not (tmp_path / INSTALLER_NAME).exists()
    assert not (tmp_path / (INSTALLER_NAME + ".part")).exists()


@pytest.mark.parametrize("cancel_at", ["checksum", "progress", "complete"])
def test_cancel_never_launches_and_allows_retry(tmp_path, cancel_at):
    content = b"safe-test-installer"
    checksum = hashlib.sha256(content).hexdigest()
    manager = UpdateManager()
    close = Mock()
    manager.set_desktop_close(close)
    manager._release = Release(
        "v1.9.11-codex.8", "installer", "checksums", len(content)
    )
    cache = tmp_path / "download"
    cache.mkdir()

    class Chunks(Response):
        def iter_content(self, chunk_size):
            yield self.content[:4]
            yield self.content[4:]

    def progress(message):
        if (
            cancel_at == "checksum"
            and "校验文件" in message
            or cancel_at == "progress"
            and "MB)" in message
            or cancel_at == "complete"
            and "100%" in message
        ):
            assert "取消" in manager.cancel_download()

    with (
        patch("pdf2zh.app_update.sys.platform", "win32"),
        patch("pdf2zh.app_update.tempfile.mkdtemp", return_value=str(cache)),
        patch(
            "pdf2zh.app_update._verified_response",
            side_effect=[
                Response(f"{checksum}  {INSTALLER_NAME}".encode()),
                Chunks(content),
            ],
        ),
        patch("pdf2zh.app_update.subprocess.Popen") as popen,
    ):
        result = manager.install(progress)
    assert "已取消" in result and "未启动安装" in result
    assert not cache.exists() or not list(cache.iterdir())
    assert manager.download_state() == {
        "busy": False,
        "cancellable": False,
        "available": True,
    }
    popen.assert_not_called()
    close.assert_not_called()
    manager.begin_translation()
    manager.end_translation()

    cache.mkdir(exist_ok=True)
    with (
        patch("pdf2zh.app_update.sys.platform", "win32"),
        patch("pdf2zh.app_update.tempfile.mkdtemp", return_value=str(cache)),
        patch(
            "pdf2zh.app_update._verified_response",
            side_effect=[
                Response(f"{checksum}  {INSTALLER_NAME}".encode()),
                Response(content),
            ],
        ),
        patch("pdf2zh.app_update.subprocess.Popen") as popen,
    ):
        assert "已通过校验" in manager.install()
    popen.assert_called_once()
    close.assert_called_once()


def test_cancel_interrupts_a_waiting_response_without_blocking_ui(tmp_path):
    import requests

    manager = UpdateManager()
    manager.set_desktop_close(Mock())
    content = b"test-installer"
    digest = hashlib.sha256(content).hexdigest()
    manager._release = Release(
        "v1.9.11-codex.8", "installer", "checksums", len(content)
    )
    streaming = threading.Event()
    closed = threading.Event()
    cache = tmp_path / "waiting-download"
    cache.mkdir()

    class WaitingResponse(Response):
        def iter_content(self, chunk_size):
            yield self.content[:1]
            streaming.set()
            assert closed.wait(5)
            raise requests.ConnectionError("closed during download")

        def close(self):
            closed.set()

    results = []
    with (
        patch("pdf2zh.app_update.sys.platform", "win32"),
        patch("pdf2zh.app_update.tempfile.mkdtemp", return_value=str(cache)),
        patch(
            "pdf2zh.app_update._verified_response",
            side_effect=[
                Response(f"{digest}  {INSTALLER_NAME}".encode()),
                WaitingResponse(content),
            ],
        ),
        patch("pdf2zh.app_update.subprocess.Popen") as popen,
    ):
        worker = threading.Thread(target=lambda: results.append(manager.install()))
        worker.start()
        try:
            assert streaming.wait(5)
            assert manager.download_state()["cancellable"]
            start = time.monotonic()
            assert "正在取消" in manager.cancel_download()
            assert time.monotonic() - start < 0.5
        finally:
            closed.set()
            worker.join(5)
        assert not worker.is_alive()
    assert "已取消" in results[0]
    assert not cache.exists()
    popen.assert_not_called()
    manager._close_desktop.assert_not_called()


def test_cancel_before_connect_and_between_redirects_avoids_next_request():
    cancelled = threading.Event()
    cancelled.set()
    with patch("pdf2zh.app_update.requests.get") as get:
        with pytest.raises(UpdateCancelled):
            _verified_response("https://github.com/file", cancel_event=cancelled)
    get.assert_not_called()
    cancelled.clear()
    response = Mock(
        is_redirect=True,
        headers={"Location": "https://objects.githubusercontent.com/file"},
    )

    def first_request(*args, **kwargs):
        cancelled.set()
        return response

    with patch("pdf2zh.app_update.requests.get", side_effect=first_request) as get:
        with pytest.raises(UpdateCancelled):
            _verified_response("https://github.com/file", cancel_event=cancelled)
    assert get.call_count == 1
    response.close.assert_called_once()


def test_cancel_after_handoff_does_not_claim_installation_was_cancelled(tmp_path):
    manager = UpdateManager()
    manager.set_desktop_close(Mock())
    payload = b"installer"
    manager._release = Release(
        "v1.9.11-codex.8", "installer", "checksums", len(payload)
    )
    messages = []

    def progress(message):
        if "正在启动安装程序" in message:
            assert not manager.download_state()["cancellable"]
            messages.append(manager.cancel_download())

    with (
        patch("pdf2zh.app_update.sys.platform", "win32"),
        patch("pdf2zh.app_update.tempfile.mkdtemp", return_value=str(tmp_path)),
        patch(
            "pdf2zh.app_update._verified_response",
            side_effect=[
                Response(
                    f"{hashlib.sha256(payload).hexdigest()}  {INSTALLER_NAME}".encode()
                ),
                Response(payload),
            ],
        ),
        patch("pdf2zh.app_update.subprocess.Popen") as popen,
    ):
        manager.install(progress)
    assert "下载阶段已结束" in messages[0]
    popen.assert_called_once()


def test_check_during_download_does_not_clear_retry_release():
    manager = UpdateManager()
    release = Release("v1.9.11-codex.8", "installer", "checksums", 5)
    manager._release = release
    manager._installing = True
    with patch("pdf2zh.app_update._verified_response") as get:
        with pytest.raises(UpdateError, match="更新正在进行"):
            manager.check()
    assert manager._release == release
    get.assert_not_called()


def test_cancel_with_real_stalled_http_stream(tmp_path):
    """Exercise requests/urllib3 closure, not just a fake response iterator."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import requests

    content = b"x" * (128 * 1024)
    checksum = f"{hashlib.sha256(content).hexdigest()}  {INSTALLER_NAME}".encode()
    waiting = threading.Event()
    consumed = threading.Event()
    release_server = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            data = checksum if self.path == "/checksums" else content
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.path == "/checksums":
                self.wfile.write(data)
            else:
                self.wfile.write(data[: 64 * 1024])
                self.wfile.flush()
                waiting.set()
                release_server.wait(10)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    real_get = requests.get

    def local_transport(url, **kwargs):
        return real_get(
            f"http://127.0.0.1:{server.server_port}/" + url.rsplit("/", 1)[-1], **kwargs
        )

    manager = UpdateManager()
    manager.set_desktop_close(Mock())
    manager._release = Release(
        "v1.9.11-codex.8",
        "https://github.com/installer",
        "https://github.com/checksums",
        len(content),
    )
    results, errors = [], []

    def install():
        try:
            results.append(
                manager.install(
                    lambda message: consumed.set() if "50%" in message else None
                )
            )
        except Exception as error:
            errors.append(error)

    try:
        with (
            patch("pdf2zh.app_update.sys.platform", "win32"),
            patch("pdf2zh.app_update.tempfile.mkdtemp", return_value=str(tmp_path)),
            patch("pdf2zh.app_update.requests.get", side_effect=local_transport),
            patch("pdf2zh.app_update.subprocess.Popen") as popen,
        ):
            worker = threading.Thread(target=install, daemon=True)
            worker.start()
            assert waiting.wait(5)
            assert consumed.wait(5)
            time.sleep(0.05)  # Let iter_content enter the next, stalled read.
            started = time.monotonic()
            manager.cancel_download()
            assert time.monotonic() - started < 0.5
            worker.join(7)
            assert not worker.is_alive()
            assert not errors
            assert "已取消" in results[0]
            popen.assert_not_called()
            manager._close_desktop.assert_not_called()
            assert not (tmp_path / (INSTALLER_NAME + ".part")).exists()
    finally:
        release_server.set()
        server.shutdown()
        server.server_close()
        serving.join(2)
