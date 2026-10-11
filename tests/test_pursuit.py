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
    assert '#1' not in memory.session_messages(sid)[-1]['content']


@pytest.mark.parametrize('first_status', ['completed', 'failed'])
def test_goal_discovers_one_next_action_from_actual_results(goal, monkeypatch, first_status):
    sid, pid = goal
    model_replies = iter([
        {'analysis': '先检查现状', 'plan': [
            {'id': 8, 'task': '检查现状', 'depends_on': [99], 'status': 'completed'},
            {'id': 9, 'task': '预先猜测的后续步骤'}]},
        {'overall_status': 'in_progress', 'progress_pct': 100, 'summary': '需要修复后验证',
         'revision': {'add': [{'task': '根据检查结果修复', 'depends_on': [99]},
                              {'task': '尚未确定的未来步骤'}]}},
        {'overall_status': 'completed', 'deliverable_summary': '修复并验证完成'},
    ])
    evaluations = []
    def decide(messages, **kwargs):
        evaluations.append(messages[-1]['content'])
        return next(model_replies)
    execution_contexts = []
    outcomes = iter([first_status, 'completed'])
    def execute(messages, **kwargs):
        execution_contexts.append(messages[-1]['content'])
        return iter([{'kind': 'content', 'text': json.dumps({
            'status': next(outcomes), 'result': '发现配置缺失', 'deliverables': ['检查报告.txt']})}])
    monkeypatch.setattr(pursuit, '_llm_json', decide)
    monkeypatch.setattr(pursuit.llm, 'chat_stream', execute)
    events = [json.loads(json.dumps(e)) for e in pursuit.run_pursuit(pid, sid)]

    initial = next(e for e in events if e['type'] == 'pursuit_plan')
    assert initial['plan'] == [{'id': 1, 'task': '检查现状', 'depends_on': [], 'status': 'pending'}]
    assert [e['task'] for e in events if e['type'] == 'pursuit_step_start'] == [
        '检查现状', '根据检查结果修复']
    assert '发现配置缺失' in evaluations[1]
    assert '检查报告.txt' in evaluations[1]
    assert '发现配置缺失' in execution_contexts[1]
    assert next(e for e in events if e['type'] == 'pursuit_progress')['progress_pct'] == 99
    assert pursuit.get_pursuit(pid)['status'] == 'completed'
    assert len(pursuit.get_pursuit(pid)['steps']) == 2


@pytest.mark.parametrize('evaluation', [
    {'overall_status': 'blocked', 'summary': '缺少外部访问权限'},
    {'overall_status': 'in_progress', 'summary': '交付物尚未完成'},
    {'error': 'JSON 解析失败'},
])
def test_goal_does_not_stop_as_success_when_first_action_is_done(goal, monkeypatch, evaluation):
    sid, pid = goal
    replies = iter([{'plan': [{'task': '检查现状'}]}, evaluation])
    monkeypatch.setattr(pursuit, '_llm_json', lambda *a, **kw: next(replies))
    events = list(pursuit.run_pursuit(pid, sid))
    assert pursuit.get_pursuit(pid)['status'] == 'failed'
    assert events[-1]['type'] == 'error'
    assert not any(e['type'] == 'pursuit_complete' for e in events)


def test_goal_stops_after_three_consecutive_failed_actions(goal, monkeypatch):
    sid, pid = goal
    monkeypatch.setattr(pursuit, '_llm_json', lambda *a, **kw: {
        'plan': [{'task': '尝试执行'}], 'overall_status': 'needs_revision',
        'summary': '操作仍然失败', 'revision': {'add': [{'task': '尝试替代方案'}]}})
    monkeypatch.setattr(pursuit.llm, 'chat_stream', lambda *a, **kw: iter([
        {'kind': 'content', 'text': '{"status":"failed","result":"无法访问"}'}]))
    events = list(pursuit.run_pursuit(pid, sid))
    assert len([e for e in events if e['type'] == 'pursuit_step_start']) == 3
    assert '连续三次行动失败' in events[-1]['message']
    assert pursuit.get_pursuit(pid)['status'] == 'failed'


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
