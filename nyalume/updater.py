"""Official Windows portable releases; download now, install after the app exits."""

import hashlib
from http.client import HTTPException as HTTPStreamError
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

from nyalume import __version__

RELEASES_URL = "https://github.com/ccliiko/Nyalume/releases"
API_URL = "https://api.github.com/repos/ccliiko/Nyalume/releases/latest"
MIRROR_PREFIX = "https://gh-proxy.org/"
_lock = threading.Lock()
_release = None
_job_result = None
_state = {"phase": "idle", "message": "点击检查 GitHub 正式发行版。"}


def app_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def version_tuple(value: str) -> tuple[int, int, int]:
    # Historical October 2026 release tag, before the current three-part scheme.
    if value in {"v0.45", "0.45"}:
        return (0, 4, 5)
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("发行版本号格式不正确，仅支持正式版本。")
    return tuple(map(int, match.groups()))


def _can_install() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _validate_source(mode: str, prefix: str) -> dict:
    if mode not in {"auto", "github", "custom"}:
        raise ValueError("请选择自动加速、GitHub 直连或自定义镜像。")
    prefix = prefix.strip()
    if mode == "custom":
        url = urlsplit(prefix)
        if (len(prefix) > 500 or url.scheme != "https" or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or any(char.isspace() or char == "\\" for char in prefix)):
            raise ValueError("镜像前缀须为 HTTPS 地址，且不能包含账号、查询参数或片段。")
        prefix = prefix.rstrip("/") + "/"
    else:
        prefix = ""
    return {"mode": mode, "prefix": prefix}


