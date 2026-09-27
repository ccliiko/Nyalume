"""专注计时的持久化、并发与真实页面闭环（不使用模型或用户数据库）。"""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from nyalume.core import focus, memory
from nyalume.frontends.web import server
from test_chat_layout import chat_page, assert_layout


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "focus.db"))
    memory.init_db()
    now = [1800000000.0]
    monkeypatch.setattr(focus.time, "time", lambda: now[0])
    return now


def test_focus_pause_restart_finish_and_idempotency(clock):
    row = focus.start("  读十页书  ", 25)["current"]
    assert row["goal"] == "读十页书"
    clock[0] += 100
    paused = focus.update(row["id"], "pause")["current"]
    assert paused["remaining"] == 1400
    clock[0] += 600
    assert focus.update(row["id"], "pause")["current"] == paused
    memory.init_db()
    assert focus.snapshot()["current"] == paused
    resumed = focus.update(row["id"], "resume")["current"]
    assert resumed["deadline"] == clock[0] + 1400
    clock[0] += 1401
    assert focus.snapshot()["current"]["status"] == "ready"
    assert focus.snapshot()["recent"] == []  # 到时不等于任务完成。
    done = focus.update(row["id"], "complete", "progress", "  读到了第七页  ")
    assert done["current"] is None
    assert done["recent"][0]["note"] == "读到了第七页"
    assert done["recent"][0]["outcome"] == "progress"
    assert len(focus.update(row["id"], "complete", "done")["recent"]) == 1
    assert focus.snapshot()["recent"][0]["outcome"] == "progress"


