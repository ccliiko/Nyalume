"""计划模式复用聊天流，但只允许调研工具。"""

import asyncio
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from nyalume.core import agent, memory, tools
from nyalume.frontends.web import server


@pytest.fixture
def plan_db(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "plan.db"))
    memory.init_db()
    tools.set_permission_mode("full")
    yield
    tools.clear_session_context()


def test_plan_reads_context_blocks_mutations_and_keeps_temp(plan_db, monkeypatch, tmp_path):
    project = memory.add_project(str(tmp_path), "计划测试")
    sid = memory.create_session(project=project["id"])
    source = tmp_path / "demo.py"
    source.write_text("print(42)", encoding="utf-8")
    temp = tmp_path / ".nyalume" / "tmp" / "existing.py"
    temp.parent.mkdir(parents=True)
    temp.write_text("keep", encoding="utf-8")
    calls = iter([
        ("file_read", {"path": "demo.py"}),
        ("file_write", {"path": "demo.py", "content": "changed"}),
        ("run_code", {"command": "python -c 'print(0)'"}),
        ("browser_click", {"label": "发送"}),
        ("skill_install_url", {"url": "https://example.com/skill.zip"}),
    ])
    rounds = 0

    def model(messages, tools=None, **kwargs):
        nonlocal rounds
        rounds += 1
        assert "【计划模式】" in messages[0]["content"]
        assert "必须立刻调用对应工具" not in messages[0]["content"]
        assert {t["function"]["name"] for t in tools} == agent._PLAN_TOOLS
        if rounds > 1:
            result = messages[-1]["content"]
            expected = "print(42)" if rounds == 2 else "计划模式已拦截"
            assert expected in result
        if rounds == 6:
            yield {"kind": "content", "text": "计划：修改 demo.py，然后运行验证。"}
            return
        name, arguments = next(calls)
        yield {"kind": "tool_delta", "index": 0, "id": f"call-{rounds}",
               "name": name, "arguments": json.dumps(arguments)}

    original_execute = agent.execute_tool
    executed = []

    def execute(name, arguments):
        executed.append(name)
        return original_execute(name, arguments)

    monkeypatch.setattr(agent.llm, "chat_stream", model)
    monkeypatch.setattr(agent, "execute_tool", execute)
    monkeypatch.setattr(agent.review_agent, "review_tool_call", lambda *args: {"approved": True})
    events = list(agent.run_stream(sid, "改完代码后运行并发送结果", plan_mode=True))
    assert not any(e["type"] == "error" for e in events), events
    assert executed == ["file_read"]
    assert events[-1]["type"] == "done"
    assert not any(e["type"] == "approval" for e in events)
    assert source.read_text(encoding="utf-8") == "print(42)"
    assert temp.exists()
    assert tools.permission_mode() == "full"
    assert memory.session_messages(sid)[-1]["content"].startswith("计划：")


def test_chat_routes_plan_flag_without_changing_normal_chat(plan_db, monkeypatch):
    seen = []

    def runner(session_id, message, cancel_event=None, *, plan_mode=False):
        seen.append((session_id, message, plan_mode))
        yield {"type": "done"}

    async def consume(response):
        return [chunk async for chunk in response.body_iterator]

    monkeypatch.setattr(server, "run_stream", runner)
    for mode in ("plan", "chat"):
        response = server.chat(server.ChatIn(session_id="plan-api", message="制定方案", mode=mode))
        assert "done" in "".join(asyncio.run(consume(response)))
    assert seen == [("plan-api", "制定方案", True), ("plan-api", "制定方案", False)]
    with pytest.raises(ValidationError):
        server.ChatIn(message="hi", mode="unknown")


def test_daily_mode_rejects_plan_before_creating_messages(plan_db):
    tools.set_permission_mode("daily")
    with pytest.raises(HTTPException) as error:
        server.chat(server.ChatIn(session_id="plan-daily", message="计划", mode="plan"))
    assert error.value.status_code == 400
    assert list(agent.run_stream("plan-daily", "计划", plan_mode=True))[0]["type"] == "error"
    assert memory.session_messages("plan-daily") == []
    tools.set_permission_mode("workspace")
    with pytest.raises(HTTPException):
        server.chat(server.ChatIn(session_id=memory.DAILY_SESSION_ID, message="计划", mode="plan"))
