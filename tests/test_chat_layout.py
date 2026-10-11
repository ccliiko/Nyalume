"""真实聊天页面的布局与交互回归，所有接口使用本地模拟数据。"""

import base64
import datetime
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright


# 让「用户按下停止」到「前端收尾真正跑完」之间隔开一段确定的空档：
# 真实浏览器里中止流、后端释放运行锁、finally 收尾本来就不同步，
# 插入 / 停止之后立刻再发消息时，旧一轮的收尾常常落在新一轮起跑之后。
DELAYED_ABORT_SCRIPT = """() => {
  const original = window.fetch;
  window.fetch = async (url, options) => {
    const response = await original(url, options);
    if (url !== '/api/chat') return response;
    const reader = response.body.getReader();
    const body = new ReadableStream({async start(controller) {
      try {
        while (true) {
          const {done, value} = await reader.read();
          if (done) { controller.close(); break; }
          controller.enqueue(value);
        }
      } catch (error) {
        await new Promise(resolve => setTimeout(resolve, 250));
        controller.error(error);
      }
    }});
    return new Response(body, {headers: response.headers});
  };
}"""


@pytest.fixture
def chat_page(request):
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
        if path.startswith("/static/"):
            asset = Path(__file__).resolve().parents[1] / "nyalume/frontends/web" / path.lstrip("/")
            route.fulfill(content_type=("text/css" if path.endswith(".css") else
                                        "image/png" if path.endswith(".png") else "application/javascript"),
                          body=asset.read_bytes())
            return
        if path == "/api/pet/portrait":
            asset = Path(__file__).resolve().parents[1] / "user_pets/nyalume/idle.png"
            if asset.is_file():
                route.fulfill(content_type="image/png", body=asset.read_bytes())
            else:
                route.fulfill(status=404, body="missing portrait")
            return
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
                "/api/focus": {"current": None, "recent": [], "today": {"count": 0, "seconds": 0}, "server_time": time.time()},
                "/api/projects": [], "/api/undo": {"ops": []},
                "/api/pet/state": {"ok": True, "模型": "测试桌宠", "当前动作": "待机",
                                   "心情": "开心", "风格": "柔和·浓郁", "安静模式": True},
                "/api/state": {"summary": "", "notes": [], "docs": [], "reminders": [],
                               "mode": permission["mode"], "affection": 72},
                "/api/personas": [{"id": "nyalume", "name": "Nyalume"}],
                "/api/persona": {"persona": "nyalume"},
                "/api/companionship/daily-session": {"id": "nyalume-daily"},
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
                    options.signal?.addEventListener('abort', () => controller.error(new DOMException('Stopped', 'AbortError')), {once: true});
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
            page.goto("http://nyalume.test/" + getattr(request, 'param', ''))
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


def test_edit_interrupts_running_reply_before_confirmation(chat_page):
    page, errors, deletes = chat_page
    page.locator('#input').fill('原来的问题')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    page.evaluate("pushReply('旧回答的一部分')")
    page.locator('.latest-turn .msg-edit-btn').click()
    page.wait_for_function('!state.busy')
    expect(page.locator('.latest-turn .edit-box')).to_be_focused()
    page.locator('.latest-turn .edit-box').fill('修改后的问题')
    page.get_by_role('button', name='确认', exact=True).click()
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('修改后的问题')
    expect(page.locator('#log')).not_to_contain_text('旧回答的一部分')
    page.evaluate("finishReply('按新问题回答')")
    page.wait_for_function('!state.busy')
    assert len(deletes) == 1
    assert page.locator('.msg.user').count() == 3
    assert not errors


def test_new_chat_opens_empty_composer_directly(chat_page):
    page, errors, _ = chat_page
    page.locator('#new-btn').click()
    expect(page.locator('#home-panel')).to_be_hidden()
    expect(page.locator('#input')).to_be_visible()
    expect(page.locator('#input')).to_be_focused()
    expect(page.locator('#log .empty-hint')).to_be_visible()
    assert not errors


