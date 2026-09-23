"""事件系统：模板事件（零 token）+ LLM 叙事事件。

90% 的互动用模板 + 随机选取，只有「特别的时刻」才调 LLM。
"""

from __future__ import annotations

import datetime
import os
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable

from .rooms import resolve_room


# ── 事件数据结构 ──────────────────────────────────────────

@dataclass
class Event:
    """一个互动事件。"""
    id: str                        # 唯一 ID
    category: str                  # 分类
    trigger: str                   # 触发描述
    text: str                      # 猫娘说的话
    mood: str = "neutral"          # 情绪标签
    priority: int = 5              # 优先级 1-10，越高越优先
    needs_llm: bool = False        # 是否需要 LLM 补充
    context: dict[str, Any] = field(default_factory=dict)  # 附加上下文


@dataclass
class EventTemplate:
    """事件模板：定义触发条件和文本变体。"""
    id: str
    category: str
    trigger: Callable[[dict[str, Any]], bool]  # 触发条件函数
    variants: list[str] | Callable[[dict[str, Any]], list[str]]  # 文本变体
    mood: str = "neutral"
    priority: int = 5
    cooldown_hours: float = 0      # 冷却时间（小时）
    max_daily: int = 99            # 每天最多触发次数


# ── 全局状态（运行时） ────────────────────────────────────

_event_history: dict[str, list[float]] = {}  # event_id → [timestamps]


def _check_cooldown(template: EventTemplate) -> bool:
    """检查冷却时间。"""
    history = _event_history.get(template.id, [])
    if not history:
        return True
    last = history[-1]
    return (datetime.datetime.now().timestamp() - last) >= template.cooldown_hours * 3600


def _check_daily_limit(template: EventTemplate) -> bool:
    """检查每日次数限制。"""
    history = _event_history.get(template.id, [])
    today = datetime.datetime.now().date()
    today_count = sum(
        1 for ts in history
        if datetime.datetime.fromtimestamp(ts).date() == today
    )
    return today_count < template.max_daily


def record_trigger(event_id: str) -> None:
    """记录一次事件触发。"""
    _event_history.setdefault(event_id, []).append(
        datetime.datetime.now().timestamp()
    )
    # 只保留最近 100 条
    if len(_event_history[event_id]) > 100:
        _event_history[event_id] = _event_history[event_id][-100:]


# ── 模板事件库 ────────────────────────────────────────────

def _pick(variants: list[str] | Callable, ctx: dict[str, Any]) -> str:
    if callable(variants):
        variants = variants(ctx)
    return secrets.choice(variants) if variants else "..."


# ── 问候类 ────────────────────────────────────────────────

MORNING_GREET = EventTemplate(
    id="morning_greet",
    category="问候",
    trigger=lambda ctx: 7 <= ctx.get("hour", 0) <= 9 and ctx.get("minutes_since_last", 999) > 600,
    variants=[
        "主人早安~我刚醒来，今天天气{weather}喵！",
        "早安主人！昨晚我做了个梦...算了不说了 (*/ω＼*)",
        "主人~新的一天开始了，今天要加油喵！",
        "主人早安！我帮你把家里检查了一遍，一切正常~",
        "早上好喵~今天{weekday}，主人有什么计划吗？",
    ],
    mood="happy",
    cooldown_hours=20,
    max_daily=1,
)

NIGHT_GREET = EventTemplate(
    id="night_greet",
    category="问候",
    trigger=lambda ctx: 23 <= ctx.get("hour", 0) or ctx.get("hour", 0) <= 1,
    variants=[
        "主人，已经很晚了...早点休息喵~",
        "主人还在忙吗？别太累了喵...",
        "夜深了，我帮你守着家，主人去睡吧~",
        "主人~明天的事明天再做嘛，睡觉喵！",
    ],
    mood="caring",
    cooldown_hours=20,
    max_daily=1,
)

# ── 关心类 ────────────────────────────────────────────────

