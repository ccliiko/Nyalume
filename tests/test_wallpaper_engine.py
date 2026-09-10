import json
import os

import pytest
from fastapi import HTTPException

from nyalume.frontends.web import server


def test_wallpaper_engine_resolves_project_image_and_mp4(tmp_path):
    image = tmp_path / "preview.JPG"
    image.write_bytes(b"image")
    video = tmp_path / "loop.mp4"
    video.write_bytes(b"video")
    project = tmp_path / "project.json"

    project.write_text(json.dumps({"file": image.name}), encoding="utf-8")
    assert server._wallpaper_engine_media(str(project)) == (str(image.resolve()), "image")

    project.write_text(json.dumps({"file": video.name}), encoding="utf-8")
    assert server._wallpaper_engine_media(str(project)) == (str(video.resolve()), "video")


def test_wallpaper_engine_rejects_unsupported_projects(tmp_path):
    scene = tmp_path / "scene.pkg"
    scene.write_bytes(b"scene")
    project = tmp_path / "project.json"
    project.write_text(json.dumps({"file": scene.name}), encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        server._wallpaper_engine_media(str(project))
    assert error.value.status_code == 415


def test_wallpaper_engine_playlist_only_reads_supported_media(monkeypatch, tmp_path):
    image = tmp_path / "still.jpg"
    image.write_bytes(b"image")
    video = tmp_path / "loop.mp4"
    video.write_bytes(b"video")
    scene = tmp_path / "scene.pkg"
    scene.write_bytes(b"scene")
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"preview")
    (tmp_path / "wallpaper64.exe").write_bytes(b"exe")
    (tmp_path / "config.json").write_text(json.dumps({
        "account": {"general": {
            "browser": {"lastselectedmonitor": "Monitor1"},
            "wallpaperconfig": {"selectedwallpapers": {
                "Monitor0": {"file": str(scene), "playlist": {"items": []}},
                "Monitor1": {
                    "file": str(image),
                    "playlist": {"items": [str(scene), str(image), str(video)]},
                },
            }},
            "wallpaperconfigrecent": [
                {"title": "静态图 (1)", "config": {"selectedwallpapers": {
                    "Monitor1": {"file": str(image)},
                }}},
                {"title": "动态视频 (2)", "config": {"selectedwallpapers": {
                    "Monitor1": {"file": str(video)},
                }}},
                {"title": "场景壁纸 (3)", "config": {"selectedwallpapers": {
                    "Monitor1": {"file": str(scene)},
                }}},
            ],
        }},
    }), encoding="utf-8")
    monkeypatch.setattr(server, "_wallpaper_engine_exe", lambda: str(tmp_path / "wallpaper64.exe"))

    assert server._wallpaper_engine_selected()[0] == str(image)
    assert server._wallpaper_engine_playlist(str(image)) == [
        (str(image.resolve()), "image"),
        (str(video.resolve()), "video"),
    ]
    assert server._wallpaper_engine_playlist(str(tmp_path / "not-in-list")) == [
        (str(image.resolve()), "image"),
        (str(video.resolve()), "video"),
    ]
    assert server._wallpaper_engine_recent_from_config() == [
        {"name": "场景壁纸", "path": str(preview.resolve()), "kind": "image", "preview": True},
        {"name": "动态视频", "path": str(video.resolve()), "kind": "video", "preview": False},
        {"name": "静态图", "path": str(image.resolve()), "kind": "image", "preview": False},
    ]


def test_wallpaper_engine_actions(monkeypatch, tmp_path):
    image = tmp_path / "current.png"
    image.write_bytes(b"image")
    video = tmp_path / "next.mp4"
    video.write_bytes(b"video")
    tray = tmp_path / "wallpaper32.exe"
    tray.write_bytes(b"exe")
    opened = []
    config = {
        "wall_mode": "engine", "wall_opacity": 70,
        "wall_engine_recent": [], "wall_engine_recent_initialized": True,
    }
    monkeypatch.setattr(server, "_we_media_path", "")
    monkeypatch.setattr(server, "_we_startup_available", False)
    monkeypatch.setattr(server, "load_config", lambda: dict(config))
    monkeypatch.setattr(server, "save_config", lambda value: config.update(value))
    monkeypatch.setattr(server, "_wallpaper_engine_selected", lambda: (str(image), []))
    monkeypatch.setattr(
        server, "_wallpaper_engine_playlist",
        lambda *_args: [(str(image.resolve()), "image"), (str(video.resolve()), "video")],
    )
    monkeypatch.setattr(server, "_wallpaper_engine_tray_exe", lambda: str(tray))
    monkeypatch.setattr(server.os, "startfile", opened.append)

    current = server.wallpaper_engine_action("current")
    assert current["kind"] == "image"

    next_wallpaper = server.wallpaper_engine_action("next")
    assert next_wallpaper["kind"] == "video"

    rows = server.wallpaper_engine_recent()
    assert [row["name"] for row in rows] == ["next", "current"]
    chosen = server.wallpaper_engine_action("recent", index=1)
    assert chosen["kind"] == "image"
    assert config["wall_mode"] == "engine"
    assert [row["name"] for row in server.wallpaper_engine_recent()] == ["current", "next"]
    assert server.clear_wallpaper_engine_recent() == {"ok": True}
    assert config["wall_engine_recent"] == []

    assert server.wallpaper_engine_action("open") == {"ok": True}
    assert opened == [str(tray)]


