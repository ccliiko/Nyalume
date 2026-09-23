"""会话记忆：用 SQLite 保存多轮历史。"""

import os
import sqlite3
import time
import uuid
import datetime
import hashlib
import json
import re

# 仓库根（nyalume/core/memory.py 的上三级），默认数据文件仍放在项目根目录
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DB_PATH = os.getenv("MEMORY_DB", os.path.join(_PROJECT_ROOT, "agent.db"))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        # 旧版品牌名只保留在这里做一次数据迁移，避免改名后丢失已抽卡片。
        legacy_daily = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'daily_cliko'"
        ).fetchone()
        current_daily = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'daily_nyalume'"
        ).fetchone()
        if legacy_daily and not current_daily:
            conn.execute("ALTER TABLE daily_cliko RENAME TO daily_nyalume")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                created_at REAL,
                daily_affection INTEGER DEFAULT 50
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_id TEXT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                attachments TEXT DEFAULT '[]',
                ts REAL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_id TEXT,
                session_id TEXT DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT DEFAULT '',
                source TEXT,
                title TEXT,
                chunk_idx INTEGER,
                content TEXT,
                ts REAL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS companion_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('promise', 'event')),
                state TEXT NOT NULL CHECK(state IN ('proposed', 'active', 'completed', 'recorded')),
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                confirmed_at REAL,
                completed_at REAL,
                event_key TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS tool_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                ts REAL,
                tool TEXT,
                args TEXT,
                ok INTEGER,
                result_head TEXT,
                ms INTEGER
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT,
                path TEXT,
                pinned INTEGER DEFAULT 0,
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS undo_ops (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                ts REAL,
                kind TEXT,
                path TEXT,
                prev TEXT DEFAULT '',
                applied INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS daily_nyalume (
                day TEXT PRIMARY KEY,
                score INTEGER NOT NULL,
                profile TEXT NOT NULL,
                blessing TEXT NOT NULL,
                created_at REAL NOT NULL,
                keyword TEXT DEFAULT '',
                source TEXT DEFAULT 'Nyalume',
                liked INTEGER DEFAULT 0,
                collected INTEGER DEFAULT 0,
                viewed INTEGER DEFAULT 0,
                card_version TEXT DEFAULT 'v1'
            );
            """
        )
        # 旧库迁移：notes 表没有 tag 列时补上
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
        if "tag" not in cols:
            conn.execute("ALTER TABLE notes ADD COLUMN tag TEXT DEFAULT ''")
        if "session_id" not in cols:
            conn.execute("ALTER TABLE notes ADD COLUMN session_id TEXT DEFAULT ''")
        doc_cols = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        if "session_id" not in doc_cols:
            conn.execute("ALTER TABLE documents ADD COLUMN session_id TEXT DEFAULT ''")
        sess_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "daily_affection" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN daily_affection INTEGER DEFAULT 50")
        # 旧库迁移：会话记忆归档进度（自动便签抽到第几条消息）
        if "memory_upto" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN memory_upto INTEGER DEFAULT 0")
        if "title" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
        if "pinned" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0")
        if "project" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN project TEXT DEFAULT ''")
        proj_cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        if "folders" not in proj_cols:
            conn.execute("ALTER TABLE projects ADD COLUMN folders TEXT DEFAULT '[]'")
        msg_cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        if "attachments" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT DEFAULT '[]'")
        if "sync_id" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN sync_id TEXT")
        if "sync_id" not in cols:
            conn.execute("ALTER TABLE notes ADD COLUMN sync_id TEXT")
        for table in ("messages", "notes"):
            rows = conn.execute(
                f"SELECT id FROM {table} WHERE sync_id IS NULL OR sync_id = ''"
            ).fetchall()
            conn.executemany(
                f"UPDATE {table} SET sync_id = ? WHERE id = ?",
                [(str(uuid.uuid4()), row["id"]) for row in rows],
            )
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_sync_id "
                f"ON {table}(sync_id)"
            )
        daily_cols = {row["name"] for row in conn.execute("PRAGMA table_info(daily_nyalume)")}
        for name, definition in (
            ("keyword", "TEXT DEFAULT ''"),
            ("source", "TEXT DEFAULT 'Nyalume'"),
            ("liked", "INTEGER DEFAULT 0"),
            ("collected", "INTEGER DEFAULT 0"),
            ("viewed", "INTEGER DEFAULT 0"),
            ("card_version", "TEXT DEFAULT 'v1'"),
        ):
            if name not in daily_cols:
                conn.execute(f"ALTER TABLE daily_nyalume ADD COLUMN {name} {definition}")
        conn.execute(
            "UPDATE daily_nyalume SET source = replace(replace(replace(source, "
            "'CLIKO', 'NYALUME'), 'Cliko', 'Nyalume'), 'cliko', 'nyalume')"
        )
        # FTS5 是 SQLite 自带的倒排索引；trigram 让中文连续文本也能按短语命中。
        # documents 仍是唯一真相来源，三个触发器只负责同步检索索引。
        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                content, content='documents', content_rowid='id', tokenize='trigram'
            );
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
                INSERT INTO documents_fts(rowid, content) VALUES (new.id, new.content);
            END;
            """
        )
        # 兼容 FTS 建立前已经导入过的旧数据库；进程启动时重建一次即可。
        conn.execute("INSERT INTO documents_fts(documents_fts) VALUES ('rebuild')")