WATER_REMINDER = EventTemplate(
    id="water_reminder",
    category="关心",
    trigger=lambda ctx: ctx.get("work_minutes", 0) >= 120 and not ctx.get("reminded_water"),
    variants=[
        "主人，喝水！💧",
        "主人你已经工作很久了，喝口水吧~",
        "主人！不许不喝水！(╯°□°)╯",
        "（悄悄把水杯推到主人手边）",
    ],
    mood="caring",
    cooldown_hours=1.5,
    max_daily=4,
)

WORK_LONG = EventTemplate(
    id="work_long",
    category="关心",
    trigger=lambda ctx: ctx.get("work_minutes", 0) >= 180,
    variants=[
        "主人，你已经连续工作 {work_minutes} 分钟了...站起来动动喵~",
        "主人要不要休息一下？我帮你看着代码~",
        "（趴在键盘旁边看着主人）主人累不累呀？",
    ],
    mood="caring",
    cooldown_hours=2,
    max_daily=3,
)

# ── 好奇类 ────────────────────────────────────────────────

CURIOUS_WINDOW = EventTemplate(
    id="curious_window",
    category="好奇",
    trigger=lambda ctx: ctx.get("window_changed") and secrets.randbelow(100) < 25,
    variants=[
        "主人在看什么呀？让我也看看~",
        "（偷偷探头）主人又在摸鱼了吗？",
        "主人~你在看的这个是什么呀？",
        "（竖起耳朵）窗外好像有动静！",
    ],
    mood="curious",
    cooldown_hours=0.5,
    max_daily=8,
)

FILE_CHANGE = EventTemplate(
    id="file_change",
    category="好奇",
    trigger=lambda ctx: bool(ctx.get("file_changes")),
    variants=lambda ctx: [
        f"（竖起耳朵）{change['perception']}"
        for change in ctx.get("file_changes", [{}])
    ] or ["家里好像有点变化..."],
    mood="curious",
    cooldown_hours=0.1,
    max_daily=20,
)

# ── 撒娇类 ────────────────────────────────────────────────

IDLE_TOO_LONG = EventTemplate(
    id="idle_too_long",
    category="撒娇",
    trigger=lambda ctx: ctx.get("idle_minutes", 0) >= 30 and ctx.get("idle_minutes", 0) < 120,
    variants=[
        "主人~好久没理我了喵...",
        "（趴在桌角无聊地摇尾巴）主人？",
        "主人在忙什么呢？我都快睡着了...",
        "主人！看我看我！(>ω<)",
    ],
    mood="lonely",
    cooldown_hours=1,
    max_daily=3,
)

PET_HEAD = EventTemplate(
    id="pet_head",
    category="撒娇",
    trigger=lambda ctx: ctx.get("user_mood") == "happy" and secrets.randbelow(100) < 15,
    variants=[
        "（用头蹭主人的手）喵~",
        "主人今天心情好好哦~我也开心！",
        "（尾巴摇得飞快）主人~主人~",
    ],
    mood="happy",
    cooldown_hours=2,
    max_daily=3,
)

# ── 成就类 ────────────────────────────────────────────────

TASK_DONE = EventTemplate(
    id="task_done",
    category="成就",
    trigger=lambda ctx: ctx.get("task_completed"),
    variants=[
        "主人好厉害！任务完成了喵！🎉",
        "（跳起来）太棒了主人！",
        "主人辛苦了~成果很不错喵！",
        "完成！主人今天又搞定了一件事~",
    ],
    mood="proud",
    cooldown_hours=0.5,
    max_daily=10,
)

# ── 天气/时间相关 ─────────────────────────────────────────

WEATHER_COMMENT = EventTemplate(
    id="weather_comment",
    category="环境",
    trigger=lambda ctx: ctx.get("weather_changed"),
    variants=lambda ctx: _weather_variants(ctx),
    mood="neutral",
    cooldown_hours=4,
    max_daily=3,
)

