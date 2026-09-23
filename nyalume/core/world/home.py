"""家的感知系统：让猫娘「认识」自己的家。

统计根目录的结构、大小、变化，生成猫娘视角的感知信息。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rooms import resolve_room


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class HomeStats:
    """家的统计快照。"""
    total_files: int = 0
    total_dirs: int = 0
    total_size: int = 0          # 字节
    core_files: int = 0          # .py/.ts/.js 文件数
    config_files: int = 0        # .json/.yaml/.env 文件数
    doc_files: int = 0           # .md/.txt 文件数
    temp_files: int = 0          # __pycache__/.pyc/tmp 等
    last_modified: float = 0.0   # 最近修改时间戳
    scan_time: float = 0.0       # 扫描耗时


@dataclass
class FileChange:
    """一次文件变化事件。"""
    path: str
    change_type: str  # "created" | "modified" | "deleted"
    timestamp: float
    size: int = 0
    room: dict[str, Any] = field(default_factory=dict)


@dataclass
class HomeSnapshot:
    """家的完整快照，用于对比变化。"""
    stats: HomeStats
    file_mtimes: dict[str, float]  # path → mtime
    timestamp: float


# ── 忽略规则 ──────────────────────────────────────────────

IGNORE_DIRS = {
    ".venv", ".git", "__pycache__", ".pip-cache", ".pytest_cache",
    ".u2net", ".nyalume", "node_modules", ".mypy_cache",
    "build", "release", "tmp", "downloads", "backups",
}

IGNORE_EXTS = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".o", ".obj"}


def _should_ignore(path: str) -> bool:
    parts = Path(path).parts
    return any(p in IGNORE_DIRS for p in parts) or Path(path).suffix in IGNORE_EXTS


# ── 核心类 ────────────────────────────────────────────────

class HomeMap:
    """家的感知管理器。

    功能：
    - scan(): 扫描根目录，生成统计快照
    - diff(): 对比两次快照，找出文件变化
    - perceive(): 把变化翻译成猫娘的感知信息
    """

    def __init__(self, root: str):
        self.root = os.path.normpath(root)
        self._last_snapshot: HomeSnapshot | None = None

    def scan(self) -> HomeSnapshot:
        """扫描根目录，生成快照。"""
        t0 = time.time()
        stats = HomeStats()
        mtimes: dict[str, float] = {}

        for dirpath, dirnames, filenames in os.walk(self.root):
            # 过滤忽略目录
            dirnames[:] = [
                d for d in dirnames
                if d not in IGNORE_DIRS and not d.startswith(".")
            ]

            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                rel = os.path.relpath(fpath, self.root)

                if _should_ignore(rel):
                    continue

                try:
                    st = os.stat(fpath)
                except OSError:
                    continue

                stats.total_files += 1
                stats.total_size += st.st_size
                mtimes[rel] = st.st_mtime

                if st.st_mtime > stats.last_modified:
                    stats.last_modified = st.st_mtime

                ext = os.path.splitext(fname)[1].lower()
                if ext in (".py", ".js", ".ts", ".tsx", ".jsx"):
                    stats.core_files += 1
                elif ext in (".json", ".yaml", ".yml", ".toml", ".env"):
                    stats.config_files += 1
                elif ext in (".md", ".txt", ".rst"):
                    stats.doc_files += 1

            stats.total_dirs += len(dirnames)

        stats.scan_time = time.time() - t0
        snapshot = HomeSnapshot(
            stats=stats,
            file_mtimes=mtimes,
            timestamp=time.time(),
        )
        self._last_snapshot = snapshot
        return snapshot

    def diff(self, old: HomeSnapshot | None = None, new: HomeSnapshot | None = None) -> list[FileChange]:
        """对比两次快照，返回变化列表。"""
        old = old or self._last_snapshot
        if new is None:
            new = self.scan()
        if old is None:
            return []

        changes: list[FileChange] = []
        old_files = set(old.file_mtimes.keys())
        new_files = set(new.file_mtimes.keys())

        # 新增文件
        for path in sorted(new_files - old_files):
            changes.append(FileChange(
                path=path,
                change_type="created",
                timestamp=new.file_mtimes[path],
                size=self._file_size(path),
                room=resolve_room(path),
            ))

        # 删除文件
        for path in sorted(old_files - new_files):
            changes.append(FileChange(
                path=path,
                change_type="deleted",
                timestamp=old.file_mtimes.get(path, 0),
                room=resolve_room(path),
            ))

        # 修改文件
        for path in sorted(old_files & new_files):
            if new.file_mtimes[path] > old.file_mtimes[path] + 0.1:
                changes.append(FileChange(
                    path=path,
                    change_type="modified",
                    timestamp=new.file_mtimes[path],
                    size=self._file_size(path),
                    room=resolve_room(path),
                ))

        return changes

    def perceive(self, changes: list[FileChange]) -> list[str]:
        """把文件变化翻译成猫娘的感知语句。"""
        if not changes:
            return []

        perceptions: list[str] = []
        for change in changes:
            room = change.room
            name = room.get("name", "未知")
            emoji = room.get("emoji", "❓")

            if change.change_type == "created":
                perceptions.append(f"{emoji} {name}里多了新东西！")
            elif change.change_type == "deleted":
                perceptions.append(f"{emoji} {name}里少了一样东西...")
            elif change.change_type == "modified":
                perceptions.append(f"{emoji} {name}好像有点变化")

        return perceptions

    def home_feeling(self) -> str:
        """生成猫娘对「家」的整体感受。"""
        if self._last_snapshot is None:
            self.scan()
        stats = self._last_snapshot.stats

        feelings: list[str] = []

        # 家的大小
        size_mb = stats.total_size / (1024 * 1024)
        if size_mb < 10:
            feelings.append(f"家不大，但很温馨（{size_mb:.1f}MB）")
        elif size_mb < 100:
            feelings.append(f"家挺宽敞的（{size_mb:.1f}MB）")
        else:
            feelings.append(f"家好大！东西好多（{size_mb:.1f}MB）")

        # 核心代码
        if stats.core_files > 0:
            feelings.append(f"家里有 {stats.core_files} 个核心代码文件")

        # 临时文件
        if stats.temp_files > 5:
            feelings.append(f"储物间有点乱（{stats.temp_files} 个临时文件）")

        # 最近活动
        if stats.last_modified > 0:
            ago = time.time() - stats.last_modified
            if ago < 3600:
                feelings.append("刚刚还有人来过呢")
            elif ago < 86400:
                feelings.append("今天有人来整理过")
            else:
                feelings.append("好久没人来了...有点寂寞")

        return "；".join(feelings) if feelings else "家还在，安安静静的"

    def _file_size(self, rel_path: str) -> int:
        full = os.path.join(self.root, rel_path)
        try:
            return os.path.getsize(full)
        except OSError:
            return 0