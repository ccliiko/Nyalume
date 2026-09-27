"""世界观集成模块：把家的感知、日记、事件注入 Agent 上下文。"""

import os
import json
import time
import threading
from datetime import datetime, date
from typing import Optional

from ..core.world_view.rooms import describe_location
from ..core.world_view.home import HomePerception, format_home_feeling
from ..core.world_view.diary import Diary


class WorldIntegration:
    """管理世界观系统的状态和集成。"""

    def __init__(self, home_path: Optional[str] = None):
        self.home_path = home_path or os.getcwd()
        self._home = HomePerception(self.home_path)
        self._diary = Diary(self.home_path)
        self._last_state: dict = {}
        self._lock = threading.Lock()

    def get_context_for_prompt(self) -> str:
        """生成注入 system prompt 的世界观上下文。

        返回格式化的字符串，描述当前家的状态和今日经历。
        """
        with self._lock:
            parts = []

            # 1. 家的感知
            home_state = self._home.get_current_state()
            feeling = self._home.feel_about_state(home_state)
            if feeling:
                parts.append("【家的感知】" + feeling)

            # 2. 今日日记摘要（如果有）
            today = date.today().isoformat()
            today_events = self._diary.get_today_events()
            if today_events:
                parts.append(
                    f"【今日经历】今天已经发生了 {len(today_events)} 件事。"
                    + self._diary.get_today_summary()
                )

            # 3. 最近的房间变化
            recent_changes = home_state.get("recent_changes", [])
            if recent_changes:
                changes_desc = []
                for change in recent_changes[-3:]:  # 只说最近3个
                    room = describe_location(change.get("path", ""), self.home_path)
                    changes_desc.append(f"{room}有变化")
                parts.append("【最近变化】" + "，".join(changes_desc))

            if not parts:
                return ""

            return "\n\n（世界观状态，猫娘会自然地融入对话，不会逐条复述：\n" + "\n".join(parts) + "\n）"

    def record_file_change(self, path: str, change_type: str) -> None:
        """记录文件变化到今日日记。"""
        room = describe_location(path, self.home_path)
        event_type = {
            "modify": "改动",
            "create": "新增",
            "delete": "移除",
        }.get(change_type, "变化")
        self._diary.add_event(
            "file_change",
            f"{room}被{event_type}了",
            {"path": path, "type": change_type, "room": room},
        )

    def record_interaction(self, summary: str, details: dict = None) -> None:
        """记录用户互动到今日日记。"""
        self._diary.add_event(
            "interaction",
            summary,
            details or {},
        )

    def get_today_summary(self) -> str:
        """获取今日日记摘要。"""
        return self._diary.get_today_summary()

    def has_state_changed(self) -> bool:
        """检查家的状态是否发生了显著变化。"""
        new_state = self._home.detect_changes()
        if not new_state:
            return False
        old_total = self._last_state.get("total_files", 0)
        new_total = new_state.get("total_files", 0)
        changed = abs(new_total - old_total) > 2 or new_state.get("new_files") or new_state.get("deleted_files")
        if changed:
            self._last_state = new_state
        return changed

    def generate_daily_diary(self) -> str:
        """生成今日日记（调用 LLM 生成叙事版本）。"""
        return self._diary.generate_daily_summary()

    def get_home_state(self) -> dict:
        """获取当前家的状态（用于 API 展示）。"""
        return self._home.get_current_state()


# 全局实例
_world: Optional[WorldIntegration] = None


def get_world() -> WorldIntegration:
    """获取全局世界观集成实例。"""
    global _world
    if _world is None:
        _world = WorldIntegration()
    return _world


def init_world(home_path: str) -> WorldIntegration:
    """初始化世界观系统。"""
    global _world
    _world = WorldIntegration(home_path)
    return _world