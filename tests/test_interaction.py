"""回应卡片与持续故事：真实流式分块、重启回显、并发存档和小计划工具。"""
import json

import pytest
from playwright.sync_api import expect
from test_chat_layout import chat_page

from nyalume.core import agent, interaction, memory, small_plans, tools


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DB_PATH', str(tmp_path / 'interaction.db'))
    memory.init_db()


def payload(revision=0):
    return {'choices': [
        {'label': '追问光点', 'text': '那粒光点像什么？再给我讲讲。'},
        {'label': '陪她种薄荷', 'text': '我选薄荷，今天陪你一起准备花盆。'},
        {'label': '先放一放', 'text': '先暂停这个故事吧，我们随便聊聊。'},
    ], 'story': {'based_on': revision, 'title': '窗边的一点微光', 'goal': '给小窝留一盏不会刺眼的小灯',
                 'scene': '我在花盆旁发现一粒微光，先拿来一个空玻璃罐，等你决定。',
                 'open_question': '它喜欢植物还是安静的地方？', 'status': 'active'}}


def test_metadata_never_leaks_at_any_chunk_boundary():
    raw = '我在花盆旁发现一粒光。\n' + interaction.MARKER + json.dumps(payload(), ensure_ascii=False) + interaction.END
    for width in (1, 2, 7, 25, len(raw)):
        parser = interaction.ReplyStream()
        shown = ''.join(parser.feed(raw[i:i+width]) for i in range(0, len(raw), width))
        tail, data = parser.finish()
        assert shown + tail == '我在花盆旁发现一粒光。\n'
        assert data == payload()
    parser = interaction.ReplyStream()
    assert parser.feed('正文' + interaction.MARKER + '{broken') == '正文'
    assert parser.finish() == ('', {})
    assert interaction.choices({'choices': [{'label': 'x', 'text': 'x'}] * 3}) == []


def test_story_keeps_choice_and_rejects_stale_update():
    first = interaction.finish(payload(), memory.daily_session(), '我想听一个小窝故事', 'turn-1')
    assert first['story']['revision'] == 1
    assert first['story']['user_response'] == '我想听一个小窝故事'
    updated = payload(1)
    updated['story']['scene'] = '你选了薄荷，我把空罐子放在花盆边。'
    interaction.finish(updated, memory.daily_session(), '我选薄荷', 'turn-2')
    interaction.finish(payload(), 'chat-b', '旧窗口的选择', 'turn-stale')
    memory.init_db()
    assert interaction.story()['revision'] == 2
    assert interaction.story()['user_response'] == '我选薄荷'
    assert '你选了薄荷' in interaction.story_context()
    with memory._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM companion_entries WHERE source='pet_story'").fetchone()[0] == 2
    paused = payload(2)
    paused['story']['status'] = 'paused'
    interaction.finish(paused, 'chat-a', '暂停吧', 'turn-3')
    assert interaction.story()['status'] == 'paused'
    completed = payload(3)
    completed['story'].update(status='completed', open_question='')
    interaction.finish(completed, 'chat-a', '微光找到家了，结束这章吧', 'turn-4')
    assert interaction.story()['status'] == 'completed'


@pytest.mark.parametrize('mode', ['workspace', 'daily'])
def test_agent_options_share_one_response_and_survive_reload(monkeypatch, mode):
    tools.set_permission_mode(mode)
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        content = '花盆旁有一点微光。\n'
        if mode == 'daily':
            content += '[daily_affection:+2]\n'
        content += interaction.MARKER + json.dumps(payload(), ensure_ascii=False) + interaction.END
        for i in range(0, len(content), 3):
            yield {'kind': 'content', 'text': content[i:i+3]}
    monkeypatch.setattr(agent.llm, 'chat_stream', model)
    monkeypatch.setattr(agent, 'resolve_persona_id', lambda: 'nyalume')
    events = list(agent.run_stream('one-call', '说说你今天的小愿望'))
    assert len(calls) == 1
    visible = ''.join(e.get('text', '') for e in events if e['type'] == 'text')
    assert visible.strip() == '花盆旁有一点微光。'
    assert events[-1]['interaction']['choices'] == payload()['choices']
    sid = memory.daily_session() if mode == 'daily' else 'one-call'
    saved = memory.session_messages(sid)[-1]
    assert saved['interaction'] == events[-1]['interaction']
    branch = memory.duplicate_session_until(sid, saved['id'])
    assert memory.session_messages(branch)[-1]['interaction'] == saved['interaction']
    if mode == 'daily':
        assert interaction.story()['revision'] == 1
        assert memory.get_daily_affection(sid) == 50
    else:
        assert not interaction.story()


