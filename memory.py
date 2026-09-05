"""会话记忆：用 SQLite 保存多轮历史。"""

import os
import sqlite3
import time

DB_PATH = os.getenv("MEMORY_DB", os.path.join(os.path.dirname(__file__), "agent.db"))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at REAL,
                affection INTEGER DEFAULT 50
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                ts REAL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT,
                tag TEXT DEFAULT '',
                ts REAL
            );
            CREATE TABLE IF NOT EXISTS summaries (
                session_id TEXT PRIMARY KEY,
                content TEXT,
                up_to_message_id INTEGER DEFAULT 0,
                updated_at REAL
            );
            """
        )
        # 旧库迁移：notes 表没有 tag 列时补上
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
        if "tag" not in cols:
            conn.execute("ALTER TABLE notes ADD COLUMN tag TEXT DEFAULT ''")
        # 旧库迁移：sessions 表没有 affection 列时补上（人设好感度状态）
        sess_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "affection" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN affection INTEGER DEFAULT 50")
        # 旧库迁移：会话记忆归档进度（自动便签抽到第几条消息）
        if "memory_upto" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN memory_upto INTEGER DEFAULT 0")


def ensure_session(session_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, affection) VALUES (?, ?, 50)",
            (session_id, time.time()),
        )


def save_message(session_id: str, role: str, content: str) -> None:
    ensure_session(session_id)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (session_id, role, content, time.time()),
        )


def load_history(session_id: str, limit: int = 20) -> list[dict]:
    """读取最近 N 条消息（含 user/assistant），用于拼 prompt。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? "
            "AND role IN ('user','assistant') ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------- 便签工具（L3 长期记忆） ----------

def note_save(content: str, tag: str = "") -> str:
    tag = (tag or "").strip()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO notes (content, tag, ts) VALUES (?, ?, ?)",
            (content, tag, time.time()),
        )
    reply = f"已保存便签：{content}"
    if tag:
        reply += f"（标签：{tag}）"
    return reply


def note_list(tag: str = "") -> str:
    tag = (tag or "").strip()
    with _conn() as conn:
        if tag:
            rows = conn.execute(
                "SELECT content, tag FROM notes WHERE tag = ? ORDER BY id DESC LIMIT 10",
                (tag,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT content, tag FROM notes ORDER BY id DESC LIMIT 10"
            ).fetchall()
    if not rows:
        return "还没有便签" if not tag else f"没有找到标签为「{tag}」的便签"
    return "\n".join(
        f"- [{r['tag']}] {r['content']}" if r["tag"] else f"- {r['content']}"
        for r in rows
    )


def get_recent_notes(limit: int = 3) -> list[dict]:
    """取最近 N 条便签，用于每轮注入上下文（L3 自动召回）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT content, tag FROM notes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{"content": r["content"], "tag": r["tag"]} for r in rows]


# ---------- L2 滚动摘要：把超出窗口的旧消息合并成摘要 ----------

def get_summary(session_id: str) -> str:
    ensure_session(session_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT content FROM summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
    return row["content"] if row else ""


def save_summary(session_id: str, content: str, up_to_message_id: int) -> None:
    ensure_session(session_id)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO summaries (session_id, content, up_to_message_id, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 content = excluded.content,
                 up_to_message_id = excluded.up_to_message_id,
                 updated_at = excluded.updated_at""",
            (session_id, content, up_to_message_id, time.time()),
        )


def pending_messages(session_id: str, keep: int = 20, chunk: int = 60) -> list[dict]:
    """窗口之外、还没进摘要的旧消息（按 id 升序，最多 chunk 条）。"""
    ensure_session(session_id)
    with _conn() as conn:
        newest = conn.execute(
            "SELECT id FROM messages WHERE session_id = ? "
            "AND role IN ('user','assistant') ORDER BY id DESC LIMIT ?",
            (session_id, keep),
        ).fetchall()
    if len(newest) < keep:
        return []  # 消息还没填满一个窗口，没有“被挤出”的内容
    oldest_kept = min(row["id"] for row in newest)
    with _conn() as conn:
        rows = conn.execute(
            """SELECT id, role, content FROM messages
               WHERE session_id = ? AND role IN ('user','assistant')
                 AND id < ? AND id > COALESCE(
                     (SELECT up_to_message_id FROM summaries WHERE session_id = ?), 0)
               ORDER BY id ASC LIMIT ?""",
            (session_id, oldest_kept, session_id, chunk),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------- 记忆归档进度（自动便签抽到第几条消息） ----------

def get_memory_upto(session_id: str) -> int:
    ensure_session(session_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT memory_upto FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return int(row["memory_upto"] or 0)


def set_memory_upto(session_id: str, message_id: int) -> None:
    ensure_session(session_id)
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET memory_upto = ? WHERE id = ?",
            (int(message_id), session_id),
        )


def messages_since(session_id: str, since_id: int, limit: int = 30) -> list[dict]:
    """取某条消息之后的对话（按 id 升序），供自动归档扫描。"""
    ensure_session(session_id)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content FROM messages WHERE session_id = ? "
            "AND role IN ('user','assistant') AND id > ? ORDER BY id ASC LIMIT ?",
            (session_id, int(since_id), limit),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------- 会话状态：好感度（人设状态机用） ----------

def get_affection(session_id: str) -> int:
    ensure_session(session_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT affection FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return int(row["affection"] or 50)


def set_affection(session_id: str, value: int) -> int:
    ensure_session(session_id)
    value = max(-100, min(200, int(value)))
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET affection = ? WHERE id = ?", (value, session_id)
        )
    return value


init_db()
