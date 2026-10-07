"""已移除的入口不再创建计划或推送提醒；历史数据仍可读取。"""

from fastapi.testclient import TestClient

from nyalume.core import agent, memory, reminders, tools
from nyalume.frontends.web import server


def test_retired_endpoints_are_unavailable():
    client = TestClient(server.app)
    for endpoint in ('/api/reminders', '/api/reminders/due', '/api/pet/plan', '/api/pet/plan/collection'):
        assert client.get(endpoint).status_code == 404
    assert client.post('/api/reminders', json={'content': '喝水', 'cron': '* * * * *'}).status_code == 404
    assert client.post('/api/pet/plan/start', json={}).status_code == 404


def test_time_mentions_and_explicit_reminders_cannot_create_reminders(monkeypatch):
    memory.init_db()
    sid = memory.create_session()
    before = reminders.list_reminders(enabled_only=False)
    captured = []
    def model(messages, tools=None, **kwargs):
        captured.extend(t['function']['name'] for t in tools or [])
        yield {'kind': 'content', 'text': '我会继续帮你处理当前问题喵。'}
    monkeypatch.setattr(agent.llm, 'chat_stream', model)
    for message in ('这个程序运行了 3 分钟', '1分钟后提醒我喝水'):
        list(agent.run_stream(sid, message))
    assert reminders.list_reminders(enabled_only=False) == before
    assert not {'create_reminder', 'remind_me_in', 'list_reminders', 'cancel_reminder', 'pet_plan'} & set(captured)
    assert '未知工具' in tools.execute_tool('pet_plan', {'action': 'start'})
