"""Web Agent 运行状态与会话恢复。"""

import asyncio
import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from nyalume.frontends.web import server


def test_activity_counter_tracks_overlapping_runs():
    server._active_runs = 0
    server._pet_tap_seq = 0
    server._pet_meow_seq = 0
    assert server.activity_state() == {
        "working": False, "pet_tap_seq": 0, "pet_meow_seq": 0,
    }
    server._change_activity(1)
    server._change_activity(1)
    assert server.activity_state()["working"] is True
    server._change_activity(-1)
    assert server.activity_state()["working"] is True
    server._change_activity(-1)
    assert server.activity_state()["working"] is False


def test_pet_tap_activity_tracks_taps_and_meows_separately():
    server._pet_tap_seq = 0
    server._pet_meow_seq = 0
    assert server.pet_tap({"meowed": False}) == {"ok": True, "seq": 1}
    assert server.pet_tap({"meowed": True}) == {"ok": True, "seq": 2}
    state = server.activity_state()
    assert state["pet_tap_seq"] == 2
    assert state["pet_meow_seq"] == 1


def test_history_messages_recover_matching_trace(monkeypatch):
    rows = [
        {"id": 1, "role": "user", "content": "开始", "ts": 100.0},
        {"id": 2, "role": "assistant", "content": "完成", "ts": 103.0},
    ]
    runs = [{
        "run_id": "run-1", "started_at": 100.2, "duration_ms": 2000,
        "status": "completed", "spans": [{"kind": "tool", "name": "file_write"}],
    }]
    monkeypatch.setattr(server.memory, "session_messages", lambda _session_id: rows)
    monkeypatch.setattr(server.tracing, "list_runs", lambda _session_id, limit=100: runs)
    restored = server.get_session_messages("session-1")
    assert restored[1]["trace"]["run_id"] == "run-1"


def test_dropped_folder_becomes_project(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "workspace_root", lambda: str(tmp_path))
    project = server.create_drop_project(server.DropProjectIn(name='课程:设计'))
    try:
        assert project["name"] == "课程_设计"
        assert Path(project["path"]).parent == tmp_path / "projects"
        assert Path(project["path"]).is_dir()
    finally:
        server.memory.delete_project(project["id"])


def test_local_path_open_respects_permission_scope(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inside = workspace / "report.pdf"
    inside.write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("ok", encoding="utf-8")
    opened = []
    monkeypatch.setattr(server, "session_work_root", lambda _session_id="": str(workspace))
    monkeypatch.setattr(server.os, "startfile", lambda path: opened.append(path))
    monkeypatch.setattr(server, "permission_mode", lambda: "workspace")

    assert server.open_local_path(server.OpenPathIn(path=str(inside)))["ok"] is True
    with pytest.raises(HTTPException) as error:
        server.open_local_path(server.OpenPathIn(path=str(outside)))
    assert error.value.status_code == 403

    monkeypatch.setattr(server, "permission_mode", lambda: "full")
    assert server.open_local_path(server.OpenPathIn(path=str(outside)))["ok"] is True
    assert opened == [str(inside), str(outside)]


def test_uploaded_document_is_saved_and_indexed_in_its_conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    session_id = server.memory.create_session()
    server.memory.save_message(session_id, "user", "整理课程资料")
    upload = UploadFile(file=io.BytesIO("期末复习重点".encode()), filename="重点.txt")
    try:
        result = asyncio.run(server.upload_docs([upload], session_id, "", False))
        root = Path(server.session_work_root(session_id))
        assert result["ok"] is True
        assert (root / "uploads" / "重点.txt").is_file()
        assert any(row["title"] == "重点.txt" for row in server.memory.doc_list(session_id))
    finally:
        server.memory.delete_session(session_id)


def test_session_workspace_clean_and_archive_preserve_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    opened = []
    monkeypatch.setattr(server.os, "startfile", lambda path: opened.append(path))
    session_id = server.memory.create_session()
    server.memory.save_message(session_id, "user", "课程报告归档")
    root = Path(server.session_work_root(session_id))
    report = root / "报告.md"
    helper = root / ".nyalume" / "tmp" / "helper.py"
    cache = root / "src" / "__pycache__" / "module.pyc"
    report.write_text("正式内容", encoding="utf-8")
    helper.parent.mkdir(parents=True)
    helper.write_text("print(1)", encoding="utf-8")
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache")
    try:
        cleaned = server.clean_session_workspace_cache(session_id)
        assert cleaned["files"] == 2
        assert report.read_text(encoding="utf-8") == "正式内容"
        assert not helper.exists()
        assert not cache.exists()

        archived = server.archive_session_workspace(session_id)
        assert Path(archived["path"]).is_file()
        assert Path(archived["folder"]) == tmp_path / "archives"
        assert report.is_file()
        assert opened == [str(tmp_path / "archives")]
    finally:
        server.memory.delete_session(session_id)
