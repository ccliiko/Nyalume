"""Undo uses explicit snapshot states; unavailable/legacy snapshots never delete files."""
import sqlite3
from pathlib import Path

import pytest

from nyalume.core import memory, tools


@pytest.fixture
def undo_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "undo.db"))
    monkeypatch.setattr(memory, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path / "workspace"))
    memory.init_db()
    memory.set_setting("workspace_root", str(tmp_path))
    tools.set_session_context("undo-recovery")
    yield Path(tools.session_work_root("undo-recovery"))
    tools.clear_session_context()


@pytest.mark.parametrize("original,state", [
    (None, "missing"),
    (b"", "text"),
    ("旧内容\r\n第二行\n".encode(), "text"),
    (b"a" * tools._UNDO_TEXT_LIMIT, "text"),
    (b"a" * (tools._UNDO_TEXT_LIMIT + 1), "unavailable"),
    (b"\x00binary", "unavailable"),
    (b"\xff", "unavailable"),
], ids=["new", "empty", "text", "limit", "oversize", "binary", "undecodable"])
def test_write_undo_preserves_existing_files_and_refuses_missing_snapshots(undo_workspace, original, state):
    target = undo_workspace / "file.txt"
    if original is not None:
        target.write_bytes(original)
    assert tools._file_write("file.txt", "replacement").startswith("已写入")
    op = memory.list_undo("undo-recovery")[0]
    assert op["snapshot_state"] == state
    result = tools.apply_undo(op["id"])
    if state == "unavailable":
        assert result.startswith("无法自动撤销")
        assert target.read_bytes() == b"replacement"
        assert memory.get_undo(op["id"])["applied"] == 0
        assert "无法自动撤销" in tools.preview_undo(op["id"])
        assert tools.diff_undo(op["id"]) is None
    else:
        assert result.startswith("已撤销")
        assert memory.get_undo(op["id"])["applied"] == 1
        if original is None:
            assert not target.exists()
            assert "新建文件" in tools.preview_undo(op["id"])
        else:
            assert target.read_bytes() == original
            if not original:
                assert "空文本文件" in tools.preview_undo(op["id"])
        assert tools.diff_undo(op["id"]) is not None


@pytest.mark.parametrize("original", [b"", b"old\r\n", b"a" * tools._UNDO_TEXT_LIMIT], ids=["empty", "text", "limit"])
def test_delete_undo_restores_empty_and_complete_text(undo_workspace, original):
    target = undo_workspace / "deleted.txt"
    target.write_bytes(original)
    assert tools._file_delete("deleted.txt").startswith("已删除文件")
    op = memory.list_undo("undo-recovery")[0]
    assert not target.exists()
    assert op["snapshot_state"] == "text"
    assert tools.apply_undo(op["id"]).startswith("已撤销")
    assert target.read_bytes() == original
    assert memory.get_undo(op["id"])["applied"] == 1


@pytest.mark.parametrize("original", [b"a" * (tools._UNDO_TEXT_LIMIT + 1), b"\x00binary"], ids=["oversize", "binary"])
def test_unrecoverable_delete_never_claims_success_or_overwrites_current_file(undo_workspace, original):
    target = undo_workspace / "deleted.bin"
    target.write_bytes(original)
    assert tools._file_delete("deleted.bin").startswith("已删除文件")
    op = memory.list_undo("undo-recovery")[0]
    assert tools.apply_undo(op["id"]).startswith("无法自动撤销")
    assert not target.exists()
    target.write_bytes(b"external replacement")
    assert tools.apply_undo(op["id"]).startswith("无法自动撤销")
    assert target.read_bytes() == b"external replacement"
    assert memory.get_undo(op["id"])["applied"] == 0


def _legacy_undo_table(conn, path):
    conn.execute("""CREATE TABLE undo_ops (
        id TEXT PRIMARY KEY, session_id TEXT, ts REAL, kind TEXT, path TEXT,
        prev TEXT DEFAULT '', applied INTEGER DEFAULT 0)""")
    conn.executemany("INSERT INTO undo_ops VALUES (?, 'legacy', 1, ?, ?, ?, 0)", [
        ("write-empty", "write", str(path), ""),
        ("delete-empty", "delete", str(path), ""),
        ("write-text", "write", str(path), "old text"),
        ("delete-text", "delete", str(path), "old text"),
    ])


@pytest.mark.parametrize("via_restore", [False, True])
def test_legacy_database_migration_is_safe_idempotent_and_restored(via_restore, undo_workspace):
    target = undo_workspace / "legacy.txt"
    if via_restore:
        backup_dir = Path(memory.BACKUP_DIR)
        backup_dir.mkdir()
        with sqlite3.connect(backup_dir / "legacy.db") as conn:
            _legacy_undo_table(conn, target)
        memory.restore_backup("legacy.db")
    else:
        with memory._conn() as conn:
            conn.execute("DROP TABLE undo_ops")
            _legacy_undo_table(conn, target)
        memory.init_db()
    memory.init_db()
    assert all(op["snapshot_state"] == "legacy" for op in memory.list_undo("legacy"))
    for op_id in ("write-empty", "delete-empty"):
        target.write_bytes(b"keep current")
        assert tools.apply_undo(op_id).startswith("无法自动撤销")
        assert target.read_bytes() == b"keep current"
        assert memory.get_undo(op_id)["applied"] == 0
        assert "无法自动撤销" in tools.preview_undo(op_id)
        assert tools.diff_undo(op_id) is None
    for op_id in ("write-text", "delete-text"):
        target.write_bytes(b"current")
        assert tools.apply_undo(op_id).startswith("已撤销")
        assert target.read_bytes() == b"old text"


def test_old_add_undo_callers_remain_safe_without_new_keyword(undo_workspace):
    target = undo_workspace / "legacy-api.txt"
    target.write_bytes(b"keep")
    memory.add_undo("old-empty-call", "undo-recovery", "write", str(target))
    assert tools.apply_undo("old-empty-call").startswith("无法自动撤销")
    assert target.read_bytes() == b"keep"
    memory.add_undo("old-text-call", "undo-recovery", "write", str(target), "before")
    assert tools.apply_undo("old-text-call").startswith("已撤销")
    assert target.read_bytes() == b"before"


def test_pdf_ocr_records_explicit_new_file_state_without_model(undo_workspace, monkeypatch):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(undo_workspace / "scan.pdf", "wb") as fh:
        writer.write(fh)
    monkeypatch.setattr(tools, "vision_configured", lambda: True)
    monkeypatch.setattr(tools, "_vision_describe", lambda *args: "synthetic OCR")
    result = tools._pdf_ocr("scan.pdf", "scan.md")
    assert result.startswith("PDF 文字已保存")
    op = memory.list_undo("undo-recovery")[0]
    assert op["snapshot_state"] == "missing"
    assert tools.apply_undo(op["id"]).startswith("已撤销")
    assert not (undo_workspace / "scan.md").exists()
    assert (undo_workspace / "scan.pdf").is_file()


def test_empty_directory_undo_keeps_its_existing_semantics(undo_workspace):
    target = undo_workspace / "empty-dir"
    target.mkdir()
    assert tools._file_delete("empty-dir").startswith("已删除空目录")
    op = memory.list_undo("undo-recovery")[0]
    assert "删除的空目录" in tools.preview_undo(op["id"])
    assert tools.apply_undo(op["id"]).startswith("已撤销")
    assert target.is_dir()
