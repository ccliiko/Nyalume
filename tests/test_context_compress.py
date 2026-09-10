"""自动上下文压缩：token 预算内保留新消息，超预算丢最旧。"""

from nyalume.core import agent, memory


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