def ensure_session(session_id: str, project: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, project) "
            "VALUES (?, ?, ?)",
            (session_id, time.time(), project),
        )


def save_message(
    session_id: str, role: str, content: str, attachments: list[str] | None = None
) -> None:
    ensure_session(session_id)
    attach_json = json.dumps(list(attachments or []), ensure_ascii=False)
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (sync_id, session_id, role, content, attachments, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), session_id, role, content, attach_json, time.time()),
        )
    return cur.lastrowid


def delete_messages_after(session_id: str, message_id: int) -> None:
    """删除某条消息之后的全部内容（编辑重发用），并清掉滚动摘要进度。"""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM messages WHERE session_id = ? AND id > ?",
            (session_id, int(message_id)),
        )
        conn.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
        conn.execute(
            "UPDATE sessions SET memory_upto = 0 WHERE id = ?", (session_id,)
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


def last_message_id(session_id: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(id) AS mid FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["mid"] or 0)


def message_count(session_id: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ? "
            "AND role IN ('user','assistant')",
            (session_id,),
        ).fetchone()
    return int(row["n"] or 0)


# ---------- 便签工具（L3 长期记忆） ----------

def note_save(content: str, tag: str = "", session_id: str = "") -> str:
    tag = (tag or "").strip()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO notes (sync_id, session_id, content, tag, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), session_id, content, tag, time.time()),
        )
    reply = f"已保存便签：{content}"
    if tag:
        reply += f"（标签：{tag}）"
    return reply


def note_list(tag: str = "", session_id: str = "") -> str:
    tag = (tag or "").strip()
    with _conn() as conn:
        if tag:
            rows = conn.execute(
                "SELECT content, tag FROM notes WHERE session_id = ? AND tag = ? "
                "ORDER BY id DESC LIMIT 10",
                (session_id, tag),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT content, tag FROM notes WHERE session_id = ? "
                "ORDER BY id DESC LIMIT 10",
                (session_id,),
            ).fetchall()
    if not rows:
        return "还没有便签" if not tag else f"没有找到标签为「{tag}」的便签"
    return "\n".join(
        f"- [{r['tag']}] {r['content']}" if r["tag"] else f"- {r['content']}"
        for r in rows
    )


