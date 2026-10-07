"""目标执行与编辑重发的回归；模型和工具均使用本地替身。"""

import json
import threading

import pytest
from fastapi import HTTPException

from nyalume.core import memory, pursuit
from nyalume.frontends.web import server


@pytest.fixture
def goal(monkeypatch):
    memory.init_db()
    sid = memory.create_session()
    monkeypatch.setattr(pursuit, '_llm_json', lambda *a, **kw:
        {'plan': [{'id': 1, 'task': '验证结果'}], 'overall_status': 'completed',
         'deliverable_summary': '结果已经验证'})
    monkeypatch.setattr(pursuit.llm, 'chat_stream', lambda *a, **kw: iter([
        {'kind': 'content', 'text': json.dumps({'status': 'completed', 'result': '验证成功'})}]))
    return sid, pursuit.create_pursuit('验证目标', sid)['id']


def test_goal_completion_saves_history_and_trace(goal):
    sid, pid = goal
    events = list(pursuit.run_pursuit(pid, sid))
    assert pursuit.get_pursuit(pid)['status'] == 'completed'
    assert events[-1]['type'] == 'done'
    assert [m['role'] for m in memory.session_messages(sid)] == ['user', 'assistant']
    assert '结果已经验证' in memory.session_messages(sid)[-1]['content']


@pytest.mark.parametrize('status,expected', [('needs_input', 'paused'), ('failed', 'failed'), ('partial', 'failed')])
def test_incomplete_step_never_reports_success(goal, monkeypatch, status, expected):
    sid, pid = goal
    monkeypatch.setattr(pursuit.llm, 'chat_stream', lambda *a, **kw: iter([
        {'kind': 'content', 'text': json.dumps({'status': status, 'result': '需要更多信息'})}]))
    events = list(pursuit.run_pursuit(pid, sid))
    assert pursuit.get_pursuit(pid)['status'] == expected
    assert not any(e['type'] == 'pursuit_complete' for e in events)


def test_failed_evaluation_never_reports_success(goal, monkeypatch):
    sid, pid = goal
    monkeypatch.setattr(pursuit, '_llm_json', lambda *a, **kw:
        {'plan': [{'id': 1, 'task': '验证结果'}], 'overall_status': 'blocked'})
    events = list(pursuit.run_pursuit(pid, sid))
    assert pursuit.get_pursuit(pid)['status'] == 'failed'
    assert events[-1]['type'] == 'error'


def test_goal_tool_denial_and_cancellation(goal, monkeypatch):
    sid, pid = goal
    calls = []
    monkeypatch.setattr(pursuit, 'approval_needed', lambda *a: (True, '需要授权'))
    monkeypatch.setattr(pursuit, 'execute_tool', lambda *a: calls.append(a) or '完成')
    responses = iter([
        [{'kind': 'tool_delta', 'index': 0, 'id': 'call1', 'name': 'file_write', 'arguments': '{}'}],
        [{'kind': 'content', 'text': '{"status":"failed","result":"用户拒绝"}'}],
    ])
    monkeypatch.setattr(pursuit.llm, 'chat_stream', lambda *a, **kw: iter(next(responses)))
    stream = pursuit.run_pursuit(pid, sid)
    while next(stream)['type'] != 'approval':
        pass
    stream.send('deny')
    list(stream)
    assert calls == []
    cancel = threading.Event()
    cancel.set()
    events = list(pursuit.run_pursuit(pid, sid, cancel_event=cancel))
    assert events[-1]['type'] == 'pursuit_stopped'
    assert pursuit.get_pursuit(pid)['status'] == 'stopped'


def test_edit_replaces_original_and_refuses_active_writer(goal):
    sid, _ = goal
    mid = memory.save_message(sid, 'user', '原始问题')
    memory.save_message(sid, 'assistant', '旧答案')
    lock = server._session_run_lock(sid)
    lock.acquire()
    try:
        with pytest.raises(HTTPException) as error:
            server.delete_messages_after(sid, mid)
        assert error.value.status_code == 409
        assert len(memory.session_messages(sid)) == 2
    finally:
        lock.release()
    server.delete_messages_after(sid, mid)
    memory.save_message(sid, 'user', '新问题')
    assert [m['content'] for m in memory.session_messages(sid)] == ['新问题']


def test_cancel_waits_for_writer_cleanup():
    cancel = threading.Event()
    server._RUN_CANCEL_EVENTS['wait-test'] = cancel
    def finish():
        cancel.wait(2)
        with server._RUNS_LOCK:
            server._RUN_CANCEL_EVENTS.pop('wait-test', None)
    thread = threading.Thread(target=finish)
    thread.start()
    try:
        assert server.cancel_run('wait-test', wait=True) == {'ok': True, 'active': False}
        assert cancel.is_set()
    finally:
        thread.join(2)


def test_cancel_before_registration_does_not_start_old_request(goal):
    sid, _ = goal
    server.cancel_run('cancel-before-register', wait=True)
    calls = []
    def runner(*args, **kwargs):
        calls.append(args)
        yield {'type': 'text', 'text': 'new'}
    response = server._chat_response(server.ChatIn(session_id=sid, message='old', request_id='cancel-before-register'), runner)
    import asyncio
    async def consume(response):
        return [item async for item in response.body_iterator]
    assert asyncio.run(consume(response)) == []
    assert not calls
    response = server._chat_response(server.ChatIn(session_id=sid, message='new', request_id='fresh-after-cancel'), runner)
    assert 'new' in ''.join(asyncio.run(consume(response)))
    assert len(calls) == 1


def test_disconnect_stops_goal_even_before_planning(goal):
    sid, pid = goal
    stream = pursuit.run_pursuit(pid, sid)
    assert next(stream)['type'] == 'pursuit_start'
    stream.close()
    assert pursuit.get_pursuit(pid)['status'] == 'stopped'


def test_goal_uses_the_chat_session_lock(goal, monkeypatch):
    sid, pid = goal
    monkeypatch.setattr(server, 'permission_mode', lambda: 'workspace')
    lock = server._session_run_lock(sid)
    lock.acquire()
    try:
        with pytest.raises(HTTPException) as error:
            server.run_pursuit_stream(pid)
        assert error.value.status_code == 409
    finally:
        lock.release()
