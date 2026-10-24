import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError, URLError
import zipfile

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nyalume import updater


def release(version="0.6.0"):
    name = f"Nyalume-v{version}-windows-x64.zip"
    return {"tag_name": "v" + version, "body": "修复与更新", "assets": [{
        "name": name, "state": "uploaded", "size": 100,
        "browser_download_url": f"https://github.com/ccliiko/Nyalume/releases/download/v{version}/{name}",
        "digest": "sha256:" + "0" * 64,
    }]}


@pytest.fixture(autouse=True)
def isolated_updater(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_state", {"phase": "idle", "message": ""})
    monkeypatch.setattr(updater, "_release", None)
    monkeypatch.setattr(updater, "_job_result", None)
    monkeypatch.setattr(updater, "__version__", "0.5.1")
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path / "app")
    monkeypatch.setattr(updater, "_can_install", lambda: True)


def test_compare_versions_and_prevent_downgrade(monkeypatch):
    assert updater.version_tuple("v0.10.0") > updater.version_tuple("0.9.9")
    assert updater.version_tuple("v0.45") < updater.version_tuple("0.5.1")
    for version in ("0.5.1", "0.5.0"):
        monkeypatch.setattr(updater, "_latest", lambda: release(version))
        assert not updater.check()["available"]
        with pytest.raises(ValueError):
            updater.install()
    monkeypatch.setattr(updater, "_latest", release)
    state = updater.check()
    assert state["installable"] and state["checked_at"] > 0
    monkeypatch.setattr(updater, "_can_install", lambda: False)
    assert "源码运行" in updater.check()["message"]
    with pytest.raises(ValueError):
        updater.install()


@pytest.mark.parametrize("tag", ["main", "v0.6.0-rc1", "v0.6", "../../0.6.0"])
def test_reject_nonrelease_versions(tag):
    with pytest.raises(ValueError):
        updater.version_tuple(tag)


def test_missing_or_unverified_assets_never_enable_install(monkeypatch):
    data = release()
    data["assets"][0]["digest"] = None
    monkeypatch.setattr(updater, "_latest", lambda: data)
    state = updater.check()
    assert state["available"] and not state["installable"]
    assert "SHA-256" in state["message"]
    data["assets"][0]["digest"] = "sha256:" + "0" * 64
    data["assets"][0]["browser_download_url"] = "https://evil.example/file.zip"
    assert not updater.check()["installable"]
    data["assets"] = []
    assert not updater.check()["installable"]


@pytest.mark.parametrize("error, message", [
    (HTTPError(updater.API_URL, 404, "missing", {}, None), "尚未发布"),
    (HTTPError(updater.API_URL, 403, "limited", {}, None), "请求受限"),
    (URLError("offline"), "检查网络"),
])
def test_network_errors_are_recoverable(monkeypatch, error, message):
    def fail(_url):
        raise error
    monkeypatch.setattr(updater, "_request", fail)
    with pytest.raises(ValueError, match=message):
        updater.check()
    assert updater.snapshot()["phase"] == "error"
    monkeypatch.setattr(updater, "_latest", release)
    assert updater.check()["installable"]


def archive_bytes(extra=None):
    output = io.BytesIO()
    files = {"Nyalume2D.exe": b"new2d", "Nyalume3D.exe": b"new3d", "VERSION.txt": b"0.6.0",
             "_internal/nyalume/data.txt": b"program"}
    files.update(extra or {})
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in files.items():
            archive.writestr("Nyalume-v0.6.0-windows-x64/" + name, data)
    return output.getvalue()


def test_extract_preserves_user_data_and_rejects_traversal(tmp_path):
    archive = tmp_path / "release.zip"
    archive.write_bytes(archive_bytes({"_internal/.env": b"bad", "_internal/agent.db": b"bad",
                                     "models/custom/model.pmx": b"bad", "_internal/workspace/a.txt": b"bad",
                                     "_internal/pet_config.json": b"bad", "update-settings.json": b"bad"}))
    files = updater._extract(archive, tmp_path / "payload", "Nyalume-v0.6.0-windows-x64")
    assert len(files) == 4
    assert not (tmp_path / "payload/_internal/.env").exists()
    assert not (tmp_path / "payload/update-settings.json").exists()
    archive.write_bytes(archive_bytes({"../escaped.txt": b"bad"}))
    with pytest.raises(ValueError, match="不安全"):
        updater._extract(archive, tmp_path / "unsafe", "Nyalume-v0.6.0-windows-x64")
    assert not (tmp_path / "escaped.txt").exists()


