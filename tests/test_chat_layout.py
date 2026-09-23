"""真实聊天页面的布局与交互回归，所有接口使用本地模拟数据。"""

import base64
import datetime
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.fixture
def chat_page():
    html = (Path(__file__).resolve().parents[1] /
            "nyalume/frontends/web/static/index.html").read_text(encoding="utf-8")
    portrait_path = (Path(__file__).resolve().parents[1] /
                     "nyalume/frontends/web/static/daily_nyalume/lucky.png")
    portrait_b64 = base64.b64encode(
        portrait_path.read_bytes() if portrait_path.is_file() else
        base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
    ).decode()
    # 固定在本地当天中午，避免 0:00 后 15 分钟内 now - 900 落到昨天。
    now = datetime.datetime.now().astimezone().replace(
        hour=12, minute=0, second=0, microsecond=0
    ).timestamp()
    rows = [
        {"id": 1, "role": "user", "content": "帮我整理一下课程设计的答辩准备。", "ts": now - 900},
        {"id": 2, "role": "assistant", "content": "可以先从项目背景、技术方案和结果验证三个部分准备。", "ts": now - 890},
        {"id": 3, "role": "user", "content": "把重点放在 Agent 的工具调用和 RAG，给我一个复习计划。", "ts": now - 100},
        {"id": 4, "role": "assistant", "content": "先用一个完整任务串起这两项能力。\n\n第一步：说明模型如何选择工具、读取结果并决定下一步。\n\n第二步：展示文档检索的来源，解释关键词召回的优势和局限。\n\n最后，准备一个失败案例，说明如何定位问题、修复和验证。", "ts": now - 90,
         "trace": {"run_id": "trace-history", "status": "completed", "duration_ms": 1800,
                   "spans": [
                       {"kind": "llm", "name": "chat_stream", "status": "returned", "duration_ms": 1700},
                       {"kind": "tool", "name": "search_docs", "status": "returned", "duration_ms": 80},
                   ]}},
    ]
    errors = []
    deletes = []
    permission = {"mode": "workspace"}
    wallpaper_recents = [
        {"name": "春至", "kind": "video"},
        {"name": "蓝色天空", "kind": "image"},
    ]
    daily_record = {
        "date": "2026-09-08", "score": 95, "profile": "lucky",
        "name": "幸运 Nyalume", "tone": "lucky", "finish": "holo", "blessing": "今天会顺利喵。",
        "keyword": "顺利", "source": "Nyalume · 今日签", "liked": False,
        "collected": False, "viewed": False, "new": True,
        "portrait_url": "data:image/png;base64," + portrait_b64,
    }

    def route_request(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(content_type="text/html", body=html)
            return
        if path.startswith("/api/daily-nyalume/portrait/"):
            route.fulfill(
                content_type="image/gif",
                body=base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="),
            )
            return
        if route.request.method == "DELETE":
            deletes.append(route.request.url)
            result = {"ok": True}
        elif path == "/api/wallpaper/custom":
            result = {
                "ok": True,
                "url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
            }
        elif path == "/api/wallpaper-engine/current":
            result = {
                "ok": True, "kind": "image",
                "url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
                "name": "current.jpg",
            }
        elif path == "/api/wallpaper-engine/next":
            result = {
                "ok": True, "kind": "video",
                "url": "data:video/mp4;base64,AAAA", "name": "next.mp4",
            }
        elif path == "/api/wallpaper-engine/open":
            result = {"ok": True}
        elif path == "/api/wallpaper-engine/recent":
            if route.request.method == "POST":
                index = int(parse_qs(urlparse(route.request.url).query)["index"][0])
                chosen = wallpaper_recents.pop(index)
                wallpaper_recents.insert(0, chosen)
                result = {
                    "ok": True, "kind": chosen["kind"],
                    "url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
                    "name": chosen["name"],
                }
            else:
                result = [dict(row, index=index)
                          for index, row in enumerate(wallpaper_recents)]
        elif path == "/api/backups":
            result = [{
                "name": "agent_20260908_100541.db", "title": "准备 Agent 面试",
                "size": 2658304, "ts": now, "sessions": 7,
                "messages": 124, "documents": 181,
            }]
        elif path == "/api/daily-nyalume/collection":
            result = [dict(daily_record)]
        elif path.startswith("/api/daily-nyalume/card/"):
            daily_record.update(json.loads(route.request.post_data or "{}"))
            result = dict(daily_record)
        elif path == "/api/daily-nyalume":
            result = dict(daily_record) if route.request.method == "POST" else {"drawn": False}
        elif path == "/api/permissions":
            if route.request.method == "PUT":
                permission["mode"] = json.loads(route.request.post_data or "{}").get("mode", "workspace")
            result = {"ok": True, "mode": permission["mode"]}
        elif path == "/api/sessions":
            result = {"id": "empty"} if route.request.method == "POST" else [
                {"id": "demo", "title": "答辩准备", "project": "", "message_count": 4},
                {"id": "empty", "title": "新对话", "project": "", "message_count": 0},
            ]
        elif path.endswith("/messages"):
            result = rows if "/demo/" in path else []
        elif path == "/api/traces/trace-demo":
            result = {
                "run_id": "trace-demo", "status": "completed", "duration_ms": 2400,
                "spans": [
                    {"kind": "llm", "name": "chat_stream", "status": "returned", "duration_ms": 2100},
                    {"kind": "tool", "name": "calculator", "status": "returned", "duration_ms": 3},
                ],
            }
        else:
            result = {
                "/api/projects": [], "/api/undo": {"ops": []},
                "/api/pet/state": {"ok": True, "模型": "测试桌宠", "当前动作": "待机",
                                   "心情": "开心", "风格": "柔和·浓郁", "安静模式": True},
                "/api/state": {"summary": "", "notes": [], "docs": [], "reminders": [],
                               "mode": permission["mode"], "affection": 72},
                "/api/personas": [{"id": "nyalume", "name": "Nyalume"}],
                "/api/persona": {"persona": "nyalume"},
                "/api/wallpaper/settings": {"mode": "", "opacity": 70},
                "/api/skills": [{
                    "id": "study-helper", "name": "学习规划", "description": "把复习任务拆成小步。",
                    "version": "1.0", "enabled": True,
                    "permissions": [{"id": "network", "label": "联网"}],
                    "source": "本地导入", "installed_at": now,
                }],
                "/api/config": {"providers": {
                    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro",
                                 "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
                                 "vision_models": ["deepseek-v4-flash-vision-exp"]},
                    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-5.6-luna",
                               "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
                               "vision_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]},
                    "custom": {"name": "自定义兼容接口", "base_url": "", "model": "", "models": [], "vision_models": []},
                }, "fields": [
                    {"key": "LLM_PROVIDER", "label": "API 供应商", "kind": "select",
                     "options": ["deepseek", "openai", "custom"],
                     "option_labels": {"deepseek": "DeepSeek", "openai": "OpenAI", "custom": "自定义兼容接口"},
                     "value": "deepseek", "default": "deepseek", "configured": False},
                    {"key": "LLM_API_KEY", "configured": True},
                    {"key": "LLM_BASE_URL", "label": "模型接口地址", "value": "https://api.deepseek.com"},
                    {"key": "LLM_MODEL", "value": "deepseek-v4-pro"},
                ]},
            }.get(path, [])
        route.fulfill(content_type="application/json", body=json.dumps(result))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 760})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", route_request)
        page.add_init_script("""(() => {
            const originalFetch = window.fetch;
            window.fetch = (url, options) => {
                if (url !== '/api/chat') return originalFetch(url, options);
                const encoder = new TextEncoder();
                const body = new ReadableStream({start(controller) {
                    const emit = event => controller.enqueue(encoder.encode('data: ' + JSON.stringify(event) + '\\n\\n'));
                    emit({type: 'user_id', message_id: 9, run_id: 'trace-demo'});
                    window.pushEvent = emit;
                    window.pushReply = text => emit({type: 'text', text});
                    window.finishReply = (text) => {
                        emit({type: 'text', text});
                        emit({type: 'done', message_id: 10, run_id: 'trace-demo'});
                        controller.close();
                    };
                }});
                return Promise.resolve(new Response(body, {headers: {'Content-Type': 'text/event-stream'}}));
            };
        })()""")
        try:
            page.goto("http://nyalume.test/")
            expect(page.locator("#log .msg.user").last).to_contain_text(rows[2]["content"])
            expect(page.locator("#persona-select")).to_have_value("nyalume")
            yield page, errors, deletes
        finally:
            browser.close()


