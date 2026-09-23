"""真实检查器的双入口，以及约定/经历从窗口到下次对话的闭环。"""
import datetime
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from test_chat_layout import chat_page

from nyalume.core import agent, companionship as life, memory, tools
from nyalume.frontends.web import server
from nyalume.frontends.pet.pet3d import model_profile, motion_check, pet3d_win as pet

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "memory.db"))
    memory.init_db()
    monkeypatch.setattr(tools, "permission_mode", lambda: "workspace")
    tools.reset_hook_state()
    yield
    tools.clear_session_context()


def test_promise_requires_confirmation_and_survives_restart(monkeypatch):
    client = TestClient(server.app)
    tools.set_session_context("a")
    draft = json.loads(tools.execute_tool("companion_journal", {"action": "propose", "content": "一起整理照片"}))
    assert draft["state"] == "proposed"
    assert "一起整理照片" not in life.prompt_context()
    assert client.patch(f"/api/companion/{draft['id']}", json={"action": "complete"}).status_code == 400
    assert "失败" in tools.execute_tool("companion_journal", {"action": "complete", "content": "一起整理照片"})
    assert client.patch(f"/api/companion/{draft['id']}", json={"action": "confirm"}).json()["state"] == "active"
    assert "尚未完成" in life.prompt_context()
    # Agent 再提议、窗口再提交不会复制或撤销已经确认的约定。
    assert life.promise("一起整理照片", proposed=True)["id"] == draft["id"]
    assert client.post("/api/companion/promises", json={"content": "一起整理照片"}).json()["id"] == draft["id"]
    completed = client.patch(f"/api/companion/{draft['id']}", json={"action": "complete"}).json()
    assert completed["completed_at"] >= completed["confirmed_at"]
    assert client.patch(f"/api/companion/{draft['id']}", json={"action": "complete"}).json() == completed
    memory.init_db()  # 重建连接/初始化不能丢失之前的记录
    monkeypatch.setattr(agent, "resolve_persona_id", lambda: "nyalume")
    assert "一起整理照片" in agent._system_prompt(mode="daily")
    assert "用户确认完成" in agent._system_prompt()
    monkeypatch.setattr(agent, "resolve_persona_id", lambda: "assistant")
    assert "一起整理照片" not in agent._system_prompt()
    assert client.delete(f"/api/companion/{draft['id']}").json()["ok"]
    assert "一起整理照片" not in life.prompt_context()
    assert client.get("/api/companion").json() == []


def test_journal_validation_pagination_and_daily_tool_guard(monkeypatch):
    client = TestClient(server.app)
    for content in ("", " ", "猫" * 201):
        assert client.post("/api/companion/promises", json={"content": content}).status_code == 400
    for i in range(52):
        life.promise(f"一起做第 {i} 件小事")
    first = client.get("/api/companion").json()
    second = client.get("/api/companion", params={"before": first[-1]["id"]}).json()
    assert len(first) == 50 and len(second) == 2
    assert len({row["id"] for row in first + second}) == 52
    assert life.prompt_context().count("用户已确认") == 3
    monkeypatch.setattr(tools, "permission_mode", lambda: "daily")
    assert "日常模式" in tools.execute_tool("companion_journal", {"action": "propose", "content": "不可创建"})
    assert "日常模式" in tools.execute_tool("pet_check_motions", {})


@pytest.fixture
def real_motion_endpoint(tmp_path, monkeypatch):
    folder = ROOT / "models/野餐式MikuQ"
    if not folder.exists():
        pytest.skip("本地模型未安装")
    monkeypatch.setattr(model_profile, "PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(pet, "_log", lambda *_: None)
    model_dir, pmx = pet._resolve_model(str(folder))
    api = pet._NativeApi(enabled=False)
    api.configure_model(str(Path(model_dir) / pmx))
    api._motions = [pet.DEFAULT_VMD]
    port = pet.start_server(model_dir, api=api)
    endpoint_dir = tmp_path / ".nyalume"
    endpoint_dir.mkdir()
    (endpoint_dir / "pet3d_endpoint.json").write_text(json.dumps({"port": port}), encoding="utf-8")
    original = tools.os.path.expanduser
    monkeypatch.setattr(tools.os.path, "expanduser", lambda path: str(tmp_path) if path == "~" else original(path))
    return api, port


def test_both_entries_use_real_checker_and_record_only_facts(real_motion_endpoint):
    api, port = real_motion_endpoint
    client = TestClient(server.app)
    window_report = client.post("/api/pet/motion-check").json()
    agent_report = json.loads(tools.execute_tool("pet_check_motions", {}))
    assert window_report == agent_report
    assert window_report["ok"] and window_report["motions"][0]["moving_bone_count"] == 0
    assert str(ROOT) not in json.dumps(window_report, ensure_ascii=False)
    assert api._pending is None  # 不改正在播放的动作
    assert len(life.entries()) == 1  # 同日重复刷新不刷共同经历
    assert life.entries()[0]["source"] == "motion_check"
    assert "检查了 1 支动作" in life.prompt_context()
    import urllib.request
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/motion_check", data=b"{}",
                                                     headers={"Origin": "https://example.com"}))
    assert exc.value.code == 403
    api._motions = []
    assert not client.post("/api/pet/motion-check").json()["ok"]
    assert len(life.entries()) == 1


