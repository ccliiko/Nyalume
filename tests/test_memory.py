"""会话/便签/摘要/好感度：SQLite 行为。"""

from mini_agent.core import memory


def setup_function():
    memory.init_db()


def test_session_history_roundtrip():
    sid = memory.create_session()
    memory.save_message(sid, "user", "你好")
    memory.save_message(sid, "assistant", "喵～")
    rows = memory.session_messages(sid)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "喵～"


def test_load_history_limit_reversed_order():
    sid = memory.create_session()
    for i in range(5):
        memory.save_message(sid, "user", f"q{i}")
        memory.save_message(sid, "assistant", f"a{i}")
    rows = memory.load_history(sid, limit=2)
    assert rows[-1]["content"] == "a4"
    assert len(rows) == 2


def test_summary_save_get():
    sid = memory.create_session()
    memory.save_summary(sid, "滚动摘要内容", 12)
    assert memory.get_summary(sid) == "滚动摘要内容"


def test_delete_session_cascades():
    sid = memory.create_session()
    memory.save_message(sid, "user", "将被删除")
    memory.save_summary(sid, "摘要", 1)
    memory.delete_session(sid)
    assert memory.session_messages(sid) == []
    assert memory.get_summary(sid) == ""


def test_affection_clamped():
    sid = memory.create_session()
    assert memory.set_affection(sid, 9999) == 200
    assert memory.set_affection(sid, -9999) == -100
    assert memory.get_affection(sid) == -100