def assert_layout(page):
    page.wait_for_function("""() => document.querySelector('#chat-floats').getBoundingClientRect().bottom
      <= document.querySelector('#input-row').getBoundingClientRect().top""")
    log = page.locator("#log").bounding_box()
    composer = page.locator("#input-row").bounding_box()
    assert log["y"] + log["height"] <= composer["y"] + 1
    assert log["height"] >= 150
    assert composer["y"] + composer["height"] <= page.viewport_size["height"] + 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_multiline_composer_and_ime(chat_page):
    page, errors, _ = chat_page
    composer = page.locator("#input")
    assert composer.evaluate("el => el.tagName") == "TEXTAREA"
    assert composer.bounding_box()["height"] >= 80
    composer.fill("第一行")
    composer.press("End")
    composer.press("Shift+Enter")
    composer.press("a")
    expect(composer).to_have_value("第一行\na")
    composer.dispatch_event("keydown", {"key": "Enter", "isComposing": True})
    assert not page.evaluate("state.busy")
    composer.press("Enter")
    expect(page.locator("#log .msg.user").last).to_contain_text("第一行\na")
    expect(composer).to_have_value("")
    page.evaluate("finishReply('收到')")
    page.wait_for_function("!state.busy")
    composer.fill("一行\n" * 30)
    assert composer.bounding_box()["height"] <= 185
    for width, height in [(760, 600), (480, 640)]:
        page.set_viewport_size({"width": width, "height": height})
        assert_layout(page)
    assert not errors


