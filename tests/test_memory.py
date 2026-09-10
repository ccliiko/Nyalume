"""会话/便签/摘要/每日 nyalume：SQLite 行为。"""

import os

from nyalume.core import memory


def setup_function():
    memory.init_db()


def test_session_history_roundtrip():
    sid = memory.create_session()
    memory.save_message(sid, "user", "你好")
    memory.save_message(sid, "assistant", "喵～")
    rows = memory.session_messages(sid)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "喵～"


def test_message_attachments_roundtrip():
    sid = memory.create_session()
    memory.save_message(
        sid,
        "assistant",
        "看图喵",
        attachments=["D:/a.png", "downloads/screenshots/x.png"],
    )
    rows = memory.session_messages(sid)
    assert rows[-1]["attachments"] == ["D:/a.png", "downloads/screenshots/x.png"]
    assert rows[-1]["content"] == "看图喵"


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


def test_daily_nyalume_is_unique_per_day(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "agent.db"))
    memory.init_db()
    first, inserted = memory.save_daily_nyalume("2026-09-08", 88, "reliable", "顺利喵")
    repeated, inserted_again = memory.save_daily_nyalume("2026-09-08", 12, "dreamy", "重复")
    assert inserted is True and inserted_again is False
    assert repeated == first
    updated = memory.update_daily_nyalume("2026-09-08", liked=True, collected=True, viewed=True)
    assert updated["liked"] == updated["collected"] == updated["viewed"] == 1
    assert memory.list_daily_nyalume()[0]["day"] == "2026-09-08"


def test_daily_affection_is_session_scoped_and_clamped():
    first = memory.create_session()
    second = memory.create_session()
    assert memory.get_daily_affection(first) == 50
    assert memory.set_daily_affection(first, 999) == 200
    assert memory.get_daily_affection(second) == 50
    assert memory.set_daily_affection(first, -999) == -100


def test_doc_save_list_delete():
    """文档库按来源整份替换/删除；delete 返回是否真的删掉。"""
    memory.doc_save(r"D:\x\报告.md", "报告.md", ["第一段", "第二段"])
    rows = memory.doc_list()
    assert len(rows) == 1 and rows[0]["chunks"] == 2
    assert memory.doc_delete(r"D:\x\报告.md") is True
    assert memory.doc_delete(r"D:\x\报告.md") is False
    assert memory.doc_list() == []


def test_session_title_pin_project(tmp_path):
    """会话可重命名/置顶/挂项目；删项目后会话退回“最近”。"""
    project = memory.add_project(str(tmp_path / "项目A"))
    assert project["name"] == "项目A"
    sid = memory.create_session(project=project["id"])
    assert memory.session_project(sid) == project["id"]

    memory.update_session(sid, title="改名后的会话", pinned=1)
    row = next(r for r in memory.list_sessions() if r["id"] == sid)
    assert row["title"] == "改名后的会话"
    assert row["pinned"] == 1
    assert row["project"] == project["id"]
    proj = next(p for p in memory.list_projects() if p["id"] == project["id"])
    assert proj["session_count"] == 1

    assert memory.update_project(project["id"], pinned=1, name="项目A★")
    memory.delete_project(project["id"])
    assert memory.session_project(sid) == ""
    assert memory.list_projects() == []


def test_project_multi_folder_scope(tmp_path):
    """项目可并入多个授权目录；主目录不能单独移出。"""
    main = tmp_path / "主目录"
    extra = tmp_path / "附加目录"
    main.mkdir()
    extra.mkdir()
    project = memory.add_project(str(main))
    assert memory.add_project_folder(project["id"], str(extra)) is True
    rows = memory.list_projects()
    assert rows[0]["folders"] == [str(main), str(extra)]

    assert memory.remove_project_folder(project["id"], str(extra)) is True
    try:
        memory.remove_project_folder(project["id"], str(main))
        raise AssertionError("主目录应禁止单独移出")
    except ValueError:
        pass
    memory.delete_project(project["id"])


def test_undo_ops_roundtrip():
    """撤销记录：写入/列出/标记已撤销。"""
    memory.add_undo("undo-test-1", "sess-x", "write", r"D:\a\b.txt", "旧内容")
    rows = memory.list_undo("sess-x")
    assert rows and rows[0]["kind"] == "write" and rows[0]["prev"] == "旧内容"
    memory.mark_undo_applied("undo-test-1")
    assert memory.list_undo("sess-x") == []


def test_search_memory_grep_like_no_index():
    """grep 召回只查当前对话里的消息、便签和摘要。"""
    sid = memory.create_session()
    other = memory.create_session()
    memory.save_message(sid, "user", "我喜欢在早上喝咖啡")
    memory.save_message(sid, "assistant", "好的喵，早上喝咖啡")
    memory.note_save("用户偏好在早上喝咖啡", "偏好", sid)
    memory.note_save("另一个对话喜欢喝茶", "偏好", other)
    out = memory.search_memory("咖啡", session_id=sid)
    assert "早上喝咖啡" in out and "偏好" in out
    assert memory.search_memory("喝茶", session_id=sid).startswith("没有找到")
    assert memory.search_memory("不存在的词xyz", session_id=sid).startswith("没有找到")


def test_notes_and_docs_are_isolated_by_session():
    first = memory.create_session()
    second = memory.create_session()
    source = r"D:\x\独立资料.md"
    memory.note_save("只属于第一段对话", session_id=first)
    memory.doc_save(source, "独立资料.md", ["第一段对话专属关键词"], first)

    assert memory.get_recent_notes(8, first)
    assert memory.get_recent_notes(8, second) == []
    assert memory.retrieve_docs("专属关键词", session_id=first)
    assert memory.retrieve_docs("专属关键词", session_id=second) == []

    memory.delete_session(first)
    assert memory.get_recent_notes(8, first) == []
    assert memory.doc_list(first) == []


def test_memory_snapshots_are_named_deduplicated_and_restore_safely(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setattr(memory, "BACKUP_DIR", str(tmp_path / "backups"))
    memory.init_db()
    sid = memory.create_session()
    memory.update_session(sid, title="test")
    memory.save_message(sid, "user", "帮我准备 Agent 岗位面试，需要重点复习工具调用")
    memory.save_message(sid, "assistant", "可以。")

    first = memory.backup_db()
    assert memory.backup_db() == first
    info = memory.list_backups()[0]
    assert info["title"].startswith("帮我准备 Agent 岗位面试") and info["title"].endswith("…")
    assert info["sessions"] == 1 and info["messages"] == 2

    memory.save_message(sid, "user", "这是备份后的新消息")
    newer = memory.backup_db()
    assert newer != first
    memory.restore_backup(os.path.basename(first))
    assert all(row["content"] != "这是备份后的新消息" for row in memory.session_messages(sid))
    assert any(row["name"].startswith("before_restore_") for row in memory.list_backups())