def get_recent_notes(limit: int = 3, session_id: str = "") -> list[dict]:
    """取最近 N 条便签，用于每轮注入上下文（L3 自动召回）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, content, tag FROM notes WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [
        {"id": r["id"], "content": r["content"], "tag": r["tag"]} for r in rows
    ]


def note_delete(note_id: int, session_id: str = "") -> bool:
    """按 id 删除便签。"""
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM notes WHERE id = ? AND session_id = ?",
            (int(note_id), session_id),
        )
    return cur.rowcount > 0


def note_delete_by_content(content: str, session_id: str = "") -> bool:
    """删除与内容完全一致的最近一条便签（供 delete_note 工具用）。"""
    content = (content or "").strip()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM notes WHERE session_id = ? AND content = ? "
            "ORDER BY id DESC LIMIT 1",
            (session_id, content),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM notes WHERE id = ?", (row["id"],))
    return True


def search_memory(query: str, limit: int = 8, session_id: str = "") -> str:
    """grep 式全文检索历史记忆：对话、便签、滚动摘要（仿 nanobot 搜 HISTORY，不建索引/不向量化）。

    语料量级只有几百条时，SQLite instr 子串匹配足够快，模型需要时才调用，零额外依赖。
    """
    query = (query or "").strip()
    if not query:
        return "搜索失败：缺少关键词"
    try:
        limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        limit = 8
    cond = "instr(lower(content), lower(:q)) > 0"
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT ts, '用户' AS kind, content FROM messages
             WHERE session_id = :sid AND role = 'user' AND {cond}
            UNION ALL
            SELECT ts, '助手' AS kind, content FROM messages
             WHERE session_id = :sid AND role = 'assistant' AND {cond}
            UNION ALL
            SELECT ts, '便签' AS kind, content FROM notes
             WHERE session_id = :sid AND {cond}
            UNION ALL
            SELECT updated_at AS ts, '摘要' AS kind, content
              FROM summaries WHERE session_id = :sid AND {cond}
            ORDER BY ts DESC LIMIT :limit
            """,
            {"q": query, "limit": limit, "sid": session_id},
        ).fetchall()
    if not rows:
        return f"没有找到与「{query}」相关的历史记录"
    lines = []
    for row in rows:
        text = " ".join((row["content"] or "").split())
        if len(text) > 160:
            text = text[:160] + "…"
        stamp = datetime.datetime.fromtimestamp(row["ts"]).strftime("%m-%d %H:%M")
        lines.append(f"[{stamp}] {row['kind']}：{text}")
    return "\n".join(lines)


# ---------- L4 文档库：导入本地文件夹/文档/PDF 后可检索 ----------

def doc_save(
    source: str, title: str, chunks: list[str], session_id: str = ""
) -> None:
    """整份替换某来源的文档分块（再次导入同一路径 = 更新，不重复堆积）。"""
    source = os.path.abspath(source)
    ts = time.time()
    with _conn() as conn:
        conn.execute(
            "DELETE FROM documents WHERE session_id = ? AND source = ?",
            (session_id, source),
        )
        conn.executemany(
            "INSERT INTO documents (session_id, source, title, chunk_idx, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (session_id, source, title, i, chunk, ts)
                for i, chunk in enumerate(chunks)
            ],
        )


def doc_delete(source: str, session_id: str = "") -> bool:
    """从文档库删除某来源的全部分块（不删除磁盘上的源文件）。"""
    source = os.path.abspath(source)
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM documents WHERE session_id = ? AND source = ?",
            (session_id, source),
        )
    return cur.rowcount > 0