def test_focus_concurrency_and_boundaries(clock):
    def start(_):
        try:
            return focus.start("整理笔记", 15)["current"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(pool.map(start, range(4)))
    assert sum(row is not None for row in attempts) == 1
    row = focus.snapshot()["current"]
    with pytest.raises(ValueError):
        focus.update(row["id"], "complete", "done")
    clock[0] += 60
    focus.update(row["id"], "finish")
    done = focus.update(row["id"], "complete", "done")
    assert done["recent"][0]["duration"] - done["recent"][0]["remaining"] == 60
    new = focus.start("另一个目标", 45)["current"]
    with pytest.raises(ValueError):
        focus.update(row["id"], "pause")
    focus.update(new["id"], "cancel")
    assert focus.snapshot()["current"] is None
    assert len(focus.snapshot()["recent"]) == 1
    focus.delete(row["id"])
    assert focus.snapshot()["recent"] == []
    assert focus.delete(row["id"])["recent"] == []
    for goal, minutes in [(" ", 15), ("a" * 121, 25), ("goal", 0), ("goal", 30)]:
        with pytest.raises(ValueError):
            focus.start(goal, minutes)


def test_focus_http_validation_and_assets(clock):
    client = TestClient(server.app)
    assert client.get("/static/companion.js").status_code == 200
    assert client.post("/api/focus", json={"goal": "", "minutes": 25}).status_code == 409
    assert client.post("/api/focus", json={"goal": "x"}).status_code == 422
    row = client.post("/api/focus", json={"goal": "写一页", "minutes": 15}).json()["current"]
    assert client.delete(f"/api/focus/{row['id']}").json()["current"]["id"] == row["id"]
    assert client.post("/api/focus", json={"goal": "x", "minutes": 15}).status_code == 409
    assert client.patch(f"/api/focus/{row['id']}", json={"action": "unknown"}).status_code == 409
    client.patch(f"/api/focus/{row['id']}", json={"action": "finish"})
    assert client.patch(f"/api/focus/{row['id']}", json={"action": "complete", "outcome": "done", "note": "x" * 501}).status_code == 409
    assert client.get("/api/focus").json()["current"]["status"] == "ready"


def test_home_drafts_commands_and_mobile(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.locator("#input").fill("还没有说完的想法")
    page.evaluate("switchTo('empty')")
    expect(page.locator("#home-panel")).to_be_visible()
    page.locator("[data-prompt]").first.click()
    assert "五分钟" in page.locator("#input").input_value()
    assert not page.evaluate("state.busy")
    page.evaluate("switchTo('demo')")
    expect(page.locator("#input")).to_have_value("还没有说完的想法")
    page.reload()
    expect(page.locator("#input")).to_have_value("还没有说完的想法")
    page.keyboard.press("Control+k")
    expect(page.locator("#command-dialog")).to_be_visible()
    page.locator("#command-search").fill("小窝")
    page.locator("#command-search").press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page.locator("#home-panel")).to_be_visible()
    expect(page.locator("#command-dialog")).not_to_be_visible()
    expect(page.locator("#focus-start-form")).to_be_visible()
    page.set_viewport_size({"width": 1440, "height": 1050})
    page.screenshot(path=str(tmp_path / "home-desktop.png"), full_page=True)
    print("PREVIEW", tmp_path / "home-desktop.png")
    for width in (390, 640):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#sidebar-toggle").click()
        expect(page.locator("#sidebar")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#sidebar")).not_to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(tmp_path / "home-mobile.png"), full_page=True)
    print("PREVIEW", tmp_path / "home-mobile.png")
    page.keyboard.press("Control+k")
    page.locator("#command-search").fill("继续对话")
    page.locator("#command-search").press("Enter")
    assert_layout(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.evaluate("removeSession({id: 'demo', message_count: 4})")
    assert page.evaluate("localStorage.getItem('nyalume:draft:demo')") is None
    assert not errors


def test_focus_ui_complete_loop(chat_page, clock, tmp_path):
    page, errors, _ = chat_page

    def route_focus(route):
        body = route.request.post_data_json or {}
        try:
            if route.request.method == "POST":
                data = focus.start(body["goal"], body["minutes"])
            elif route.request.method == "DELETE":
                data = focus.delete(int(route.request.url.rsplit("/", 1)[1]))
            elif route.request.method == "PATCH":
                entry_id = int(route.request.url.rsplit("/", 1)[1])
                data = focus.update(entry_id, **body)
            else:
                data = focus.snapshot()
            route.fulfill(content_type="application/json", body=json.dumps(data))
        except ValueError as exc:
            route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": str(exc)}))

    page.route("**/api/focus**", route_focus)
    page.locator("#home-nav").click()
    page.locator("#focus-goal").fill("整理一页笔记")
    page.locator("#focus-start-form button").click()
    expect(page.locator("#focus-running")).to_be_visible()
    expect(page.locator("#focus-chip")).to_contain_text("陪伴中")
    page.locator("#focus-pause").click()
    expect(page.locator("#focus-stage")).to_contain_text("已暂停")
    page.reload()
    page.locator("#home-nav").click()
    expect(page.locator("#focus-stage")).to_contain_text("已暂停")
    page.locator("#focus-pause").click()
    expect(page.locator("#focus-stage")).to_contain_text("专注中")
    clock[0] += 100
    page.locator("#focus-finish").click()
    expect(page.locator("#focus-review")).to_be_visible()
    page.locator("#focus-note").fill("<script>不是脚本</script>\n下一步：补上配图")
    page.locator('#focus-review button[value="progress"]').click()
    expect(page.locator("#focus-start-form")).to_be_visible()
    expect(page.locator("#focus-history")).to_contain_text("有进展，下次继续")
    expect(page.locator("#focus-history script")).to_have_count(0)
    page.reload()
    page.locator("#home-nav").click()
    expect(page.locator("#focus-history")).to_contain_text("下一步：补上配图")
    page.locator(".history-continue").click()
    expect(page.locator("#focus-goal")).to_have_value("整理一页笔记")
    page.screenshot(path=str(tmp_path / "focus-review-saved.png"))
    print("PREVIEW", tmp_path / "focus-review-saved.png")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(".history-remove").click()
    expect(page.locator(".focus-history-row")).to_have_count(0)
    assert focus.snapshot()["recent"] == []
    assert not errors


def test_focus_error_retry_and_quick_chat(chat_page):
    page, errors, _ = chat_page
    page.route("**/api/focus", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"服务暂时不可用"}'))
    page.locator("#home-nav").click()
    expect(page.locator("#focus-retry")).to_be_visible()
    expect(page.locator("#focus-feedback")).to_contain_text("服务暂时不可用")
    page.unroute("**/api/focus")
    page.locator("#focus-retry").click()
    expect(page.locator("#focus-retry")).not_to_be_visible()
    page.goto("http://nyalume.test/?quick=1")
    expect(page.locator("#input")).to_be_visible()
    page.evaluate("switchTo('empty')")
    expect(page.locator("#home-panel")).not_to_be_visible()
    expect(page.locator("#input")).to_be_visible()
    assert not errors


def test_home_portrait_missing_keeps_character_fallback(chat_page):
    page, errors, _ = chat_page
    page.route('**/api/pet/plan/portrait', lambda route: route.fulfill(status=404, body='missing portrait'))
    page.goto('http://nyalume.test/?home=1')
    expect(page.locator('#home-panel')).to_be_visible()
    expect(page.locator('#home-portrait')).not_to_be_visible()
    expect(page.locator('.home-cat')).to_be_visible()
    expect(page.locator('.home-dialogue')).to_contain_text('给你留了位置喵')
    assert not errors
