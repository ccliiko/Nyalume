"""Fake SDK chunks through the real adapter, Agent, trace and temporary database.

No credentials, user database, model requests or desktop startup are needed.
"""

import json
import threading
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

# llm and tools.vision normally load .env during import; these tests never do.
with patch("dotenv.load_dotenv", return_value=False):
    from nyalume.core import agent, llm, memory, tracing


def chunk(text=None, reason=None, *, tools=None, reasoning=None, usage=None):
    return NS(usage=usage, choices=[NS(
        finish_reason=reason,
        delta=NS(content=text, tool_calls=tools, reasoning_content=reasoning),
    )])


def tool_delta(arguments, *, name=None, call_id=None, index=0):
    return NS(index=index, id=call_id, function=NS(name=name, arguments=arguments))


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.consumed = 0

    def __iter__(self):
        for item in self.chunks:
            self.consumed += 1
            if isinstance(item, Exception):
                raise item
            if callable(item):
                item = item()
            yield item

    def close(self):
        self.closed = True


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "stream.db"))
    memory.init_db()
    monkeypatch.setattr(agent, "_system_prompt", lambda **kwargs: "Synthetic test")
    monkeypatch.setattr(agent, "cleanup_session_temp", lambda *args: None)
    monkeypatch.setattr(agent, "resolve_persona_id", lambda: "assistant")
    monkeypatch.setattr(llm, "get_model", lambda: "synthetic-model")
    monkeypatch.setattr(llm, "_effort_options", lambda: {})
    streams, requests = [], []

    def create(**kwargs):
        requests.append(kwargs)
        assert streams, "Unexpected model request (including automatic retry)"
        return streams.pop(0)

    monkeypatch.setattr(llm, "get_client", lambda: NS(chat=NS(completions=NS(create=create))))

    def install(*responses):
        prepared = [FakeStream(response) for response in responses]
        streams.extend(prepared)
        return prepared

    return NS(install=install, requests=requests)


@pytest.mark.parametrize("reason", ["length", None, "content_filter"])
def test_incomplete_response_keeps_visible_text_but_never_archives_success(runtime, reason):
    response = [chunk("synthetic partial")]
    if reason is not None:
        response.append(chunk(reason=reason))
    stream, = runtime.install(response)
    events = list(agent.run_stream("incomplete", "synthetic request"))
    assert "".join(e.get("text", "") for e in events if e["type"] == "text") == "synthetic partial"
    assert events[-1]["type"] == "error"
    assert "未完成" in events[-1]["message"] or "完整回复" in events[-1]["message"]
    assert "未作为完整回复保存" in events[-1]["message"]
    assert [row["role"] for row in memory.session_messages("incomplete")] == ["user"]
    trace = tracing.list_runs("incomplete")[0]
    assert trace["status"] == "error"
    assert trace["error_type"] == "IncompleteResponseError"
    assert trace["spans"][-1]["status"] == "error"
    assert stream.closed
    assert len(runtime.requests) == 1


def test_stop_consumes_usage_tail_and_saves_complete_reply(runtime):
    usage = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
    stream, = runtime.install([
        chunk("complete", reasoning="thinking"),
        chunk(reason="stop"),
        NS(choices=[], usage=usage),
    ])
    events = list(agent.run_stream("complete", "synthetic request"))
    assert [e["type"] for e in events] == ["user_id", "reasoning", "reasoning_end", "text", "done"]
    assert memory.session_messages("complete")[-1]["content"] == "complete"
    assert tracing.list_runs("complete")[0]["status"] == "completed"
    counters = json.loads(memory.get_setting("llm_usage"))[-1]
    assert counters["usage"] == usage
    assert counters["first_token_ms"] is not None
    assert stream.consumed == 3 and stream.closed


def test_usage_only_tail_is_not_a_completion_marker(runtime):
    usage = {"prompt_tokens": 12, "completion_tokens": 4}
    runtime.install([chunk("partial"), NS(choices=[], usage=usage)])
    events = list(agent.run_stream("usage-without-stop", "synthetic request"))
    assert events[-1]["type"] == "error"
    assert [row["role"] for row in memory.session_messages("usage-without-stop")] == ["user"]
    assert json.loads(memory.get_setting("llm_usage"))[-1]["usage"] == usage


@pytest.mark.parametrize("reason", ["function_call", "tool_calls", "unknown"])
def test_nontext_terminal_reason_does_not_complete_a_text_response(runtime, reason):
    runtime.install([chunk("partial"), chunk(reason=reason)])
    events = list(agent.run_stream("invalid-terminal", "synthetic request"))
    assert events[-1]["type"] == "error"
    assert tracing.list_runs("invalid-terminal")[0]["status"] == "error"


