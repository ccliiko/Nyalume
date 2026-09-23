"""按模型隔离、原子保存、白名单及真实 MMD 页面接线。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from nyalume.frontends.pet.pet3d import model_profile as profiles, pet3d_win as pet

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(pet, "_log", lambda *_: None)
    monkeypatch.setattr(pet, "_SETTINGS_PATH", str(tmp_path / "settings.json"))


def test_profiles_keep_models_and_manual_settings_separate(tmp_path):
    a, b = str(tmp_path / "a/model.pmx"), str(tmp_path / "b/model.pmx")
    profiles.save(a, rotate=180, expressions={"happy": ["笑い"]}, motion_whitelist=["sub/dance.vmd"])
    profiles.save(a, zoom=1.8, view_yaw=-45)
    assert profiles.load(a)["expressions"] == {"happy": ["笑い"]}
    assert profiles.load(a)["rotate"] == 180
    assert profiles.load(a)["zoom"] == 1.8
    assert profiles.load(b) == profiles.defaults()
    assert not list(Path(profiles.PROFILE_DIR).glob("*.tmp"))
    broken = Path(profiles.profile_path(a))
    broken.write_text("broken json", encoding="utf-8")
    with pytest.raises(ValueError):
        profiles.save(a, zoom=2)
    assert broken.read_text(encoding="utf-8") == "broken json"


@pytest.mark.parametrize("values", [
    {"zoom": 0}, {"zoom": True}, {"rotate": float("nan")}, {"scale": 100},
    {"expressions": {"invalid": []}}, {"expressions": {"happy": "笑い"}},
    {"motion_whitelist": "dance.vmd"},
])
def test_invalid_profiles_are_rejected(values):
    with pytest.raises(ValueError):
        profiles.validate(values)


def test_native_profile_library_and_bridge(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    filename = model / "demo.pmx"
    filename.touch()
    motions = tmp_path / "motions"
    (motions / "allowed").mkdir(parents=True)
    (motions / "other").mkdir()
    for folder in ("allowed", "other"):
        (motions / folder / "dance.vmd").write_bytes(Path(pet.DEFAULT_VMD).read_bytes())
    profiles.save(str(filename), motion_whitelist=["allowed/dance.vmd"], rotate=90)
    api = pet._NativeApi(enabled=False)
    api.configure_model(str(filename), scale=1.2)
    api.load_library(str(motions), str(model))
    assert api._model_profile["scale"] == 1.2 and api._model_profile["rotate"] == 90
    assert len(api._motions) == 1 and "allowed" in api._motions[0]
    assert api.pet_command({"action": "dance", "name": "dance"})["ok"]
    assert "/allowed/" in api._pending["url"]
    assert api.view_info(1.5, -60)["ok"]
    assert profiles.load(str(filename))["scale"] == 1  # 临时 CLI 缩放不污染手工配置
    assert profiles.load(str(filename))["view_yaw"] == -60
    assert not api.view_info(9, 0)["ok"]
    assert profiles.load(str(filename))["zoom"] == 1.5
    profiles.save(str(filename), motion_whitelist=[])
    api.configure_model(str(filename))
    api.load_library(str(motions), str(model))
    assert not api._motions
    assert not api.pet_command({"action": "dance", "name": "dance"})["ok"]
    monkeypatch.setattr(sys, "argv", ["pet", "--scale=1.5", "--rotate", "180", "--model", "old"])
    calls = []
    monkeypatch.setattr(pet.subprocess, "Popen", lambda args, **_: calls.append(args))
    monkeypatch.setattr(pet, "_release_single_instance", lambda: None)
    monkeypatch.setattr(api, "_destroy_window", lambda: None)
    assert api.restart_with(model="new")
    assert "--scale=1.5" not in calls[0] and "--rotate" not in calls[0]
    assert calls[0][-1] == "new"


def test_compat_checker_uses_real_parser(tmp_path):
    model_dir = ROOT / "models/野餐式MikuQ"
    if not model_dir.exists():
        pytest.skip("本地模型未安装")
    model_dir, pmx = pet._resolve_model(str(model_dir))
    result = subprocess.run(["node", "tools/check_motion_compat.mjs", str(Path(model_dir) / pmx),
                             pet.DEFAULT_VMD, "--json", "--strict"], cwd=ROOT, capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["missing_bones"] == []
    missing = tmp_path / "missing-bone.vmd"
    data = bytearray(Path(pet.DEFAULT_VMD).read_bytes())
    data[54:69] = b"missing_bone".ljust(15, b"\0")
    missing.write_bytes(data)
    result = subprocess.run(["node", "tools/check_motion_compat.mjs", str(Path(model_dir) / pmx),
                             str(missing), "--json", "--strict"], cwd=ROOT, capture_output=True, encoding="utf-8")
    assert result.returncode == 1
    assert json.loads(result.stdout)[0]["missing_bones"] == ["missing_bone"]
    broken = tmp_path / "broken.vmd"
    broken.write_bytes(b"broken")
    result = subprocess.run(["node", "tools/check_motion_compat.mjs", str(Path(model_dir) / pmx),
                             str(broken), "--json"], cwd=ROOT, capture_output=True, encoding="utf-8")
    assert result.returncode == 2
    assert json.loads(result.stdout)[0]["error"]


def test_menu_pages_keep_all_motion_choices():
    api = pet._NativeApi(enabled=False)
    api._motions = [f"动作-{i}.vmd" for i in range(19)]
    api._motion_index = 12
    seen = []
    for number in range(4):
        api._menu_page = f"motions:{number}"
        items = api.menu_items()
        assert len(items) <= 8
        assert items[0]["id"] == "page:root"
        seen += [it["id"] for it in items if it["id"].startswith("motion:")]
    assert seen == [f"motion:{i}" for i in range(19)]
    assert "idle" in {it["id"] for it in items}
    api._menu_page = "motions:999"
    assert api.menu_items() == items


@pytest.mark.parametrize("box,dpr", [("440x660", 1), ("198x297", 2)])
def test_menu_is_readable_and_within_frame(tmp_path, box, dpr):
    model_dir = ROOT / "models/野餐式MikuQ"
    if not model_dir.exists():
        pytest.skip("本地模型未安装")
    model_dir, pmx = pet._resolve_model(str(model_dir))
    api = pet._NativeApi(enabled=False)
    api.configure_model(str(Path(model_dir) / pmx))
    api._models = [("野餐式MikuQ", model_dir), ("锁暝", model_dir)]
    port = pet.start_server(model_dir, api=api)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 700, "height": 740}, device_scale_factor=dpr)
        page.add_init_script("window.pywebview = {api: {menu_rects: rs => { window.rects = rs; }}}")
        try:
            from urllib.parse import quote
            page.goto(f"http://127.0.0.1:{port}/viewer.html?pmx=/model/{quote(pmx)}&box={box}&physics=0")
            page.wait_for_function("document.querySelector('#tip').textContent === ''")
            for kind in ("root", "config:1"):
                api._menu_page = kind
                items = api.menu_items()
                page.evaluate("items => window.__menu(items)", items)
                rects = page.evaluate("window.rects")
                width, height = [int(v) * dpr for v in box.split("x")]
                assert len(rects) == len(items)
                assert min(r["y"] for r in rects) > height * 0.22
                for r in rects:
                    assert r["h"] >= 24 * dpr - 0.1
                    assert 0 <= r["x"] < r["x"] + r["w"] <= width
                    assert 0 <= r["y"] < r["y"] + r["h"] <= height
                page.screenshot(path=str(tmp_path / f"menu-{kind.replace(':', '-')}.png"))
            page.evaluate("window.__menu_close()")
            assert page.evaluate("window.rects") == []
        finally:
            browser.close()


@pytest.mark.parametrize("folder,physics", [("野餐式MikuQ", 0), ("锁瞑", 1)])
def test_real_renderer_restores_view_and_custom_expression(tmp_path, folder, physics):
    model_dir = ROOT / "models" / folder
    if not model_dir.exists():
        pytest.skip("本地模型未安装")
    model_dir, pmx = pet._resolve_model(str(model_dir))
    filename = str(Path(model_dir) / pmx)
    profiles.save(filename, zoom=1.2, view_yaw=30, expressions={"happy": ["笑い"], "head": []})
    api = pet._NativeApi(enabled=False)
    api.configure_model(filename)
    port = pet.start_server(model_dir, api=api)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 700, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.expose_function("saveView", lambda zoom, yaw: api.view_info(zoom, yaw))
        page.add_init_script("""window.actions = []; window.pywebview = {api: {
          view_info: async (...args) => {
            const result = await window.saveView(...args); window.savedView = args; return result;
          },
          poll_action: async () => window.actions.shift() || null
        }}""")
        try:
            from urllib.parse import quote
            url = f"http://127.0.0.1:{port}/viewer.html?pmx=/model/{quote(pmx)}&box=440x660&physics={physics}&idle=1"
            page.goto(url)
            page.wait_for_function("document.querySelector('#tip').textContent === ''", timeout=60000)
            assert page.locator("canvas").first.evaluate("el => el.style.width") == "528px"
            assert page.evaluate("window.__face('happy')")
            page.evaluate("window.__react('head', '')")
            assert page.evaluate("window.__lastReact") == ["head", ""]
            page.evaluate("window.actions.push({kind: 'zoom', steps: 1}, {kind: 'spin', dx: 100})")
            page.wait_for_function("document.querySelector('canvas').style.width === '570px'")
            page.wait_for_function("window.savedView && window.savedView[0] > 1.29 && window.savedView[1] > 35")
            assert profiles.load(filename)["zoom"] == pytest.approx(1.296), (
                api._model_file, profiles.profile_path(filename), pet.model_profile is profiles,
                api._model_profile, api._profile_error)
            page.reload()
            page.wait_for_function("document.querySelector('#tip').textContent === ''", timeout=60000)
            assert page.locator("canvas").first.evaluate("el => el.style.width") == "570px"
            page.screenshot(path=str(tmp_path / "restored-model.png"))
            print("PREVIEW", tmp_path / "restored-model.png")
            assert not errors
        finally:
            browser.close()