def test_check_errors_are_visible_and_lock_is_released(real_motion_endpoint, monkeypatch):
    api, _ = real_motion_endpoint
    monkeypatch.setattr(motion_check.shutil, "which", lambda _: None)
    assert "Node.js" in motion_check.check(api._model_file, api._motions)["error"]
    monkeypatch.setattr(motion_check.shutil, "which", lambda _: "node")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("node", 45)
    monkeypatch.setattr(motion_check.subprocess, "run", timeout)
    assert "超时" in motion_check.check(api._model_file, api._motions)["error"]
    assert "超时" in motion_check.check(api._model_file, api._motions)["error"]


def test_events_roll_over_by_local_day_without_model_call(monkeypatch):
    report = {"ok": True, "model": "测试模型", "motions": [{"error": "", "moving_bone_count": 0, "missing_morph_count": 0}]}
    life.record_motion_check(report)
    life.record_motion_check(report)
    assert len(life.entries()) == 1
    class Tomorrow(datetime.date):
        @classmethod
        def today(cls):
            return cls(2099, 1, 1)
    monkeypatch.setattr(life.datetime, "date", Tomorrow)
    life.record_motion_check(report)
    assert len(life.entries()) == 2


def test_real_window_check_and_promise_lifecycle(chat_page, real_motion_endpoint, tmp_path):
    page, errors, _ = chat_page
    client = TestClient(server.app)
    from urllib.parse import urlparse

    def backend(route):
        request = route.request
        url = urlparse(request.url)
        response = client.request(request.method, url.path + ("?" + url.query if url.query else ""),
                                  content=request.post_data,
                                  headers={"Content-Type": "application/json"})
        route.fulfill(status=response.status_code, content_type="application/json", body=response.content)

    page.route("**/api/companion**", backend)
    page.route("**/api/pet/motion-check", backend)
    draft = life.promise("一起整理照片 <img src=x onerror=alert(1)>", proposed=True)
    page.locator("#sec-state .side-sec-head").click()
    page.evaluate("renderState()")
    expect(page.locator("#companion-list")).to_contain_text("等你确认")
    assert page.locator("#companion-list img").count() == 0
    page.get_by_role("button", name="好，就这么约定").click()
    expect(page.locator("#companion-list")).to_contain_text("我们约好的事")
    page.get_by_role("button", name="我们完成了").click()
    expect(page.locator("#companion-list")).to_contain_text("一起完成的事")
    assert "用户确认完成" in agent._system_prompt()
    page.locator("#pet-motion-check").click()
    expect(page.locator("#pet-motion-report table tbody tr")).to_have_count(1)
    expect(page.locator("#companion-list")).to_contain_text("检查了 1 支动作")
    page.reload()
    page.evaluate("renderState()")
    expect(page.locator("#companion-list")).to_contain_text("一起完成的事")
    if "collapsed" in page.locator("#sec-state").get_attribute("class"):
        page.locator("#sec-state .side-sec-head").click()
    page.locator("#companion-content").fill("一起给桌宠选一支合适的舞")
    page.get_by_role("button", name="记下我们的约定").click()
    expect(page.locator("#companion-list")).to_contain_text("一起给桌宠选一支合适的舞")
    page.locator("#companion-list").scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / "companion-window.png"))
    card = page.locator(".companion-card").filter(has_text="一起整理照片")
    card.get_by_role("button", name="删除记录").click()
    expect(card).to_have_count(0)
    assert "一起整理照片" not in life.prompt_context()
    assert not errors
