"""Continuity acceptance checks: SQLite restart, real chat assembly, and renderer acknowledgement."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nyalume.core import agent, companionship as life, memory, tools, llm, small_plans
from nyalume.frontends.web import server
from nyalume.frontends.pet import web_chat
from nyalume.frontends.pet.pet3d import proactive, pet3d_win


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, 'DB_PATH', str(tmp_path / 'continuity.db'))
    memory.init_db()
    tools.clear_session_context()
    tools.set_permission_mode('workspace')
    yield
    tools.clear_session_context()


def user(text):
    memory.save_message(memory.daily_session(), 'user', text)
    return life.user_turn(text, memory.last_message_id(memory.DAILY_SESSION_ID))


def model(monkeypatch):
    calls = []
    def reply(messages, **kwargs):
        calls.append((messages, kwargs))
        yield {'kind': 'content', 'text': '好，那件事我们慢慢来。'}
    monkeypatch.setattr(llm, 'chat_stream', reply)
    monkeypatch.setattr(agent, '_maybe_auto_notes', lambda *a, **kw: None)
    return calls


def test_one_daily_session_with_project_and_legacy_isolation(monkeypatch):
    calls = model(monkeypatch)
    user('叫我小林')
    project = memory.add_project(str(__import__('pathlib').Path(memory.DB_PATH).parent / 'project'))
    memory.create_session('project-chat', project=project['id'])
    memory.doc_save('private', '项目秘密', ['不应进入日常的项目资料'], 'project-chat')
    memory.note_save('旧用户事实未经核实', '偏好', 'old-chat')
    memory.set_daily_affection('old-chat', 150)
    tools.set_permission_mode('daily')
    list(agent.run_stream('web-new', '你好'))
    memory.init_db()
    list(agent.run_stream('bubble-new', '晚上好'))
    assert len(memory.session_messages(memory.daily_session())) == 5
    assert memory.get_daily_affection(memory.daily_session()) == 50
    assert '小林' in str(calls[-1])
    assert '项目秘密' not in str(calls[-1]) and '旧用户事实' not in str(calls[-1])
    assert memory.get_recent_notes(3, 'old-chat')
    assert all(s['function']['name'] == 'pet_plan' for s in calls[-1][1]['tools'])
    tools.set_permission_mode('workspace')
    list(agent.run_stream('project-chat', '你好'))
    assert 'shared_experiences' not in str(calls[-1]) and '小林' in str(calls[-1])


def test_delivery_followup_pause_block_survive_restart(monkeypatch):
    calls = model(monkeypatch)
    user('我打算明天整理相册')
    topic = life.topics()[0]
    with memory._conn() as conn:
        conn.execute('UPDATE companion_topics SET updated_at=?', (time.time() - 86400,))
    assert life.candidates()[0]['id'] == topic['id']
    did = life.prepare_delivery({'text': '相册想好怎么整理了吗？', 'topic_id': topic['id'], 'memory_ids': []})
    assert life.topics()[0]['last_mentioned'] is None
    assert life.delivered(did) and not life.delivered(did)
    assert not life.candidates()  # no repeat chase while unanswered
    list(agent.run_stream(memory.daily_session(), '还没'))
    packet = json.loads(calls[-1][0][-2]['content'].split('\n', 1)[1])
    assert packet['response_topic']['content'] == '我打算明天整理相册'
    assert packet['response_topic']['invitation'] == '相册想好怎么整理了吗？'
    assert sum(m['content'] == '还没' for m in calls[-1][0]) == 1
    user('先不聊')
    assert life.topics()[0]['state'] == 'paused'
    assert not life.candidates()
    user('想好了')
    assert life.topics()[0]['state'] == 'open'
    user('别再问这个')
    memory.init_db()
    assert life.topics()[0]['state'] == 'blocked' and not life.candidates()


def test_late_result_and_renderer_busy_drop_are_not_delivered():
    user('我准备整理相册')
    topic = life.topics()[0]
    action = {'text': '相册想好怎么整理了吗？', 'topic_id': topic['id'], 'memory_ids': []}
    did = life.prepare_delivery(action)
    assert memory.message_count(memory.daily_session()) == 1

    assert life.topics()[0]['waiting'] == 0
    pr = proactive.Proposer(chat=lambda _: json.dumps({'action': 'say', **action}, ensure_ascii=False))
    result = pr.propose({}, {}, '昨日话题')
    assert result['topic_id'] == topic['id']
    assert pr.recent == [] and not pr.pending_ack
    api = pet3d_win._NativeApi(enabled=False)
    api._pending = {'kind': 'say', 'text': action['text'], 'delivery_id': did}
    api._desk = {'busy': True}
    api.update_look = lambda *a: None
    assert api.poll_action() is None
    assert life.topics()[0]['last_mentioned'] is None
    assert memory.message_count(memory.daily_session()) == 1


def test_queued_result_expires_when_user_chats_and_old_delivery_is_redacted():
    user('我准备整理相册')
    topic = life.topics()[0]
    mid = json.loads(topic['memory_ids'])[0]
    action = {'text': '相册整理好了吗？', 'topic_id': topic['id'], 'memory_ids': [mid]}
    did = life.prepare_delivery(action)
    memory.companion_time('chat', time.time())
    assert not life.delivery_valid(did)
    assert not life.delivered(did)
    second = life.prepare_delivery(action)
    assert life.delivered(second)
    assert memory.update_companion_memory(mid, content='用户计划：暂时不整理相册')
    assert action['text'] not in memory.companion_redact(action['text'])


def test_memory_provenance_edits_delete_and_no_resurrection():
    user('我喜欢乌龙茶')
    old = memory.companion_memories()[0]
    assert old['source_message'] and old['source_text'] == '我喜欢乌龙茶'
    assert old['scope'] == 'shared' and old['created_at'] > 0
    user('现在我喜欢红茶')
    assert [m['content'] for m in memory.companion_memories()] == ['偏好：红茶']
    assert '我喜欢乌龙茶' not in memory.companion_redact('我喜欢乌龙茶')
    with memory._conn() as conn:
        conn.execute('INSERT INTO companion_topics(id,content,memory_ids,created_at,updated_at) VALUES(?,?,?,?,?)',
                     ('stale', '茶的话题', json.dumps([old['id']]), time.time(), time.time()))
    assert not life.candidates()
    new = memory.companion_memories()[0]
    client = TestClient(server.app)
    assert client.patch('/api/companionship/memories/' + new['id'], json={'content': '偏好：白茶'}).status_code == 200
    edited = memory.companion_memories()[0]
    assert edited['source_text'] == '用户在记忆面板修正'
    assert client.patch('/api/companionship/memories/' + edited['id'], json={'proactive': False}).status_code == 200
    assert client.delete('/api/companionship/memories/' + edited['id']).status_code == 200
    memory.init_db()
    assert not memory.companion_memories()
    user('引用：“我喜欢红茶”')
    user('如果我喜欢红茶呢')
    assert not memory.companion_memories()
    with pytest.raises(ValueError):
        memory.save_companion_memory('guess', '用户喜欢红茶', '助手猜测', 9999)


def test_boundaries_keep_negation_and_independent_preferences():
    user('不要催我')
    user('我喜欢猫')
    user('我喜欢红茶')
    facts = {m['content'] for m in memory.companion_memories()}
    assert facts == {'互动边界：不要催我', '偏好：猫', '偏好：红茶'}
    user('我不喜欢红茶了')
    assert '偏好：红茶' not in {m['content'] for m in memory.companion_memories()}
    assert '偏好：猫' in {m['content'] for m in memory.companion_memories()}


def test_time_touch_is_distinct_from_chat_and_anomalies(monkeypatch):
    calls = model(monkeypatch)
    now = time.time()
    memory.companion_time('chat', now - 3 * 86400)
    memory.companion_time('interaction', now - 10)
    list(agent.run_stream(memory.daily_session(), '你好'))
    packet = json.loads(calls[-1][0][-2]['content'].split('\n', 1)[1])
    assert packet['time']['chat']['elapsed_seconds'] >= 3 * 86400
    assert packet['time']['interaction']['elapsed_seconds'] < 60
    assert '没聊天”不等于“没来过' in calls[-1][0][0]['content']
    memory.init_db()
    for value in ('nan', 'inf', 'unknown', str(now + 86400), '-1'):
        memory.set_setting('companion_last_chat', value)
        assert memory.companion_times(now)['chat']['elapsed_seconds'] is None
    assert memory.get_daily_affection(memory.daily_session()) == 50


def test_virtual_plan_is_saved_and_system_is_stable(monkeypatch):
    calls = model(monkeypatch)
    plan = small_plans.tick()
    list(agent.run_stream(memory.daily_session(), '你好'))
    first_system = calls[-1][0][0]
    first = json.loads(calls[-1][0][-2]['content'].split('\n', 1)[1])
    assert first['virtual_plans'] == json.loads(json.dumps(small_plans.current()))
    user('叫我小林')
    memory.set_daily_affection(memory.daily_session(), 75)
    list(agent.run_stream(memory.daily_session(), '今晚好'))
    assert first_system == calls[-1][0][0]
    assert plan['id'] == small_plans.current()['id']
    assert small_plans.current()['stage'] == plan['stage']
    tools.set_session_context(memory.daily_session())
    assert tools.permission_mode() == 'daily'
    assert '日常模式' in tools.execute_tool('run_code', {'language': 'python', 'code': 'print(1)'})


def test_protocol_validation_and_no_model_polling():
    assert proactive.parse_reply('{"action":"say","text":"' + '字' * 26 + '"}') is None
    assert proactive.parse_reply('{"action":"say","text":"好吗？要吗？"}') is None
    assert proactive.parse_reply('{"action":"face","text":"made-up"}') is None
    assert proactive.parse_reply('{"action":"idle","text":"任意"}')['text'] == ''
    pr = proactive.Proposer(chat=lambda _: (_ for _ in ()).throw(AssertionError('unneeded call')))
    assert pr.propose({}, {}, '陪伴')['action'] == 'face'
    user('我准备整理相册')
    pr.chat = lambda _: '{"action":"say","text":"好了？","topic_id":"invented","memory_ids":[]}'
    assert pr.propose({}, {}, '昨日话题') is None
    pr.chat = lambda _: '{"action":"say","text":"好了？","memory_ids":["invented"]}'
    assert pr.propose({}, {}, '昨日话题') is None


def test_supplier_usage_counts_and_unknown_cache(monkeypatch):
    llm.record_usage({'prompt_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 80}}, time.perf_counter())
    llm.record_usage({'prompt_tokens': 90, 'prompt_cache_hit_tokens': 30}, time.perf_counter(), 12)
    llm.record_usage(None, time.perf_counter())
    rows = TestClient(server.app).get('/api/llm/usage').json()
    assert [r['cached_tokens'] for r in rows] == [80, 30, None]
    assert rows[1]['first_token_ms'] == 12


def test_stream_receives_actual_usage_from_final_empty_chunk(monkeypatch):
    class Stream:
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='喵', tool_calls=[], reasoning=None))], usage=None)
            yield SimpleNamespace(choices=[], usage={'prompt_tokens': 100, 'prompt_cache_hit_tokens': 70})
        def close(self):
            pass
    seen = []
    def create(**kwargs):
        seen.append(kwargs)
        return Stream()
    monkeypatch.setattr(llm, 'get_client', lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert list(llm.chat_stream([{'role': 'user', 'content': '你好'}])) == [{'kind': 'content', 'text': '喵'}]
    assert seen[0]['stream_options'] == {'include_usage': True}
    assert json.loads(memory.get_setting('llm_usage'))[-1]['cached_tokens'] == 70


def test_plan_notice_is_only_claimed_after_display():
    plan = small_plans.tick()
    notice = small_plans.claim_notice(plan, commit=False)
    assert notice
    did = life.prepare_delivery({'text': notice}, plan=plan)
    assert small_plans.claim_notice(plan, commit=False) == notice
    assert life.delivered(did)
    assert not small_plans.claim_notice(plan, commit=False)
    assert not life.delivered(did)


def test_bubble_and_web_use_same_session_without_duplicate_invitation(monkeypatch):
    did = life.prepare_delivery({'text': '今天想折一条纸船。', 'topic_id': None, 'memory_ids': []})
    life.delivered(did)
    chat = web_chat.WebChat(quick=True)
    monkeypatch.setattr(chat, 'show', lambda: True)
    monkeypatch.setattr(chat, '_send', lambda _: None)
    assert chat.open_reply('今天想折一条纸船。')
    assert chat._sid == TestClient(server.app).get('/api/companionship/daily-session').json()['id']
    assert memory.message_count(chat._sid) == 1
    assert web_chat.WebChat()._sid == chat._sid


def test_2d_touch_records_direct_interaction_without_chat():
    from nyalume.frontends.pet.pet import PetApp

    app = PetApp.__new__(PetApp)
    app.window = SimpleNamespace(poke=lambda: None, cheer=lambda _: None)
    app.session_id = 'legacy-pet'
    before = time.time()
    app._pet_interact('head')
    times = memory.companion_times()
    assert before <= times['interaction']['timestamp'] <= times['now']
    assert times['chat']['timestamp'] is None


def delivered_plan():
    user('我准备整理相册')
    topic = life.topics()[0]
    action = {'text': '相册整理好了吗？', 'topic_id': topic['id'],
              'memory_ids': json.loads(topic['memory_ids'])}
    did = life.prepare_delivery(action)
    assert life.delivered(did)
    return topic, action


@pytest.mark.parametrize('via_api', [False, True])
def test_completed_plan_memory_and_short_reply_stay_consistent(via_api):
    topic, _ = delivered_plan()
    if via_api:
        assert TestClient(server.app).patch('/api/companionship/topics/' + topic['id'],
                                            json={'state': 'completed'}).status_code == 200
    else:
        response = user('完成了！')
        assert response['state'] == 'completed' and response['waiting'] == 0
    memory.init_db()
    packet = life.packet()
    assert packet['topics'][0]['state'] == 'completed'
    assert packet['memories'][0]['content'] == '用户计划（已完成）：我准备整理相册'
    assert user('还没') is None  # do not resume a closed invitation, or fall back to an older one
    assert life.topics()[0]['state'] == 'completed'
    assert not life.candidates()
    assert life.topic_control(topic['id'], 'open')
    assert memory.companion_memories()[0]['content'] == '用户计划（尚未完成）：我准备整理相册'


def test_unrelated_or_quoted_reply_does_not_acknowledge_invitation(monkeypatch):
    calls = model(monkeypatch)
    topic, _ = delivered_plan()
    for text in ('今天吃什么', '朋友说“完成了”', '如果想好了再告诉你', '还没吃饭', '完成了？'):
        list(agent.run_stream(memory.daily_session(), text))
        packet = json.loads(calls[-1][0][-2]['content'].split('\n', 1)[1])
        assert packet['response_topic'] is None
        saved = life.topics()[0]
        assert saved['waiting'] == 1 and saved['state'] == 'open'
    assert not life.candidates(time.time() + 2 * 86400)
    assert user('还没。')['id'] == topic['id']
    assert life.topics()[0]['waiting'] == 0


def test_delete_daily_session_invalidates_memories_topics_and_queue():
    topic, action = delivered_plan()
    user('还没')
    queued = life.prepare_delivery(action)
    user('叫我小林')
    memory.create_session('work-kept')
    memory.save_message('work-kept', 'user', '保留工作记录')
    memory.note_save('工作便签', '工作', 'work-kept')
    client = TestClient(server.app)
    assert client.delete('/api/sessions/' + memory.DAILY_SESSION_ID).status_code == 200
    memory.init_db()
    assert not memory.companion_memories()
    assert not life.topics() and not life.candidates()
    assert not life.delivery_valid(queued) and not life.delivered(queued)
    assert memory.message_count(memory.daily_session()) == 0
    assert memory.companion_times()['chat']['timestamp'] is None
    with memory._conn() as conn:
        assert conn.execute('SELECT count(*) FROM companion_deliveries').fetchone()[0] == 0
    assert memory.message_count('work-kept') == 1
    assert memory.get_recent_notes(3, 'work-kept')
    assert '小林' not in memory.companion_redact('称呼：小林')


def test_missing_source_cannot_supply_proactive_memory():
    user('我准备整理相册')
    topic = life.topics()[0]
    queued = life.prepare_delivery({'text': '相册整理好了吗？', 'topic_id': topic['id'],
                                    'memory_ids': json.loads(topic['memory_ids'])})
    with memory._conn() as conn:
        conn.execute('DELETE FROM messages WHERE id=?', (topic['source_message'],))
    assert not memory.companion_memories() and not life.candidates()
    assert not life.delivery_valid(queued)


def test_touch_after_queue_prevents_poll_and_display_ack(monkeypatch):
    clock = time.time()
    # Patch only the companionship module clock, not the process-wide time module.
    monkeypatch.setattr(life, 'time', SimpleNamespace(time=lambda: clock))
    queued = life.prepare_delivery({'text': '一起歇一会儿吧。'})
    clock += 1
    memory.companion_time('interaction', clock)
    api = pet3d_win._NativeApi(enabled=False)
    api._desk = {}
    api.update_look = lambda *a: None
    api._pending = {'kind': 'say', 'text': '一起歇一会儿吧。', 'delivery_id': queued}
    assert api.poll_action() is None
    assert not api.proactive_displayed(queued)
    assert memory.message_count(memory.daily_session()) == 0
    # New output produced after that interaction remains deliverable.
    clock += 1
    fresh = life.prepare_delivery({'text': '好，陪你坐一会儿。'})
    assert life.delivered(fresh)


def test_memory_revision_preserves_new_history_and_summary(monkeypatch):
    calls = model(monkeypatch)
    user('我喜欢红茶')
    old = memory.companion_memories()[0]
    assert memory.update_companion_memory(old['id'], content='偏好：红茶和乌龙茶')
    revised = '现在我喜欢红茶和乌龙茶'
    memory.save_message(memory.daily_session(), 'user', revised)
    memory.save_summary(memory.daily_session(), revised, old['source_message'])
    list(agent.run_stream(memory.daily_session(), '记住了吗'))
    messages = calls[-1][0]
    assert any(m['content'] == revised for m in messages)
    assert not any(m['content'] == '我喜欢红茶' for m in messages)
    assert revised in messages[-2]['content']
    # The same version boundary is applied when old messages roll into an L2 summary.
    pending = memory.pending_messages(memory.daily_session(), keep=1, redact=True)
    assert revised in [m['content'] for m in pending]
    assert '我喜欢红茶' not in [m['content'] for m in pending]
    assert memory.companion_redact('以前我喜欢红茶，现在我喜欢红茶和乌龙茶') == \
        '以前[旧记忆已修正或删除]，现在我喜欢红茶和乌龙茶'


def test_spoken_correction_and_restored_preference_are_not_redacted():
    user('我喜欢红茶')
    user('现在我喜欢红茶和乌龙茶')
    history = memory.load_history(memory.daily_session(), redact=True)
    assert history[0]['content'] != '我喜欢红茶'
    assert history[1]['content'] == '现在我喜欢红茶和乌龙茶'
    # A previously superseded value may become the user's current explicit preference again.
    user('现在我喜欢红茶')
    assert memory.load_history(memory.daily_session(), redact=True)[-1]['content'] == '现在我喜欢红茶'
    assert memory.companion_redact('偏好：红茶') == '偏好：红茶'