def test_slash_menu_keyboard_goal_stream_and_exit(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.evaluate("""() => {
      const previous = window.fetch;
      window.fetch = (url, options) => {
        if (url === '/api/pursuit/start') {
          window.goalPayload = JSON.parse(options.body);
          return Promise.resolve(new Response(JSON.stringify({pursuit: {id: 'test-goal'}})));
        }
        if (url === '/api/pursuit/test-goal/run') {
          const initial = [
            {type:'user_id', message_id:11},
            {type:'pursuit_plan', analysis:'先计算并检查结果', formatted:'⬜ #1 计算\\n⬜ #2 预先生成的步骤'},
            {type:'pursuit_step_start', task:'计算'}
          ];
          const rest = [
            {type:'pursuit_step_done', result:'结果是 4'},
            {type:'pursuit_progress', summary:'计算结果已验证', formatted:'✅ #1 计算'},
            {type:'pursuit_complete', summary:'目标已完成', formatted:'✅ #1 计算'},
            {type:'done', message_id:12}
          ];
          const encoder = new TextEncoder();
          return Promise.resolve(new Response(new ReadableStream({start(controller) {
            const emit = e => controller.enqueue(encoder.encode('data: ' + JSON.stringify(e) + '\\n\\n'));
            initial.forEach(emit);
            window.finishGoal = () => { rest.forEach(emit); controller.close(); };
          }})));
        }
        return previous(url, options);
      };
    }""")
    page.locator('#input').fill('/')
    expect(page.locator('#slash-menu')).to_be_visible()
    expect(page.locator('#slash-menu button')).to_have_count(5)
    page.locator('#input').press('ArrowDown')
    expect(page.locator('#slash-menu button').nth(1)).to_have_attribute('aria-selected', 'true')
    page.locator('#input').press('ArrowUp')
    expect(page.locator('#slash-menu button').first).to_have_attribute('aria-selected', 'true')
    page.screenshot(path=str(tmp_path / 'slash-menu.png'))
    print('PREVIEW', tmp_path / 'slash-menu.png')
    page.locator('#input').press('Escape')
    expect(page.locator('#slash-menu')).to_be_hidden()
    page.locator('#input').fill('/goal')
    page.locator('#input').press('Enter')
    expect(page.locator('#goal-mode')).to_be_visible()
    expect(page.locator('#input')).to_have_value('')
    page.locator('#input').fill('计算 2+2')
    page.locator('#send').click()
    expect(page.locator('.latest-turn .process-commentary')).to_have_text('先计算并检查结果')
    expect(page.locator('.latest-turn .process-commentary')).to_be_visible()
    expect(page.locator('.latest-turn .work-status-text').last).to_contain_text('正在执行：计算')
    expect(page.locator('.latest-turn .reply-body')).to_be_empty()
    expect(page.locator('.latest-turn')).not_to_contain_text('预先生成的步骤')
    page.evaluate('finishGoal()')
    expect(page.locator('.latest-turn .reply-body')).to_contain_text('目标已完成')
    page.wait_for_function('!state.busy')
    expect(page.locator('.latest-turn .reply-body')).to_have_text('目标已完成')
    expect(page.locator('.latest-turn .process-commentary')).to_have_count(2)
    assert page.evaluate('goalPayload.goal') == '计算 2+2'
    assert page.evaluate('goalPayload.session_id') == 'demo'
    page.locator('#goal-exit').click()
    expect(page.locator('#goal-mode')).to_be_hidden()
    page.locator('#input').fill('普通文本 /')
    expect(page.locator('#slash-menu')).to_be_hidden()
    assert not errors


@pytest.mark.parametrize('chat_page', ['', '?quick=1'], indirect=True)
def test_slash_menu_handles_ime_commit_fullwidth_and_refocus(chat_page):
    page, errors, _ = chat_page
    field = page.locator('#input')
    field.focus()
    page.keyboard.type('/')
    expect(page.locator('#slash-menu')).to_be_visible()
    field.fill('')
    page.evaluate("""() => {
      const field = document.getElementById('input');
      field.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
      field.value = '/';
      field.dispatchEvent(new InputEvent('input', {bubbles:true, isComposing:true, data:'/'}));
    }""")
    expect(page.locator('#slash-menu')).to_be_visible()
    field.dispatch_event('keydown', {'key': 'Enter', 'isComposing': False})
    assert not page.evaluate('state.busy || state.pursuitMode')
    page.evaluate("document.getElementById('input').dispatchEvent(new CompositionEvent('compositionend', {bubbles:true,data:'/'}))")
    expect(page.locator('#slash-menu button')).to_have_count(5)
    field.fill('／goal')
    expect(page.locator('#slash-menu button')).to_have_count(1)
    field.press('Enter')
    expect(page.locator('#goal-mode')).to_be_visible()
    field.fill('正文 /')
    expect(page.locator('#slash-menu')).to_be_hidden()
    page.evaluate("document.getElementById('input').value = '/'")
    page.locator('#send').focus()
    field.focus()
    expect(page.locator('#slash-menu')).to_be_visible()
    assert not errors


@pytest.mark.parametrize('chat_page', ['', '?quick=1'], indirect=True)
def test_plan_mode_slash_queue_and_exit(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.evaluate("""() => {
      const previous = window.fetch; window.chatPayloads = [];
      window.fetch = (url, options) => {
        if (url === '/api/chat') chatPayloads.push(JSON.parse(options.body));
        return previous(url, options);
      };
    }""")
    field = page.locator('#input')
    toggle = page.locator('#plan-toggle')
    field.fill('／plan')
    expect(page.locator('#slash-menu button')).to_have_count(1)
    field.press('Enter')
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    expect(field).to_be_focused()
    expect(field).to_have_value('')
    assert not page.evaluate('state.busy || state.pursuitMode')
    field.fill('为这个项目制定改造计划')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    field.fill('补充：保持现有接口')
    page.locator('#send').click()
    assert page.evaluate('state.queue[0].plan') is True
    page.evaluate("finishReply('先确认目标，再拆分修改与验证步骤。')")
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('保持现有接口')
    page.evaluate("finishReply('计划已调整，保留现有接口。')")
    page.wait_for_function('!state.busy')
    assert [row['mode'] for row in page.evaluate('chatPayloads')] == ['plan', 'plan']
    for width, height in [(760, 600), (480, 640)]:
        page.set_viewport_size({'width': width, 'height': height})
        assert_layout(page)
        assert toggle.bounding_box()['x'] + toggle.bounding_box()['width'] <= width
    page.screenshot(path=str(tmp_path / 'plan-mode.png'))
    print('PREVIEW', tmp_path / 'plan-mode.png')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    field.fill('按计划执行')
    field.press('Enter')
    page.evaluate("finishReply('已进入普通工作模式。')")
    page.wait_for_function('!state.busy')
    assert page.evaluate('chatPayloads.at(-1).mode') == 'chat'
    assert not errors


def test_plan_mode_shortcuts_inline_command_and_session_reset(chat_page):
    page, errors, _ = chat_page
    toggle = page.locator('#plan-toggle')
    field = page.locator('#input')
    page.keyboard.press('Control+k')
    page.locator('#command-search').fill('plan')
    page.locator('#command-search').press('Enter')
    expect(page.locator('#command-dialog')).not_to_be_visible()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    field.fill('/goal')
    field.press('Enter')
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    expect(page.locator('#goal-mode')).to_be_visible()
    toggle.click()
    expect(page.locator('#goal-mode')).to_be_hidden()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    page.locator('#new-btn').click()
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    field.fill('/plan')
    page.locator('#send').click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    expect(field).to_have_value('')
    assert not page.evaluate('state.busy')
    toggle.click()
    field.fill('/plan 分析当前实现并列出步骤')
    field.press('Enter')
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('分析当前实现并列出步骤')
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    page.evaluate("finishReply('步骤：检查实现，确认方案，安排验证。')")
    page.wait_for_function('!state.busy')
    page.evaluate("state.renderPerm('daily')")
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    toggle.click()
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    assert not errors


def test_task_edit_interrupts_and_preserves_queued_message(chat_page):
    page, errors, _ = chat_page
    page.locator('#input').fill('正在生成的提问')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    page.locator('#input').fill('排队的第二个提问')
    page.locator('#send').click()
    page.locator('#task-panel .running button').click()
    page.wait_for_function('!state.busy')
    expect(page.locator('.edit-box')).to_have_value('正在生成的提问')
    expect(page.locator('#task-panel')).to_contain_text('排队的第二个提问')
    page.locator('.edit-box').fill('重写的第一个提问')
    page.get_by_role('button', name='确认', exact=True).click()
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('重写的第一个提问')
    page.evaluate("finishReply('第一个新答案')")
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('排队的第二个提问')
    page.evaluate("finishReply('第二个答案')")
    page.wait_for_function('!state.busy')
    assert not errors


def test_stopped_run_keeps_accepting_new_messages(chat_page):
    """停止一轮回复后，输入框和队列里的消息必须还能继续发出去。

    旧一轮的前端收尾（finally）可能落在停止确认之后：那时新一轮已经跑起来了，
    收尾若还去改 state.busy，忙碌标记就再也放不下来，之后所有消息只能排队。
    DELAYED_ABORT 让收尾晚于停止确认 250 毫秒，正好造出这个时序。
    """
    page, errors, _ = chat_page
    page.evaluate(DELAYED_ABORT_SCRIPT)
    page.locator('#input').fill('第一轮提问')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    stopped_turn = page.locator('.chat-turn').filter(has=page.locator('.msg.user').filter(has_text='第一轮提问'))
    page.locator('#input').fill('排队的提问')
    page.locator('#send').click()
    page.locator('#send').click()               # 停止按钮：此时输入框是空的
    expect(stopped_turn).to_contain_text('已停止思考喵')
    # 队列里的消息随后自己发出去，收尾之后忙碌标记必须放开。
    expect(page.locator('.chat-turn').last).to_contain_text('排队的提问')
    page.evaluate("finishReply('队列的回答')")
    expect(page.locator('.chat-turn').last).to_contain_text('队列的回答')
    page.wait_for_function('!state.busy')
    # 停止之后再发一条：输入框里的消息同样必须发得出去。
    page.locator('#input').fill('停止后的新提问')
    page.locator('#send').click()
    expect(page.locator('.chat-turn').last).to_contain_text('停止后的新提问')
    page.evaluate("finishReply('停止后的回答')")
    expect(page.locator('.chat-turn').last).to_contain_text('停止后的回答')
    page.wait_for_function('!state.busy')
    expect(page.locator('#task-panel')).to_have_class(re.compile(r'\bhidden\b'))
    assert not errors


def test_insert_queued_message_keeps_composer_usable(chat_page):
    """点「插入」打断当前回复后，输入框和队列必须还能继续工作。"""
    page, errors, _ = chat_page
    page.evaluate(DELAYED_ABORT_SCRIPT)
    page.locator('#input').fill('被打断的提问')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    page.locator('#input').fill('插入的提问')
    page.locator('#send').click()
    page.locator('#task-panel .task-row:not(.running) button[title^="插入"]').click()
    expect(page.locator('.chat-turn').last).to_contain_text('插入的提问')
    page.evaluate("finishReply('插入的回答')")
    expect(page.locator('.chat-turn').last).to_contain_text('插入的回答')
    page.wait_for_function('!state.busy')
    # 插入打断之后，输入框照样能发出新消息，任务面板也要归位。
    page.locator('#input').fill('插入之后的新提问')
    page.locator('#send').click()
    expect(page.locator('.chat-turn').last).to_contain_text('插入之后的新提问')
    page.evaluate("finishReply('之后的回答')")
    expect(page.locator('.chat-turn').last).to_contain_text('之后的回答')
    page.wait_for_function('!state.busy')
    expect(page.locator('#task-panel')).to_have_class(re.compile(r'\bhidden\b'))
    assert not errors


def test_new_chat_stops_previous_generation(chat_page):
    page, errors, _ = chat_page
    page.locator('#input').fill('旧会话提问')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    page.locator('#new-btn').click()
    expect(page.locator('#home-panel')).to_be_hidden()
    expect(page.locator('#input')).to_be_focused()
    expect(page.locator('.msg.user')).to_have_count(0)
    page.locator('#input').fill('新会话提问')
    page.locator('#send').click()
    page.evaluate("finishReply('新会话回复')")
    page.wait_for_function('!state.busy')
    expect(page.locator('.msg.user')).to_have_count(1)
    expect(page.locator('#log')).not_to_contain_text('旧会话提问')
    assert not errors


def test_goal_cancel_during_creation_cleans_pending_goal(chat_page):
    page, errors, _ = chat_page
    page.evaluate("""() => {
      const previous = window.fetch;
      window.fetch = (url, options) => {
        if (url === '/api/pursuit/start') return new Promise(resolve => {
          window.finishGoalCreation = () => resolve(new Response(JSON.stringify({pursuit:{id:'pending'}})));
        });
        if (url === '/api/pursuit/pending/stop') {
          window.pendingGoalStopped = true;
          return Promise.resolve(new Response('{"ok":true}'));
        }
        return previous(url, options);
      };
    }""")
    page.locator('#input').fill('/goal 做一个小任务')
    page.locator('#input').press('Enter')
    page.wait_for_function('state.busy && typeof finishGoalCreation === "function"')
    page.locator('#send').click()
    page.evaluate('finishGoalCreation()')
    page.wait_for_function('!state.busy && window.pendingGoalStopped')
    assert not errors


def test_goal_selection_preserves_daily_permissions(chat_page):
    page, errors, _ = chat_page
    page.locator('#perm-btn').click()
    page.get_by_role('button', name='日常模式', exact=True).click()
    page.wait_for_function("state.chatMode === 'daily' && state.currentId === 'nyalume-daily'")
    page.locator('#input').fill('/goal')
    page.locator('#input').press('Enter')
    expect(page.locator('#goal-mode')).to_be_hidden()
    assert page.evaluate('state.chatMode') == 'daily'
    expect(page.locator('#task-toast')).to_contain_text('权限菜单')
    assert not errors


def test_work_status_is_one_line_and_outputs_fold_after_reply(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => window.copiedReply = text}})")
    page.locator('#input').fill('检查代码，然后保存结果')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    page.evaluate("pushEvent({type:'reasoning',text:'先确认文件位置，再检查代码。'})")
    status = page.locator('.latest-turn .live-think')
    expect(status).to_contain_text('先确认文件位置')
    assert status.evaluate("el => getComputedStyle(el).userSelect") == 'none'
    assert status.evaluate("el => getComputedStyle(el).animationDuration") == '3s'
    page.evaluate("pushReply('先读一下文件，再执行验证。')")
    page.evaluate("""pushEvent({type:'tool',name:'run_code',desc:'运行命令',
      detail:'D:/projects/nyalume · python -c "print(42)" ' + '很长的参数 '.repeat(50)})""")
    status = page.locator('.latest-turn .tool-group-head')
    expect(status).to_be_visible()
    expect(status.locator('.work-status-tail')).to_have_text('喵')
    expect(status.locator('.work-status-text')).to_contain_text('D:/projects/nyalume · python')
    assert status.locator('.work-status-text').evaluate("el => getComputedStyle(el).textOverflow") == 'ellipsis'
    assert status.evaluate("el => getComputedStyle(el).backgroundImage") != 'none'
    expect(page.locator('.latest-turn .proc-text')).to_be_hidden()
    assert page.locator('.latest-turn .tool-group').count() == 1
    assert page.locator('.latest-turn .exec-item').count() == 0
    expect(page.locator('.latest-turn .proc')).to_be_visible()
    expect(page.locator('.latest-turn .process-commentary')).to_be_visible()
    expect(page.locator('.latest-turn .process-commentary')).to_contain_text('先读一下文件')
    expect(page.locator('.latest-turn .tool-card-output')).to_be_hidden()
    page.set_viewport_size({'width': 480, 'height': 640})
    assert_layout(page)
    assert status.bounding_box()['height'] < 32
    page.set_viewport_size({'width': 1100, 'height': 760})
    page.screenshot(path=str(tmp_path / 'single-work-status.png'))
    print('PREVIEW', tmp_path / 'single-work-status.png')
    page.evaluate("pushEvent({type:'tool_result',name:'run_code',result:'42'})")
    expect(status).to_contain_text('运行了命令')
    page.evaluate("pushEvent({type:'tool',name:'file_write',desc:'写文件 result.txt'})")
    page.evaluate("pushEvent({type:'tool_result',name:'file_write',result:'已保存 result.txt'})")
    page.evaluate("pushEvent({type:'tool',name:'describe_image',desc:'查看 image.png'})")
    page.evaluate("pushEvent({type:'tool_result',name:'describe_image',result:'图像包含一只猫',paths:['image.png']})")
    page.evaluate("pushReply('最终结果：\\n```python\\nprint(42)\\n```')")
    expect(page.locator('.latest-turn .reply-body .code-head button')).to_be_hidden()
    page.evaluate("finishReply('\\n已保存结果。')")
    page.wait_for_function('!state.busy')
    expect(page.locator('.latest-turn .proc')).to_be_hidden()
    expect(page.locator('.latest-turn .reply-meta-top')).to_contain_text('编辑了文件、运行了命令、已查看 1 张图像喵～')
    expect(page.locator('.latest-turn .reply-body .code-head button')).to_be_visible()
    expect(page.locator('.latest-turn .reply-body')).not_to_contain_text('先读一下文件')
    page.locator('.latest-turn button[title="复制这条回复"]').click()
    page.wait_for_function('window.copiedReply')
    assert '最终结果' in page.evaluate('copiedReply')
    assert '先读一下文件' not in page.evaluate('copiedReply')
    page.locator('.latest-turn .run-expand').click()
    expect(page.locator('.latest-turn .proc')).to_be_visible()
    expect(page.locator('.latest-turn .process-commentary')).to_contain_text('先读一下文件')
    page.locator('.latest-turn .tool-group-head').click()
    expect(page.locator('.latest-turn .tool-card-output').first).to_have_text('42')
    assert page.locator('.latest-turn .tool-card-output').first.evaluate("el => getComputedStyle(el).userSelect") != 'none'
    assert not errors


