"""主动话题、桌面便笺与接话小窗的回归检查，模型及桌面文件全部隔离。"""
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse, quote
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect, sync_playwright
from test_chat_layout import chat_page

from nyalume.core import memory
from nyalume.frontends.pet import web_chat
from nyalume.frontends.pet.pet3d import proactive as P, pet3d_win as pet
from nyalume.frontends.pet.pet3d.keepsakes import Keepsakes


def test_topics_use_recent_events_and_reject_repeats():
    pr = P.Proposer(chat=lambda _: '{"action":"say","text":"想给云朵起个名字"}', mode="talkative")
    state = {"taps": 456, "history": [{"t": 50, "kind": "tap"}, {"t": 995, "kind": "dance"}]}
    data = json.loads(pr.build_prompt({}, state, "陪伴", 1000)[1]["content"])
    assert "456" not in str(data) and "tap" not in str(data)
    assert data["近期互动"] == ["dance"]
    action = pr.propose({}, state, "battery", 1000)
    assert action and not pr.pending_ack and not pr.recent
    pr.note_delivered(action['text'], 1000)
    assert pr.propose({}, state, "battery", 1060) is None
    assert pr.last_reply_at == 1000
    pr.chat = lambda _: '{"action":"say","text":"今天想学会折纸船"}'
    action = pr.propose({}, state, "battery", 1120)
    assert action
    pr.note_delivered(action['text'], 1120)
    assert pr.last_reply_at == 1000
    pr.tick_ignored(1601)
    assert pr.ignored == 1
    assert "想给云朵起个名字" in str(pr.build_prompt({}, {}, "陪伴", 1700))
    pr.note_touch(1602)
    assert pr.ignored == 0 and not pr.pending_ack
    assert pr.blocked({}, {"last_interaction": 1602}, 1633) == ""


def test_failure_is_throttled_and_note_requires_scheduled_reason():
    def fail(_):
        raise OSError("offline")

    pr = P.Proposer(chat=fail, mode="talkative")
    with pytest.raises(RuntimeError):
        pr.propose({}, {}, "battery", 1000)
    assert 1045 <= pr.next_allowed_at <= 1090
    pr.chat = lambda _: '{"action":"note","text":"我想在这里种一片想象中的花园。"}'
    assert pr.propose({}, {}, "battery", 1100) is None
    assert pr.propose({}, {}, "写便笺", 1200)["action"] == "note"
    assert not pr.pending_ack  # 她自己写便笺不要求用户回应


def test_keepsakes_survive_restart_without_overwriting(tmp_path):
    root = tmp_path / "桌面" / "Nyalume 的小窝"
    notes = Keepsakes(root)
    now = time.mktime(time.strptime("2026-09-24 12:00", "%Y-%m-%d %H:%M"))
    assert notes.due(now)
    path = notes.write("想把今天的一点好心情收起来。", "锁瞑", now)
    assert path.parent == root
    assert "锁瞑" in path.read_text(encoding="utf-8")
    path.write_text("用户补充的文字", encoding="utf-8")
    restarted = Keepsakes(root)
    assert not restarted.due(now + 120)
    assert restarted.write("另一段文字", "锁瞑", now + 120) is None
    assert path.read_text(encoding="utf-8") == "用户补充的文字"
    assert restarted.due(now + 86400)
    assert restarted.write("明天也在这里。", "锁瞑", now + 86400)
    assert len(list(root.glob("*.txt"))) == 3


@pytest.mark.parametrize("enabled,quiet,interrupted", [(True, False, False), (False, False, False),
                                                        (True, True, False), (True, False, True)])
def test_loop_writes_only_when_allowed(tmp_path, monkeypatch, enabled, quiet, interrupted):
    from nyalume.frontends.pet.pet3d import keepsakes

    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "memory.db"))
    memory.init_db()
    now = [1000.0]
    state = {"last_interaction": 0, "energy": 1}
    api = SimpleNamespace(_model_dir="锁瞑", _talk_mode="talkative", _proactive=True,
                          _notes_enabled=enabled, _pending=None, _desk={"quiet": quiet},
                          _pet_state=SimpleNamespace(snapshot=lambda: dict(state)))
    notes = Keepsakes(tmp_path / "小窝")
    monkeypatch.setattr(keepsakes, "Keepsakes", lambda: notes)
    monkeypatch.setattr(P, "_log", lambda _: None)

    def sleep(_):
        if now[0] == 2000:
            raise SystemExit
        now[0] = 2000

    def chat(messages):
        if interrupted:
            state["last_interaction"] = 2001  # 等模型时用户打开聊天
        task = json.loads(messages[1]["content"])["我在做的事"]
        return json.dumps({"action": "note" if task == "写便笺" else "say", "text": "想在窗边种一朵小花。"})

    monkeypatch.setattr(P, "time", SimpleNamespace(time=lambda: now[0], sleep=sleep,
                                                strftime=time.strftime, localtime=time.localtime))
    monkeypatch.setattr(P, "default_chat", chat)
    with pytest.raises(SystemExit):
        P.run_loop(api)
    if enabled and not quiet and not interrupted:
        assert not notes.due(2000)
        assert "便笺" in api._pending["text"]
    else:
        assert not notes.folder().exists()


