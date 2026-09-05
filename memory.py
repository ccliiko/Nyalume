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


# ---------- 便签工具用的两个函数 ----------

# ---------- 便签工具用的两个函数 ----------

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