def test_prepare_verifies_archive_without_touching_installed_files(monkeypatch, tmp_path):
    root = updater.app_root()
    root.mkdir()
    (root / "Nyalume3D.exe").write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(root / "Nyalume3D.exe"))
    payload = archive_bytes()
    data = release()
    data["assets"][0].update(size=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(updater, "_request", lambda _url: io.BytesIO(payload))
    launched = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kwargs: launched.append(args))
    updater._download_and_prepare(data)
    assert updater.snapshot()["phase"] == "ready"
    assert (root / "Nyalume3D.exe").read_bytes() == b"old"
    plan = json.loads((Path(launched[0][-1]).parent / "plan.json").read_text(encoding="utf-8"))
    assert plan["restart"] == "Nyalume3D.exe"
    assert len(plan["files"]) == 4
    assert Path(launched[0][-1]).read_bytes().startswith(b"\xef\xbb\xbf")  # Windows PowerShell 5.1
    updater._job_result.write_text(json.dumps({"phase": "error", "message": "timeout"}), encoding="utf-8")
    assert updater.snapshot()["phase"] == "error"
    launched.clear()
    data["assets"][0]["digest"] = "sha256:" + "0" * 64
    updater._download_and_prepare(data)
    assert updater.snapshot()["phase"] == "error"
    assert "校验失败" in updater.snapshot()["message"]
    assert not launched and (root / "Nyalume3D.exe").read_bytes() == b"old"


def test_install_starts_only_one_download(monkeypatch):
    monkeypatch.setattr(updater, "_latest", release)
    updater.check()
    calls = []
    class Worker:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def start(self):
            pass
    monkeypatch.setattr(updater.threading, "Thread", Worker)
    assert updater.install()["phase"] == "downloading"
    with pytest.raises(ValueError):
        updater.install()
    with pytest.raises(ValueError):
        updater.check()
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows installer")
@pytest.mark.parametrize("fail_copy", [False, True])
def test_windows_installer_applies_or_rolls_back_and_keeps_data(tmp_path, fail_copy):
    root, stage = tmp_path / "安装目录", tmp_path / "更新目录"
    root.mkdir()
    stage.mkdir()
    (root / "VERSION.txt").write_bytes(b"0.5.1")
    (root / "Nyalume3D.exe").write_bytes(b"old")
    (root / "_internal").mkdir()
    (root / "_internal/.env").write_bytes(b"keep-key")
    (root / "_internal/agent.db").write_bytes(b"keep-db")
    (root / "models/custom").mkdir(parents=True)
    (root / "models/custom/model.pmx").write_bytes(b"keep-model")
    archive = stage / "release.zip"
    archive.write_bytes(archive_bytes())
    files = updater._extract(archive, stage / "payload", "Nyalume-v0.6.0-windows-x64")
    if fail_copy:
        # A file in place of a parent forces failure AFTER earlier replacements succeeded.
        (root / "_internal/nyalume").write_bytes(b"blocking-file")
    (stage / "plan.json").write_text(json.dumps(dict(root=str(root), stage=str(stage), files=files, restart="")), encoding="utf-8")
    helper = stage / "install.ps1"
    helper.write_text(Path(updater.__file__).with_name("update_windows.ps1").read_text(encoding="utf-8"), encoding="utf-8-sig")
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                               capture_output=True, timeout=30)
    assert completed.returncode == (1 if fail_copy else 0), completed.stderr
    result = json.loads((stage / "result.json").read_text(encoding="utf-8-sig"))
    assert result["phase"] == ("error" if fail_copy else "installed")
    assert (root / "Nyalume3D.exe").read_bytes() == (b"old" if fail_copy else b"new3d")
    assert (root / "VERSION.txt").read_bytes() == (b"0.5.1" if fail_copy else b"0.6.0")
    assert (root / "_internal/.env").read_bytes() == b"keep-key"
    assert (root / "_internal/agent.db").read_bytes() == b"keep-db"
    assert (root / "models/custom/model.pmx").read_bytes() == b"keep-model"
    if fail_copy:
        assert not (root / "Nyalume2D.exe").exists()


def test_update_api_requires_local_same_origin_requests(monkeypatch):
    from nyalume.frontends.web import server
    monkeypatch.setattr(updater, "_latest", release)
    def request(host="127.0.0.1", peer="127.0.0.1", origin="http://127.0.0.1:8000", header=True):
        headers = [(b"host", (host + ":8000").encode()), (b"origin", origin.encode())]
        if header:
            headers.append((b"x-nyalume-update", b"1"))
        return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/updates/check",
                        "query_string": b"", "headers": headers, "client": (peer, 1234), "server": (host, 8000)})
    assert server.check_update(request())["available"]
    updater.app_root().mkdir()
    assert server.save_update_source(server.UpdateSourceIn(mode='github'), request())['source']['mode'] == 'github'
    for bad in [request(peer="192.168.0.2"), request(host="evil.example"),
                request(origin="https://evil.example"), request(header=False)]:
        with pytest.raises(HTTPException) as error:
            server.install_update(bad)
        assert error.value.status_code == 403
        with pytest.raises(HTTPException):
            server.save_update_source(server.UpdateSourceIn(mode='auto'), bad)


