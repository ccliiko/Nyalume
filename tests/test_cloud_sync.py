import time

import pytest

from nyalume.core import cloud_sync, memory


class FakeCloud:
    def __init__(self):
        self.records = {}
        self.revision = 0
        self.push_calls = 0

    def __call__(self, method, path, payload=None, token=""):
        assert token == "test-token"
        if method == "POST" and path == "/api/v1/sync/push":
            self.push_calls += 1
            for item in payload["records"]:
                self.revision += 1
                self.records[(item["record_type"], item["record_id"])] = {
                    "record_type": item["record_type"],
                    "record_id": item["record_id"],
                    "payload": item["payload"],
                    "deleted": item["deleted"],
                    "revision": self.revision,
                }
            return {"applied": []}
        if method == "GET" and path.startswith("/api/v1/sync/pull"):
            since = int(path.split("since=", 1)[1].split("&", 1)[0])
            rows = sorted(
                (row for row in self.records.values() if row["revision"] > since),
                key=lambda row: row["revision"],
            )
            return {
                "records": rows[:500],
                "next_cursor": rows[-1]["revision"] if rows else since,
                "has_more": len(rows) > 500,
            }
        raise AssertionError((method, path))


def test_incremental_sync_and_private_local_data(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "sync.db"))
    memory.init_db()
    sid = memory.create_session("sync-chat")
    memory.save_message(sid, "user", "hello", ["D:/private/photo.png"])
    memory.save_summary(sid, "summary", 1)
    memory.note_save("remember this", "study", sid)
    memory.save_daily_nyalume("2099-01-02", 95, "spark", "good luck")
    with memory._conn() as conn:
        conn.execute(
            "INSERT INTO documents (session_id, source, title, chunk_idx, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "D:/private/notes.md", "notes", 0, "private rag", time.time()),
        )

    cloud = FakeCloud()
    first = cloud_sync.sync_once("user-a", "test-token", cloud)
    kinds = {kind for kind, _ in cloud.records}
    assert first["pushed"] == 5
    assert kinds == {"session", "message", "summary", "note", "daily_nyalume"}
    message = next(row for (kind, _), row in cloud.records.items() if kind == "message")
    assert "attachments" not in message["payload"]
    assert "private" not in str(cloud.records)

    second = cloud_sync.sync_once("user-a", "test-token", cloud)
    assert second["pushed"] == 0
    assert second["pulled"] == 0

    memory.delete_session(sid)
    deleted = cloud_sync.sync_once("user-a", "test-token", cloud)
    assert deleted["pushed"] == 4
    assert all(
        row["deleted"]
        for (kind, _), row in cloud.records.items()
        if kind in {"session", "message", "summary", "note"}
    )

    with pytest.raises(ValueError, match="已绑定另一个"):
        cloud_sync.sync_once("user-b", "test-token", cloud)