def test_daily_plan_tool_applies_real_choice_without_exposing_other_tools(monkeypatch):
    first = small_plans.tick(kind='garden')
    tools.set_permission_mode('daily')
    rounds = []
    def model(messages, tools=None, **kwargs):
        rounds.append(1)
        assert [t['function']['name'] for t in tools] == ['pet_plan']
        if len(rounds) == 1:
            yield {'kind': 'tool_delta', 'index': 0, 'id': 'plan-choice', 'name': 'pet_plan',
                   'arguments': json.dumps({'action': 'choose', 'plan_id': first['id'], 'choice': '薄荷'})}
        else:
            assert any('薄荷' in m.get('content', '') for m in messages if m['role'] == 'tool')
            yield {'kind': 'content', 'text': '那就种薄荷。[daily_affection:+1]'}
    monkeypatch.setattr(agent.llm, 'chat_stream', model)
    events = list(agent.run_stream('choose-mint', '我选薄荷'))
    assert events[-1]['type'] == 'done'
    assert small_plans.current()['plant'] == '薄荷'
    assert small_plans.current()['chosen_by'] == 'user'
    assert '日常模式' in tools.execute_tool('pet_perform', {'action': 'dance', 'value': '舞'})


def test_daily_missing_choices_allow_natural_end_without_extra_call(monkeypatch):
    tools.set_permission_mode('daily')
    calls = []
    expected = [
        {'label': '给纸船起名', 'text': '先给这艘纸船起一个名字吧。'},
        {'label': '画一张航线图', 'text': '我们一起给纸船画一张航线图。'},
        {'label': '问问乘客', 'text': '这艘纸船准备载谁出发呢？'}]
    def model(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            yield {'kind': 'content', 'text': '我折好了一艘纸船，还不知道它要去哪里。[daily_affection:+1]'}
        else:
            assert '纸船' in messages[-1]['content'] and '聊聊你的手工' in messages[-1]['content']
            assert not kwargs.get('tools')
            yield {'kind': 'content', 'text': json.dumps({'choices': expected}, ensure_ascii=False)}
    monkeypatch.setattr(agent.llm, 'chat_stream', model)
    monkeypatch.setattr(agent, 'resolve_persona_id', lambda: 'nyalume')
    events = list(agent.run_stream('dynamic-choices', '聊聊你的手工'))
    assert len(calls) == 1 and events[-1]['type'] == 'done'
    assert events[-1]['interaction']['choices'] == []
    assert memory.session_messages(memory.daily_session())[-1]['interaction']['choices'] == []


def test_choice_generation_failure_never_invents_fixed_cards(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('offline')
    monkeypatch.setattr(agent.llm, 'chat_text', fail)
    assert interaction.generate_choices('问候', '晚上好。') == []
    assert interaction.choices() == []


def test_missing_choices_do_not_render_fixed_cards(chat_page):
    page, errors, _ = chat_page
    page.locator('#input').fill('随便聊聊')
    page.locator('#input').press('Enter')
    page.wait_for_function('typeof window.finishReply === "function"')
    page.evaluate('finishReply("今天想折一艘纸船。")')
    expect(page.locator('.msg.assistant.latest .reply-choice-card')).to_have_count(0)
    expect(page.locator('#input')).to_be_enabled()
    assert not errors


def test_three_choice_cards_select_then_send_and_keep_draft(chat_page, tmp_path):
    page, errors, _ = chat_page
    page.set_viewport_size({'width': 480, 'height': 560})
    page.goto('http://nyalume.test/?quick=1&session=demo')
    page.locator('#input').fill('说说你的小愿望')
    page.locator('#input').press('Enter')
    page.wait_for_function('typeof window.finishReply === "function"')
    page.evaluate('(data) => pushEvent({type:"done", message_id:10, interaction:data})', payload())
    page.evaluate('finishReply("我想给窗边的光点找个家。你愿意陪我想想吗？")')
    # fixture 的第二个 done 不带 interaction，不应抹掉先收到的选项。
    box = page.locator('.msg.assistant.latest .reply-options')
    expect(box).to_be_visible()
    expect(box.locator('input[type=radio]')).to_have_count(3)
    expect(box.get_by_role('button', name='发送这个回应')).to_be_disabled()
    page.locator('#input').fill('我还没写完的草稿')
    box.get_by_text('陪她种薄荷', exact=True).click()
    expect(page.locator('#input')).to_have_value('我还没写完的草稿')
    expect(page.locator('#log .msg.user').last).to_contain_text('说说你的小愿望')
    page.screenshot(path=str(tmp_path / 'reply-choice-cards.png'))
    print('PREVIEW', tmp_path / 'reply-choice-cards.png')
    box.get_by_role('button', name='发送这个回应').click()
    expect(page.locator('#log .msg.user').last).to_contain_text('我选薄荷，今天陪你一起准备花盆。')
    expect(page.locator('#input')).to_have_value('我还没写完的草稿')
    page.evaluate('finishReply("那就一起种薄荷吧。")')
    expect(page.locator('#send')).to_have_attribute('title', '发送')
    page.locator('#input').fill('其实我想自己写一段回应')
    page.locator('#input').press('Enter')
    expect(page.locator('#log .msg.user').last).to_contain_text('其实我想自己写一段回应')
    page.evaluate('finishReply("好，我听你说。")')
    assert not errors
