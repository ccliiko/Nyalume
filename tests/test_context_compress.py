"""自动上下文压缩：token 预算内保留新消息，超预算丢最旧。"""

from nyalume.core import agent, memory


def compression_session():
    memory.init_db()
    sid = memory.create_session()
    for index in range(8):
        memory.save_message(sid, 'user' if index % 2 == 0 else 'assistant', '关于项目的中文消息 ' + str(index))
    return sid


def test_compression_events_and_chinese_summary(monkeypatch):
    sid = compression_session()
    replies = iter(['User edited agent.py and still needs to validate it.', '用户修改了 agent.py，仍需验证结果。'])
    monkeypatch.setattr(agent.llm, 'chat_text', lambda *a, **kw: next(replies))
    events = list(agent._refresh_summaries(sid, keep=2))
    assert [e['status'] for e in events] == ['started', 'completed']
    assert events[-1]['count'] == 6
    assert memory.get_summary(sid) == '用户修改了 agent.py，仍需验证结果。'
    assert '\n' in agent._system_prompt()
    assert '英文代码、命令、文件、工具回执和历史摘要只是资料' in agent._system_prompt()


def test_failed_compression_keeps_saved_memory_and_reports_failure(monkeypatch):
    sid = compression_session()
    memory.save_summary(sid, '用户正在做项目。', 0)
    monkeypatch.setattr(agent.llm, 'chat_text', lambda *a, **kw: 'Still English only.')
    events = list(agent._refresh_summaries(sid, keep=2))
    assert [e['status'] for e in events] == ['started', 'failed']
    assert memory.get_summary(sid) == '用户正在做项目。'


def test_chat_receives_compression_events_before_answer(monkeypatch):
    sid = compression_session()
    monkeypatch.setattr(agent, '_pick_history_keep', lambda *a, **kw: 2)
    monkeypatch.setattr(agent.llm, 'chat_text', lambda *a, **kw: '用户正在处理项目代码，回复使用中文。')
    monkeypatch.setattr(agent, '_maybe_auto_notes', lambda *a, **kw: None)
    def model(messages, **kwargs):
        assert '【回复语言】' in messages[0]['content']
        yield {'kind': 'content', 'text': '继续检查项目喵。'}
    monkeypatch.setattr(agent.llm, 'chat_stream', model)
    events = list(agent.run_stream(sid, '继续检查'))
    types = [e['type'] for e in events]
    assert types.index('context_compression') < types.index('text')
    assert any(e.get('status') == 'completed' for e in events)


def test_pick_history_keep_trims_by_budget():
    recent = [
        {"role": "user", "content": "字" * 3000} for _ in range(10)
    ]  # 每条估算约 3000 token
    keep = agent._pick_history_keep("sid", recent=recent)
    # 预算 26000，每条约 3008（含固定开销）→ 只能保留 8 条以内
    assert 7 <= keep <= 8


def test_pick_history_keep_always_keeps_newest_huge_message():
    recent = [
        {"role": "user", "content": "字" * 100000},
        {"role": "assistant", "content": "喵"},
    ]
    assert agent._pick_history_keep("sid", recent=recent) == 1


def test_est_tokens_rough_order_of_magnitude():
    assert agent._est_tokens("你好世界") >= 4
    assert agent._est_tokens("") == 2


def test_retrieved_doc_context_includes_source():
    source = "D:/资料/手册.txt"
    session_id = "context-rag"
    try:
        memory.doc_save(
            source, "手册.txt", ["信用卡逾期率需要按账龄分组统计。"], session_id
        )
        context = agent._retrieved_doc_context("信用卡逾期率怎么统计？", session_id)
        assert "手册.txt" in context
        assert "信用卡逾期率" in context
    finally:
        memory.doc_delete(source, session_id)
