"""奇幻剧情：连续存档、真实选择、养成和主动邀请。模型与桌面文件隔离。"""
import json
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect
from test_chat_layout import chat_page

from nyalume.core import agent, fantasy, fantasy_worldbook, interaction, memory, tools, tracing
from nyalume.frontends.pet import web_chat
from nyalume.frontends.pet.pet3d import keepsakes, proactive


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DB_PATH', str(tmp_path / 'fantasy.db'))
    memory.init_db()
    monkeypatch.setattr(keepsakes, 'home_path', lambda: tmp_path / '小窝')


def proposal(revision=0, **changes):
    data = dict(based_on=revision, title='逆流藏书馆', goal='找回 Nyalume 丢失的星图',
                scene='馆长提出用一张车票换取进入地下馆的许可，我们还没有答应。', location='藏书馆门廊',
                open_question='馆长为什么认得那张星图？', status='active',
                canon={'馆长身份': '馆长是不会离开门廊的守书灵。'}, gains=[], uses=[], skills=[])
    return {**data, **changes}


def choices():
    return [{'label': '询问馆长', 'text': '先问馆长这张车票有什么用途。'},
            {'label': '研究星图', 'text': '一起查看折角地图上的标记。'},
            {'label': '暂缓前行', 'text': '先留在门廊，想清楚再作决定。'}]


def worldbook():
    return {'name': '逆流藏书馆', 'premise': '失窃的星图正被送往无昼王城。',
            'boundary_rule': '借用月光必须付出一段清晰记忆。', 'nyalume_goal': '找回自己绘制的星图',
            'starting_location': '藏书馆门廊', 'starting_items': ['旧车票', '折角地图'],
            'entries': [
                {'kind': 'character', 'name': 'Nyalume', 'aliases': ['露米'],
                 'content': '她能读懂旧式星图，却害怕抹除记忆。'},
                {'kind': 'place', 'name': '藏书馆门廊', 'aliases': ['门廊'],
                 'content': '门廊只在月光逆流时露出入口。'}]}


def save(revision=0, *, now=1800000000, **changes):
    return fantasy.commit(proposal(revision, **changes), '馆长伸出手，Nyalume先问他车票的用途。',
                          choices(), '先问清楚代价', f'turn-{revision}', now=now)


def test_first_start_saves_worldbook_before_chapter_and_supplies_server_version(monkeypatch):
    from nyalume.core import llm

    generated = []
    def make_book(messages, **kwargs):
        generated.append(messages)
        return json.dumps(worldbook(), ensure_ascii=False)
    def write_chapter(messages, **kwargs):
        state = fantasy.current()
        assert state['revision'] == 0
        assert state['inventory'] == ['旧车票', '折角地图']
        assert state['worldbook']['name'] == '逆流藏书馆'
        assert '失窃的星图' in str(messages[1:])
        assert '失窃的星图' not in messages[0]['content']
        data = proposal()
        data.pop('based_on')  # 版本号由服务端补齐；模型不负责维护并发令牌。
        yield {'kind': 'content', 'text': 'Nyalume 把旧车票放在手心，望向门廊。'
               + interaction.MARKER + json.dumps({'fantasy': data, 'choices': choices()}, ensure_ascii=False)
               + interaction.END}
    monkeypatch.setattr(llm, 'chat_text', make_book)
    monkeypatch.setattr(llm, 'chat_stream', write_chapter)
    events = list(fantasy.run_stream(fantasy.SESSION_ID, '开启第一章'))
    assert events[-1]['type'] == 'done'
    assert len(generated) == 1
    state = fantasy.current()
    assert state['revision'] == 1 and state['inventory'] == ['旧车票', '折角地图']
    assert state['worldbook']['name'] == '逆流藏书馆'
    assert '世界底稿' in (keepsakes.home_path() / '折月诸境 · 世界底稿.txt').read_text(encoding='utf-8')
    assert (keepsakes.home_path() / '奇幻手记' / '第0001章.txt').exists()