def test_tables_in_history_stream_and_code(chat_page, tmp_path):
    page, errors, _ = chat_page
    table = "| 内容 | 复杂度 | 说明 |\n\\|:------|:--------:|------:|\n\\| **输入框** | 低 | 正常 |\n\\| | | |"
    page.evaluate("text => append('assistant', text, 42)", table)
    history = page.locator("#log .msg.assistant").last
    expect(history.locator("table")).to_have_count(1)
    expect(history.locator("th")).to_have_count(3)
    expect(history.locator("tbody tr")).to_have_count(2)
    expect(history.locator("td .hl")).to_have_text("输入框")
    assert history.locator("th").nth(2).evaluate("el => el.style.textAlign") == "right"
    page.locator("#input").fill("给我一个表格")
    page.locator("#input").press("Enter")
    page.evaluate("pushReply('| A | B |\\n|---|')")
    expect(page.locator(".latest-turn table")).to_have_count(0)
    page.evaluate("pushReply('---|\\n| 1 | 2 |')")
    expect(page.locator(".latest-turn table")).to_have_count(1)
    page.evaluate("finishReply('\\n')")
    page.wait_for_function("!state.busy")
    sample = (
        "A | B\n--- | ---\na\\|b | `<script>alert(1)</script>`\n"
        "`a|b` | **粗体**\n\n普通 | 文本\n\n```text\n" + table + "\n```"
    )
    page.evaluate("text => append('assistant', text, 43)", sample)
    reply = page.locator("#log .msg.assistant").last
    expect(reply.locator("table")).to_have_count(1)
    expect(reply.locator("td").first).to_have_text("a|b")
    expect(reply.locator("tbody tr").nth(1).locator("td").first).to_have_text("`a|b`")
    expect(reply.locator("script")).to_have_count(0)
    expect(reply.locator(".code-block")).to_contain_text("| 内容 |")
    expect(reply).to_contain_text("普通 | 文本")
    page.set_viewport_size({"width": 480, "height": 640})
    assert_layout(page)
    page.screenshot(path=str(tmp_path / "tables-and-composer.png"))
    print("PREVIEW", tmp_path / "tables-and-composer.png")
    wide = "|" + "列|" * 12 + "\n|" + "---|" * 12 + "\n|" + "内容|" * 12
    page.evaluate("text => append('assistant', text, 44)", wide)
    assert_layout(page)
    wrap = page.locator(".chat-table-wrap").last
    assert wrap.evaluate("el => el.scrollWidth > el.clientWidth")
    assert not errors


def test_pet_status_panel(chat_page):
    page, errors, _ = chat_page
    page.locator("#sec-state .side-sec-head").click()
    expect(page.locator("#state-body")).to_contain_text("测试桌宠 · 待机 · 开心 · 柔和·浓郁 · 安静中")
    assert not errors


