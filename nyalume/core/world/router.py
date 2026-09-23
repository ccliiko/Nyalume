"""事件路由器：决定什么时候触发什么事件。

核心原则：
- 90% 用模板（零 token）
- LLM 只在"特别的时刻"调用
- 不要频繁打扰用户
- 事件有优先级和冷却
"""

from __future__ import annotations

import datetime
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .events import (
    Event,
    detect_milestones,
    evaluate_templates,
    random_meme,
    record_trigger,
)
from .home import HomeMap, FileChange


# ── 路由配置 ──────────────────────────────────────────────

# 最短事件间隔（秒），防止连续轰炸
MIN_EVENT_INTERVAL = 30

# 每小时最多几个主动事件
MAX_EVENTS_PER_HOUR = 6

# LLM 事件每天最多几次
MAX_LLM_EVENTS_PER_DAY = 3

# LLM 事件概率（每次路由循环有 X% 概率触发）
LLM_EVENT_CHANCE = 0.05  # 5%


# ── 路由器状态 ────────────────────────────────────────────

@dataclass
class RouterState:
    """路由器的运行时状态。"""
    last_event_time: float = 0.0
    events_this_hour: int = 0
    hour_start: float = 0.0
    llm_events_today: int = 0
    today: str = ""
    # 文件监控
    last_scan_time: float = 0.0
    visited_rooms: set[str] = field(default_factory=set)
    # 用户活动
    last_user_activity: float = 0.0
    current_window: str = ""
    work_start_time: float = 0.0
    water_reminded: bool = False


class EventRouter:
    """事件路由器。

    调用方式：
    1. feed_context(ctx) — 喂入当前上下文（窗口变化、文件变化等）
    2. tick() — 定时调用，返回应该触发的事件列表（可能为空）
    3. on_event(callback) — 注册事件回调
    """

    def __init__(self, home: HomeMap | None = None, root: str = ""):
        self.home = home or HomeMap(root or os.getcwd())
        self.state = RouterState()
        self._callbacks: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()

        # 初始扫描
        self.home.scan()

    def on_event(self, callback: Callable[[Event], None]) -> None:
        """注册事件回调（事件触发时调用）。"""
        self._callbacks.append(callback)

    def feed_context(self, **kwargs: Any) -> None:
        """喂入上下文信息（窗口变化、用户活动等）。"""
        with self._lock:
            if "window_title" in kwargs:
                self.state.current_window = kwargs["window_title"]
                self.state.last_user_activity = time.time()
            if "user_active" in kwargs and kwargs["user_active"]:
                self.state.last_user_activity = time.time()
                self.state.water_reminded = False  # 用户活跃后重置
            if "work_started" in kwargs:
                self.state.work_start_time = kwargs["work_started"]

    def tick(self) -> list[Event]:
        """定时调用（建议每 30-60 秒调一次），返回要触发的事件。"""
        now = time.time()
        today = datetime.datetime.now().date().isoformat()

        with self._lock:
            # 重置每日计数
            if today != self.state.today:
                self.state.today = today
                self.state.llm_events_today = 0
                self.state.events_this_hour = 0

            # 重置小时计数
            if now - self.state.hour_start > 3600:
                self.state.hour_start = now
                self.state.events_this_hour = 0

            # 检查冷却
            if now - self.state.last_event_time < MIN_EVENT_INTERVAL:
                return []
            if self.state.events_this_hour >= MAX_EVENTS_PER_HOUR:
                return []

        # 构建上下文
        ctx = self._build_context(now)

        # 扫描文件变化
        changes = self._scan_file_changes(now)
        if changes:
            ctx["file_changes"] = [
                {
                    "path": c.path,
                    "type": c.change_type,
                    "perception": f"{c.room.get('emoji', '❓')} {c.room.get('name', '未知')}里{'多了新东西' if c.change_type == 'created' else '少了一样东西' if c.change_type == 'deleted' else '有点变化'}",
                }
                for c in changes
            ]

        # 检测里程碑
        milestones = detect_milestones(ctx)

        # 评估模板事件
        template_events = evaluate_templates(ctx)

        # 合并所有事件
        all_events = milestones + template_events

        # 低概率随机 AI 梗
        meme = random_meme()
        if meme:
            all_events.append(meme)

        if not all_events:
            return []

        # 选择最高优先级的事件（每次最多 1-2 个）
        all_events.sort(key=lambda e: e.priority, reverse=True)
        selected = all_events[:2]

        # 记录触发
        with self._lock:
            self.state.last_event_time = now
            self.state.events_this_hour += len(selected)

        for event in selected:
            record_trigger(event.id)
            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception:
                    pass

        return selected

    def should_use_llm(self) -> bool:
        """判断这次是否应该用 LLM 生成事件。"""
        if self.state.llm_events_today >= MAX_LLM_EVENTS_PER_DAY:
            return False
        return secrets.randbelow(100) < int(LLM_EVENT_CHANCE * 100)

    def record_llm_event(self) -> None:
        """记录一次 LLM 事件使用。"""
        self.state.llm_events_today += 1

    def get_context_for_llm(self) -> dict[str, Any]:
        """获取当前上下文，用于构建 LLM 事件的 prompt。"""
        ctx = self._build_context(time.time())
        ctx["visited_rooms"] = list(self.state.visited_rooms)
        return ctx

    def _build_context(self, now: float) -> dict[str, Any]:
        """构建当前上下文。"""
        dt = datetime.datetime.now()
        idle_minutes = (now - self.state.last_user_activity) / 60 if self.state.last_user_activity else 0
        work_minutes = (now - self.state.work_start_time) / 60 if self.state.work_start_time else 0

        return {
            "hour": dt.hour,
            "minute": dt.minute,
            "weekday_num": dt.weekday(),
            "weekday": ["周一","周二","周三","周四","周五","周六","周日"][dt.weekday()],
            "date": dt.date().isoformat(),
            "window_title": self.state.current_window,
            "window_changed": False,  # 由外部设置
            "idle_minutes": idle_minutes,
            "work_minutes": work_minutes,
            "reminded_water": self.state.water_reminded,
            "minutes_since_last": (now - self.state.last_event_time) / 60,
            # 以下由外部填充
            "weather": "",
            "weather_changed": False,
            "user_mood": "",
            "task_completed": False,
        }

    def _scan_file_changes(self, now: float) -> list[FileChange]:
        """扫描文件变化（限制频率）。"""
        if now - self.state.last_scan_time < 10:  # 最多 10 秒扫一次
            return []
        self.state.last_scan_time = now

        changes = self.home.diff()
        # 更新已访问房间
        for change in changes:
            room_name = change.room.get("name", "")
            if room_name:
                self.state.visited_rooms.add(room_name)

        return changes