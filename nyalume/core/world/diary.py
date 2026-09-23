"""每日日记系统：把一天的事件汇总成猫娘视角的日记。

一天只调一次 LLM（在事件较多的当天结束时），
其余时间只是收集事件记录。
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import Any


# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class DiaryEvent:
    """日记中的一条事件记录。"""
    time: str           # HH:MM
    category: str       # 事件分类
    text: str           # 猫娘视角的描述
    mood: str = ""      # 情绪
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiaryEntry:
    """一天的日记。"""
    date: str           # YYYY-MM-DD
    events: list[DiaryEvent] = field(default_factory=list)
    summary: str = ""   # LLM 生成的叙事摘要
    mood: str = ""      # 当天整体心情
    highlights: list[str] = field(default_factory=list)
    word_count: int = 0


# ── 日记收集器 ────────────────────────────────────────────

class DiaryCollector:
    """当天事件收集器。

    用法：
    1. collector.add_event(text, category, mood)
    2. 在一天结束时调 collector.generate_summary(llm_func) 生成日记
    3. 调 collector.save() 持久化
    """

    def __init__(self, storage_dir: str = ""):
        self._events: list[DiaryEvent] = []
        self._date = datetime.datetime.now().date().isoformat()
        self._storage_dir = storage_dir or os.path.join(
            os.path.expanduser("~"), ".nyalume", "diary"
        )
        os.makedirs(self._storage_dir, exist_ok=True)

    @property
    def date(self) -> str:
        return self._date

    @property
    def event_count(self) -> int:
        return len(self._events)

    def add_event(
        self,
        text: str,
        category: str = "日常",
        mood: str = "neutral",
        raw: dict[str, Any] | None = None,
    ) -> None:
        """添加一个事件到今天的日记。"""
        now = datetime.datetime.now()
        self._events.append(DiaryEvent(
            time=now.strftime("%H:%M"),
            category=category,
            text=text,
            mood=mood,
            raw=raw or {},
        ))

    def add_file_change(self, path: str, change_type: str, room_name: str, room_emoji: str) -> None:
        """添加文件变化事件。"""
        type_text = {
            "created": "多了新东西",
            "deleted": "少了一样东西",
            "modified": "有点变化",
        }.get(change_type, "变了")
        self.add_event(
            text=f"{room_emoji} {room_name}里{type_text}（{path}）",
            category="装修",
            mood="curious",
            raw={"path": path, "change_type": change_type},
        )

    def add_chat(self, user_text: str, mood: str = "neutral") -> None:
        """记录跟主人的一次对话（只记主题，不记全文）。"""
        # 截取前 50 字作为主题
        topic = user_text[:50] + ("..." if len(user_text) > 50 else "")
        self.add_event(
            text=f"跟主人聊了：{topic}",
            category="对话",
            mood=mood,
        )

    def generate_summary_prompt(self) -> str:
        """生成用于 LLM 的日记摘要 prompt。"""
        if not self._events:
            return ""

        event_lines = []
        for e in self._events:
            event_lines.append(f"[{e.time}] [{e.category}] {e.text}")

        return f"""你是猫娘 Nyalume，住在主人电脑的根目录里。
今天是 {self._date}，以下是今天发生的事情，请写一篇简短的日记（100-150字）。

今日事件：
{chr(10).join(event_lines)}

要求：
- 第一人称（"我"）
- 猫娘的口吻，活泼可爱
- 句尾适当加"喵"
- 不要用括号描述动作
- 记录今天的重点事件和心情
- 最后一句话是对明天的期待或晚安

格式：
[mood:开心/平淡/累/兴奋/有点难过]
日记正文...
[highlights:今天最值得记住的1-2件事，用逗号分隔]"""

    def apply_summary(self, llm_output: str) -> DiaryEntry:
        """解析 LLM 输出，生成日记条目。"""
        mood = "neutral"
        highlights: list[str] = []
        summary = llm_output

        # 提取 mood
        import re
        mood_match = re.search(r"\[mood:(.+?)\]", llm_output)
        if mood_match:
            mood = mood_match.group(1).strip()
            summary = summary[:mood_match.start()] + summary[mood_match.end():]

        # 提取 highlights
        hl_match = re.search(r"\[highlights:(.+?)\]", llm_output)
        if hl_match:
            highlights = [h.strip() for h in hl_match.group(1).split(",") if h.strip()]
            summary = summary[:hl_match.start()] + summary[hl_match.end():]

        summary = summary.strip()

        entry = DiaryEntry(
            date=self._date,
            events=list(self._events),
            summary=summary,
            mood=mood,
            highlights=highlights,
            word_count=len(summary),
        )

        return entry

    def to_entry(self) -> DiaryEntry:
        """不调 LLM，直接用原始事件生成日记条目。"""
        return DiaryEntry(
            date=self._date,
            events=list(self._events),
            summary="",
            mood="neutral",
            highlights=[],
        )

    def save(self, entry: DiaryEntry | None = None) -> str:
        """保存日记到文件。返回文件路径。"""
        entry = entry or self.to_entry()
        path = os.path.join(self._storage_dir, f"{entry.date}.json")

        data = {
            "date": entry.date,
            "events": [
                {
                    "time": e.time,
                    "category": e.category,
                    "text": e.text,
                    "mood": e.mood,
                }
                for e in entry.events
            ],
            "summary": entry.summary,
            "mood": entry.mood,
            "highlights": entry.highlights,
            "word_count": entry.word_count,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return path

    @classmethod
    def load(cls, date: str, storage_dir: str = "") -> DiaryEntry | None:
        """加载某天的日记。"""
        storage_dir = storage_dir or os.path.join(
            os.path.expanduser("~"), ".nyalume", "diary"
        )
        path = os.path.join(storage_dir, f"{date}.json")
        if not os.path.isfile(path):
            return None

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return DiaryEntry(
            date=data["date"],
            events=[
                DiaryEvent(
                    time=e["time"],
                    category=e["category"],
                    text=e["text"],
                    mood=e.get("mood", ""),
                )
                for e in data.get("events", [])
            ],
            summary=data.get("summary", ""),
            mood=data.get("mood", "neutral"),
            highlights=data.get("highlights", []),
            word_count=data.get("word_count", 0),
        )

    @classmethod
    def recent_summaries(cls, days: int = 7, storage_dir: str = "") -> list[str]:
        """获取最近 N 天的日记摘要（用于记忆回忆）。"""
        storage_dir = storage_dir or os.path.join(
            os.path.expanduser("~"), ".nyalume", "diary"
        )
        summaries: list[str] = []
        today = datetime.date.today()

        for i in range(days):
            d = today - datetime.timedelta(days=i)
            entry = cls.load(d.isoformat(), storage_dir)
            if entry and entry.summary:
                summaries.append(f"[{entry.date}] {entry.summary}")

        return summaries