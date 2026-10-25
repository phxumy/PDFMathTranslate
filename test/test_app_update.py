import hashlib
from unittest.mock import Mock, patch

import pytest

from pdf2zh.app_update import (
    CHECKSUM_NAME,
    INSTALLER_NAME,
    REPOSITORY_URL,
    Release,
    UpdateError,
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