def doc_list(session_id: str = "") -> list[dict]:
    """列出文档库里所有来源（含每个来源的分块数与最近导入时间）。"""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT source, title, COUNT(*) AS chunks, MAX(ts) AS ts
               FROM documents WHERE session_id = ? GROUP BY source, title
               ORDER BY MAX(ts) DESC""",
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def retrieve_docs(
    query: str, limit: int = 6, session_id: str = ""
) -> list[dict]:
    """用 SQLite FTS5 召回文档分块，返回按 BM25 相关度排序的来源片段。"""
    query = (query or "").strip()
    if not query:
        return []
    try:
        limit = max(1, min(10, int(limit)))
    except (TypeError, ValueError):
        limit = 6
    # trigram 至少需要三个字符。把自然语言问题拆成少量去重片段，用 OR 召回，
    # 再由 BM25 把同时命中多个片段的段落排到前面。
    terms: list[str] = []
    for token in re.findall(r"[0-9A-Za-z_\u4e00-\u9fff]{3,}", query.lower()):
        for i in range(len(token) - 2):
            term = token[i : i + 3]
            if term not in terms:
                terms.append(term)
            if len(terms) >= 16:
                break
        if len(terms) >= 16:
            break
    if terms:
        match = " OR ".join(f'"{term}"' for term in terms)
        with _conn() as conn:
            rows = conn.execute(
                """SELECT d.source, d.title, d.chunk_idx, d.content
                   FROM documents_fts f JOIN documents d ON d.id = f.rowid
                   WHERE documents_fts MATCH ? AND d.session_id = ?
                   ORDER BY bm25(documents_fts) LIMIT ?""",
                (match, session_id, limit),
            ).fetchall()
        if rows:
            return [dict(row) for row in rows]
    # 两字查询、纯符号查询或极旧 SQLite 兼容回退：仍能按包含关系找到资料。
    with _conn() as conn:
        rows = conn.execute(
            """SELECT source, title, chunk_idx, content FROM documents
               WHERE session_id = ? AND instr(lower(content), lower(?)) > 0
               ORDER BY ts DESC, id DESC LIMIT ?""",
            (session_id, query, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def search_docs(query: str, limit: int = 6, session_id: str = "") -> str:
    """在文档库检索，返回带来源的 FTS5/BM25 相关原文片段。"""
    query = (query or "").strip()
    if not query:
        return "搜索失败：缺少关键词"
    rows = retrieve_docs(query, limit, session_id)
    if not rows:
        titles = "、".join(d["title"] for d in doc_list(session_id)[:6])
        hint = f"（文档库：{titles}）" if titles else ""
        return f"文档库{hint}里没有找到与「{query}」相关的内容，试试换文档里更可能出现的原词再搜"
    lines = [f"「{query}」相关文档片段（按相关度排序）："]
    for row in rows:
        text = " ".join((row["content"] or "").split())
        if len(text) > 500:
            text = text[:500] + "…"
        lines.append(f"- 《{row['title']}》#{row['chunk_idx']}：{text}")
    return "\n".join(lines)


# ---------- 简单键值设置（工作目录等） ----------

def get_setting(key: str, default: str = "") -> str:
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )


def log_tool_call(
    session_id: str, tool: str, args: str, ok: bool, result_head: str, ms: int
) -> None:
    """工具审计：每执行一次工具记一行（失败静默，不影响对话）。"""
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO tool_audit (session_id, ts, tool, args, ok, result_head, ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(session_id or "")[:64],
                    time.time(),
                    str(tool or "")[:64],
                    str(args or "")[:300],
                    1 if ok else 0,
                    str(result_head or "")[:200],
                    int(ms),
                ),
            )
    except Exception:
        pass


def list_tool_audit(limit: int = 30) -> list[dict]:
    """最近工具调用记录（新的在前），供“纪律设置”页查看。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, session_id, ts, tool, args, ok, result_head, ms "
            "FROM tool_audit ORDER BY id DESC LIMIT ?",
            (max(1, min(100, int(limit))),),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------- 记忆备份：SQLite 在线备份到仓库 backups/ ----------

BACKUP_DIR = os.path.join(_PROJECT_ROOT, "backups")


def _db_signature(path: str) -> str:
    """比较业务数据，忽略可重建的 FTS 内部页。"""
    digest = hashlib.sha256()
    with sqlite3.connect(path) as conn:
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'documents_fts%' "
            "ORDER BY name"
        ).fetchall()
        for table, schema in tables:
            digest.update(f"{table}\0{schema}\0".encode("utf-8"))
            for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                digest.update((repr(tuple(row)) + "\n").encode("utf-8", "surrogatepass"))
    return digest.hexdigest()