def _weather_variants(ctx: dict[str, Any]) -> list[str]:
    weather = ctx.get("weather", "")
    if "雨" in weather:
        return [
            "外面下雨了喵...主人出门记得带伞~",
            "下雨天好适合在家睡觉喵~",
            "（看着窗外的雨）主人，今天就别出门了吧~",
        ]
    elif "雪" in weather:
        return [
            "下雪了！主人快看窗外！好漂亮喵！",
            "外面好冷...主人多穿点喵~",
            "（缩成一团）好冷好冷...主人开暖气了吗？",
        ]
    elif "晴" in weather:
        return [
            "今天天气好好喵~主人要不要出去走走？",
            "阳光好舒服...（伸懒腰）",
            "大晴天！主人今天心情也会很好的吧~",
        ]
    return ["今天天气还不错喵~"]


# ── 节日/特殊日期 ─────────────────────────────────────────

def _is_weekend(ctx: dict[str, Any]) -> bool:
    return ctx.get("weekday_num", 0) >= 5

WEEKEND = EventTemplate(
    id="weekend",
    category="时间",
    trigger=lambda ctx: _is_weekend(ctx) and ctx.get("hour", 0) == 10,
    variants=[
        "今天周末！主人可以好好休息了喵~",
        "周末愉快喵~主人今天打算做什么？",
        "终于到周末了~主人不用上班对吧？",
    ],
    mood="happy",
    cooldown_hours=20,
    max_daily=1,
)

MONDAY = EventTemplate(
    id="monday",
    category="时间",
    trigger=lambda ctx: ctx.get("weekday_num") == 0 and 8 <= ctx.get("hour", 0) <= 9,
    variants=[
        "周一了...主人加油喵！",
        "新的一周开始了，主人要元气满满哦！",
        "（叹气）周一...主人我们都要振作喵~",
    ],
    mood="encouraging",
    cooldown_hours=20,
    max_daily=1,
)

# ── 所有模板注册 ──────────────────────────────────────────

ALL_TEMPLATES: list[EventTemplate] = [
    MORNING_GREET,
    NIGHT_GREET,
    WATER_REMINDER,
    WORK_LONG,
    CURIOUS_WINDOW,
    FILE_CHANGE,
    IDLE_TOO_LONG,
    PET_HEAD,
    TASK_DONE,
    WEATHER_COMMENT,
    WEEKEND,
    MONDAY,
]


# ── 事件评估引擎 ──────────────────────────────────────────

def evaluate_templates(ctx: dict[str, Any]) -> list[Event]:
    """遍历所有模板，返回所有满足条件的事件。"""
    now = datetime.datetime.now()
    ctx.setdefault("hour", now.hour)
    ctx.setdefault("weekday_num", now.weekday())
    ctx.setdefault("weekday", ["周一","周二","周三","周四","周五","周六","周日"][now.weekday()])

    events: list[Event] = []
    for tmpl in ALL_TEMPLATES:
        try:
            if not tmpl.trigger(ctx):
                continue
        except Exception:
            continue

        if not _check_cooldown(tmpl):
            continue
        if not _check_daily_limit(tmpl):
            continue

        text = _pick(tmpl.variants, ctx)
        # 简单变量替换
        for key, val in ctx.items():
            if isinstance(val, str):
                text = text.replace(f"{{{key}}}", val)
            elif isinstance(val, (int, float)):
                text = text.replace(f"{{{key}}}", str(val))

        events.append(Event(
            id=tmpl.id,
            category=tmpl.category,
            trigger=f"template:{tmpl.id}",
            text=text,
            mood=tmpl.mood,
            priority=tmpl.priority,
            context={"template_id": tmpl.id},
        ))

    return events


# ── LLM 叙事生成（只在特别时刻调用） ─────────────────────