def test_wallpaper_media_url_identifies_selected_file_even_with_same_mtime(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    timestamp = 1_700_000_000
    os.utime(first, (timestamp, timestamp))
    os.utime(second, (timestamp, timestamp))

    assert server._wallpaper_engine_url(str(first)) != server._wallpaper_engine_url(str(second))


def test_wallpaper_engine_next_falls_back_to_recents(monkeypatch, tmp_path):
    image = tmp_path / "recent.jpg"
    image.write_bytes(b"image")
    monkeypatch.setattr(server, "_we_media_path", "")
    monkeypatch.setattr(server, "_wallpaper_engine_selected", lambda: ("scene.pkg", [{}]))
    monkeypatch.setattr(server, "_wallpaper_engine_playlist", lambda *_args: [])
    monkeypatch.setattr(server, "_wallpaper_engine_recent_items", lambda: [{
        "name": "最近图片", "path": str(image), "kind": "image", "preview": False,
    }])
    monkeypatch.setattr(server, "load_config", lambda: {})
    monkeypatch.setattr(server, "save_config", lambda _value: None)

    result = server.wallpaper_engine_action("next")
    assert result["kind"] == "image"
    assert result["name"] == "recent"


def test_wallpaper_settings_falls_back_to_cached_when_current_unsupported(monkeypatch, tmp_path):
    cached = tmp_path / "cached.jpg"
    cached.write_bytes(b"image")
    unsupported = tmp_path / "scene.pkg"
    unsupported.write_bytes(b"scene")
    config = {
        "wall_mode": "engine", "wall_opacity": 60,
        "wall_engine_media": str(cached),
        "wall_engine_recent": [],
        "wall_engine_recent_initialized": True,
    }
    syncs = []
    monkeypatch.setattr(server, "_we_media_path", "")
    monkeypatch.setattr(server, "_we_startup_available", False)
    monkeypatch.setattr(server, "load_config", lambda: dict(config))
    monkeypatch.setattr(server, "save_config", lambda value: config.update(value))
    monkeypatch.setattr(server, "_wallpaper_engine_tray_exe", lambda: "wallpaper32.exe")
    monkeypatch.setattr(
        server, "_wallpaper_engine_selected",
        lambda: (syncs.append(True) or str(unsupported), []),
    )
    monkeypatch.setattr(
        server, "_wallpaper_engine_playlist",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应跳过上次支持的壁纸")),
    )

    first = server._wallpaper_settings()
    second = server._wallpaper_settings()
    assert first["mode"] == second["mode"] == "engine"
    assert first["engine_kind"] == "image"
    assert syncs == [True, True]


def test_wallpaper_settings_syncs_current_each_open(monkeypatch, tmp_path):
    current = tmp_path / "current.jpg"
    current.write_bytes(b"image")
    config = {"wall_mode": "engine", "wall_opacity": 70}
    syncs = []
    monkeypatch.setattr(server, "_we_media_path", "")
    monkeypatch.setattr(server, "_we_startup_available", False)
    monkeypatch.setattr(server, "load_config", lambda: dict(config))
    monkeypatch.setattr(server, "save_config", lambda value: config.update(value))
    monkeypatch.setattr(server, "_wallpaper_engine_tray_exe", lambda: "wallpaper32.exe")
    monkeypatch.setattr(
        server, "_wallpaper_engine_selected",
        lambda: (syncs.append(True) or str(current), []),
    )

    assert server._wallpaper_settings()["mode"] == "engine"
    assert server._wallpaper_settings()["mode"] == "engine"
    assert len(syncs) == 2
    assert config["wall_engine_media"] == str(current.resolve())


def test_wallpaper_startup_without_tray_falls_back_to_custom(monkeypatch):
    config = {"wall_mode": "engine", "wall_custom": "mine.jpg", "wall_opacity": 70}
    monkeypatch.setattr(server, "_we_startup_available", False)
    monkeypatch.setattr(server, "load_config", lambda: dict(config))
    monkeypatch.setattr(server, "_wallpaper_engine_tray_exe", lambda: "")

    settings = server._wallpaper_settings()
    assert settings["mode"] == "custom"
    assert settings["custom_url"].endswith("/mine.jpg")


def test_removed_character_wallpaper_falls_back_safely(monkeypatch):
    monkeypatch.setattr(
        server, "load_config",
        lambda: {"wall_mode": "character", "wall_custom": "mine.jpg"},
    )
    settings = server._wallpaper_settings()
    assert settings["mode"] == "custom"

    monkeypatch.setattr(server, "load_config", lambda: {"wall_mode": "character"})
    assert server._wallpaper_settings()["mode"] == ""


def test_wallpaper_engine_open_reports_missing_tray(monkeypatch):
    monkeypatch.setattr(server, "_wallpaper_engine_tray_exe", lambda: "")
    with pytest.raises(HTTPException) as error:
        server.wallpaper_engine_action("open")
    assert error.value.status_code == 404
    assert error.value.detail == "托盘中不存在"