def _backup_info(path: str) -> dict:
    info = {"title": "记忆快照", "sessions": 0, "messages": 0, "documents": 0}
    try:
        with sqlite3.connect(path) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            for table in ("sessions", "messages", "documents"):
                if table in tables:
                    info[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if "messages" in tables:
                latest = conn.execute(
                    "SELECT session_id FROM messages ORDER BY ts DESC, id DESC LIMIT 1"
                ).fetchone()
                if latest:
                    title = conn.execute(
                        "SELECT title FROM sessions WHERE id = ?", latest
                    ).fetchone() if "sessions" in tables else None
                    first = conn.execute(
                        "SELECT content FROM messages WHERE session_id = ? AND role = 'user' "
                        "ORDER BY id LIMIT 1", latest
                    ).fetchone()
                    text = (first[0] if first else title[0] if title and title[0] else "") or ""
                    text = re.sub(r"\s+", " ", text).strip()
                    if text:
                        info["title"] = text[:18] + ("…" if len(text) > 18 else "")
    except sqlite3.Error:
        pass
    return info


def backup_db(name: str = "") -> str:
    """把当前 agent.db 完整备份一份（在线备份，不打断读写），返回文件路径。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    requested_name = (name or "").strip()
    if not requested_name:
        backups = list_backups()
        if backups:
            latest = os.path.join(BACKUP_DIR, backups[0]["name"])
            try:
                if _db_signature(DB_PATH) == _db_signature(latest):
                    return latest
            except (OSError, sqlite3.Error):
                pass
    name = requested_name or time.strftime("agent_%Y%m%d_%H%M%S.db")
    if not name.lower().endswith(".db"):
        name += ".db"
    dest = os.path.join(BACKUP_DIR, os.path.basename(name))
    stem, ext = os.path.splitext(dest)
    suffix = 2
    while os.path.exists(dest):
        dest = f"{stem}_{suffix}{ext}"
        suffix += 1
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def list_backups() -> list[dict]:
    """列出 backups/ 下的备份文件（新的在前）。"""
    if not os.path.isdir(BACKUP_DIR):
        return []
    rows = []
    for name in os.listdir(BACKUP_DIR):
        if not name.lower().endswith(".db"):
            continue
        path = os.path.join(BACKUP_DIR, name)
        rows.append(
            {
                "name": name,
                "size": os.path.getsize(path),
                "ts": os.path.getmtime(path),
                **_backup_info(path),
            }
        )
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def restore_backup(name: str) -> str:
    """先保护当前状态，再用备份覆盖当前库；只接受 backups/ 下的文件名。"""
    name = os.path.basename((name or "").strip())
    src_path = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(src_path):
        raise ValueError(f"备份不存在：{name}")
    backup_db(time.strftime("before_restore_%Y%m%d_%H%M%S.db"))
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(DB_PATH)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return src_path


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


# ---------- 会话管理：WebUI 列表 / 新建 / 删除 ----------

def create_session(session_id: str | None = None, project: str = "") -> str:
    """新建会话（可挂在某项目下），返回会话 id。"""
    sid = session_id or f"chat-{uuid.uuid4().hex[:10]}"
    ensure_session(sid, project)
    return sid


def update_session(
    session_id: str,
    title: str | None = None,
    pinned: int | None = None,
    project: str | None = None,
) -> None:
    """改会话的自定义标题/置顶/所属项目（传 None 表示不改）。"""
    ensure_session(session_id)
    sets, params = [], []
    if title is not None:
        sets.append("title = ?")
        params.append((title or "").strip() or None)
    if pinned is not None:
        sets.append("pinned = ?")
        params.append(1 if pinned else 0)
    if project is not None:
        sets.append("project = ?")
        params.append(project or "")
    if not sets:
        return
    params.append(session_id)
    with _conn() as conn:
        conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)


def session_project(session_id: str) -> str:
    """会话所属项目 id（无则空串）。"""
    ensure_session(session_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(project, '') AS project FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return str(row["project"] or "")


def session_title(session_id: str) -> str:
    """会话的可读名称：自定义标题优先，否则取第一句用户消息。"""
    ensure_session(session_id)
    with _conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(NULLIF(s.title, ''),
                      (SELECT m.content FROM messages m
                       WHERE m.session_id = s.id AND m.role = 'user'
                       ORDER BY m.id ASC LIMIT 1), '') AS title
               FROM sessions s WHERE s.id = ?""",
            (session_id,),
        ).fetchone()
    return str(row["title"] or "") if row else ""


