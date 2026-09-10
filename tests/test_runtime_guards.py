import threading
import time

import pytest
from fastapi import HTTPException

from nyalume.core import agent, memory, tools
from nyalume.frontends.web import server


@pytest.fixture
def runtime_db(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "runtime.db"))
    memory.init_db()
    yield
    tools.clear_session_context()


def test_high_risk_browser_actions_always_need_approval(runtime_db):
    assert tools.approval_needed(
        "browser_click", {"label": "确认购买"}
    )[0]
    assert tools.approval_needed(
        "browser_fill", {"label": "信用卡安全码", "value": "123"}
    )[0]
    assert not tools.approval_needed("browser_click", {"label": "下一页"})[0]
    assert not tools.approval_rememberable("browser_click")


def test_run_code_is_really_stopped_by_cancel(runtime_db, tmp_path):
    memory.set_setting("workspace_root", str(tmp_path))
    tools.set_session_context("cancel-tool")
    cancel_event = threading.Event()
    tools.set_run_control(cancel_event, time.monotonic() + 10)
    timer = threading.Timer(0.2, cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        result = tools.execute_tool(
            "run_code",
            {"command": 'python -c "import time; time.sleep(5)"', "timeout": 10},
        )
    finally:
        timer.cancel()
        memory.set_setting("workspace_root", "")
    assert "取消" in result
    assert time.monotonic() - started < 2


def test_agent_cancel_and_task_timeout_have_terminal_states(runtime_db, monkeypatch):
    cancel_event = threading.Event()

    def cancelling_model(*args, **kwargs):
        yield {"kind": "content", "text": "开始"}
        cancel_event.set()
        return

    monkeypatch.setattr(agent.llm, "chat_stream", cancelling_model)
    events = list(agent.run_stream("cancel-run", "开始", cancel_event=cancel_event))
    assert events[-1]["type"] == "cancelled"
    assert memory.session_messages("cancel-run")[-1]["role"] == "user"

    monkeypatch.setattr(agent, "TASK_TIMEOUT_SECONDS", 0)
    events = list(agent.run_stream("timeout-run", "开始"))
    assert events[-1]["type"] == "error"
    assert "已经自动停止" in events[-1]["message"]


def test_server_isolates_run_locks_by_session_and_cancel_is_idempotent():
    first = server._session_run_lock("same-session")
    other = server._session_run_lock("other-session")
    assert first is server._session_run_lock("same-session")
    assert first is not other
    assert first.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as error:
            server.chat(server.ChatIn(session_id="same-session", message="hello"))
        assert error.value.status_code == 409
        assert other.acquire(blocking=False)
        other.release()
    finally:
        first.release()

    cancel_event = threading.Event()
    with server._RUNS_LOCK:
        server._RUN_CANCEL_EVENTS["run-test"] = cancel_event
    try:
        assert server.cancel_run("run-test") == {"ok": True, "active": True}
        assert cancel_event.is_set()
        assert server.cancel_run("missing") == {"ok": True, "active": False}
    finally:
        with server._RUNS_LOCK:
            server._RUN_CANCEL_EVENTS.pop("run-test", None)
