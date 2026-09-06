"""定时主动提醒：标准 5 段 cron 表达式 + SQLite 持久化 + 后台调度线程。

设计：提醒是全局的（不绑定会话）；cron 命中且“本分钟还没触发过”时
check_due 返回并落库 last_fired，避免重复触发。
CLI / 桌宠用 ReminderScheduler 后台轮询，“到点主动冒出来，
不需要用户发消息”——agent 会自己动。
"""

import datetime
import sqlite3
import threading
import time

from .memory import DB_PATH


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                cron TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                last_fired REAL,
                one_shot INTEGER DEFAULT 0,
                created_at REAL
            )
            """
        )
    cols = {
        row["name"]
        for row in _conn().execute("PRAGMA table_info(reminders)")
    }
    if "one_shot" not in cols:
        with _conn() as conn:
            conn.execute(
                "ALTER TABLE reminders ADD COLUMN one_shot INTEGER DEFAULT 0"
            )


# ---------- cron 解析（只支持数字；5 段：分 时 日 月 周） ----------

def _parse_field(field: str, low: int, high: int) -> set[int]:
    """解析单段 cron：支持 *、数值、a-b、a,b,c、*/n、a-b/n。"""
    field = field.strip()
    allowed: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
        else:
            base, step = part, 1
        if base == "*":
            start, end = low, high
        elif "-" in base:
            a_s, b_s = base.split("-", 1)
            start, end = int(a_s), int(b_s)
        else:
            start = end = int(base)
        if step < 1:
            step = 1
        value = start
        while value <= end and value <= high:
            if value >= low:
                allowed.add(value)
            value += step
    return allowed


def cron_matches(expression: str, when: datetime.datetime | None = None) -> bool:
    """判断某个时间点是否命中 cron。星期用 cron 约定：0/7=周日，1~6=周一~周六。"""
    parts = (expression or "").split()
    if len(parts) != 5:
        raise ValueError(
            "cron 需要 5 段（分 时 日 月 周），例如每天 9 点：0 9 * * *"
        )
    minute_s, hour_s, day_s, month_s, dow_s = parts
    minutes = _parse_field(minute_s, 0, 59)
    hours = _parse_field(hour_s, 0, 23)
    days = _parse_field(day_s, 1, 31)
    months = _parse_field(month_s, 1, 12)
    weekdays = _parse_field(dow_s, 0, 7)
    weekdays = {7 if w == 0 else w for w in weekdays}  # 0 也当作周日

    now = when or datetime.datetime.now()
    cron_dow = (now.weekday() + 1) % 7  # 周一=1 … 周日=0
    if cron_dow == 0:
        cron_dow = 7
    return (
        now.minute in minutes
        and now.hour in hours
        and now.day in days
        and now.month in months
        and cron_dow in weekdays
    )


# ---------- CRUD ----------

def add_reminder(content: str, cron: str, one_shot: bool = False) -> int:
    """新增提醒；cron 非法时抛 ValueError。返回自增 id。"""
    content = (content or "").strip()
    cron = (cron or "").strip()
    if not content:
        raise ValueError("提醒内容不能为空")
    cron_matches(cron)  # 校验语法（用当前时间测试匹配）
    _ensure_table()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (content, cron, enabled, one_shot, created_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (content, cron, 1 if one_shot else 0, time.time()),
        )
    return int(cur.lastrowid)


def list_reminders(enabled_only: bool = True) -> list[dict]:
    """列出提醒，按创建时间倒序。"""
    _ensure_table()
    sql = (
        "SELECT id, content, cron, enabled, last_fired, one_shot, created_at"
        " FROM reminders"
    )
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id DESC"
    with _conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(row) for row in rows]


def delete_reminder(reminder_id: int) -> bool:
    _ensure_table()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM reminders WHERE id = ?", (int(reminder_id),))
    return cur.rowcount > 0


def check_due(now: datetime.datetime | None = None, claim: bool = True) -> list[dict]:
    """返回当前这一分钟到期的提醒。

    claim=True（桌宠/CLI 调度器）：把 last_fired 更新为现在，
    一次性提醒（remind_me_in）触发后自动删除；
    claim=False（Web 轮询）：只读展示，不抢触发权。
    """
    now = now or datetime.datetime.now()
    _ensure_table()
    minute_start = now.replace(second=0, microsecond=0).timestamp()
    due: list[dict] = []
    for row in list_reminders():
        try:
            if not cron_matches(row["cron"], now):
                continue
        except ValueError:
            continue
        last = row.get("last_fired")
        if claim and last is not None and last >= minute_start:
            continue  # 这一分钟已经触发过
        due.append(
            {
                "id": row["id"],
                "content": row["content"],
                "cron": row["cron"],
            }
        )
    if due:
        with _conn() as conn:
            for item in due:
                if claim:
                    row = conn.execute(
                        "SELECT one_shot FROM reminders WHERE id = ?",
                        (item["id"],),
                    ).fetchone()
                    conn.execute(
                        "UPDATE reminders SET last_fired = ? WHERE id = ?",
                        (now.timestamp(), item["id"]),
                    )
                    if row and row["one_shot"]:
                        # 一次性提醒：触发即删，不会再响
                        conn.execute(
                            "DELETE FROM reminders WHERE id = ?", (item["id"],)
                        )
    return due


# ---------- 后台调度线程（CLI / 桌宠用） ----------

class ReminderScheduler(threading.Thread):
    """后台线程：每隔 interval 秒检查一次，到点调用 callback(content)。"""

    def __init__(self, callback, interval: float = 20.0):
        super().__init__(daemon=True, name="reminder-scheduler")
        self._callback = callback
        self._interval = max(5.0, float(interval))
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                for item in check_due():
                    try:
                        self._callback(item["content"])
                    except Exception as e:
                        self._log_callback_error(item, e)
            except Exception:
                pass  # 调度失败不拖垮宿主进程
            self._stop_event.wait(self._interval)

    def _log_callback_error(self, item: dict, exc: Exception) -> None:
        import os
        import tempfile
        import traceback

        path = os.path.join(tempfile.gettempdir(), "cliko_scheduler_err.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    f"[{time.strftime('%H:%M:%S')}] reminder #{item.get('id')} "
                    f"callback error: {type(exc).__name__}: {exc}\n"
                    + traceback.format_exc()
                )
        except Exception:
            pass

    def stop(self) -> None:
        self._stop_event.set()