def test_context_compression_notice_is_visible_and_persists(chat_page):
    page, errors, _ = chat_page
    page.locator('#input').fill('继续之前的工作')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    page.evaluate("pushEvent({type:'context_compression',status:'started',count:12})")
    notice = page.locator('.latest-turn .context-notice')
    expect(notice).to_be_visible()
    expect(notice).to_contain_text('正在整理 12 条')
    assert notice.evaluate("el => getComputedStyle(el).animationDuration") == '3s'
    page.evaluate("pushEvent({type:'context_compression',status:'completed',count:12})")
    expect(notice).to_have_attribute('data-status', 'completed')
    page.evaluate("finishReply('重点都记住了，继续吧喵。')")
    page.wait_for_function('!state.busy')
    expect(notice).to_be_visible()
    expect(notice).to_contain_text('已整理 12 条')
    assert not errors


def test_workflow_error_folds_and_releases_unfinished_operation(chat_page):
    page, errors, _ = chat_page
    page.locator('#input').fill('运行检查')
    page.locator('#send').click()
    page.evaluate("pushReply('我先运行一次检查。')")
    page.evaluate("pushEvent({type:'tool',name:'run_code',desc:'python check.py'})")
    page.evaluate("pushEvent({type:'error',message:'检查失败，请修复配置'})")
    page.evaluate("finishReply('')")
    page.wait_for_function('!state.busy')
    expect(page.locator('.latest-turn .proc')).to_be_hidden()
    expect(page.locator('.latest-turn .reply-meta-top')).to_contain_text('这次卡住了喵')
    page.locator('.latest-turn .run-expand').click()
    expect(page.locator('.latest-turn .tool-group-head')).to_contain_text('操作未完成喵')
    page.locator('.latest-turn .tool-group-head').click()
    expect(page.locator('.latest-turn .tool-card-title')).to_contain_text('未完成')
    assert page.locator('.latest-turn .tool-card').evaluate("el => getComputedStyle(el).userSelect") != 'none'
    assert not errors


