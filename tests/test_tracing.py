import json
import sqlite3

import pytest

from nyalume.core import agent, memory, tracing


@pytest.fixture
def trace_db(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "trace.db"))
    memory.init_db()
    monkeypatch.setattr(agent.llm, "chat_once", lambda *args, **kwargs: pytest.fail("Unexpected API call"))


def test_trace_records_tool_loop_without_content(trace_db, monkeypatch):
    rounds = []

    def model(messages, tools=None, **kwargs):
        rounds.append(messages)
        if len(rounds) == 1:
            yield {"kind": "tool_delta", "index": 0, "id": "call-1",
                   "name": "calculator", "arguments": '{"expression":"123*456"}'}
        else:
            yield {"kind": "content", "text": "private-response"}

    monkeypatch.setattr(agent.llm, "chat_stream", model)
    events = list(agent.run_stream("trace-loop", "private-question"))
    trace = tracing.list_runs("trace-loop")[0]
    assert tracing.get_run(trace["run_id"])["status"] == "completed"
    assert trace["run_id"] == events[0]["run_id"] == events[-1]["run_id"]
    assert trace["status"] == "completed"
    assert [span["kind"] for span in trace["spans"]] == ["llm", "tool", "llm"]
    assert all(span["duration_ms"] >= 0 for span in trace["spans"])
    saved = json.dumps(trace)
    assert not any(value in saved for value in ("private-question", "private-response", "123*456"))


@pytest.mark.parametrize("outcome", ["error", "cancelled", "max_rounds"])
def test_trace_terminal_states(trace_db, monkeypatch, outcome):
    def model(*args, **kwargs):
        if outcome == "error":
            raise RuntimeError("private-error")
        if outcome == "max_rounds":
            yield {"kind": "tool_delta", "index": 0, "id": "call-1",
                   "name": "calculator", "arguments": '{"expression":"1+1"}'}
        else:
            yield {"kind": "content", "text": "x" * 80}

    monkeypatch.setattr(agent.llm, "chat_stream", model)
    monkeypatch.setattr(agent, "MAX_TOOL_ROUNDS", 2)
    stream = agent.run_stream("trace-state", "test")
    if outcome == "cancelled":
        next(stream)
        next(stream)
        stream.close()
    else:
        events = list(stream)
    trace = tracing.list_runs("trace-state")[0]
    assert trace["status"] == outcome
    if outcome == "error":
        assert trace["error_type"] == "RuntimeError"
        assert "private-error" not in json.dumps(trace)
    if outcome == "max_rounds":
        assert sum(span["kind"] == "llm" for span in trace["spans"]) == 2
        text = "".join(event.get("text", "") for event in events)
        assert "保留前面完成的结果" in text
        assert "换个说法" not in text


def test_trace_preserves_approval_send(trace_db, monkeypatch):
    rounds = []

    def model(*args, **kwargs):
        rounds.append(1)
        if len(rounds) == 1:
            yield {"kind": "tool_delta", "index": 0, "id": "call-1",
                   "name": "calculator", "arguments": '{"expression":"2+2"}'}
        else:
            yield {"kind": "content", "text": "done"}

    monkeypatch.setattr(agent.llm, "chat_stream", model)
    monkeypatch.setattr(agent, "approval_needed", lambda *args: (True, "test approval"))
    monkeypatch.setattr(agent, "set_approved_context", lambda *args: None)
    stream = agent.run_stream("trace-approval", "test")
    for event in stream:
        if event["type"] == "approval":
            assert stream.send("allow")["type"] == "tool_result"
    trace = tracing.list_runs("trace-approval")[0]
    assert trace["status"] == "completed"
    assert next(span for span in trace["spans"] if span["kind"] == "approval")["status"] == "allow"


def test_trace_storage_failure_does_not_stop_chat(trace_db, monkeypatch):
    def unavailable():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(tracing, "_connect", unavailable)
    monkeypatch.setattr(agent.llm, "chat_stream", lambda *args, **kwargs: iter([
        {"kind": "content", "text": "done"}
    ]))
    assert list(agent.run_stream("trace-storage", "test"))[-1]["type"] == "done"
