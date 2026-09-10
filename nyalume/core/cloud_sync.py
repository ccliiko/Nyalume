"""Nyalume 账号的轻量增量同步；SQLite 仍是本机唯一运行时数据库。"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable

from . import memory

RequestFn = Callable[[str, str, dict | None, str], dict]
_sync_lock = threading.Lock()


def _json_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _init(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cloud_sync_state (
            user_id TEXT NOT NULL,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT '',
            deleted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, record_type, record_id)
        );
        CREATE TABLE IF NOT EXISTS cloud_sync_meta (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        );
        """
    )
    # 本地同步游标沿用旧版本时，直接换成新记录类型，避免重复上传。
    conn.execute(
        "UPDATE OR IGNORE cloud_sync_state SET record_type = 'daily_nyalume' "
        "WHERE record_type = 'daily_cliko'"
    )
    conn.execute(
        "DELETE FROM cloud_sync_state WHERE record_type = 'daily_cliko'"
    )
    for table in ("messages", "notes"):
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "sync_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN sync_id TEXT")
        missing = conn.execute(
            f"SELECT id FROM {table} WHERE sync_id IS NULL OR sync_id = ''"
        ).fetchall()
        conn.executemany(
            f"UPDATE {table} SET sync_id = ? WHERE id = ?",
            [(str(uuid.uuid4()), row["id"]) for row in missing],
        )
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_sync_id ON {table}(sync_id)"
        )


def _snapshot(conn) -> dict[tuple[str, str], dict]:
    records: dict[tuple[str, str], dict] = {}
    for row in conn.execute(
        "SELECT id, created_at, title, pinned, daily_affection FROM sessions"
    ):
        payload = {
            "id": row["id"],
            "created_at": row["created_at"],
            "title": row["title"] or "",
            "pinned": int(row["pinned"] or 0),
            "daily_affection": int(row["daily_affection"] or 50),
        }
        records[("session", row["id"])] = payload
    for row in conn.execute(
        "SELECT sync_id, session_id, role, content, ts FROM messages"
    ):
        payload = dict(row)
        records[("message", row["sync_id"])] = payload
    for row in conn.execute(
        "SELECT session_id, content, updated_at FROM summaries"
    ):
        payload = dict(row)
        records[("summary", row["session_id"])] = payload
    for row in conn.execute(
        "SELECT sync_id, session_id, content, tag, ts FROM notes"
    ):
        payload = dict(row)
        records[("note", row["sync_id"])] = payload
    for row in conn.execute(
        "SELECT day, score, profile, blessing, created_at, keyword, source, "
        "liked, collected, viewed, card_version FROM daily_nyalume"
    ):
        payload = dict(row)
        for flag in ("liked", "collected", "viewed"):
            payload[flag] = int(payload[flag] or 0)
        records[("daily_nyalume", row["day"])] = payload
    return records


def _ensure_session(conn, session_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, created_at) VALUES (?, strftime('%s','now'))",
        (session_id,),
    )


def _apply(conn, record: dict) -> None:
    kind = record["record_type"]
    if kind == "daily_cliko":  # 接收改名前尚未迁移的云端记录。
        kind = "daily_nyalume"
    record_id = record["record_id"]
    payload = record.get("payload") or {}
    if record.get("deleted"):
        if kind == "session":
            for table in ("messages", "summaries", "notes", "documents"):
                conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (record_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (record_id,))
        elif kind == "message":
            conn.execute("DELETE FROM messages WHERE sync_id = ?", (record_id,))
        elif kind == "summary":
            conn.execute("DELETE FROM summaries WHERE session_id = ?", (record_id,))
        elif kind == "note":
            conn.execute("DELETE FROM notes WHERE sync_id = ?", (record_id,))
        elif kind == "daily_nyalume":
            conn.execute("DELETE FROM daily_nyalume WHERE day = ?", (record_id,))
        return

    if kind == "session":
        _ensure_session(conn, record_id)
        conn.execute(
            "UPDATE sessions SET created_at = ?, title = ?, pinned = ?, "
            "daily_affection = ? WHERE id = ?",
            (
                payload.get("created_at"), payload.get("title") or None,
                int(payload.get("pinned") or 0),
                int(payload.get("daily_affection", 50)), record_id,
            ),
        )
    elif kind == "message":
        session_id = str(payload.get("session_id") or "")
        _ensure_session(conn, session_id)
        conn.execute(
            """INSERT INTO messages
                   (sync_id, session_id, role, content, attachments, ts)
               VALUES (?, ?, ?, ?, '[]', ?)
               ON CONFLICT(sync_id) DO UPDATE SET
                 session_id = excluded.session_id, role = excluded.role,
                 content = excluded.content, ts = excluded.ts""",
            (
                record_id, session_id, payload.get("role") or "assistant",
                payload.get("content") or "", payload.get("ts"),
            ),
        )
    elif kind == "summary":
        session_id = str(payload.get("session_id") or record_id)
        _ensure_session(conn, session_id)
        max_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS value FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()["value"]
        conn.execute(
            """INSERT INTO summaries (session_id, content, up_to_message_id, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET content = excluded.content,
                 up_to_message_id = excluded.up_to_message_id,
                 updated_at = excluded.updated_at""",
            (session_id, payload.get("content") or "", max_id, payload.get("updated_at")),
        )
    elif kind == "note":
        session_id = str(payload.get("session_id") or "")
        if session_id:
            _ensure_session(conn, session_id)
        conn.execute(
            """INSERT INTO notes (sync_id, session_id, content, tag, ts)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(sync_id) DO UPDATE SET session_id = excluded.session_id,
                 content = excluded.content, tag = excluded.tag, ts = excluded.ts""",
            (
                record_id, session_id, payload.get("content") or "",
                payload.get("tag") or "", payload.get("ts"),
            ),
        )
    elif kind == "daily_nyalume":
        conn.execute(
            """INSERT INTO daily_nyalume
                   (day, score, profile, blessing, created_at, keyword, source,
                    liked, collected, viewed, card_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET score = excluded.score,
                 profile = excluded.profile, blessing = excluded.blessing,
                 created_at = excluded.created_at, keyword = excluded.keyword,
                 source = excluded.source, liked = excluded.liked,
                 collected = excluded.collected, viewed = excluded.viewed,
                 card_version = excluded.card_version""",
            (
                record_id, int(payload.get("score") or 0), payload.get("profile") or "",
                payload.get("blessing") or "", payload.get("created_at"),
                payload.get("keyword") or "", payload.get("source") or "Nyalume",
                int(payload.get("liked") or 0), int(payload.get("collected") or 0),
                int(payload.get("viewed") or 0), payload.get("card_version") or "v1",
            ),
        )