def test_user_messages_stay_in_normal_history(chat_page, tmp_path):
    page, errors, _ = chat_page
    assert_layout(page)
    expect(page.locator("#user-shelf")).to_have_count(0)
    expect(page.locator("#account-open")).to_have_count(0)
    expect(page.locator("#panel-account")).to_have_count(0)
    assert page.locator(".msg.user").count() == 2
    page.evaluate("""Object.defineProperty(navigator, 'clipboard', {value: {
        writeText: async text => {window.copiedText = text;}
    }})""")
    latest = page.locator("#log .msg.user").last
    latest.locator('button[title="复制"]').click()
    assert "给我一个复习计划" in page.evaluate("window.copiedText")
    latest.locator(".msg-edit-btn").click()
    page.get_by_role("button", name="取消", exact=True).click()
    expect(latest).to_contain_text("给我一个复习计划")
    page.screenshot(path=str(tmp_path / "desktop.png"))
    print("PREVIEW", tmp_path / "desktop.png")
    assert latest.evaluate("el => el.closest('#log') !== null")
    page.evaluate("switchTo('empty')")
    expect(page.locator("#user-shelf")).to_be_hidden()
    page.evaluate("switchTo('demo')")
    expect(page.locator("#log .msg.user").last).to_contain_text("给我一个复习计划")
    history_reply = page.locator("#log .msg.assistant").last
    expect(history_reply.locator(".reply-meta-top")).to_contain_text(
        "思考完毕喵～ · 2 秒 · 2 个步骤"
    )
    expect(history_reply.locator(".proc")).to_be_hidden()
    expect(history_reply.locator(".run-expand")).to_have_text("展开")
    expect(history_reply).not_to_contain_text("执行详情")
    assert not errors


def test_skill_manager_shows_status_and_permissions(chat_page):
    page, errors, _ = chat_page
    page.locator("#config-btn").click()
    page.get_by_role("button", name="Skill 管理", exact=True).click()
    expect(page.locator("#panel-skills")).to_be_visible()
    expect(page.locator("#skill-list")).to_contain_text("学习规划")
    expect(page.locator("#skill-list")).to_contain_text("联网")
    expect(page.locator("#skill-list input[type=checkbox]")).to_be_checked()
    assert not errors


def test_sidebar_sections_reserve_space_and_only_extend_down(chat_page):
    page, errors, _ = chat_page
    projects = page.locator("#sec-projects").bounding_box()
    recent = page.locator("#sec-recent").bounding_box()
    assert projects["height"] >= 180
    assert recent["height"] >= 180
    assert recent["y"] >= projects["y"] + projects["height"] - 1

    page.locator("#sec-state .side-sec-head").click()
    state_box = page.locator("#sec-state").bounding_box()
    assert state_box["height"] >= 180
    assert state_box["y"] >= recent["y"] + recent["height"] - 1
    assert page.locator("#sec-projects .side-sec-head").bounding_box()["height"] == 36
    assert page.locator("#sec-recent .side-sec-head").bounding_box()["height"] == 36
    assert page.locator("#sec-state .side-sec-head").bounding_box()["height"] == 36
    expect(page.locator("#backup-list")).to_contain_text("准备 Agent 面试")
    expect(page.locator("#backup-list")).not_to_contain_text("agent_20260908")
    assert not errors


def test_wallpaper_always_covers_the_whole_window(chat_page):
    page, errors, _ = chat_page
    expect(page.locator("#wall-full")).to_have_count(0)
    page.evaluate("applyWallpaper('data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==')")
    box = page.locator("#wall-bg").bounding_box()
    assert box == {"x": 0, "y": 0, "width": 1100, "height": 760}
    style = page.locator("#wall-bg").evaluate(
        "el => ({size: getComputedStyle(el).backgroundSize, repeat: getComputedStyle(el).backgroundRepeat})"
    )
    assert style == {"size": "cover", "repeat": "no-repeat"}
    assert not errors