def list_sessions(limit: int = 50) -> list[dict]:
    """列出会话：置顶优先，其次最近活动；标题 = 自定义标题或第一句用户消息。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id AS id,
                   s.created_at AS created_at,
                   s.pinned AS pinned,
                   COALESCE(s.project, '') AS project,
                   s.title AS custom_title,
                   (SELECT m.ts FROM messages m
                     WHERE m.session_id = s.id ORDER BY m.id DESC LIMIT 1) AS last_ts,
                   (SELECT COUNT(*) FROM messages m
                     WHERE m.session_id = s.id AND m.role IN ('user','assistant'))
                     AS message_count,
                   (SELECT m.content FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'user'
                     ORDER BY m.id ASC LIMIT 1) AS title
            FROM sessions s
            ORDER BY s.pinned DESC, COALESCE(last_ts, s.created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["title"] = row["custom_title"] or row["title"] or ""
        item.pop("custom_title", None)
        result.append(item)
    return result


def search_sessions(q: str, limit: int = 20) -> list[dict]:
    """按标题/消息内容搜会话，返回 [{id, custom_title, first_user,
    project, last_ts, hits, snippet}]，按最近命中排序。"""
    q = (q or "").strip()
    if not q:
        return []
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id AS id,
                   COALESCE(s.title, '') AS custom_title,
                   COALESCE(s.project, '') AS project,
                   (SELECT m.content FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'user'
                     ORDER BY m.id ASC LIMIT 1) AS first_user,
                   MAX(m.ts) AS last_ts,
                   COUNT(DISTINCT m.id) AS hits,
                   (SELECT m2.content FROM messages m2
                     WHERE m2.session_id = s.id
                       AND m2.role IN ('user', 'assistant')
                       AND instr(lower(m2.content), lower(?)) > 0
                     ORDER BY m2.id DESC LIMIT 1) AS snippet
            FROM sessions s
            LEFT JOIN messages m
              ON m.session_id = s.id
             AND m.role IN ('user', 'assistant')
             AND instr(lower(m.content), lower(?)) > 0
            WHERE instr(lower(COALESCE(s.title, '')), lower(?)) > 0
               OR m.id IS NOT NULL
            GROUP BY s.id
            ORDER BY last_ts DESC
            LIMIT ?
            """,
            (q, q, q, limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        snippet = (item.get("snippet") or "").strip()
        if not snippet and item.get("custom_title"):
            snippet = "（标题匹配）"
        item["snippet"] = " ".join(snippet.split())[:120]
        result.append(item)
    return result


# ---------- 项目：文件夹级工作区（用户显式添加 = 授权该目录内删改） ----------

def add_project(path: str, name: str = "") -> dict:
    """登记一个项目文件夹（不存在则创建）；同路径不重复添加。"""
    path = os.path.abspath(path)
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM projects WHERE path = ?", (path,)
        ).fetchone()
        if existing:
            return get_project(existing["id"])
        pid = f"proj-{uuid.uuid4().hex[:8]}"
        name = (name or "").strip() or os.path.basename(path) or path
        os.makedirs(path, exist_ok=True)
        conn.execute(
            "INSERT INTO projects (id, name, path, pinned, created_at, folders) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (pid, name, path, time.time(), json.dumps([path])),
        )
    return get_project(pid)


def _project_folders(row) -> list[str]:
    folders = json.loads(row["folders"] or "[]") if isinstance(row, sqlite3.Row) or isinstance(row, dict) else []
    folders = [f for f in folders if isinstance(f, str)]
    if not folders and row["path"]:
        folders = [row["path"]]
    return folders


def get_project(project_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    if not row:
        return {}
    item = dict(row)
    item["folders"] = _project_folders(row)
    return item


def add_project_folder(project_id: str, folder: str) -> bool:
    """把另一个已存在目录并入项目授权范围（不复制、不移动磁盘文件）。"""
    project = get_project(project_id)
    if not project:
        return False
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ValueError(f"目录不存在：{folder}")
    folders = project["folders"]
    if folder not in folders:
        folders.append(folder)
        with _conn() as conn:
            conn.execute(
                "UPDATE projects SET folders = ? WHERE id = ?",
                (json.dumps(folders), project_id),
            )
    return True


def remove_project_folder(project_id: str, folder: str) -> bool:
    """把某个目录移出授权范围；主目录不可移除。磁盘文件不动。"""
    project = get_project(project_id)
    if not project:
        return False
    folder = os.path.abspath(folder)
    folders = project["folders"]
    if folder not in folders:
        return False
    if folder == project.get("path"):
        raise ValueError("主目录不能移除；可删除整个项目")
    folders.remove(folder)
    with _conn() as conn:
        conn.execute(
            "UPDATE projects SET folders = ? WHERE id = ?",
            (json.dumps(folders), project_id),
        )
    return True


def list_projects() -> list[dict]:
    """项目列表（置顶优先）；附每个项目下的会话数。"""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.path, p.pinned, p.created_at, p.folders,
                      (SELECT COUNT(*) FROM sessions s WHERE s.project = p.id)
                        AS session_count
               FROM projects p
               ORDER BY p.pinned DESC, p.created_at ASC"""
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["folders"] = _project_folders(row)
        result.append(item)
    return result


def update_project(
    project_id: str, name: str | None = None, pinned: int | None = None
) -> bool:
    sets, params = [], []
    if name is not None:
        sets.append("name = ?")
        params.append((name or "").strip() or "未命名项目")
    if pinned is not None:
        sets.append("pinned = ?")
        params.append(1 if pinned else 0)
    if not sets:
        return True
    params.append(project_id)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params
        )
    return cur.rowcount > 0


def delete_project(project_id: str) -> None:
    """删除项目登记：项目下会话退回“最近”，磁盘文件夹不动。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET project = '' WHERE project = ?", (project_id,)
        )
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# ---------- 文件操作撤销（写/删/移/建目录前留快照） ----------

