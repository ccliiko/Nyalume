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
                created_at REAL
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
                ts REAL
            );
            """
        )


def ensure_session(session_id: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", (session_id, time.time()))


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

def note_save(content: str) -> str:
    with _conn() as conn:
        conn.execute("INSERT INTO notes (content, ts) VALUES (?, ?)", (content, time.time()))
    return "已保存便签"


def note_list() -> str:
    with _conn() as conn:
        rows = conn.execute("SELECT content FROM notes ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        return "还没有便签"
    return "\n".join(f"- {r['content']}" for r in rows)


init_db()