def test_daily_nyalume_uses_inline_card_not_modal(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.locator("#daily-nyalume-btn").click()
    card = page.locator(".daily-nyalume-card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("幸运 Nyalume · 95 分")
    expect(card).to_contain_text("今天会顺利喵")
    expect(card).to_contain_text("Nyalume · 今日签")
    expect(card).to_have_attribute("data-finish", "holo")
    expect(card.locator(".daily-nyalume-art")).to_have_count(1)
    expect(page.locator(".modal-overlay:not(.hidden)")).to_have_count(0)
    assert card.locator(".daily-nyalume-portrait").bounding_box()["width"] <= 280
    page.set_viewport_size({"width": 640, "height": 760})
    popover_box = page.locator("#daily-nyalume-popover").bounding_box()
    portrait_box = card.locator(".daily-nyalume-portrait").bounding_box()
    assert popover_box["x"] >= 0
    assert popover_box["x"] + popover_box["width"] <= 640
    assert portrait_box["width"] <= min(280, popover_box["width"])
    page.screenshot(path=str(tmp_path / "daily-nyalume-card.png"))
    print("PREVIEW", tmp_path / "daily-nyalume-card.png")
    card.get_by_role("button", name="添加喜欢").click()
    expect(card.get_by_role("button", name="取消喜欢")).to_be_visible()
    card.get_by_role("button", name="查看收藏卡片").click()
    expect(page.locator(".daily-nyalume-collection")).to_contain_text("顺利")
    page.get_by_role("button", name="♥ 喜欢").click()
    expect(page.locator(".daily-card-thumb")).to_have_count(1)
    page.locator(".daily-card-thumb").click()
    card.get_by_role("button", name="确认并收起今日 Nyalume").click()
    expect(page.locator("#daily-nyalume-layer")).to_have_class("hidden")
    page.locator("#daily-nyalume-btn").click()
    expect(page.locator(".daily-nyalume-card")).to_be_visible()
    assert not errors


def test_cat_head_taps_persist_and_follow_daily_score(chat_page):
    page, errors, _ = chat_page
    page.locator("#cat-tap-stats-btn").click()
    before_taps = int(page.locator("#cat-tap-count").inner_text().split()[0])
    before_meows = int(page.locator("#cat-meow-count").inner_text().split()[0])
    expect(page.locator("#cat-meow-chance")).to_contain_text("喵概率 20%")
    page.locator("#cat-tap-stats-btn").click()

    # 第一项随机数固定为 0，保证触发喵；后续随机数只控制扇形方向与距离。
    page.evaluate("Math.random = () => 0")
    with page.expect_request("**/api/pet/tap") as request_info:
        page.locator("#cat-tap-btn").click()
    assert request_info.value.post_data_json == {"meowed": True}
    expect(page.locator("#cat-tap-feedback")).to_have_text(f"喵次数：{before_meows + 1} 次")
    expect(page.locator(".cat-meow-particle")).to_have_count(1)

    page.locator("#cat-tap-stats-btn").click()
    expect(page.locator("#cat-tap-count")).to_have_text(f"{before_taps + 1} 次")
    expect(page.locator("#cat-meow-count")).to_have_text(f"{before_meows + 1} 次")
    saved = page.evaluate("JSON.parse(localStorage.getItem('nyalume.catTapStats.v1'))")
    assert saved == {"taps": before_taps + 1, "meows": before_meows + 1}

    page.locator("#daily-nyalume-btn").click()
    expect(page.locator("#cat-meow-chance")).to_contain_text("今日 95 分 · 喵概率 48%")
    assert page.evaluate("Math.abs(catMeowChance(0) - .12) < 1e-9")
    assert page.evaluate("Math.abs(catMeowChance(50) - .30) < 1e-9")
    assert page.evaluate("Math.abs(catMeowChance(100) - .48) < 1e-9")
    assert not errors


def test_wallpaper_menu_has_no_builtin_character_wallpaper(chat_page):
    page, errors, _ = chat_page
    page.locator("#wall-btn").click()
    expect(page.locator('#wall-menu button[data-act="char"]')).to_have_count(0)
    expect(page.locator('#wall-menu button[data-act="upload"]')).to_be_visible()
    expect(page.locator('#wall-menu button[data-act="we-recent"]')).to_be_visible()
    assert not errors


def test_daily_mode_disables_files_and_shows_affection(chat_page):
    page, errors, _ = chat_page
    page.locator("#perm-btn").click()
    page.get_by_role("button", name="日常模式", exact=True).click()
    expect(page.locator("#perm-btn")).to_have_text("日常模式")
    expect(page.locator("#add-btn")).to_be_disabled()
    page.locator("#sec-state .side-sec-head").click()
    expect(page.locator("#state-body")).to_contain_text("当前好感度：72")
    assert not errors


def test_openai_provider_preset_fills_endpoint_and_model(chat_page):
    page, errors, _ = chat_page
    expect(page.locator("#input")).to_have_attribute("placeholder", "和 Nyalume 说点什么吧～")
    page.locator("#config-btn").click()
    expect(page.locator("#cfg-help")).to_have_text("?")
    expect(page.locator("#nav-discipline")).to_have_count(0)
    page.locator("#cfg-input-LLM_PROVIDER").select_option("openai")
    expect(page.locator("#cfg-input-LLM_BASE_URL")).to_have_value("https://api.openai.com/v1")
    expect(page.locator("#cfg-input-LLM_MODEL")).to_have_value("gpt-5.6-luna")
    expect(page.locator('[data-config-key="LLM_MODEL"]')).to_be_hidden()
    page.locator("#cfg-input-LLM_PROVIDER").select_option("custom")
    expect(page.locator('[data-config-key="LLM_MODEL"]')).to_be_visible()
    assert not errors


def test_model_menu_follows_provider_and_empty_chat_deletes_quietly(chat_page):
    page, errors, deletes = chat_page
    page.locator("#model-chip").click()
    expect(page.locator("#model-menu")).to_contain_text("deepseek-v4-pro · 文字模型")
    expect(page.locator("#model-menu")).to_contain_text("deepseek-v4-flash-vision-exp · 可看图")
    expect(page.locator("#model-menu")).not_to_contain_text("gpt-5.6")
    expect(page.locator("#model-menu")).not_to_contain_text("识图模型")

    page.evaluate("window.confirmMessages = []; window.confirm = m => { if (m) confirmMessages.push(m); return false; }")
    empty = page.locator(".session-item", has_text="新对话")
    empty.hover()
    empty.get_by_title("会话与工作目录").click()
    empty.get_by_role("button", name="删除会话", exact=True).click()
    page.wait_for_timeout(50)
    assert page.evaluate("confirmMessages") == []
    assert any("/api/sessions/empty" in item for item in deletes)

    filled = page.locator(".session-item", has_text="答辩准备")
    filled.hover()
    filled.get_by_title("会话与工作目录").click()
    filled.get_by_role("button", name="删除会话", exact=True).click()
    assert "小记忆" in page.evaluate("confirmMessages[0]")
    assert not any("/api/sessions/demo" in item for item in deletes)
    assert not errors


def test_custom_wallpaper_opens_cropper_before_upload(chat_page, tmp_path):
    page, errors, _ = chat_page
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAD0lEQVR4nGP4z8DAwMAAAA0AAf8CBRQAAAAASUVORK5CYII="
    )
    page.locator("#wall-file").set_input_files(
        {"name": "wall.png", "mimeType": "image/png", "buffer": png}
    )
    expect(page.locator("#wall-crop-overlay")).to_be_visible()
    expect(page.locator("#wall-crop-zoom-val")).to_have_text("100%")
    page.locator("#wall-crop-zoom").fill("150")
    page.locator("#wall-crop-zoom").dispatch_event("input")
    expect(page.locator("#wall-crop-zoom-val")).to_have_text("150%")
    page.screenshot(path=str(tmp_path / "wall-cropper.png"))
    print("PREVIEW", tmp_path / "wall-cropper.png")
    page.get_by_role("button", name="使用此裁剪").click()
    expect(page.locator("#wall-crop-overlay")).to_be_hidden()
    expect(page.locator("body")).to_have_class("wall-on")
    assert not errors


def test_wallpaper_engine_current_next_and_open(chat_page):
    page, errors, _ = chat_page
    page.locator("#wall-btn").click()
    expect(page.get_by_role("button", name="最近壁纸")).to_have_count(1)
    expect(page.get_by_role("button", name="下一张")).to_have_count(1)
    expect(page.get_by_role("button", name="打开 Wallpaper Engine")).to_have_count(1)
    expect(page.get_by_role("button", name="上一张")).to_have_count(0)

    page.get_by_role("button", name="最近壁纸").click()
    expect(page.locator("#we-recent-list")).to_be_visible()
    expect(page.locator("#we-recent-list")).to_contain_text("春至")
    expect(page.locator("#we-recent-list")).to_contain_text("蓝色天空")

    page.locator('#we-recent-list button[data-index="1"]').click()
    expect(page.locator("#wall-menu")).to_have_class("wall-menu hidden")
    page.locator("#wall-btn").click()
    expect(page.locator("#we-recent-list button").first).to_contain_text("蓝色天空")
    expect(page.get_by_role("button", name="清空最近记录")).to_have_count(1)
    page.get_by_role("button", name="▶ 春至").click()
    expect(page.locator("body")).to_have_class("wall-on")

    page.evaluate("useWallpaperEngine('current')")
    expect(page.locator("body")).to_have_class("wall-on")
    expect(page.locator("#wall-bg")).to_be_visible()

    page.evaluate("useWallpaperEngine('next')")
    expect(page.locator("#wall-video")).to_be_visible()
    expect(page.locator("#wall-bg")).to_be_hidden()

    page.evaluate("useWallpaperEngine('open')")
    assert not errors


def test_stream_edit_and_small_window(chat_page, tmp_path):
    page, errors, deletes = chat_page
    page.locator("#input").fill("请详细解释一下工具参数的流式拼接。")
    page.locator("#send").click()
    expect(page.locator("#log .msg.user").last).to_contain_text("流式拼接")
    expect(page.locator(".latest-turn")).to_have_class("chat-turn latest-turn pending-turn")
    expect(page.locator(".live-think")).to_be_visible()
    assert_layout(page)
    assert page.locator(".latest-turn .live-panel").bounding_box()["y"] >= page.locator("#log").bounding_box()["y"]
    page.evaluate("finishReply('工具参数分多次到达，需要先拼接完整 JSON，再校验并执行。')")
    page.wait_for_function("!state.busy")
    expect(page.locator(".latest-turn")).not_to_have_class("pending-turn")
    expect(page.locator(".latest-turn .reply-meta-top")).to_contain_text("思考完毕喵～ · 2 秒 · 2 个步骤")
    expect(page.locator(".latest-turn .proc")).to_be_hidden()
    expect(page.locator(".latest-turn .run-expand")).to_have_text("展开")
    expect(page.locator(".latest-turn")).not_to_contain_text("执行详情")
    page.locator(".latest-turn .run-expand").click()
    expect(page.locator(".latest-turn .proc")).to_be_visible()
    expect(page.locator(".latest-turn .proc-text")).to_contain_text("工具 · calculator")
    assert page.locator("#log .msg.user").last.get_attribute("data-msgid") == "9"
    page.locator("#log .msg.user").last.locator(".msg-edit-btn").click()
    page.locator("#log .msg.user").last.locator("textarea").fill("再给我一个简单例子。")
    page.get_by_role("button", name="确认", exact=True).click()
    expect(page.locator("#log .msg.user").last).to_contain_text("再给我一个简单例子")
    page.evaluate("finishReply('例如分两次接收参数，然后调用计算器。')")
    page.wait_for_function("!state.busy")
    assert len(deletes) == 1 and "message_id=9" in deletes[0]
    assert page.locator(".msg.user").count() == 3
    expect(page.locator("#log")).not_to_contain_text("工具参数分多次到达")
    for width, height in [(760, 600), (480, 640)]:
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_function("nearBottom()")
        assert_layout(page)
    page.screenshot(path=str(tmp_path / "compact.png"))
    print("PREVIEW", tmp_path / "compact.png")
    page.locator("#input").fill("很长的提问。" * 100)
    page.locator("#send").click()
    expect(page.locator("#log .msg.user").last).to_contain_text("很长的提问")
    assert_layout(page)
    page.evaluate("pushReply('测试长回复。\\n'.repeat(200))")
    page.wait_for_function("nearBottom()")
    page.locator("#log").evaluate("element => {element.scrollTop = 0;}")
    page.wait_for_function("!followLatest")
    page.evaluate("finishReply('这是最后一段。')")
    page.wait_for_function("!state.busy")
    assert page.locator("#log").evaluate("element => element.scrollTop") == 0
    assert_layout(page)
    assert not errors


def test_tool_results_code_blocks_file_folding_and_time_dividers(chat_page):
    page, errors, _ = chat_page
    expect(page.locator(".time-divider")).to_have_count(2)
    expect(page.locator(".time-divider").first).to_contain_text("今天")

    page.locator("#input").fill("执行两个步骤")
    page.locator("#send").click()
    page.evaluate("pushEvent({type:'tool', name:'run_code', desc:'运行 python'})")
    page.evaluate("""pushEvent({type:'tool_result', name:'run_code', result:'第一步输出：42',
      paths:['D:\\\\work\\\\demo-project\\\\workspace\\\\demo.py']})""")
    expect(page.locator(".tool-card-output")).to_contain_text("第一步输出：42")
    page.evaluate("pushEvent({type:'tool', name:'run_code', desc:'运行测试'})")
    page.evaluate("pushEvent({type:'tool_result', name:'run_code', result:'测试通过'})")
    expect(page.locator(".tool-group").first.locator(".tool-group-head")).to_have_text(
        "运行命令 · 2 步"
    )
    page.evaluate("pushEvent({type:'tool', name:'file_read', desc:'读取结果文件'})")
    page.evaluate("pushEvent({type:'tool_result', name:'file_read', result:'第二步输出：完成'})")
    expect(page.locator(".tool-card-output").nth(2)).to_contain_text("第二步输出：完成")
    expect(page.locator(".tool-group").nth(1).locator(".tool-group-head")).to_have_text(
        "读取与检索 · 1 步"
    )
    page.evaluate("pushEvent({type:'tool', name:'web_search', desc:'联网搜索「千恋万花」'})")
    page.evaluate("""pushEvent({type:'tool_result', name:'web_search', result:
      '「千恋万花」的搜索结果：\\n1. 千恋万花官网\\n   https://example.com/official\\n   官方作品介绍\\n2. 千恋万花资料\\n   https://example.com/wiki\\n   角色与剧情资料'})""")
    search_card = page.locator(".tool-card.search-card")
    expect(search_card.locator(".tool-card-title")).to_have_text("联网搜索 · 2 条来源")
    expect(search_card.locator(".search-source-chip")).to_have_count(2)
    assert not search_card.locator(".search-results-fold").evaluate("node => node.open")
    expect(search_card.locator(".search-source-chip > a").first).to_have_attribute(
        "title", "千恋万花官网\n官方作品介绍"
    )
    expect(search_card.locator(".search-results-fold a").first).to_have_attribute(
        "href", "https://example.com/official"
    )
    page.evaluate("finishReply('结果如下：\\n```python\\nprint(42)\\n```')")
    page.wait_for_function("!state.busy")
    expect(page.locator(".latest-turn .proc")).to_be_hidden()
    page.locator(".latest-turn .run-expand").click()
    expect(page.locator(".latest-turn .local-path-link")).to_have_attribute(
        "title", "D:\\work\\demo-project\\workspace\\demo.py"
    )
    with page.expect_request("**/api/local/open") as request_info:
        page.locator(".latest-turn .local-path-link").click()
    assert request_info.value.post_data_json["path"].endswith("workspace\\demo.py")
    expect(page.locator(".latest-turn .code-block code")).to_have_text("print(42)")
    expect(page.locator(".latest-turn .reply-body")).not_to_contain_text("```")

    page.evaluate("""log.appendChild(makeOpsBubble(Array.from({length: 6}, (_, i) => ({
      id: 'op-' + i, kind: 'write', path: 'src/file-' + i + '.py'
    }))))""")
    bubble = page.locator(".ops-bubble").last
    expect(bubble.locator(":scope > .ops-line")).to_have_count(3)
    expect(bubble.locator(".ops-more summary")).to_contain_text("其余 3 个文件")

    assert page.evaluate("getComputedStyle(document.querySelector('.latest-turn')).minHeight === '0px'")
    labels = page.evaluate("""() => {
      log.innerHTML = '';
      state.lastMessageTs = 0;
      state.seenTodayMessage = false;
      const yesterday = new Date();
      yesterday.setDate(yesterday.getDate() - 1);
      const older = new Date();
      older.setDate(older.getDate() - 3);
      append('user', '昨天消息', 20, yesterday.getTime() / 1000);
      append('assistant', '昨天回复', 21, yesterday.getTime() / 1000 + 5);
      append('user', '更早消息', 22, older.getTime() / 1000);
      return Array.from(document.querySelectorAll('.time-divider'), x => x.textContent);
    }""")
    assert labels[0].startswith("昨天 ")
    assert "月" in labels[1] and "日" in labels[1]
    assert not errors