def test_tool_fragments_finish_before_execution_and_final_answer(runtime, monkeypatch):
    streams = runtime.install([
        chunk(tools=[tool_delta('{"expression":', name="calculator", call_id="call-1")]),
        chunk(tools=[tool_delta('"2+2"}')]),
        chunk(reason="tool_calls"),
    ], [chunk("four"), chunk(reason="stop")])
    executed = []
    monkeypatch.setattr(agent, "approval_needed", lambda *args: (False, ""))
    monkeypatch.setattr(agent.review_agent, "review_tool_call", lambda *args: {"approved": True})

    def execute(name, arguments):
        assert streams[0].closed
        executed.append((name, arguments))
        return "4"

    monkeypatch.setattr(agent, "execute_tool", execute)
    events = list(agent.run_stream("tools", "synthetic request"))
    assert executed == [("calculator", {"expression": "2+2"})]
    assert events[-1]["type"] == "done"
    assert [row["content"] for row in memory.session_messages("tools") if row["role"] == "assistant"] == ["four"]
    assert all(stream.closed for stream in streams)
    assert runtime.requests[1]["messages"][-1]["role"] == "tool"


@pytest.mark.parametrize("reason, arguments", [
    ("length", '{"expression":"2+2"}'),
    (None, '{"expression":"2+2"}'),
    ("stop", '{"expression":"2+2"}'),
    ("tool_calls", '{"expression":'),
    ("tool_calls", '[]'),
])
def test_unfinished_tool_call_is_never_executed(runtime, monkeypatch, reason, arguments):
    runtime.install([chunk(tools=[tool_delta(arguments, name="calculator", call_id="call-1")]),
                     chunk(reason=reason)])
    monkeypatch.setattr(agent, "execute_tool", lambda *args: pytest.fail("Incomplete tool executed"))
    events = list(agent.run_stream("incomplete-tool", "synthetic request"))
    assert events[-1]["type"] == "error"
    assert not any(e["type"] in ("tool", "done") for e in events)
    assert [row["role"] for row in memory.session_messages("incomplete-tool")] == ["user"]
    assert len(runtime.requests) == 1


def test_transport_exception_keeps_existing_error_contract(runtime):
    stream, = runtime.install([chunk("partial"), ConnectionError("synthetic transport failure")])
    events = list(agent.run_stream("network", "synthetic request"))
    assert events[-1]["type"] == "error"
    assert "ConnectionError" in events[-1]["message"]
    assert tracing.list_runs("network")[0]["error_type"] == "ConnectionError"
    assert [row["role"] for row in memory.session_messages("network")] == ["user"]
    assert stream.closed


def test_cancellation_wins_over_missing_finish_reason_and_retains_usage(runtime):
    cancelled = threading.Event()
    usage = {"prompt_tokens": 5, "completion_tokens": 1}

    def cancel():
        cancelled.set()
        return NS(choices=[], usage=usage)

    stream, = runtime.install([chunk("partial"), cancel])
    events = list(agent.run_stream("cancelled", "synthetic request", cancelled))
    assert events[-1]["type"] == "cancelled"
    assert not any(e["type"] == "error" for e in events)
    assert tracing.list_runs("cancelled")[0]["status"] == "cancelled"
    assert [row["role"] for row in memory.session_messages("cancelled")] == ["user"]
    assert json.loads(memory.get_setting("llm_usage"))[-1]["usage"] == usage
    assert stream.closed


def test_daily_mode_flushes_short_partial_text_without_saving_it(runtime):
    runtime.install([chunk("还没说完的短句"), chunk(reason="length")])
    events = list(agent.run_stream(memory.DAILY_SESSION_ID, "synthetic request"))
    assert [e["text"] for e in events if e["type"] == "text"] == ["还没说完的短句"]
    assert events[-1]["type"] == "error"
    assert [row["role"] for row in memory.session_messages(memory.DAILY_SESSION_ID)] == ["user"]


@pytest.mark.parametrize("reason", ["length", None, "content_filter"])
def test_chat_text_rejects_incomplete_output_for_summary_and_memory_callers(runtime, reason):
    runtime.install([chunk('[{"content":"partial fact"}]'), chunk(reason=reason)])
    with pytest.raises(llm.IncompleteResponseError):
        llm.chat_text([{"role": "user", "content": "synthetic extraction"}])


def test_chat_text_normal_stop_and_explicit_cancellation_remain_supported(runtime):
    runtime.install([chunk(" complete "), chunk(reason="stop")])
    assert llm.chat_text([]) == "complete"
    cancelled = threading.Event()

    def cancel():
        cancelled.set()
        return chunk()

    runtime.install([chunk("partial"), cancel])
    assert llm.chat_text([], cancel_event=cancelled) == "partial"


def test_failed_summary_and_memory_extraction_do_not_advance_checkpoints(runtime):
    sid = "memory-checkpoints"
    for index in range(8):
        memory.save_message(sid, "user" if index % 2 == 0 else "assistant", "synthetic " + str(index))
    memory.save_summary(sid, "原摘要", 0)
    runtime.install([chunk("截断的新摘要"), chunk(reason="length")])
    events = list(agent._refresh_summaries(sid, keep=2))
    assert [e["status"] for e in events] == ["started", "failed"]
    assert memory.get_summary(sid) == "原摘要"
    runtime.install([chunk('[{"content":"partial fact"}]'), chunk(reason="length")])
    agent._maybe_auto_notes(sid)
    assert memory.get_memory_upto(sid) == 0
    assert memory.get_recent_notes(50, sid) == []