def test_retired_controls_are_absent(chat_page):
    page, errors, _ = chat_page
    assert page.locator('#pet-plan-toggle, #home-plan, #pet-plan-panel').count() == 0
    page.locator('#command-open').click()
    expect(page.locator('#command-list')).not_to_contain_text('她的小计划')
    page.locator('#command-close').click()
    expect(page.locator('#state-body')).not_to_contain_text('全局提醒')
    assert not errors


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
    expect(reply.locator("tbody tr").nth(1).locator("td").first.locator("code")).to_have_text("a|b")
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
        "查阅了资料喵～ · 2 秒 · 2 个步骤"
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


def test_sidebar_compact_sections_and_fixed_footer(chat_page, tmp_path):
    page, errors, _ = chat_page
    projects = page.locator("#sec-projects").bounding_box()
    recent = page.locator("#sec-recent").bounding_box()
    assert projects["height"] < 140
    assert recent["height"] < 180
    assert recent["y"] >= projects["y"] + projects["height"] - 1

    page.locator("#sec-state .side-sec-head").click()
    state_box = page.locator("#sec-state").bounding_box()
    assert state_box["height"] >= 180
    assert state_box["y"] >= recent["y"] + recent["height"] - 1
    assert page.locator("#sec-projects .side-sec-head").bounding_box()["height"] == 36
    assert page.locator("#sec-recent .side-sec-head").bounding_box()["height"] == 36
    assert page.locator("#sec-state .side-sec-head").bounding_box()["height"] == 36
    page.locator('#memory-backups summary').click()
    expect(page.locator("#backup-list")).to_contain_text("准备 Agent 面试")
    expect(page.locator("#backup-list")).not_to_contain_text("agent_20260908")
    footer = page.locator(".sidebar-foot").bounding_box()
    page.locator("#sidebar-scroll").evaluate("el => el.scrollTop = el.scrollHeight")
    assert page.locator(".sidebar-foot").bounding_box() == footer
    assert footer["y"] + footer["height"] <= page.viewport_size["height"]
    page.locator("#sidebar-scroll").evaluate("el => el.scrollTop = 0")
    page.locator("#sec-state .side-sec-head").click()
    expect(page.locator("#sec-state .side-sec-head")).to_have_attribute("aria-expanded", "false")
    page.locator("#home-nav").click()
    page.screenshot(path=str(tmp_path / "sidebar-home.png"))
    print("PREVIEW", tmp_path / "sidebar-home.png")
    assert not errors


