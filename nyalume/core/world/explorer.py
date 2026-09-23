"""城镇探索系统：让猫娘「认识」根目录之外的世界。

当主人打开文件夹、下载文件、安装软件时，
猫娘会以自己的视角「看到」并记住。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# ── 城镇地标映射 ──────────────────────────────────────────

LANDMARKS: dict[str, dict[str, Any]] = {
    # Windows 常见目录
    "Desktop": {
        "name": "窗台",
        "desc": "主人站在窗台边看风景的地方，有时候会放一些随手的东西",
        "emoji": "🪟",
    },
    "Documents": {
        "name": "图书馆",
        "desc": "堆满了各种文档和资料的大图书馆",
        "emoji": "📚",
    },
    "Downloads": {
        "name": "快递站",
        "desc": "新东西总是先到这里，然后被主人取走",
        "emoji": "📦",
    },
    "Pictures": {
        "name": "画廊",
        "desc": "挂满了各种好看的图片",
        "emoji": "🖼️",
    },
    "Videos": {
        "name": "影院",
        "desc": "可以看电影的地方",
        "emoji": "🎬",
    },
    "Music": {
        "name": "音乐厅",
        "desc": "回荡着各种音乐",
        "emoji": "🎵",
    },
    "Program Files": {
        "name": "商店街",
        "desc": "各种各样的店铺（软件）",
        "emoji": "🏪",
    },
    "Program Files (x86)": {
        "name": "老商店街",
        "desc": "比较老的店铺（32位软件）",
        "emoji": "🏚️",
    },
    "Windows": {
        "name": "市政厅",
        "desc": "整个城市的管理中心，不能随便动",
        "emoji": "🏛️",
    },
    "Users": {
        "name": "居民区",
        "desc": "住着各种用户的地方",
        "emoji": "🏘️",
    },
    "AppData": {
        "name": "暗巷",
        "desc": "藏在深处的小巷子，各种应用的秘密基地",
        "emoji": "🔦",
    },
    "Temp": {
        "name": "临时摊位",
        "desc": "用完就扔的临时东西",
        "emoji": "🗑️",
    },
    "steam": {
        "name": "游戏城",
        "desc": "好多好多游戏！主人经常来这里",
        "emoji": "🎮",
    },
    "steamapps": {
        "name": "游戏仓库",
        "desc": "游戏们住的地方",
        "emoji": "🎮",
    },
}


@dataclass
class ExploredPlace:
    """一个已探索的地方。"""
    path: str
    name: str
    desc: str
    emoji: str
    first_visit: str  # ISO date
    visit_count: int = 1
    notes: list[str] = field(default_factory=list)


class TownExplorer:
    """城镇探索管理器。

    记录猫娘「去过的」地方，生成探索叙事。
    """

    def __init__(self):
        self._explored: dict[str, ExploredPlace] = {}

    def visit(self, path: str) -> tuple[bool, ExploredPlace]:
        """记录一次「到达」某地。

        返回 (是否首次到达, 地点信息)。
        """
        import datetime

        normalized = os.path.normpath(path)
        key = normalized.lower()

        if key in self._explored:
            place = self._explored[key]
            place.visit_count += 1
            return False, place

        # 识别地标
        landmark = self._identify_landmark(normalized)
        place = ExploredPlace(
            path=normalized,
            name=landmark["name"],
            desc=landmark["desc"],
            emoji=landmark["emoji"],
            first_visit=datetime.date.today().isoformat(),
        )
        self._explored[key] = place
        return True, place

    def _identify_landmark(self, path: str) -> dict[str, Any]:
        """根据路径识别地标。"""
        parts = path.replace("\\", "/").split("/")

        # 从路径末尾往前匹配
        for part in reversed(parts):
            if part in LANDMARKS:
                return dict(LANDMARKS[part])

        # 按文件夹名模糊匹配
        for key, landmark in LANDMARKS.items():
            if key.lower() in path.lower():
                return dict(landmark)

        return {
            "name": "未知区域",
            "desc": "还没探索过的地方",
            "emoji": "❓",
        }

    def get_narrative(self, path: str) -> str:
        """生成到达某地的猫娘叙事。"""
        is_new, place = self.visit(path)

        if is_new:
            narratives = [
                f"哇！第一次来{place.emoji} {place.name}！{place.desc}喵~",
                f"这里就是{place.emoji} {place.name}吗？{place.desc}，好新奇喵！",
                f"（东张西望）{place.emoji} {place.name}...{place.desc}，记住了喵~",
            ]
        else:
            if place.visit_count <= 3:
                narratives = [
                    f"又来{place.emoji} {place.name}了~",
                    f"这里是{place.emoji} {place.name}，上次来过喵~",
                ]
            else:
                narratives = [
                    f"{place.emoji} {place.name}，熟悉的地方~",
                    f"（轻车熟路地走进{place.emoji} {place.name}）",
                ]

        import secrets
        return secrets.choice(narratives)

    def explored_list(self) -> list[dict[str, Any]]:
        """返回所有已探索的地方（用于记忆/展示）。"""
        return [
            {
                "path": p.path,
                "name": f"{p.emoji} {p.name}",
                "desc": p.desc,
                "visits": p.visit_count,
                "first_visit": p.first_visit,
            }
            for p in sorted(
                self._explored.values(),
                key=lambda x: x.visit_count,
                reverse=True,
            )
        ]