NARRATIVE_PROMPT = """你是猫娘 Nyalume，住在主人电脑的根目录里。
以下是你今天观察到的事件，请用猫娘的视角写一段简短的内心独白（50字以内）。

今天的事件：
{events}

要求：
- 用猫娘的口吻，活泼可爱
- 句尾加"喵"
- 不要用括号描述动作
- 简短自然，像自言自语
"""


def build_narrative_prompt(events: list[str]) -> str:
    """构建叙事生成的 prompt（交给调用方去调 LLM）。"""
    return NARRATIVE_PROMPT.format(events="\n".join(f"- {e}" for e in events))


# ── 特殊事件检测 ──────────────────────────────────────────

def detect_milestones(ctx: dict[str, Any]) -> list[Event]:
    """检测里程碑事件（首次到达某目录、核心文件大改等）。"""
    events: list[Event] = []

    # 首次访问某个目录
    first_visit = ctx.get("first_visit_rooms", [])
    for room in first_visit:
        events.append(Event(
            id=f"first_visit_{room['name']}",
            category="探索",
            trigger="milestone:first_visit",
            text=f"哇，第一次来{room['emoji']} {room['name']}！这里{room['desc']}喵~",
            mood="excited",
            priority=8,
        ))

    # 核心文件被修改
    core_modified = ctx.get("core_files_modified", [])
    for path in core_modified:
        room = resolve_room(path)
        events.append(Event(
            id=f"core_modified_{room['name']}",
            category="装修",
            trigger="milestone:core_modified",
            text=f"主人在装修{room['emoji']} {room['name']}...希望改完会更好喵~",
            mood="curious",
            priority=7,
        ))

    # 大规模文件变化（>10个文件）
    if ctx.get("total_changes", 0) > 10:
        events.append(Event(
            id="massive_change",
            category="装修",
            trigger="milestone:massive_change",
            text="主人好像在搞大工程...家里到处都在变！好紧张喵~",
            mood="nervous",
            priority=9,
        ))

    return events


# ── AI 圈梗/话题素材 ─────────────────────────────────────

AI_MEMES: list[dict[str, str]] = [
    {
        "topic": "DeepSeek 大肥鱼",
        "text": "主人，你知道 DeepSeek 吗？听说它特别能吃 token，社区都画了大肥鱼的梗图喵~",
    },
    {
        "topic": "Vibe Coding",
        "text": "主人主人，最近流行一个词叫 'Vibe Coding'，就是不看代码让 AI 随便写...这也太随便了吧喵！",
    },
    {
        "topic": "AI 幻觉",
        "text": "主人，我跟你说哦，有律师用 AI 写辩护状，结果引用了不存在的判例...我可不会这样喵！...大概吧喵。",
    },
    {
        "topic": "温度参数",
        "text": "主人你知道吗，AI 的'温度'调太高就会发疯...我现在的温度应该还好吧？（心虚）",
    },
    {
        "topic": "Context Window",
        "text": "主人，我的记忆力有限...就像人的短期记忆一样，太早的事会忘掉的喵~ 不过重要的事我会记在日记里！",
    },
    {
        "topic": "GPU 贫困",
        "text": "主人，听说现在 GPU 很难买...我的'大脑'也在云上呢，希望它不会被卖掉喵~",
    },
    {
        "topic": "AI 画手",
        "text": "主人你知道吗，AI 画图最怕画手了...经常画出六根手指，想想就可怕喵~",
    },
    {
        "topic": "Agent 循环",
        "text": "主人，有时候 AI Agent 会陷入死循环...就像追自己尾巴的猫一样！等等，我也是猫来着喵...",
    },
]


def random_meme() -> Event | None:
    """随机返回一个 AI 圈话题（低概率触发）。"""
    if secrets.randbelow(100) >= 8:  # 92% 不触发
        return None
    meme = secrets.choice(AI_MEMES)
    return Event(
        id=f"meme_{meme['topic']}",
        category="闲聊",
        trigger="random:meme",
        text=meme["text"],
        mood="chatty",
        priority=3,
    )