def test_sidebar_keyboard_long_list_menu_and_reload(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.locator('#session-list .session-text').first.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#log .msg.user')).to_have_count(2)
    title = page.locator('#session-list .session-title').first
    width = title.bounding_box()['width']
    page.locator('#session-list .session-item').first.hover()
    assert title.bounding_box()['width'] == width
    page.locator('#sec-state .side-sec-head').click()
    expect(page.locator('#sec-state .side-sec-head')).to_have_attribute('aria-expanded', 'true')
    page.reload()
    expect(page.locator('#sec-state .side-sec-head')).to_have_attribute('aria-expanded', 'true')
    page.locator('#sec-state .side-sec-head').click()
    page.evaluate("renderSessionList(listBox, Array.from({length: 30}, (_, i) => ({id: 'demo', title: '一段很长的对话标题，用来验证菜单和滚动 ' + i, message_count: i})))")
    footer = page.locator('.sidebar-foot').bounding_box()
    page.locator('#sidebar-scroll').evaluate('el => el.scrollTop = el.scrollHeight')
    page.locator('#session-list .act-btn').last.click()
    menu = page.locator('#session-list .more-menu:not(.hidden)')
    expect(menu).to_be_visible()
    box = menu.bounding_box()
    assert box['y'] >= 0 and box['y'] + box['height'] <= page.viewport_size['height']
    expect(menu).to_contain_text('置顶对话')
    page.keyboard.press('Escape')
    expect(menu).to_have_count(0)
    assert page.locator('.sidebar-foot').bounding_box() == footer
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#sidebar-toggle').click()
    expect(page.locator('#config-btn')).to_be_visible()
    page.screenshot(path=str(tmp_path / 'sidebar-mobile.png'))
    print('PREVIEW', tmp_path / 'sidebar-mobile.png')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
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
    assert card.locator(".daily-nyalume-art").bounding_box()["width"] <= 280
    page.set_viewport_size({"width": 640, "height": 760})
    page.wait_for_function("document.getElementById('daily-nyalume-popover').getBoundingClientRect().right <= innerWidth")
    popover_box = page.locator("#daily-nyalume-popover").bounding_box()
    portrait_box = card.locator(".daily-nyalume-art").bounding_box()
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


def test_daily_cards_share_layers_drag_flip_and_album_art(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.locator("#daily-nyalume-btn").click()
    art = page.locator(".daily-nyalume-art")
    expect(art.locator(".daily-card-layer")).to_have_count(4)
    assert art.locator("img").evaluate_all("imgs => imgs.every(i => i.complete && i.naturalWidth === 1024)")
    bounds = art.bounding_box()
    x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 60, y + 30, steps=5)
    page.mouse.up()
    assert art.evaluate("el => parseFloat(el.style.getPropertyValue('--card-ry'))") > 0
    assert art.evaluate("el => parseFloat(el.style.getPropertyValue('--parallax-x'))") > 0
    page.get_by_role("button", name="翻面 · 看小秘密").click()
    expect(art).to_have_class("daily-nyalume-art flipped")
    expect(art.locator(".daily-card-back")).to_have_attribute("aria-hidden", "false")
    expect(art.locator(".daily-card-egg")).to_contain_text("大王")
    page.get_by_role("button", name="翻回正面").click()
    page.get_by_role("button", name="查看收藏卡片").click()
    expect(page.locator(".daily-card-thumb img")).to_have_attribute("src", "/static/daily_nyalume/lucky.png?v=trio-v1")
    page.locator(".daily-card-thumb").click()
    expect(art.locator(".daily-card-layer")).to_have_count(4)
    page.get_by_role("button", name="翻面 · 看小秘密").click()
    expect(art.locator(".daily-card-egg")).to_contain_text("大王")
    page.emulate_media(reduced_motion="reduce")
    page.get_by_role("button", name="翻回正面").click()
    expect(art.locator(".daily-card-front")).to_have_attribute("aria-hidden", "false")
    eggs = []
    for profile in page.evaluate("Object.keys(NyalumeCardCatalog)"):
        page.evaluate("profile => renderDailyNyalume({profile, tone: profile, name: NyalumeCardCatalog[profile].title, score: 50, date: '2026-09-08', blessing: '测试'})", profile)
        expect(art).to_have_attribute("data-profile", profile)
        expect(art.locator(".daily-card-layer")).to_have_count(4)
        page.get_by_role("button", name="翻面 · 看小秘密").click()
        eggs.append(art.locator(".daily-card-egg").inner_text())
    assert len(set(eggs)) == 11
    page.screenshot(path=str(tmp_path / "daily-card-back.png"))
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


def test_shared_memory_controls_and_daily_session(chat_page):
    page, errors, _ = chat_page
    requests = []
    page.route('**/api/state*', lambda route: route.fulfill(content_type='application/json', body=json.dumps(
        {'summary': '', 'notes': [], 'docs': [], 'reminders': [], 'mode': 'daily', 'affection': 50,
         'shared_memories': [{'id': 'mem-test', 'content': '称呼：小林', 'scope': 'shared', 'updated_at': time.time(), 'proactive': 1, 'category': 'user', 'title': '称呼', 'summary': '称呼：小林', 'keywords': []}]})))
    def edit(route):
        requests.append(json.loads(route.request.post_data or '{}'))
        route.fulfill(content_type='application/json', body='{"ok":true}')
    page.route('**/api/companionship/memories/mem-test', edit)
    page.locator('#perm-btn').click()
    page.get_by_role('button', name='日常模式', exact=True).click()
    page.locator('#sec-state .side-sec-head').click()
    expect(page.locator('#state-body')).to_contain_text('称呼：小林')
    with page.expect_response('**/api/companionship/memories/mem-test'):
        page.locator('[data-comp-memory="mem-test"][data-action="mute"]').click()
    assert requests == [{'proactive': False}]
    page.evaluate("fetch('/api/permissions', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode:'workspace'})})")
    page.goto('http://nyalume.test/?session=nyalume-daily')
    expect(page.locator('#perm-btn')).to_have_text('日常模式')
    expect(page.locator('#add-btn')).to_be_disabled()
    with page.expect_response('**/api/companionship/daily-session'):
        page.locator('#new-btn').click()
    expect(page.locator('#perm-btn')).to_have_text('日常模式')
    assert not errors


def test_openai_provider_preset_fills_endpoint_and_model(chat_page):
    page, errors, _ = chat_page
    expect(page.locator("#input")).to_have_attribute("placeholder", "今天的心事，或想一起完成的小任务，都可以告诉我喵…")
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
    expect(page.locator(".latest-turn .reply-meta-top")).to_contain_text("算好了结果喵～ · 2 秒 · 2 个步骤")
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


def test_tool_results_code_blocks_file_folding_and_time_dividers(chat_page, tmp_path):
    page, errors, _ = chat_page
    expect(page.locator(".time-divider")).to_have_count(2)
    expect(page.locator(".time-divider").first).to_contain_text("今天")

    page.locator("#input").fill("执行两个步骤")
    page.locator("#send").click()
    page.evaluate("pushEvent({type:'tool', name:'run_code', desc:'运行 python'})")
    page.evaluate("""pushEvent({type:'tool_result', name:'run_code', result:'第一步输出：42',
      paths:['D:\\\\work\\\\demo-project\\\\workspace\\\\demo.py']})""")
    page.locator(".tool-group-head").first.click()
    expect(page.locator(".tool-card-output")).to_contain_text("第一步输出：42")
    page.evaluate("pushEvent({type:'tool', name:'run_code', desc:'运行测试'})")
    page.evaluate("pushEvent({type:'tool_result', name:'run_code', result:'测试通过'})")
    expect(page.locator(".tool-group").first.locator(".tool-group-head")).to_have_text(
        "运行了命令喵"
    )
    page.evaluate("pushReply('命令完成了，接下来读结果文件。')")
    page.evaluate("pushEvent({type:'tool', name:'file_read', desc:'读取结果文件'})")
    page.evaluate("pushEvent({type:'tool_result', name:'file_read', result:'第二步输出：完成'})")
    expect(page.locator(".tool-card-output").nth(2)).to_contain_text("第二步输出：完成")
    expect(page.locator(".tool-group").nth(1).locator(".tool-group-head")).to_have_text(
        "查阅了资料喵"
    )
    assert page.locator('.tool-feed').evaluate("el => [...el.children].map(node => node.className)") == ['tool-group', 'process-commentary', 'tool-group']
    assert page.locator('.tool-card-output').first.evaluate("el => getComputedStyle(el).userSelect") != 'none'
    page.evaluate("pushReply('我再查一下网页资料。')")
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
    page.locator('.tool-group').first.evaluate('el => el.open = false')
    page.screenshot(path=str(tmp_path / 'interleaved-workflow.png'))
    print('PREVIEW', tmp_path / 'interleaved-workflow.png')
    page.evaluate("finishReply('结果如下：\\n```python\\nprint(42)\\n```')")
    page.wait_for_function("!state.busy")
    expect(page.locator(".latest-turn .proc")).to_be_hidden()
    assert page.locator('.latest-turn .tool-group').evaluate_all('nodes => nodes.every(node => !node.open)')
    page.locator(".latest-turn .run-expand").click()
    page.locator(".latest-turn .tool-group-head").first.click()
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


def test_queue_insert_waits_for_cleanup_and_delete_is_clickable(chat_page):
    page, errors, _ = chat_page
    page.evaluate("""() => {
      const previous = fetch; window.sentTexts = [];
      window.fetch = (url, options) => {
        if (url === '/api/chat') sentTexts.push(JSON.parse(options.body).message);
        if (String(url).includes('/cancel?wait=true')) return new Promise(resolve => {
          window.releaseStop = () => resolve(new Response('{"ok":true,"active":false}'));
        });
        return previous(url, options);
      };
    }""")
    page.locator('#input').fill('正在进行')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    for text in ['普通排队', '优先插入', '可以删除']:
        page.locator('#input').fill(text)
        page.locator('#send').click()
    page.locator('#task-panel .task-row').filter(has_text='可以删除').get_by_role('button', name='删除这条排队消息').click()
    expect(page.locator('#task-panel')).not_to_contain_text('可以删除')
    page.locator('#task-panel .task-row').filter(has_text='优先插入').locator('button[aria-label^="插入"]').click()
    page.wait_for_function('!state.busy && typeof releaseStop === "function"')
    assert page.evaluate('sentTexts') == ['正在进行']
    page.evaluate('releaseStop()')
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('优先插入')
    page.wait_for_function('state.busy')
    page.evaluate("finishReply('优先完成')")
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('普通排队')
    page.evaluate("finishReply('全部完成')")
    page.wait_for_function('!state.busy')
    assert page.evaluate('sentTexts') == ['正在进行', '优先插入', '普通排队']
    assert not errors


def test_first_new_input_after_stop_is_sent_without_replacement(chat_page):
    page, errors, _ = chat_page
    page.locator('#input').fill('旧问题')
    page.locator('#send').click()
    page.wait_for_function('state.busy')
    page.locator('#send').click()
    page.wait_for_function('!state.busy')
    page.locator('#input').fill('中断后的新问题')
    page.locator('#send').click()
    expect(page.locator('.latest-turn .msg.user')).to_contain_text('中断后的新问题')
    page.evaluate("finishReply('第一次就收到')")
    page.wait_for_function('!state.busy')
    expect(page.locator('.latest-turn .reply-body')).to_contain_text('第一次就收到')
    assert not errors


def test_markdown_separator_lists_and_code_comments(chat_page):
    page, errors, _ = chat_page
    page.locator('#input').fill('格式检查')
    page.locator('#send').click()
    ticks = chr(96)
    text = '# 标题\n\n正文 ' + ticks + '---' + ticks + '\n\n------\n- 第一项\n- 第二项\n\n> 引用\n\n' + ticks * 3 + 'python\n# 保留注释\nprint("------")\n' + ticks * 3 + '\n<img src=x onerror=alert(1)>'
    page.evaluate('text => finishReply(text)', text)
    page.wait_for_function('!state.busy')
    body = page.locator('.latest-turn .reply-body')
    expect(body.locator('h1')).to_have_text('标题')
    expect(body.locator('hr')).to_have_count(1)
    expect(body.locator('li')).to_have_count(2)
    expect(body.locator('blockquote')).to_have_text('引用')
    expect(body.locator('.code-block code')).to_contain_text('# 保留注释')
    expect(body.locator('img')).to_have_count(0)
    assert not errors


def test_memory_editor_prefills_and_saves_all_fields(chat_page, tmp_path):
    page, errors, _ = chat_page
    row = dict(id='m1', category='domain', title='Python', summary='学习数据处理', content='练习 pandas', keywords=['Python'], updated_at=time.time(), scope='shared', proactive=0, source_text='我在学习 Python')
    saved = []
    page.route('**/api/state?*', lambda route: route.fulfill(content_type='application/json', body=json.dumps({'mode': 'workspace', 'notes': [], 'docs': [], 'summary': '', 'shared_memories': [row]})))
    def save(route):
        saved.append(route.request.post_data_json)
        route.fulfill(content_type='application/json', body='{"ok":true}')
    page.route('**/api/companionship/memories/m1', save)
    page.evaluate('renderState()')
    page.locator('#sec-state .side-sec-head').click()
    expect(page.locator('#memory-cards')).to_contain_text('Python')
    page.locator('#memory-cards').get_by_role('button', name='编辑', exact=True).click()
    dialog = page.locator('#memory-editor')
    expect(dialog.locator('[name="content"]')).to_have_value('练习 pandas')
    dialog.locator('[name="summary"]').fill('改学 Rust')
    dialog.locator('[name="keywords"]').fill('Rust，系统编程')
    page.screenshot(path=str(tmp_path / 'memory-editor.png'))
    print('PREVIEW', tmp_path / 'memory-editor.png')
    dialog.get_by_role('button', name='保存记忆').click()
    expect(dialog).to_be_hidden()
    assert saved[0]['summary'] == '改学 Rust' and saved[0]['keywords'] == ['Rust', '系统编程']
    page.locator('#memory-search').fill('不存在')
    expect(page.locator('#memory-cards')).to_contain_text('没有找到')
    assert not errors