def test_failed_opening_retains_worldbook_for_retry(monkeypatch):
    from nyalume.core import llm

    generated = []
    monkeypatch.setattr(llm, 'chat_text', lambda *args, **kwargs: generated.append(1) or json.dumps(worldbook(), ensure_ascii=False))
    def incomplete(*args, **kwargs):
        yield {'kind': 'content', 'text': '还没有写完的草稿。'}
    monkeypatch.setattr(llm, 'chat_stream', incomplete)
    events = list(fantasy.run_stream(fantasy.SESSION_ID, '开启第一章'))
    assert events[-1]['type'] == 'error'
    assert not any(event['type'] == 'text' for event in events)
    assert fantasy.current()['worldbook']['name'] == '逆流藏书馆'
    assert fantasy.current()['revision'] == 0
    assert fantasy.current()['inventory'] == ['旧车票', '折角地图']
    note = keepsakes.home_path() / '折月诸境 · 世界底稿.txt'
    assert memory.message_count(fantasy.SESSION_ID) == 0
    assert fantasy.history() == []
    trace = tracing.list_runs(fantasy.SESSION_ID, limit=1)[0]
    assert trace['status'] == 'error'
    assert any('fantasy' in span.get('reason', '') for span in trace['spans'])
    assert '还没有写完的草稿' not in json.dumps(trace, ensure_ascii=False)
    note.write_text('用户自己的批注', encoding='utf-8')

    def complete(*args, **kwargs):
        yield {'kind': 'content', 'text': '钟声响起。' + interaction.MARKER + json.dumps(
            {'fantasy': proposal(), 'choices': choices()}, ensure_ascii=False) + interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', complete)
    assert list(fantasy.run_stream(fantasy.SESSION_ID, '再试开启第一章'))[-1]['type'] == 'done'
    assert len(generated) == 1
    assert fantasy.current()['revision'] == 1
    assert note.read_text(encoding='utf-8') == '用户自己的批注'
    assert [row['content'] for row in fantasy.history()] == ['再试开启第一章', '钟声响起。']


def test_worldbook_selects_lore_without_changing_initial_inventory():
    book = worldbook()
    book['entries'].append({'kind': 'faction', 'name': '无昼王城', 'aliases': ['王城'],
                            'content': '王城的钟每日只敲一次。'})
    fantasy.bootstrap_worldbook(book)
    context = fantasy.context('我听见王城的钟')
    assert '王城的钟每日只敲一次' in context
    assert '门廊只在月光逆流时露出入口' in context  # 第一章起点始终可见。
    assert '门廊只在月光逆流时露出入口' not in str(fantasy_worldbook.selected(book, '王城'))
    assert fantasy.current()['inventory'] == ['旧车票', '折角地图']
    with pytest.raises(ValueError, match='不能重新生成|状态已变化'):
        fantasy.bootstrap_worldbook(book)


def test_canon_inventory_concurrency_and_growth_survive_reload():
    memory.set_setting(interaction.STORY_KEY, json.dumps({'title': '窗边的小灯'}))
    first = save(gains=['月轨车票'])
    assert first['bond_days'] == 1
    second = save(1, uses=['月轨车票'], skills=['辨认逆流文字'])
    assert second['inventory'] == [] and second['bond_days'] == 1
    for change, error in [({'canon': {'馆长身份': '馆长是一只猫'}}, '不能改写'),
                          ({'uses': ['月轨车票']}, '尚未拥有')]:
        with pytest.raises(ValueError, match=error):
            save(2, **change)
    with pytest.raises(ValueError, match='进度已变化'):
        save(0)
    memory.init_db()
    assert fantasy.current()['revision'] == 2
    assert interaction.story()['title'] == '窗边的小灯'
    assert '辨认逆流文字' in fantasy.context()
    assert '先问清楚代价' in fantasy.context()
    third = save(2, now=1800000000 + 86400 * 30, status='paused')
    assert third['bond_days'] == 1 and not fantasy.due(1900000000)
    fourth = save(3, now=1800000000 + 86400 * 31)
    assert fourth['bond_days'] == 2


def test_archive_retries_without_overwriting_user_edits(monkeypatch):
    save()
    original = keepsakes.Keepsakes.write_chapter
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(keepsakes.Keepsakes, 'write_chapter', fail)
    with pytest.raises(OSError):
        fantasy.export_pending()
    monkeypatch.setattr(keepsakes.Keepsakes, 'write_chapter', original)
    paths = fantasy.export_pending()
    from pathlib import Path
    path = Path(paths[1])
    assert '馆长伸出手' in path.read_text(encoding='utf-8')
    assert '先问清楚代价' in path.read_text(encoding='utf-8')
    path.write_text('用户手记', encoding='utf-8')
    with memory._conn() as conn:
        conn.execute("UPDATE fantasy_chapters SET exported_path=''")
    fantasy.export_pending()
    assert path.read_text(encoding='utf-8') == '用户手记'


@pytest.mark.parametrize('mode', ['daily', 'workspace'])
def test_chat_one_response_archives_full_chapter_and_choices(monkeypatch, mode):
    tools.set_permission_mode(mode)
    calls = []
    def model(messages, **kwargs):
        calls.append(messages)
        assert '固定设定与账本' not in messages[0]['content']
        data = {'choices': choices(), 'fantasy': proposal()}
        raw = '逆流的书页从天花板落下。' + interaction.MARKER + json.dumps(data, ensure_ascii=False) + interaction.END
        for start in range(0, len(raw), 5):
            yield {'kind': 'content', 'text': raw[start:start+5]}
    monkeypatch.setattr(agent, 'resolve_persona_id', lambda: 'nyalume')
    monkeypatch.setattr(agent.llm, 'chat_stream', model)
    events = list(agent.run_stream('adventure', '开始奇幻冒险'))
    assert len(calls) == 1
    assert events[-1]['type'] == 'done'
    assert 'fantasy' not in events[-1]['interaction']
    assert fantasy.current() == {}
    sid = memory.daily_session() if mode == 'daily' else 'adventure'
    assert memory.session_messages(sid)[-1]['content'] == '逆流的书页从天花板落下。'
    assert not (keepsakes.home_path() / '奇幻手记' / '第0001章.txt').exists()


def test_invalid_chapter_does_not_change_progress_or_export():
    save()
    result = interaction.finish({'fantasy': proposal(1, canon={'world': '一切都是梦'})},
                                fantasy.SESSION_ID, '继续', 'bad', narrative='醒了，之前都不存在。')
    assert '未归档' in result['fantasy_warning']
    assert fantasy.current()['revision'] == 1
    assert not keepsakes.home_path().exists()


@pytest.mark.parametrize('interrupted', [False, True])
def test_proactive_invitation_waits_and_opens_full_chapter(monkeypatch, interrupted):
    now = [1800000000.0]
    state = {'last_interaction': 0, 'energy': 1}
    api = SimpleNamespace(_model_dir='Nyalume', _talk_mode='talkative', _proactive=True,
                          _notes_enabled=False, _pending=None, _desk={},
                          _pet_state=SimpleNamespace(snapshot=lambda: dict(state)))
    def sleep(_):
        if now[0] > 1800000000:
            raise SystemExit
        now[0] += 1000
    def model(messages):
        assert json.loads(messages[1]['content'])['我在做的事'] == '奇幻来信'
        if interrupted:
            state['last_interaction'] = now[0] + 1
        return json.dumps(dict(action='fantasy', text='收到藏书馆的来信，要看看吗？',
                               narrative='一封用星光封缄的信停在门廊，Nyalume等你拆开。',
                               fantasy=proposal(), choices=choices()), ensure_ascii=False)
    monkeypatch.setattr(proactive.time, 'time', lambda: now[0])
    monkeypatch.setattr(proactive.time, 'sleep', sleep)
    monkeypatch.setattr(proactive, 'default_chat', model)
    generated = []
    monkeypatch.setattr(fantasy_worldbook, 'generate', lambda *args, **kwargs: generated.append(1) or worldbook())
    with pytest.raises(SystemExit):
        proactive.run_loop(api)
    if interrupted:
        assert fantasy.current() == {} and api._pending is None
        assert not generated
        return
    chapter = fantasy.current()
    assert chapter['awaiting_reply'] and chapter['bond_days'] == 0
    assert generated == [1] and chapter['inventory'] == ['旧车票', '折角地图']
    assert chapter['worldbook']['nyalume_goal'] == chapter['goal']
    assert not fantasy.due(now[0] + 86400)
    chat = web_chat.WebChat(quick=True)
    monkeypatch.setattr(chat, 'show', lambda: True)
    monkeypatch.setattr(chat, '_send', lambda _: None)
    assert chat.open_reply(api._pending['text'])
    assert chat._sid == fantasy.SESSION_ID and chat.fantasy
    message = {'content': fantasy.current()['narrative'], 'interaction': {'fantasy': fantasy.current(), 'choices': fantasy.current()['choices']}}
    assert message['content'] == chapter['narrative']
    assert message['interaction']['choices'] == chapter['choices']
    save(1, now=now[0] + 86400)
    assert fantasy.due(now[0] + 86400 + 1801)


def test_proactive_cannot_spend_or_reward_without_user():
    with pytest.raises(ValueError, match='主动邀请不能'):
        fantasy.commit(proposal(gains=['星图']), '她等你回应。', choices(), '', 'auto',
                       origin='proactive', now=1800000000)
    assert fantasy.current() == {}


def test_fantasy_choices_render_and_allow_custom_answer(chat_page):
    page, errors, _ = chat_page
    page.goto('http://nyalume.test/?quick=1&session=demo')
    page.locator('#input').fill('去奇幻冒险')
    page.locator('#input').press('Enter')
    page.wait_for_function('typeof window.finishReply === "function"')
    data = {'choices': choices(), 'fantasy': save()}
    page.evaluate('(data) => pushEvent({type:"done",message_id:10,interaction:data})', data)
    page.evaluate('finishReply("馆长递来一封信。")')
    box = page.locator('.msg.assistant.latest .reply-options')
    expect(box).to_contain_text('奇幻手记 · 逆流藏书馆')
    expect(box).to_contain_text('初识的同行者')
    expect(box.locator('input[type=radio]')).to_have_count(3)
    page.locator('#input').fill('我想先查看信上的封印')
    page.locator('#input').press('Enter')
    expect(page.locator('#log .msg.user').last).to_contain_text('我想先查看信上的封印')
    page.evaluate('finishReply("好，我们先看封印。")')
    assert not errors


def test_dedicated_runner_has_no_work_tools_and_checks_before_display(monkeypatch):
    from nyalume.core import llm
    fantasy.bootstrap_worldbook(worldbook())
    tools.set_permission_mode('full')
    memory.save_message('work', 'user', '工作秘密：报告草稿')
    def model(messages, **kwargs):
        assert {tool['function']['name'] for tool in kwargs.get('tools') or []} <= {'recall_chapters', 'check_chapter'}
        assert '工作秘密' not in str(messages)
        yield {'kind': 'content', 'text': '门廊深处传来钟声。' + interaction.MARKER + json.dumps(
            {'choices': choices(), 'fantasy': proposal()}, ensure_ascii=False) + interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', model)
    result = list(fantasy.run_stream(fantasy.SESSION_ID, '开始冒险'))
    assert result[-1]['type'] == 'done'
    assert tools.permission_mode() == 'full'
    assert memory.message_count('work') == 1
    # 重放旧版本不会先把无效正文展示给用户。
    rejected = list(fantasy.run_stream(fantasy.SESSION_ID, '再试一次'))
    assert rejected[-1]['type'] == 'error'
    assert not any(event['type'] == 'text' for event in rejected)


def test_pause_invalidates_inflight_chapter_and_survives_restart(monkeypatch):
    from nyalume.core import llm
    fantasy.bootstrap_worldbook(worldbook())
    def model(*args, **kwargs):
        fantasy.set_paused(True)
        yield {'kind': 'content', 'text': '迟到的章节' + interaction.MARKER + json.dumps(
            {'fantasy': proposal(), 'choices': choices()}) + interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', model)
    result = list(fantasy.run_stream(fantasy.SESSION_ID, '开始'))
    assert result[-1]['type'] == 'error'
    memory.init_db()
    assert fantasy.current()['status'] == 'paused' and not fantasy.due()
    assert fantasy.current()['worldbook']['starting_items'] == ['旧车票', '折角地图']
    fantasy.set_paused(False)
    assert fantasy.current()['revision'] == 2
    assert fantasy.current()['canon']['world'] == fantasy.BASE_CANON['world']


def test_fantasy_and_work_requests_cancel_independently(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from nyalume.core import llm
    from nyalume.frontends.web import server
    tools.set_permission_mode('full')
    fantasy.bootstrap_worldbook(worldbook())
    work_started, story_started = threading.Event(), threading.Event()
    finish_work, finish_story = threading.Event(), threading.Event()
    def work(sid, text, cancel_event=None):
        memory.save_message(sid, 'user', text)
        work_started.set()
        assert finish_work.wait(10)
        assert not cancel_event.is_set()
        yield {'type': 'done'}
    def story(*args, **kwargs):
        story_started.set()
        assert finish_story.wait(10)
        yield {'kind': 'content', 'text': '不会写入的半段正文'}
    monkeypatch.setattr(server, 'run_stream', work)
    monkeypatch.setattr(llm, 'chat_stream', story)
    client = TestClient(server.app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        working = pool.submit(client.post, '/api/chat', json={'session_id': 'work', 'message': '整理报告', 'request_id': 'work-run'})
        try:
            assert work_started.wait(5)
            adventure = pool.submit(client.post, '/api/fantasy/chat', json={
                'session_id': fantasy.SESSION_ID, 'message': '打开信封', 'request_id': 'story-run'})
            assert story_started.wait(5)
            assert server.activity_state()['working']
            duplicate = client.post('/api/chat', json={'session_id': 'other', 'message': 'x', 'request_id': 'work-run'})
            assert duplicate.status_code == 409
            assert not server._session_run_lock('other').locked()
            assert client.post('/api/runs/story-run/cancel').json()['active']
            finish_story.set()
            assert 'cancelled' in adventure.result(timeout=5).text
            assert not working.done()
            assert server.activity_state()['working']
            assert fantasy.current()['revision'] == 0
            assert tools.permission_mode() == 'full'
        finally:
            finish_story.set(); finish_work.set()
        assert working.result(timeout=5).status_code == 200
    assert not server.activity_state()['working']
    assert not server._session_run_lock(fantasy.SESSION_ID).locked()
    assert client.post('/api/fantasy/chat', json={'session_id': 'work', 'message': 'x'}).status_code == 400
    assert client.post('/api/chat', json={'session_id': fantasy.SESSION_ID, 'message': 'x'}).status_code == 400
    assert fantasy.SESSION_ID not in [row['id'] for row in client.get('/api/sessions').json()]


def test_dedicated_page_choices_draft_pause_and_errors(chat_page, tmp_path):
    from pathlib import Path
    page, errors, _ = chat_page
    data = {'session_id': fantasy.SESSION_ID, 'chapter': save(plan={
        'aim': '找回星图', 'next_step': '核对馆长交来的目录', 'waiting_for': '是否进入地下馆',
        'lesson': '先问清代价再行动'}), 'messages': []}
    calls = []
    fail = [False]
    html = (Path(__file__).resolve().parents[1] / 'nyalume/frontends/web/static/fantasy.html').read_text(encoding='utf-8')
    page.route('**/fantasy', lambda route: route.fulfill(content_type='text/html', body=html))
    def route_api(route):
        if route.request.url.endswith('/chat'):
            body = json.loads(route.request.post_data)
            calls.append(body)
            if fail[0]:
                event = {'type': 'error', 'message': '模型暂时不可用'}
            else:
                data['messages'].append({'role': 'user', 'content': body['message']})
                data['messages'].append({'role': 'assistant', 'content': data['chapter']['narrative']})
                event = {'type': 'done', 'interaction': {'fantasy': data['chapter']}}
            route.fulfill(content_type='text/event-stream', body='data: '+json.dumps(event)+'\n\n')
        elif route.request.method == 'PATCH':
            data['chapter']['status'] = 'paused' if json.loads(route.request.post_data)['paused'] else 'active'
            route.fulfill(json=data['chapter'])
        else:
            route.fulfill(json=data)
    page.route('**/api/fantasy**', route_api)
    page.set_viewport_size({'width': 480, 'height': 660})
    page.goto('http://nyalume.test/fantasy')
    page.get_by_text('她在惦记什么', exact=True).click()
    expect(page.locator('#plan-copy')).to_contain_text('核对馆长交来的目录')
    expect(page.locator('#plan-copy')).to_contain_text('先问清代价再行动')
    expect(page.locator('#cards input')).to_have_count(3)
    page.locator('#input').fill('我尚未写完的草稿')
    page.locator('#cards label').nth(1).click()
    page.get_by_role('button', name='发送这个回应').click()
    expect(page.locator('#status')).to_have_text('已记入奇幻手记。')
    expect(page.locator('#input')).to_have_value('我尚未写完的草稿')
    assert calls[-1]['session_id'] == fantasy.SESSION_ID
    page.get_by_role('button', name='暂停冒险').click()
    expect(page.get_by_role('button', name='继续冒险')).to_be_visible()
    expect(page.locator('#send')).to_be_disabled()
    page.reload()
    expect(page.locator('#send')).to_be_disabled()
    page.get_by_role('button', name='继续冒险').click()
    expect(page.locator('#send')).to_be_enabled()
    fail[0] = True
    page.locator('#input').fill('我想先研究封印')
    page.locator('#input').press('Enter')
    expect(page.locator('#status')).to_have_text('模型暂时不可用')
    expect(page.locator('#feedback')).to_have_attribute('data-state', 'error')
    expect(page.locator('#retry')).to_be_visible()
    expect(page.locator('#log')).not_to_contain_text('我想先研究封印')
    expect(page.locator('#send')).to_be_enabled()
    expect(page.locator('#input')).to_have_value('我想先研究封印')
    fail[0] = False
    page.locator('#retry').click()
    expect(page.locator('#status')).to_have_text('已记入奇幻手记。')
    expect(page.locator('#log .user').filter(has_text='我想先研究封印')).to_have_count(1)
    expect(page.locator('#retry')).to_be_hidden()
    page.locator('#dismiss').click()
    expect(page.locator('#feedback')).to_be_hidden()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width': 380, 'height': 560})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#choose').scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / 'fantasy-entry.png'), full_page=True)
    print('PREVIEW', tmp_path / 'fantasy-entry.png')
    assert not errors


def test_dedicated_page_shows_saved_opening_worldbook(chat_page):
    from pathlib import Path

    page, errors, _ = chat_page
    html = (Path(__file__).resolve().parents[1] / 'nyalume/frontends/web/static/fantasy.html').read_text(encoding='utf-8')
    page.route('**/fantasy', lambda route: route.fulfill(content_type='text/html', body=html))
    page.route('**/api/fantasy', lambda route: route.fulfill(json={
        'session_id': fantasy.SESSION_ID, 'chapter': fantasy.bootstrap_worldbook(worldbook()), 'messages': [],
        'last_run': {'started_at': 1800000000, 'duration_ms': 5000, 'status': 'error', 'error_type': 'ValueError',
                     'spans': [{'name': 'chapter', 'status': 'error', 'duration_ms': 1, 'reason': '最终回复缺少 fantasy 对象'}]}}))
    page.goto('http://nyalume.test/fantasy')
    expect(page.get_by_role('button', name='继续开启第一章')).to_be_visible()
    expect(page.locator('#summary')).to_contain_text('世界底稿与初始背包已保存')
    page.locator('#worldbook summary').click()
    expect(page.locator('#worldbook-copy')).to_contain_text('旧车票')
    page.locator('#diagnostics summary').click()
    expect(page.locator('#diagnostic-copy')).to_contain_text('最终回复缺少 fantasy 对象')
    expect(page.locator('#log')).not_to_contain_text('最终回复缺少')
    assert not errors


def test_dedicated_page_shows_live_progress_and_can_stop(chat_page, tmp_path):
    from pathlib import Path
    page, errors, _ = chat_page
    html = (Path(__file__).resolve().parents[1] / 'nyalume/frontends/web/static/fantasy.html').read_text(encoding='utf-8')
    page.route('**/fantasy', lambda route: route.fulfill(content_type='text/html', body=html))
    page.route('**/api/fantasy', lambda route: route.fulfill(json={
        'session_id': fantasy.SESSION_ID, 'chapter': {}, 'messages': []}))
    page.goto('http://nyalume.test/fantasy')
    page.evaluate('''() => {
      const original=window.fetch;
      window.fetch=(url,options)=>url==='/api/fantasy/chat'
        ? Promise.resolve(new Response(new ReadableStream({start(controller){
            window.storyController=controller;
            options.signal.addEventListener('abort',()=>controller.error(new DOMException('Stopped','AbortError')));
          }}),{headers:{'Content-Type':'text/event-stream'}}))
        : original(url,options);
    }''')
    page.locator('#start').click()
    page.wait_for_function('!!window.storyController')
    page.evaluate('''() => storyController.enqueue(new TextEncoder().encode(
      'data: '+JSON.stringify({type:'status',message:'她正在核对世界规则……'})+'\\n\\n'))''')
    expect(page.locator('#feedback')).to_be_visible()
    expect(page.locator('#status')).to_have_text('她正在核对世界规则……')
    expect(page.locator('#feedback')).to_have_attribute('data-state', 'busy')
    expect(page.locator('#elapsed')).to_contain_text('已等待')
    expect(page.locator('#stop')).to_be_visible()
    page.set_viewport_size({'width': 380, 'height': 560})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#progress summary').click()
    page.screenshot(path=str(tmp_path / 'fantasy-progress.png'), full_page=True)
    print('PREVIEW', tmp_path / 'fantasy-progress.png')
    page.locator('#stop').click()
    expect(page.locator('#status')).to_have_text('已停止。')
    expect(page.locator('#log .user')).to_have_count(0)
    expect(page.locator('#retry')).to_be_visible()
    expect(page.locator('#send')).to_be_enabled()
    assert not errors


def test_native_entry_uses_separate_window():
    chat = web_chat.WebChat(quick=True, fantasy=True)
    assert chat._window_url().endswith('/fantasy?quick=1')
    work = web_chat.WebChat()
    assert '/fantasy' not in work._window_url()


def test_dedicated_page_recovers_saved_chapter_when_done_event_is_lost(chat_page):
    from pathlib import Path
    page, errors, _ = chat_page
    save()
    html = (Path(__file__).resolve().parents[1] / 'nyalume/frontends/web/static/fantasy.html').read_text(encoding='utf-8')
    page.route('**/fantasy', lambda route: route.fulfill(content_type='text/html', body=html))
    def route_api(route):
        if route.request.url.endswith('/chat'):
            text = json.loads(route.request.post_data)['message']
            fantasy.commit(proposal(1), '馆长回应了你的问题。', choices(), text, 'saved-before-disconnect')
            route.fulfill(content_type='text/event-stream', body='')  # 存档成功后连接断开。
        else:
            route.fulfill(json={'session_id': fantasy.SESSION_ID, 'chapter': fantasy.current(), 'messages': fantasy.history()})
    page.route('**/api/fantasy**', route_api)
    page.goto('http://nyalume.test/fantasy')
    page.locator('#input').fill('先问馆长')
    page.locator('#send').click()
    expect(page.locator('#status')).to_have_text('已记入奇幻手记。')
    expect(page.locator('#retry')).to_be_hidden()
    expect(page.locator('#input')).to_have_value('')
    expect(page.locator('#log .user').filter(has_text='先问馆长')).to_have_count(1)
    assert fantasy.current()['revision'] == 2 and not errors


def test_long_story_reopens_at_recent_history_without_model_request(monkeypatch):
    from nyalume.frontends.web import server
    from fastapi.testclient import TestClient
    def unexpected(*args, **kwargs):
        raise AssertionError('打开入口不能调用模型')
    monkeypatch.setattr(fantasy, 'run_stream', unexpected)
    for index in range(35):
        fantasy.commit(proposal(index), f'章节{index}', choices(), str(index), f'history-{index}')
    memory.save_message(fantasy.SESSION_ID, 'user', '以前失败的请求')
    client = TestClient(server.app)
    assert client.get('/fantasy').status_code == 200
    data = client.get('/api/fantasy').json()
    assert len(data['messages']) == 60
    assert data['messages'][0]['content'] == '5' and data['messages'][-1]['content'] == '章节34'
    assert '以前失败的请求' not in str(data['messages'])
    assert client.patch('/api/fantasy', json={'paused': True}).json()['status'] == 'paused'
    assert client.get('/api/fantasy').json()['chapter']['status'] == 'paused'
    assert client.post('/api/fantasy/chat', json={'session_id': fantasy.SESSION_ID, 'message': ' '}).status_code == 400


def test_main_entry_does_not_stop_active_work(chat_page):
    page, errors, _ = chat_page
    page.goto('http://nyalume.test/?session=demo')
    page.locator('#input').fill('继续整理报告')
    page.locator('#input').press('Enter')
    page.wait_for_function('typeof window.finishReply === "function"')
    entry = page.locator('#fantasy-entry')
    expect(entry).to_have_attribute('target', '_blank')
    expect(entry).to_have_attribute('href', '/fantasy')
    # 新窗口目标由浏览器原生处理；入口不绑定会话切换/取消逻辑。
    assert page.evaluate('document.getElementById("fantasy-entry").onclick === null')
    expect(page.locator('#send')).to_have_attribute('title', '停止生成')
    page.evaluate('finishReply("报告整理完成。")')
    expect(page.locator('#send')).to_have_attribute('title', '发送')
    assert not errors


def test_story_agent_recalls_previews_and_keeps_its_plan(monkeypatch):
    from nyalume.core import llm
    old = fantasy.commit(proposal(gains=['月轨车票']), '那枚封蜡闻起来有蓝莓味。',
                         choices(), '把车票收好', 'old', now=1800000000)
    for revision in range(1, 5):
        save(revision)
    plan = {'aim': '找回星图', 'next_step': '先核对馆长交来的目录',
            'waiting_for': '是否一起进入地下馆', 'lesson': '先问清代价再交出车票'}
    next_chapter = proposal(5, uses=['月轨车票'], gains=['地下馆目录'], plan=plan)
    rounds = []
    def model(messages, **kwargs):
        rounds.append(1)
        if len(rounds) == 1:
            args = json.dumps({'query': '蓝莓味'}, ensure_ascii=False)
            yield {'kind': 'tool_delta', 'index': 0, 'id': 'recall', 'name': 'recall_chapters', 'arguments': args[:6]}
            yield {'kind': 'tool_delta', 'index': 0, 'id': '', 'name': '', 'arguments': args[6:]}
        elif len(rounds) == 2:
            recalled = json.loads(messages[-1]['content'])['chapters']
            assert len(recalled) == 1 and recalled[0]['revision'] == old['revision']
            assert '蓝莓味' in recalled[0]['narrative']
            yield {'kind': 'tool_delta', 'index': 0, 'id': 'check', 'name': 'check_chapter',
                   'arguments': json.dumps({'proposal': next_chapter, 'narrative': '我们交出票，换来了目录。'})}
        else:
            assert json.loads(messages[-1]['content'])['ok']
            assert fantasy.current()['revision'] == 5
            assert fantasy.current()['inventory'] == ['月轨车票']
            yield {'kind': 'content', 'text': '我们交出票，换来了目录。'+interaction.MARKER+json.dumps(
                {'fantasy': next_chapter, 'choices': choices()}, ensure_ascii=False)+interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', model)
    events = list(fantasy.run_stream(fantasy.SESSION_ID, '按约定用车票换目录'))
    assert len(rounds) == 3 and events[-1]['type'] == 'done'
    assert any('翻阅' in event.get('message', '') for event in events)
    assert any('核对线索' in event.get('message', '') for event in events)
    assert fantasy.current()['inventory'] == ['地下馆目录']
    assert fantasy.current()['plan'] == plan
    path = keepsakes.home_path() / '奇幻手记' / '第0006章.txt'
    assert '先问清代价再交出车票' in path.read_text(encoding='utf-8')
    memory.init_db()
    assert '先核对馆长交来的目录' in fantasy.context()
    # 下一章省略计划也会保留，主动来信可以继续引用。
    continued = save(6)
    assert continued['plan'] == plan
    messages = proactive.Proposer().build_prompt({}, {}, '奇幻来信', now=1800000000)
    assert '先核对馆长交来的目录' not in messages[0]['content']
    assert '先核对馆长交来的目录' in messages[1]['content']


def test_story_can_use_more_than_four_rounds_and_batch_queries(monkeypatch):
    from nyalume.core import llm
    fantasy.bootstrap_worldbook(worldbook())
    calls = []
    def model(messages, **kwargs):
        calls.append(1)
        if len(calls) <= 6:
            assert kwargs['tools']
            for index in range(3):
                yield {'kind': 'tool_delta', 'index': index, 'id': f'q-{len(calls)}-{index}',
                       'name': 'recall_chapters', 'arguments': json.dumps({'query': f'线索{len(calls)}-{index}'})}
        else:
            yield {'kind': 'content', 'text': '她把查到的线索摆在桌上。' + interaction.MARKER
                   + json.dumps({'fantasy': proposal(), 'choices': choices()}) + interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', model)
    result = list(fantasy.run_stream(fantasy.SESSION_ID, '逐项查清这些线索'))
    assert len(calls) == 7 and result[-1]['type'] == 'done'


def test_repeated_tool_loop_reuses_result_and_gets_a_final_answer(monkeypatch):
    from nyalume.core import llm
    fantasy.bootstrap_worldbook(worldbook())
    rounds, reads = [], []
    original = fantasy._story_tool
    def read(*args):
        reads.append(1)
        return original(*args)
    def model(messages, **kwargs):
        rounds.append(1)
        if kwargs['tools']:
            yield {'kind': 'tool_delta', 'index': 0, 'id': f'q-{len(rounds)}',
                   'name': 'recall_chapters', 'arguments': '{"query":"旧车票"}'}
        else:
            assert '停止查阅工具' in messages[-1]['content']
            yield {'kind': 'content', 'text': '旧事尚不确定，我们先向馆长询问。' + interaction.MARKER
                   + json.dumps({'fantasy': proposal(), 'choices': choices()}) + interaction.END}
    monkeypatch.setattr(fantasy, '_story_tool', read)
    monkeypatch.setattr(llm, 'chat_stream', model)
    result = list(fantasy.run_stream(fantasy.SESSION_ID, '想想这张车票'))
    assert reads == [1] and len(rounds) == 8
    assert result[-1]['type'] == 'done'


def test_world_preparation_has_its_own_time_budget(monkeypatch):
    from nyalume.core import llm
    clock = [0]
    monkeypatch.setattr(fantasy.time, 'monotonic', lambda: clock[0])
    def make_book(*args, **kwargs):
        clock[0] += 200
        return json.dumps(worldbook())
    def model(*args, **kwargs):
        assert kwargs['timeout'] == 120
        yield {'kind': 'content', 'text': '月轮亮起。' + interaction.MARKER
               + json.dumps({'fantasy': proposal(), 'choices': choices()}) + interaction.END}
    monkeypatch.setattr(llm, 'chat_text', make_book)
    monkeypatch.setattr(llm, 'chat_stream', model)
    assert list(fantasy.run_stream(fantasy.SESSION_ID, '开启第一章'))[-1]['type'] == 'done'


def test_world_preparation_can_repair_multiple_different_missing_fields(monkeypatch):
    from nyalume.core import llm
    complete = worldbook()
    replies = iter([{'name': complete['name']},
                    {k: complete[k] for k in ('name', 'premise')},
                    {k: complete[k] for k in ('name', 'premise', 'boundary_rule')}, complete])
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        return json.dumps(next(replies))
    monkeypatch.setattr(llm, 'chat_text', model)
    assert fantasy_worldbook.generate('开篇') == complete
    assert len(calls) == 4 and fantasy.current() == {}


def test_world_preparation_does_not_stop_at_six_changed_drafts(monkeypatch):
    from nyalume.core import llm
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        return json.dumps({'name': f'修改中的世界{len(calls)}'} if len(calls) <= 8 else worldbook())
    monkeypatch.setattr(llm, 'chat_text', model)
    assert fantasy_worldbook.generate('开篇') == worldbook()
    assert len(calls) == 9


def test_first_chapter_can_exceed_old_round_and_time_limits(monkeypatch):
    from nyalume.core import llm
    fantasy.bootstrap_worldbook(worldbook())
    clock, calls = [0], []
    monkeypatch.setattr(fantasy.time, 'monotonic', lambda: clock[0])
    def model(*args, **kwargs):
        calls.append(1)
        clock[0] += 30
        if len(calls) <= 34:
            yield {'kind': 'tool_delta', 'index': 0, 'id': str(len(calls)), 'name': 'recall_chapters',
                   'arguments': json.dumps({'query': f'开场线索{len(calls)}'})}
        else:
            yield {'kind': 'content', 'text': '我们准备出发。' + interaction.MARKER + json.dumps(
                {'fantasy': proposal(), 'choices': choices()}) + interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', model)
    result = list(fantasy.run_stream(fantasy.SESSION_ID, '仔细准备开篇'))
    assert len(calls) == 35 and clock[0] > 900 and result[-1]['type'] == 'done'


def test_story_safety_budget_reserves_a_final_answer(monkeypatch):
    from nyalume.core import llm
    fantasy.bootstrap_worldbook(worldbook())
    monkeypatch.setattr(fantasy, 'MAX_ROUNDS', 5)
    monkeypatch.setattr(fantasy, 'FIRST_CHAPTER_ROUNDS', 5)
    calls = []
    def model(messages, **kwargs):
        calls.append(1)
        if kwargs['tools']:
            yield {'kind': 'tool_delta', 'index': 0, 'id': f'q-{len(calls)}',
                   'name': 'recall_chapters', 'arguments': json.dumps({'query': str(len(calls))})}
        else:
            yield {'kind': 'content', 'text': '我们决定先听馆长的说明。' + interaction.MARKER
                   + json.dumps({'fantasy': proposal(), 'choices': choices()}) + interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', model)
    result = list(fantasy.run_stream(fantasy.SESSION_ID, '继续调查'))
    assert len(calls) == 5 and result[-1]['type'] == 'done'


def test_preview_and_unrecognised_tools_never_mutate_story():
    save(gains=['月轨车票'])
    before = fantasy.current()
    result = fantasy._story_tool('check_chapter', {'proposal': proposal(1, uses=['月轨车票']), 'narrative': '交出了车票。'}, '我愿意')
    assert result['ok'] and result['inventory'] == []
    without_version = proposal(1, uses=['月轨车票'])
    without_version.pop('based_on')
    assert fantasy._story_tool('check_chapter', {'proposal': without_version, 'narrative': '交出了车票。'}, '我愿意')['ok']
    assert fantasy.current() == before
    # 每章重复携带的世界设定不算本章发生过的事件。
    assert fantasy._story_tool('recall_chapters', {'query': '折月诸境'}, '')['chapters'] == []
    with memory._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM fantasy_chapters').fetchone()[0] == 1
    assert not keepsakes.home_path().exists()
    with pytest.raises(ValueError, match='只能查阅剧情'):
        fantasy._story_tool('run_code', {'command': 'echo unexpected'}, '')


@pytest.mark.parametrize('repair', [True, False])
def test_story_agent_repairs_conflicts_with_a_bounded_budget(monkeypatch, repair):
    from nyalume.core import llm
    fantasy.bootstrap_worldbook(worldbook())
    calls = []
    def model(messages, **kwargs):
        calls.append(1)
        fixed = repair and len(calls) > 1
        if len(calls) > 1:
            assert '尚未拥有' in messages[-1]['content']
        data = proposal(uses=[] if fixed else ['从未得到的钥匙'])
        yield {'kind': 'content', 'text': ('先敲门询问。' if fixed else '我们用钥匙开门。') + interaction.MARKER + json.dumps(
            {'fantasy': data, 'choices': choices()}) + interaction.END}
    monkeypatch.setattr(llm, 'chat_stream', model)
    events = list(fantasy.run_stream(fantasy.SESSION_ID, '看看怎么进门'))
    visible = ''.join(event.get('text', '') for event in events if event['type'] == 'text')
    assert '用钥匙开门' not in visible
    assert len(calls) == (2 if repair else 6)
    assert events[-1]['type'] == ('done' if repair else 'error')
    assert fantasy.current().get('revision', 0) == (1 if repair else 0)


def test_cancel_during_story_tools_does_not_save_plan(monkeypatch):
    import threading
    from nyalume.core import llm
    cancel = threading.Event()
    fantasy.bootstrap_worldbook(worldbook())
    def model(messages, **kwargs):
        yield {'kind': 'tool_delta', 'index': 0, 'id': 'recall', 'name': 'recall_chapters', 'arguments': '{"query":""}'}
    monkeypatch.setattr(llm, 'chat_stream', model)
    stream = fantasy.run_stream(fantasy.SESSION_ID, '继续', cancel_event=cancel)
    assert next(stream)['type'] == 'status'
    cancel.set()
    assert list(stream)[-1]['type'] == 'cancelled'
    assert fantasy.current()['revision'] == 0