def test_update_source_persists_validates_and_resets_checked_release(monkeypatch):
    root = updater.app_root()
    root.mkdir()
    assert updater.snapshot()['source']['mode'] == 'auto'
    monkeypatch.setattr(updater, '_latest', release)
    updater.check()
    (root / 'update-result.json').write_text(json.dumps({'message': 'previous update result'}), encoding='utf-8')
    assert '更新源已保存' in updater.save_source('custom', ' https://mirror.example/proxy ')['message']
    assert updater.update_source() == {'mode': 'custom', 'prefix': 'https://mirror.example/proxy/'}
    assert updater.snapshot()['phase'] == 'configured' and updater._release is None
    assert updater._source_urls(updater.API_URL) == ['https://mirror.example/proxy/' + updater.API_URL, updater.API_URL]
    for prefix in ['http://mirror.example/', 'https://user:secret@mirror.example/', 'https://mirror.example/?key=x',
                   'https://mirror.example/#x', 'https://mirror.example/ bad', 'https://mirror.example/\\bad']:
        with pytest.raises(ValueError):
            updater.save_source('custom', prefix)
    assert updater.update_source()['mode'] == 'custom'
    updater._set(phase='downloading')
    with pytest.raises(ValueError, match='等待'):
        updater.save_source('github')
    updater._set(phase='error')
    updater.save_source('github')
    assert updater._source_urls(updater.API_URL) == [updater.API_URL]
    (root / 'update-settings.json').write_text('broken', encoding='utf-8')
    assert updater.update_source()['mode'] == 'auto'


@pytest.mark.parametrize('network_error', ['connection', 'interrupted_stream'])
def test_metadata_uses_official_then_mirror_and_handles_historical_release(monkeypatch, network_error):
    calls = []
    def fetch(url):
        calls.append(url)
        if url == updater.API_URL:
            if network_error == 'interrupted_stream':
                from http.client import IncompleteRead
                raise IncompleteRead(b'{')
            raise URLError('offline')
        return io.BytesIO(json.dumps(release('0.45')).encode())
    monkeypatch.setattr(updater, '_request', fetch)
    state = updater.check()
    assert calls == [updater.API_URL, updater.MIRROR_PREFIX + updater.API_URL]
    assert state['latest_version'] == 'v0.45' and not state['available']
    assert state['metadata_source'] == '镜像加速'
    updater.app_root().mkdir()
    updater.save_source('custom', 'https://mirror.example/')
    calls.clear()
    def invalid_mirror(url):
        calls.append(url)
        return io.BytesIO(b'<html>not JSON</html>' if url.startswith('https://mirror.example/')
                          else json.dumps(release()).encode())
    monkeypatch.setattr(updater, '_request', invalid_mirror)
    assert updater.check()['metadata_source'] == 'GitHub'
    assert calls == ['https://mirror.example/' + updater.API_URL, updater.API_URL]


@pytest.mark.parametrize('failure', ['network', 'checksum'])
def test_download_falls_back_from_mirror_and_discards_partial_bytes(monkeypatch, tmp_path, failure):
    data = release()
    payload = b'official contents'
    asset = data['assets'][0]
    asset.update(size=len(payload), digest='sha256:' + hashlib.sha256(payload).hexdigest())
    calls = []
    def fetch(url):
        calls.append(url)
        if url.startswith(updater.MIRROR_PREFIX):
            if failure == 'network':
                raise URLError('mirror unavailable')
            return io.BytesIO(b'corrupt')
        return io.BytesIO(payload)
    monkeypatch.setattr(updater, '_request', fetch)
    archive = tmp_path / 'download.zip'
    archive.write_bytes(b'previous partial bytes that must not be appended')
    updater._download(asset, archive)
    assert calls == [updater.MIRROR_PREFIX + asset['browser_download_url'], asset['browser_download_url']]
    assert archive.read_bytes() == payload and updater.snapshot()['download_source'] == 'GitHub'
    updater.app_root().mkdir()
    updater.save_source('github')
    calls.clear()
    updater._download(asset, archive)
    assert calls == [asset['browser_download_url']]