def sync_once(user_id: str, token: str, request: RequestFn) -> dict:
    """推送本地变化后拉取云端变化；同一进程只运行一个同步任务。"""
    if not _sync_lock.acquire(blocking=False):
        return {"busy": True, "pushed": 0, "pulled": 0}
    try:
        with memory._conn() as conn:
            _init(conn)
            owner = conn.execute(
                "SELECT value FROM settings WHERE key = 'cloud_sync_owner'"
            ).fetchone()
            if owner and owner["value"] != user_id:
                raise ValueError("这份本地数据已绑定另一个 Nyalume 账号")
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('cloud_sync_owner', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (user_id,),
            )
            current = _snapshot(conn)
            state = {
                (row["record_type"], row["record_id"]): dict(row)
                for row in conn.execute(
                    "SELECT record_type, record_id, content_hash, deleted "
                    "FROM cloud_sync_state WHERE user_id = ?", (user_id,)
                )
            }

        changes = []
        for key, payload in current.items():
            digest = _json_hash(payload)
            previous = state.get(key)
            if not previous or previous["deleted"] or previous["content_hash"] != digest:
                changes.append((key, payload, digest, False))
        for key, previous in state.items():
            if key not in current and not previous["deleted"]:
                changes.append((key, {}, "", True))

        for start in range(0, len(changes), 200):
            batch = changes[start:start + 200]
            request(
                "POST", "/api/v1/sync/push",
                {"records": [
                    {
                        "mutation_id": str(uuid.uuid4()), "record_type": key[0],
                        "record_id": key[1], "payload": payload, "deleted": deleted,
                    }
                    for key, payload, _, deleted in batch
                ]}, token,
            )
            with memory._conn() as conn:
                _init(conn)
                conn.executemany(
                    """INSERT INTO cloud_sync_state
                           (user_id, record_type, record_id, content_hash, deleted)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, record_type, record_id) DO UPDATE SET
                         content_hash = excluded.content_hash, deleted = excluded.deleted""",
                    [
                        (user_id, key[0], key[1], digest, int(deleted))
                        for key, _, digest, deleted in batch
                    ],
                )

        with memory._conn() as conn:
            row = conn.execute(
                "SELECT value FROM cloud_sync_meta WHERE user_id = ? AND key = 'cursor'",
                (user_id,),
            ).fetchone()
            cursor = int(row["value"]) if row else 0
        pulled = 0
        while True:
            page = request(
                "GET", f"/api/v1/sync/pull?since={cursor}&limit=500", None, token
            )
            records = page.get("records") or []
            with memory._conn() as conn:
                _init(conn)
                for record in records:
                    _apply(conn, record)
                    payload = record.get("payload") or {}
                    conn.execute(
                        """INSERT INTO cloud_sync_state
                               (user_id, record_type, record_id, content_hash, deleted)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(user_id, record_type, record_id) DO UPDATE SET
                             content_hash = excluded.content_hash,
                             deleted = excluded.deleted""",
                        (
                            user_id, record["record_type"], record["record_id"],
                            "" if record.get("deleted") else _json_hash(payload),
                            int(bool(record.get("deleted"))),
                        ),
                    )
                cursor = int(page.get("next_cursor", cursor))
                conn.execute(
                    """INSERT INTO cloud_sync_meta (user_id, key, value)
                       VALUES (?, 'cursor', ?)
                       ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value""",
                    (user_id, str(cursor)),
                )
            pulled += len(records)
            if not page.get("has_more"):
                break
        return {"busy": False, "pushed": len(changes), "pulled": pulled, "cursor": cursor}
    finally:
        _sync_lock.release()
