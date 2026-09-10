"""本地 SQLite 运行记录；只保存元数据，不记录对话、工具参数或结果正文。"""

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import memory

logger = logging.getLogger(__name__)


def _connect():
    connection = sqlite3.connect(memory.DB_PATH, timeout=1)
    connection.row_factory = sqlite3.Row
    return connection


def list_runs(session_id: str = "", limit: int = 20) -> list[dict]:
    with _connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'agent_traces'"
        ).fetchone():
            return []
        rows = connection.execute(
            "SELECT * FROM agent_traces WHERE (? = '' OR session_id = ?) "
            "ORDER BY started_at DESC LIMIT ?",
            (session_id, session_id, max(1, min(100, limit))),
        ).fetchall()
    results = []
    for row in rows:
        result = dict(row)
        result["spans"] = json.loads(result["spans"])
        results.append(result)
    return results


def get_run(run_id: str) -> dict | None:
    """读取单次运行的脱敏 Trace。"""
    with _connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'agent_traces'"
        ).fetchone():
            return None
        row = connection.execute(
            "SELECT * FROM agent_traces WHERE run_id = ?", (run_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["spans"] = json.loads(result["spans"])
    return result


class Trace:
    def __init__(self, session_id: str):
        self.run_id = uuid.uuid4().hex
        self.session_id = session_id
        self.started_at = time.time()
        self.started = time.perf_counter()
        self.status = "running"
        self.spans = []
        self.error_type = ""
        self._save()

    def _save(self):
        try:
            with _connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS agent_traces ("
                    "run_id TEXT PRIMARY KEY, session_id TEXT, started_at REAL, "
                    "duration_ms REAL, status TEXT, error_type TEXT, spans TEXT)"
                )
                connection.execute(
                    "INSERT OR REPLACE INTO agent_traces VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self.run_id, self.session_id, self.started_at,
                     round((time.perf_counter() - self.started) * 1000, 2),
                     self.status, self.error_type, json.dumps(self.spans)),
                )
        except (sqlite3.Error, OSError):
            logger.warning("Could not persist agent trace", exc_info=True)

    @contextmanager
    def span(self, kind: str, name: str):
        entry = {"kind": kind, "name": name, "status": "returned"}
        started = time.perf_counter()
        try:
            yield entry
        except GeneratorExit:
            entry["status"] = "cancelled"
            raise
        except BaseException as error:
            status = {
                "_RunCancelled": "cancelled",
                "_RunTimedOut": "timeout",
            }.get(type(error).__name__, "error")
            entry.update(status=status, error_type=type(error).__name__)
            raise
        finally:
            entry["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            self.spans.append(entry)
            self._save()

    def call(self, function, name: str, arguments: dict):
        with self.span("tool", name):
            return function(name, arguments)

    def stream(self, function, *args, **kwargs):
        with self.span("llm", "chat_stream"):
            yield from function(*args, **kwargs)

    def finish(self):
        if self.status == "running":
            self.status = "cancelled"
        self._save()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="查看最近的 Agent Trace")
    parser.add_argument("--session", default="")
    parser.add_argument("--limit", type=int, default=20)
    options = parser.parse_args()
    print(json.dumps(list_runs(options.session, options.limit), ensure_ascii=False, indent=2))
