"""本地陪伴专注：时间结束与用户确认完成是两个独立状态。"""
import datetime
import time

from . import memory


def _refresh(conn, now):
    conn.execute(
        "UPDATE focus_sessions SET status='ready', remaining=0, deadline=NULL "
        "WHERE status='active' AND deadline<=?", (now,),
    )


def snapshot():
    now = time.time()
    with memory._conn() as conn:
        _refresh(conn, now)
        current = conn.execute(
            "SELECT * FROM focus_sessions WHERE status IN ('active','paused','ready')"
        ).fetchone()
        recent = conn.execute(
            "SELECT * FROM focus_sessions WHERE status='completed' ORDER BY finished_at DESC LIMIT 7"
        ).fetchall()
        midnight = datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp()
        today = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(duration-remaining),0) AS seconds "
            "FROM focus_sessions WHERE status='completed' AND finished_at>=?", (midnight,),
        ).fetchone()
    return {"current": dict(current) if current else None, "recent": [dict(r) for r in recent],
            "today": dict(today), "server_time": now}


def start(goal: str, minutes: int):
    goal = goal.strip()
    if not goal or len(goal) > 120 or minutes not in (15, 25, 45):
        raise ValueError("写下 1–120 字的小目标，并选择 15、25 或 45 分钟")
    now = time.time()
    with memory._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM focus_sessions WHERE status IN ('active','paused','ready')").fetchone():
            raise ValueError("已有一段专注，请先继续或收尾")
        conn.execute(
            "INSERT INTO focus_sessions(goal,duration,remaining,deadline,status,created_at) "
            "VALUES (?,?,?,?,'active',?)", (goal, minutes * 60, minutes * 60, now + minutes * 60, now),
        )
    return snapshot()


def update(entry_id: int, action: str, outcome: str = "", note: str = ""):
    note = note.strip()
    if action not in ("pause", "resume", "finish", "complete", "cancel") or len(note) > 500:
        raise ValueError("操作无效，回顾最多 500 字")
    if action == "complete" and outcome not in ("done", "progress"):
        raise ValueError("请选择这次专注的结果")
    now = time.time()
    with memory._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _refresh(conn, now)
        row = conn.execute("SELECT * FROM focus_sessions WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise ValueError("这段专注不存在")
        target = {"pause": "paused", "resume": "active", "finish": "ready",
                  "complete": "completed", "cancel": "cancelled"}[action]
        allowed = {"pause": ("active",), "resume": ("paused",), "finish": ("active", "paused"),
                   "complete": ("ready",), "cancel": ("active", "paused", "ready")}[action]
        if row["status"] != target:
            if row["status"] not in allowed:
                raise ValueError("专注状态已变化，请刷新后重试")
            remaining = max(0, min(row["remaining"], row["deadline"] - now)) if row["status"] == "active" else row["remaining"]
            conn.execute(
                "UPDATE focus_sessions SET status=?,remaining=?,deadline=?,outcome=?,note=?,finished_at=? WHERE id=?",
                (target, remaining, now + remaining if target == "active" else None,
                 outcome if action == "complete" else "", note if action == "complete" else "",
                 now if target in ("completed", "cancelled") else None, entry_id),
            )
    return snapshot()


def delete(entry_id: int):
    with memory._conn() as conn:
        conn.execute("DELETE FROM focus_sessions WHERE id=? AND status IN ('completed','cancelled')", (entry_id,))
    return snapshot()