def add_undo(op_id: str, session_id: str, kind: str, path: str, prev: str = "") -> None:
    """记录一次可撤销的文件操作（kind: write/delete/move/mkdir）。"""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM undo_ops WHERE id = ?", (op_id,)
        )
        conn.execute(
            "INSERT INTO undo_ops (id, session_id, ts, kind, path, prev, applied) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (op_id, session_id, time.time(), kind, path, prev),
        )
        # 每会话只留最近 40 条可撤销记录
        conn.execute(
            """DELETE FROM undo_ops WHERE session_id = ?
               AND id NOT IN (
                 SELECT id FROM undo_ops WHERE session_id = ?
                 ORDER BY ts DESC LIMIT 40)""",
            (session_id, session_id),
        )


def list_undo(session_id: str, limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM undo_ops WHERE session_id = ? AND applied = 0
               ORDER BY ts DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_undo(op_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM undo_ops WHERE id = ?", (op_id,)
        ).fetchone()
    return dict(row) if row else {}


def mark_undo_applied(op_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE undo_ops SET applied = 1 WHERE id = ?", (op_id,)
        )


def session_messages(session_id: str, limit: int = 200) -> list[dict]:
    """取某会话完整消息（按时间正序），供 WebUI 切换会话时回显历史。"""
    ensure_session(session_id)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, attachments, ts FROM messages WHERE session_id = ? "
            "AND role IN ('user','assistant') ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["attachments"] = json.loads(item.get("attachments") or "[]")
        except (ValueError, TypeError):
            item["attachments"] = []
        if not isinstance(item["attachments"], list):
            item["attachments"] = []
        result.append(item)
    return result


def duplicate_session_until(
    source_session: str, up_to_message_id: int, project: str = ""
) -> str:
    """把某个会话截止到指定消息的内容复制成一个新会话（分支）。"""
    target = create_session(project=project)
    with _conn() as conn:
        conn.execute(
            """INSERT INTO messages (session_id, role, content, attachments, ts)
               SELECT ?, role, content, attachments, ts FROM messages
               WHERE session_id = ? AND id <= ? ORDER BY id ASC""",
            (target, source_session, int(up_to_message_id)),
        )
    return target


def delete_session(session_id: str) -> None:
    """删除会话及其独立的消息、摘要、便签和检索文档。"""
    with _conn() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM notes WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM documents WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


# ---------- 今日 nyalume ----------

def get_daily_affection(session_id: str) -> int:
    ensure_session(session_id)
    with _conn() as conn:
        row = conn.execute(
            "SELECT daily_affection FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return int(row["daily_affection"] if row else 50)


def set_daily_affection(session_id: str, value: int) -> int:
    ensure_session(session_id)
    value = max(-100, min(200, int(value)))
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET daily_affection = ? WHERE id = ?",
            (value, session_id),
        )
    return value

def get_daily_nyalume(day: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT day, score, profile, blessing, created_at, keyword, source, "
            "liked, collected, viewed, card_version "
            "FROM daily_nyalume WHERE day = ?",
            (day,),
        ).fetchone()
    return dict(row) if row else None


def list_daily_nyalume() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT day, score, profile, blessing, created_at, keyword, source, "
            "liked, collected, viewed, card_version FROM daily_nyalume "
            "ORDER BY day DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def save_daily_nyalume(
    day: str, score: int, profile: str, blessing: str,
    keyword: str = "", source: str = "Nyalume", card_version: str = "v1",
) -> tuple[dict, bool]:
    """首次写入当天结果；并发重复抽取时返回已经存在的那一份。"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO daily_nyalume "
            "(day, score, profile, blessing, created_at, keyword, source, card_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (day, int(score), profile, blessing, time.time(), keyword, source, card_version),
        )
    return get_daily_nyalume(day), bool(cur.rowcount)


def update_daily_nyalume(
    day: str, *, liked: bool | None = None, collected: bool | None = None,
    viewed: bool | None = None,
) -> dict | None:
    values = {"liked": liked, "collected": collected, "viewed": viewed}
    changes = [(name, int(value)) for name, value in values.items() if value is not None]
    if not changes:
        return get_daily_nyalume(day)
    with _conn() as conn:
        conn.execute(
            "UPDATE daily_nyalume SET " + ", ".join(f"{name} = ?" for name, _ in changes)
            + " WHERE day = ?",
            tuple(value for _, value in changes) + (day,),
        )
    return get_daily_nyalume(day)


init_db()