def update_source() -> dict:
    try:
        data = json.loads((app_root() / "update-settings.json").read_text(encoding="utf-8"))
        return _validate_source(data["mode"], data.get("prefix", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return {"mode": "auto", "prefix": ""}


def save_source(mode: str, prefix: str = "") -> dict:
    global _release
    source = _validate_source(mode, prefix)
    with _lock:
        if _state["phase"] in {"checking", "downloading", "ready"}:
            raise ValueError("请等待当前更新操作完成后再切换更新源。")
        path = app_root() / "update-settings.json"
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(source), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            raise ValueError("更新源保存失败，请检查安装目录的写入权限。") from exc
        _release = None
        _state.clear()
        _state.update(phase="configured", message="更新源已保存，请重新检查更新。")
    return snapshot()


def _source_urls(url: str) -> list[str]:
    source = update_source()
    if source["mode"] == "github":
        return [url]
    prefix = source["prefix"] if source["mode"] == "custom" else MIRROR_PREFIX
    # Prefer authoritative metadata when reachable; large downloads use acceleration first.
    return [url, prefix + url] if url == API_URL and source["mode"] == "auto" else [prefix + url, url]


def snapshot() -> dict:
    with _lock:
        result = dict(_state)
    if result["phase"] == "ready" and _job_result and _job_result.is_file():
        try:
            previous = json.loads(_job_result.read_text(encoding="utf-8-sig"))
            _set(phase=previous["phase"], message=previous["message"])
            result.update(phase=previous["phase"], message=previous["message"])
        except (OSError, ValueError, KeyError):
            pass
    result.update(current_version=__version__, can_install=_can_install(), releases_url=RELEASES_URL,
                  source=update_source())
    if result["phase"] == "idle":
        try:
            previous = json.loads((app_root() / "update-result.json").read_text(encoding="utf-8-sig"))
            result["message"] = previous["message"]
        except (OSError, ValueError, KeyError):
            pass
    return result


def _set(**values) -> None:
    with _lock:
        _state.update(values)


def _request(url: str):
    return urlopen(Request(url, headers={"User-Agent": "Nyalume/" + __version__,
                                        "Accept": "application/vnd.github+json"}), timeout=6 if url == API_URL else 30)


def _latest() -> dict:
    error = "无法读取发行信息，请重试或切换更新源。"
    for url in _source_urls(API_URL):
        try:
            with _request(url) as response:
                release = json.load(response)
            version_tuple(release.get("tag_name", ""))
            if release.get("draft") or release.get("prerelease"):
                raise ValueError("没有可用的正式发行版。")
            _set(metadata_source="GitHub" if url == API_URL else "镜像加速")
            return release
        except HTTPError as exc:
            if exc.code == 404:
                error = "尚未发布正式版本，请稍后再试。"
            elif exc.code in (403, 429):
                error = "GitHub 或镜像请求受限，请稍后重试或切换更新源。"
            else:
                error = f"更新源返回 {exc.code}，请稍后重试或切换更新源。"
        except (URLError, TimeoutError, OSError, HTTPStreamError):
            error = "无法连接更新源，请检查网络或切换更新源后重试。"
        except (ValueError, TypeError, AttributeError):
            error = "更新源返回了无效的发行信息，请切换更新源后重试。"
    raise ValueError(error)


def _asset(release: dict) -> dict | None:
    # Never silently switch to a no-2d or personal-model variant.
    version = release["tag_name"].removeprefix("v")
    name = f"Nyalume-v{version}-windows-x64.zip"
    for asset in release.get("assets", []):
        if asset.get("name") != name or asset.get("state") != "uploaded":
            continue
        url = urlsplit(asset.get("browser_download_url", ""))
        if (url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment
                or url.path != f"/ccliiko/Nyalume/releases/download/{release['tag_name']}/{name}"):
            raise ValueError("发行包下载地址不属于官方仓库。")
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset.get("digest") or ""):
            raise ValueError("发行包缺少 SHA-256 校验信息，请打开发行页手动更新。")
        return asset
    return None


def check() -> dict:
    global _release
    with _lock:
        if _state["phase"] in {"checking", "downloading", "ready"}:
            raise ValueError("检查或更新正在进行中。")
        _state.update(phase="checking", message="正在检查最新正式版…", available=False, installable=False)
    try:
        release = _latest()
        latest, current = version_tuple(release["tag_name"]), version_tuple(__version__)
        available = latest > current
        asset_error = ""
        try:
            asset = _asset(release) if available else None
        except ValueError as exc:
            asset, asset_error = None, str(exc)
        message = "已是最新正式版。" if not available else "发现新版本，可下载更新。"
        if current > latest:
            message = "当前版本高于最新已发布正式版，无需更新。"
        if available and not _can_install():
            message = "发现新版本。当前为源码运行，请从发行页下载便携版，或通过 Git 更新源码。"
        elif available and not asset:
            message = asset_error or "发现新版本，但尚未上传标准 Windows x64 发行包，请稍后重试。"
        with _lock:
            _release = release
            _state.update(phase="checked", latest_version=release["tag_name"], available=available,
                          installable=available and bool(asset) and _can_install(), message=message,
                          notes=str(release.get("body") or ""), checked_at=int(time.time()))
    except Exception as exc:
        _set(phase="error", installable=False, message=str(exc))
        raise ValueError(str(exc)) from exc
    return snapshot()


def _program_file(parts: tuple[str, ...]) -> bool:
    """Only shipped program trees and notices; never import user data from an archive."""
    lowered = tuple(part.lower() for part in parts)
    if any(part in {".env", "agent.db", "pet_config.json", "persona_config.json",
                    "material-state.json", "workspace", "conversations", "backups", "skills",
                    "screenshots", "downloads"} or part.endswith((".db", ".db-wal", ".db-shm"))
           or part.startswith(".env.") for part in lowered):
        return False
    if lowered[0] in {"_internal", "unity"}:
        return len(parts) > 1
    return len(parts) == 1 and (lowered[0] in {"nyalume.exe", "nyalume2d.exe", "nyalume3d.exe", "license"}
                               or lowered[0].endswith((".md", ".txt", ".cmd")))


def _extract(archive: Path, destination: Path, package: str) -> list[str]:
    files = []
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        if len(members) > 100000 or sum(entry.file_size for entry in members) > 12 * 1024**3:
            raise ValueError("发行包解压大小异常。")
        seen = set()
        for entry in members:
            name = entry.filename
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != package or "\\" in name or ":" in name
                    or any(part in {"..", "."} or part.rstrip(" .") != part for part in parts)
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("发行包包含不安全的路径。")
            if entry.is_dir():
                continue
            relative = parts[1:]
            if not relative or not _program_file(relative):
                continue
            key = "/".join(relative).lower()
            if key in seen:
                raise ValueError("发行包包含重复路径。")
            seen.add(key)
            target = destination.joinpath(*relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            files.append("/".join(relative))
    if (not {"nyalume2d.exe", "nyalume3d.exe", "version.txt"} <= seen
            or not any(name.startswith("_internal/") for name in seen)):
        raise ValueError("发行包缺少程序文件，请打开发行页手动更新。")
    return files


def _download(asset: dict, archive: Path) -> None:
    total = int(asset["size"])
    error = "下载失败。"
    for index, url in enumerate(_source_urls(asset["browser_download_url"])):
        if index:
            _set(message="加速下载失败，正在切换 GitHub 直连…", downloaded=0)
        try:
            digest = hashlib.sha256()
            downloaded = 0
            with _request(url) as response, archive.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > total:
                        raise ValueError("下载大小与发行信息不符。")
                    digest.update(chunk)
                    output.write(chunk)
                    _set(downloaded=downloaded, total=total, message=f"正在下载… {downloaded * 100 // total}%")
            if downloaded != total or digest.hexdigest() != asset["digest"].split(":", 1)[1].lower():
                raise ValueError("下载校验失败。")
            _set(download_source="GitHub" if url == asset["browser_download_url"] else "镜像加速")
            return
        except (OSError, URLError, HTTPStreamError, ValueError) as exc:
            error = str(exc)
    raise ValueError(error)


def _download_and_prepare(release: dict) -> None:
    global _job_result
    stage = None
    try:
        asset = _asset(release)
        if not asset:
            raise ValueError("没有标准 Windows x64 发行包。")
        root = app_root()
        # Keep staging/backup on the install volume; no writes to the running program yet.
        stage = Path(tempfile.mkdtemp(prefix=".nyalume-update-", dir=root.parent))
        archive = stage / "release.zip"
        total = int(asset["size"])
        if total <= 0 or total > 6 * 1024**3:
            raise ValueError("发行包大小异常。")
        if shutil.disk_usage(stage).free < total * 4:
            raise ValueError("磁盘空间不足，请至少预留发行包大小的四倍空间。")
        _download(asset, archive)
        _set(message="下载完成，正在校验并解压…")
        files = _extract(archive, stage / "payload", asset["name"][:-4])
        if (stage / "payload/VERSION.txt").read_text(encoding="utf-8-sig").strip() != release["tag_name"].removeprefix("v"):
            raise ValueError("发行包版本与发行信息不符。")
        restart = Path(sys.executable).name
        if restart.lower() not in {"nyalume.exe", "nyalume2d.exe", "nyalume3d.exe"}:
            raise ValueError("无法识别当前桌宠入口，请手动更新。")
        if restart.lower() == "nyalume.exe":
            restart = "Nyalume2D.exe"
        plan = dict(root=str(root), stage=str(stage), files=files, restart=restart)
        (stage / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        helper = stage / "install.ps1"
        helper.write_text(Path(__file__).with_name("update_windows.ps1").read_text(encoding="utf-8"), encoding="utf-8-sig")
        _job_result = stage / "result.json"
        subprocess.Popen(
            [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
             "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
            cwd=stage, creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _set(phase="ready", installable=False,
             message="更新已准备好。请从右键菜单退出桌宠，并关闭聊天窗口；更新完成后会自动重新启动。")
    except Exception as exc:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)
        _set(phase="error", installable=False, message=f"更新失败：{exc}。原程序未更改，可重新检查后重试。")


def install() -> dict:
    with _lock:
        if not _can_install():
            raise ValueError("一键安装仅支持 Windows 便携版。源码运行请通过 Git 更新。")
        if _state["phase"] != "checked" or not _state.get("installable") or not _release:
            raise ValueError("请先检查更新，确认存在可安装的新版本。")
        release = _release
        _state.update(phase="downloading", installable=False, downloaded=0, total=0, message="正在连接下载服务器…")
        threading.Thread(target=_download_and_prepare, args=(release,), daemon=True).start()
    return snapshot()
