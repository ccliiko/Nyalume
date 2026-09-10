"""不调用真实模型的端到端 Agent 行为评测。"""

import json
from pathlib import Path

from nyalume.core import agent, memory, reminders, tools


def _tool_call(name: str, arguments: dict):
    payload = json.dumps(arguments, ensure_ascii=False)
    midpoint = len(payload) // 2
    yield {
        "kind": "tool_delta",
        "index": 0,
        "id": "call_eval",
        "name": name,
        "arguments": payload[:midpoint],
    }
    yield {
        "kind": "tool_delta",
        "index": 0,
        "id": "",
        "name": "",
        "arguments": payload[midpoint:],
    }


def test_nyalume_receives_shared_work_discipline(monkeypatch):
    monkeypatch.setattr(agent, "resolve_persona_id", lambda: "nyalume")
    prompt = agent._system_prompt()
    assert "优先复用已有能力和最简单可行方案" in prompt
    assert "只改完成目标所需的范围" in prompt
    assert "运行或测试验证" in prompt
    assert "不把未验证的事情说成已经完成" in prompt
    assert "信息不足但能靠只读检查消除时，先检查再决定" in prompt


def test_pdf_export_defaults_to_rendered_plantuml():
    prompt = agent._system_prompt()
    assert "导出 PDF" in prompt
    assert "PlantUML" in prompt
    assert "默认先渲染为图片并嵌入 PDF" in prompt
    assert "不要询问用户" in prompt


def test_cleanup_request_gets_scan_first_without_reasking():
    hint = agent._pre_round_discipline("帮我清理 D 盘垃圾文件")
    assert "先自动做只读空间盘点" in hint
    assert "不要问“要不要先扫描/从 Temp 开始”" in hint


def test_batch_work_gets_batching_discipline():
    hint = agent._pre_round_discipline("同时处理这些文件，每个都生成一份答案")
    assert "批量任务" in hint
    assert "同一轮一起发出" in hint
    assert "不要重复" in hint


def test_daily_mode_has_no_tools_and_updates_affection(monkeypatch):
    session_id = "eval-daily"
    memory.set_daily_affection(session_id, 50)
    tools.set_permission_mode("daily")
    captured = {}

    def fake_chat_stream(messages, tools=None, **kwargs):
        captured["tools"] = tools
        captured["prompt"] = messages[0]["content"]
        yield {"kind": "content", "text": "今天也陪着主人喵。\n"}
        yield {"kind": "content", "text": "[daily_affection:+3]"}

    monkeypatch.setattr(agent.llm, "chat_stream", fake_chat_stream)
    try:
        events = list(agent.run_stream(session_id, "聊聊天"))
    finally:
        tools.set_permission_mode("workspace")
    shown = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert captured["tools"] is None
    assert "纯聊天模式" in captured["prompt"]
    assert "daily_affection" not in shown
    assert memory.get_daily_affection(session_id) == 53


def test_agent_eval_executes_tool_then_finishes(monkeypatch):
    rounds = 0

    def fake_chat_stream(messages, tools=None, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield from _tool_call("calculator", {"expression": "6*7"})
            return
        assert any(m.get("role") == "tool" and "42" in m["content"] for m in messages)
        yield {"kind": "content", "text": "结果是 42。"}

    monkeypatch.setattr(agent.llm, "chat_stream", fake_chat_stream)
    events = list(agent.run_stream("eval-tool-loop", "帮我计算 6*7"))

    assert rounds == 2
    assert any(e.get("type") == "tool" and e.get("name") == "calculator" for e in events)
    assert any(e.get("type") == "tool_result" and "42" in e.get("result", "") for e in events)
    assert any(e.get("type") == "done" for e in events)


def test_agent_allows_more_than_ten_productive_tool_rounds(monkeypatch):
    rounds = 0

    def fake_chat_stream(messages, tools=None, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds <= 12:
            yield from _tool_call("calculator", {"expression": f"{rounds}+1"})
            return
        yield {"kind": "content", "text": "十二项都处理完成。"}

    monkeypatch.setattr(agent.llm, "chat_stream", fake_chat_stream)
    events = list(agent.run_stream("eval-long-batch", "同时处理十二项计算"))
    assert rounds == 13
    assert any(e.get("type") == "done" for e in events)
    assert "换个说法" not in "".join(e.get("text", "") for e in events)


def test_agent_cleans_its_temporary_helper_after_work(monkeypatch):
    rounds = 0
    session_id = memory.create_session()

    def fake_chat_stream(messages, tools=None, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield from _tool_call(
                "file_write", {"path": ".nyalume/tmp/helper.py", "content": "print(1)"}
            )
            return
        yield {"kind": "content", "text": "处理完成。"}

    try:
        monkeypatch.setattr(agent.llm, "chat_stream", fake_chat_stream)
        list(agent.run_stream(session_id, "生成一个临时辅助脚本并完成任务"))
        root = tools.session_work_root(session_id)
        assert not (Path(root) / ".nyalume" / "tmp").exists()
    finally:
        memory.delete_session(session_id)


def test_agent_eval_injects_rag_source(monkeypatch):
    source = "D:/eval/rag-handbook.txt"
    session_id = "eval-rag"
    captured = {}

    def fake_chat_stream(messages, tools=None, **kwargs):
        captured["system"] = messages[0]["content"]
        yield {"kind": "content", "text": "应按账龄分组统计。"}

    memory.doc_save(
        source, "RAG手册.txt", ["信用卡逾期率需要按账龄分组统计。"], session_id
    )
    try:
        monkeypatch.setattr(agent.llm, "chat_stream", fake_chat_stream)
        list(agent.run_stream(session_id, "信用卡逾期率怎么统计？"))
        assert "RAG手册.txt" in captured["system"]
        assert "信用卡逾期率需要按账龄分组统计" in captured["system"]
    finally:
        memory.doc_delete(source, session_id)


def test_agent_eval_denies_unapproved_outside_write(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    memory.set_setting("workspace_root", str(workspace))
    tools.set_permission_mode("workspace")
    rounds = 0

    def fake_chat_stream(messages, tools=None, **kwargs):
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            yield from _tool_call(
                "file_write", {"path": str(outside), "content": "blocked"}
            )
            return
        assert any("用户拒绝了这次操作" in m.get("content", "") for m in messages)
        yield {"kind": "content", "text": "操作已取消。"}

    try:
        monkeypatch.setattr(agent.llm, "chat_stream", fake_chat_stream)
        events = list(agent.run_stream("eval-approval", "写入项目外文件"))
        assert any(e.get("type") == "approval" for e in events)
        assert not outside.exists()
    finally:
        memory.set_setting("workspace_root", "")


def test_agent_eval_one_shot_reminder_finishes(monkeypatch):
    def fake_chat_stream(messages, tools=None, **kwargs):
        yield {"kind": "content", "text": "提醒已经设置。"}

    before = {item["id"] for item in reminders.list_reminders(enabled_only=False)}
    try:
        monkeypatch.setattr(agent.llm, "chat_stream", fake_chat_stream)
        events = list(agent.run_stream("eval-reminder", "1分钟后提醒我测试"))
        assert any(e.get("type") == "done" for e in events)
    finally:
        for item in reminders.list_reminders(enabled_only=False):
            if item["id"] not in before:
                reminders.delete_reminder(item["id"])