def test_reply_button_hitbox_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "memory.db"))
    memory.init_db()
    chat = web_chat.WebChat(quick=True)
    sent = []
    monkeypatch.setattr(chat, "show", lambda: True)
    monkeypatch.setattr(chat, "_send", sent.append)
    assert chat.open_reply("今天想学会折纸船")
    sid = chat._sid
    assert memory.load_history(sid) == [{"role": "assistant", "content": "今天想学会折纸船"}]
    assert parse_qs(urlparse(chat._window_url()).query) == {"quick": ["1"], "session": [sid]}
    assert sent[-1].startswith("navigate http://127.0.0.1:")
    assert chat.open_reply("今天想学会折纸船") and chat._sid == sid
    assert memory.message_count(sid) == 1

    api = pet._NativeApi(enabled=False)
    api._x, api._y, api._w, api._h = 100, 200, 400, 600
    opened = []
    monkeypatch.setattr(api, "open_chat", lambda reply: opened.append(reply))
    api.reply_target([0.2, 0.1, 0.3, 0.1], "今天想学会折纸船")
    assert not api.reply_at(110, 210)
    assert api.reply_at(200, 290)
    api._x += 100
    assert not api.reply_at(200, 290) and api.reply_at(300, 290)
    assert opened == ["今天想学会折纸船"] * 2
    rect, text, timestamp = api._reply_target
    api._reply_target = rect, text, timestamp - 3
    assert not api.reply_at(300, 290)
    api.reply_target(None)
    assert not api.reply_at(300, 290)


def test_quick_chat_layout_and_stream(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.set_viewport_size({"width": 480, "height": 560})
    page.goto("http://nyalume.test/?quick=1&session=demo")
    expect(page.locator("#sidebar")).to_be_hidden()
    expect(page.locator("#full-chat-link")).to_have_attribute("href", "/?session=demo")
    expect(page.locator("#input")).to_be_focused()
    page.locator("#input").fill("好呀，一起折纸船")
    page.locator("#send").click()
    page.wait_for_function("typeof window.finishReply === 'function'")
    page.evaluate("window.finishReply('那就先折一条小小的船。')")
    expect(page.locator("#log .msg.assistant").last).to_contain_text("那就先折一条小小的船。")
    for width, height in [(480, 560), (380, 420)]:
        page.set_viewport_size({"width": width, "height": height})
        send = page.locator("#send").bounding_box()
        assert send and 0 <= send["x"] and send["x"] + send["width"] <= width
        assert send["y"] + send["height"] <= height
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "quick-chat.png"))
    print("PREVIEW", tmp_path / "quick-chat.png")
    assert not errors


def test_renderer_reply_button_follows_bubble(tmp_path):
    root = Path(__file__).resolve().parents[1]
    model_dir = root / "models" / "野餐式MikuQ"
    if not model_dir.is_dir():
        pytest.skip("本地模型未安装")
    model_dir, pmx = pet._resolve_model(str(model_dir))
    api = pet._NativeApi(enabled=False)
    port = pet.start_server(model_dir, api=api)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 700, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("""window.actions = []; window.pywebview = {api: {
            poll_action: async () => window.actions.shift() || null,
            reply_target: (rect, text) => { window.reply = {rect, text}; }
        }};""")
        try:
            page.goto(f"http://127.0.0.1:{port}/viewer.html?pmx=/model/{quote(pmx)}&box=440x660&physics=0")
            page.wait_for_function("document.querySelector('#tip').textContent === ''", timeout=60000)
            page.evaluate("window.actions.push({kind:'say', text:'今天想学会折纸船'})")
            page.wait_for_function("window.reply?.rect?.length === 4")
            rect = page.evaluate("window.reply.rect")
            assert 0 <= rect[0] < rect[0] + rect[2] <= 1
            assert 0 <= rect[1] < rect[1] + rect[3] <= 1
            assert page.evaluate("window.reply.text") == "今天想学会折纸船"
            page.screenshot(path=str(tmp_path / "reply-bubble.png"))
            print("PREVIEW", tmp_path / "reply-bubble.png")
            page.evaluate("window.__menu([{id:'chat', label:'打开聊天窗口'}])")
            page.wait_for_function("window.reply.rect === null")
            page.evaluate("window.__menu_close()")
            page.wait_for_function("window.reply.rect !== null")
            assert not errors
        finally:
            browser.close()
