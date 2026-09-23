"""世界观管理器：单例，协调 HomeMap + Router + Diary。

用法（在 server.py 启动时初始化）：
    from nyalume.core.world.manager import world
    world.start(root)          # 启动后台 tick
    world.shutdown()           # 停止

用法（在 agent.py 中读取状态）：
    from nyalume.core.world.manager import world
    ctx = world.get_prompt_context()  # 拼入 system prompt
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

from .diary import DiaryCollector
from .events import Event, build_narrative_prompt
from .home import HomeMap
from .router import EventRouter


class WorldManager:
    """世界观系统单例管理器。"""

    def __init__(self):
        self._home: HomeMap | None = None
        self._router: EventRouter | None = None
        self._diary: DiaryCollector | None = None
        self._tick_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False
        self._root: str = ""
        # 最近的待播报事件（模板事件，零 token）
        self._pending_events: list[Event] = []
        self._events_lock = threading.Lock()
        # 用户活动回调（可由 server.py 注册，用于喂 context 给 router）
        self._on_event_callbacks: list[Callable[[Event], None]] = []

    @property
    def started(self) -> bool:
        return self._started

    @property
    def home(self) -> HomeMap | None:
        return self._home

    @property
    def router(self) -> EventRouter | None:
        return self._router

    @property
    def diary(self) -> DiaryCollector | None:
        return self._diary

    def start(self, root: str, tick_interval: float = 45.0) -> None:
        """初始化并启动后台 tick 循环。"""
        if self._started:
            return
        self._root = os.path.normpath(root)
        self._home = HomeMap(self._root)
        self._router = EventRouter(home=self._home, root=self._root)
        self._diary = DiaryCollector()

        # 注册路由器回调：事件触发时收集到待播报队列
        self._router.on_event(self._on_router_event)

        self._started = True
        self._stop_event.clear()

        self._tick_thread = threading.Thread(
            target=self._tick_loop,
            args=(tick_interval,),
            daemon=True,
            name="world-tick",
        )
        self._tick_thread.start()

    def shutdown(self) -> None:
        """停止后台 tick。"""
        self._stop_event.set()
        if self._tick_thread and self._tick_thread.is_alive():
            self._tick_thread.join(timeout=5)
        self._started = False

    def feed_context(self, **kwargs: Any) -> None:
        """喂入用户活动上下文（窗口变化、用户活跃等）。"""
        if self._router:
            self._router.feed_context(**kwargs)

    def record_chat(self, user_text: str, mood: str = "neutral") -> None:
        """记录一次用户对话到日记。"""
        if self._diary:
            self._diary.add_chat(user_text, mood)

    def record_file_change(
        self, path: str, change_type: str, room_name: str, room_emoji: str
    ) -> None:
        """记录一次文件变化到日记。"""
        if self._diary:
            self._diary.add_file_change(path, change_type, room_name, room_emoji)

    def pop_pending_events(self) -> list[Event]:
        """取出并清空待播报的模板事件。"""
        with self._events_lock:
            events = list(self._pending_events)
            self._pending_events.clear()
        return events

    def get_prompt_context(self) -> str:
        """生成家的感知信息，用于注入 system prompt。

        内容包括：家的状态 + 最近事件摘要（全模板，零 token）。
        """
        if not self._started:
            return ""

        parts: list[str] = []

        # 家的整体感受
        if self._home:
            feeling = self._home.home_feeling()
            if feeling:
                parts.append(f"【家的现状】{feeling}")

        # 待播报的模板事件
        with self._events_lock:
            if self._pending_events:
                event_texts = [e.text for e in self._pending_events[-3:]]
                parts.append("【最近的家事】" + "；".join(event_texts))

        # 最近日记摘要（如果有昨天的）
        if self._diary:
            from .diary import DiaryCollector
            recent = DiaryCollector.recent_summaries(days=2)
            if recent:
                parts.append("【最近日记】" + recent[0][:150])

        return "\n".join(parts) if parts else ""

    def on_event(self, callback: Callable[[Event], None]) -> None:
        """注册事件回调（事件触发时调用）。"""
        self._on_event_callbacks.append(callback)

    # ── 内部方法 ──────────────────────────────────────────

    def _on_router_event(self, event: Event) -> None:
        """路由器事件回调：收集事件到待播报队列 + 写日记。"""
        with self._events_lock:
            self._pending_events.append(event)
            # 最多保留 10 个
            if len(self._pending_events) > 10:
                self._pending_events = self._pending_events[-10:]

        # 写日记
        if self._diary:
            self._diary.add_event(
                text=event.text,
                category=event.category,
                mood=event.mood,
            )

        # 外部回调
        for cb in self._on_event_callbacks:
            try:
                cb(event)
            except Exception:
                pass

    def _tick_loop(self, interval: float) -> None:
        """后台 tick 循环。"""
        while not self._stop_event.is_set():
            try:
                if self._router:
                    events = self._router.tick()
                    # tick 返回的事件已通过回调处理
            except Exception:
                pass
            self._stop_event.wait(interval)


# 全局单例
world = WorldManager()