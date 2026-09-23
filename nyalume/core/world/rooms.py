"""房间角色映射：把文件路径翻译成猫娘世界的"空间"。

每个路径对应一个房间角色（如 pet.py → 客厅），
以及猫娘对这个空间的情感标签。
"""

from __future__ import annotations

import os
from typing import Any


# ── 核心路径 → 房间映射 ──────────────────────────────────
# key: 文件名或相对路径（相对于根目录）
# value: 房间信息 dict
ROOM_MAP: dict[str, dict[str, Any]] = {
    # 主要代码文件
    "pet.py": {
        "name": "客厅",
        "desc": "猫娘最常待的地方，也是家的入口",
        "importance": 5,
        "emoji": "🛋️",
    },
    "server.py": {
        "name": "厨房",
        "desc": "提供「食物」（API 服务）的地方",
        "importance": 4,
        "emoji": "🍳",
    },
    "run_pet.pyw": {
        "name": "大门",
        "desc": "家的入口，从这里进出",
        "importance": 4,
        "emoji": "🚪",
    },
    "cli.py": {
        "name": "门铃",
        "desc": "主人按门铃的方式",
        "importance": 2,
        "emoji": "🔔",
    },
    "persona_config.json": {
        "name": "日记本",
        "desc": "猫娘的性格档案，记录她是谁",
        "importance": 5,
        "emoji": "📔",
    },
    "pet_config.json": {
        "name": "备忘录",
        "desc": "桌面上贴的便利贴，记录各种设置",
        "importance": 3,
        "emoji": "📋",
    },
    ".env": {
        "name": "钥匙盒",
        "desc": "家的钥匙和密码，很重要",
        "importance": 5,
        "emoji": "🔑",
    },
    ".env.example": {
        "name": "钥匙模板",
        "desc": "钥匙的制作说明",
        "importance": 2,
        "emoji": "🗝️",
    },
    "requirements.txt": {
        "name": "购物清单",
        "desc": "家里需要的物资清单",
        "importance": 3,
        "emoji": "🛒",
    },
    "requirements-dev.txt": {
        "name": "装修清单",
        "desc": "装修时需要的特殊材料",
        "importance": 2,
        "emoji": "🔧",
    },
    ".gitignore": {
        "name": "储物间标签",
        "desc": "标记哪些东西不用放进仓库",
        "importance": 1,
        "emoji": "🏷️",
    },
    "README.md": {
        "name": "门牌",
        "desc": "家门口挂的牌子，介绍这个家",
        "importance": 3,
        "emoji": "🏠",
    },
    "start_pet.bat": {
        "name": "启动按钮",
        "desc": "一键启动整个家的按钮",
        "importance": 3,
        "emoji": "▶️",
    },
    "nyalume_blessings.md": {
        "name": "祝福墙",
        "desc": "墙上贴满了每天的祝福",
        "importance": 2,
        "emoji": "✨",
    },
    "pytest.ini": {
        "name": "质检手册",
        "desc": "检查家里有没有问题的标准",
        "importance": 2,
        "emoji": "✅",
    },
    "agent.db": {
        "name": "记忆柜",
        "desc": "存放所有记忆的柜子",
        "importance": 5,
        "emoji": "🧠",
    },
    # 目录级映射
    "nyalume/": {
        "name": "家的主体",
        "desc": "整个家的结构",
        "importance": 5,
        "emoji": "🏠",
    },
    "nyalume/core/": {
        "name": "心脏",
        "desc": "家的核心运转系统",
        "importance": 5,
        "emoji": "❤️",
    },
    "nyalume/core/agent.py": {
        "name": "大脑",
        "desc": "猫娘思考和决策的地方",
        "importance": 5,
        "emoji": "🧠",
    },
    "nyalume/core/memory.py": {
        "name": "记忆房间",
        "desc": "存放所有回忆的地方",
        "importance": 5,
        "emoji": "💭",
    },
    "nyalume/core/tools.py": {
        "name": "工具间",
        "desc": "放满了各种工具的房间",
        "importance": 4,
        "emoji": "🧰",
    },
    "nyalume/core/personas.py": {
        "name": "衣帽间",
        "desc": "猫娘换装和决定今天风格的地方",
        "importance": 3,
        "emoji": "👗",
    },
    "nyalume/core/daily_nyalume.py": {
        "name": "占卜台",
        "desc": "每天早上抽签看今日运势",
        "importance": 3,
        "emoji": "🔮",
    },
    "nyalume/core/llm.py": {
        "name": "通讯室",
        "desc": "跟外面世界通话的设备",
        "importance": 4,
        "emoji": "📡",
    },
    "nyalume/core/reminders.py": {
        "name": "闹钟架",
        "desc": "挂满了各种闹钟",
        "importance": 3,
        "emoji": "⏰",
    },
    "nyalume/core/skills.py": {
        "name": "技能树",
        "desc": "猫娘学会的各种技能",
        "importance": 3,
        "emoji": "🌟",
    },
    "nyalume/core/tracing.py": {
        "name": "监控室",
        "desc": "记录家里的运转日志",
        "importance": 2,
        "emoji": "📹",
    },
    "nyalume/core/vision.py": {
        "name": "望远镜",
        "desc": "猫娘用来看图片的眼睛",
        "importance": 2,
        "emoji": "🔭",
    },
    "nyalume/core/cloud_sync.py": {
        "name": "信鸽窝",
        "desc": "跟云端同步消息的信鸽",
        "importance": 2,
        "emoji": "🕊️",
    },
    "nyalume/frontends/": {
        "name": "门面",
        "desc": "家对外展示的部分",
        "importance": 4,
        "emoji": "🏪",
    },
    "nyalume/frontends/pet/": {
        "name": "阳台",
        "desc": "猫娘出现在主人桌面上的地方",
        "importance": 4,
        "emoji": "🌸",
    },
    "nyalume/frontends/pet/pet3d/": {
        "name": "3D 小窝",
        "desc": "猫娘的 3D 形态待的地方",
        "importance": 4,
        "emoji": "🎮",
    },
    "nyalume/frontends/web/": {
        "name": "会客厅",
        "desc": "主人通过网页来聊天的地方",
        "importance": 4,
        "emoji": "💬",
    },
    "nyalume/frontends/web/static/": {
        "name": "展示柜",
        "desc": "摆放各种好看的界面素材",
        "importance": 3,
        "emoji": "🖼️",
    },
    "tests/": {
        "name": "体检中心",
        "desc": "定期检查家的各个部分是否健康",
        "importance": 3,
        "emoji": "🏥",
    },
    "cloud_service/": {
        "name": "云端驿站",
        "desc": "跟远方通信的中转站",
        "importance": 3,
        "emoji": "☁️",
    },
    "docs/": {
        "name": "书房",
        "desc": "存放各种文档和说明",
        "importance": 3,
        "emoji": "📚",
    },
    "designs/": {
        "name": "画室",
        "desc": "存放设计稿和美术素材",
        "importance": 2,
        "emoji": "🎨",
    },
    "ideas/": {
        "name": "灵感墙",
        "desc": "贴满了各种奇思妙想",
        "importance": 2,
        "emoji": "💡",
    },
    "skills/": {
        "name": "技能书架",
        "desc": "放着可以学习的新技能书",
        "importance": 2,
        "emoji": "📖",
    },
    "models/": {
        "name": "模型仓库",
        "desc": "存放 3D 模型的大仓库",
        "importance": 3,
        "emoji": "🎭",
    },
    "motions/": {
        "name": "舞蹈室",
        "desc": "存放动作和舞蹈的地方",
        "importance": 2,
        "emoji": "💃",
    },
}


def guess_room(path: str) -> dict[str, Any]:
    """根据路径猜测一个房间角色（没有精确匹配时的兜底）。"""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()

    # 按扩展名兜底
    ext_rooms = {
        ".py": {"name": "代码间", "desc": "Python 代码文件", "importance": 3, "emoji": "🐍"},
        ".js": {"name": "代码间", "desc": "JavaScript 代码文件", "importance": 3, "emoji": "📜"},
        ".ts": {"name": "代码间", "desc": "TypeScript 代码文件", "importance": 3, "emoji": "📜"},
        ".json": {"name": "配置间", "desc": "配置文件", "importance": 2, "emoji": "⚙️"},
        ".md": {"name": "书架", "desc": "文档文件", "importance": 2, "emoji": "📝"},
        ".txt": {"name": "便签墙", "desc": "文本文件", "importance": 1, "emoji": "📝"},
        ".html": {"name": "橱窗", "desc": "网页文件", "importance": 2, "emoji": "🌐"},
        ".css": {"name": "调色板", "desc": "样式文件", "importance": 2, "emoji": "🎨"},
        ".bat": {"name": "开关面板", "desc": "启动脚本", "importance": 2, "emoji": "🔌"},
        ".cmd": {"name": "开关面板", "desc": "启动脚本", "importance": 2, "emoji": "🔌"},
        ".yaml": {"name": "配置间", "desc": "配置文件", "importance": 2, "emoji": "⚙️"},
        ".yml": {"name": "配置间", "desc": "配置文件", "importance": 2, "emoji": "⚙️"},
        ".toml": {"name": "配置间", "desc": "配置文件", "importance": 2, "emoji": "⚙️"},
        ".sql": {"name": "档案室", "desc": "数据库文件", "importance": 3, "emoji": "🗄️"},
        ".png": {"name": "画廊", "desc": "图片文件", "importance": 1, "emoji": "🖼️"},
        ".jpg": {"name": "画廊", "desc": "图片文件", "importance": 1, "emoji": "🖼️"},
        ".mp4": {"name": "影院", "desc": "视频文件", "importance": 1, "emoji": "🎬"},
    }
    if ext in ext_rooms:
        return ext_rooms[ext]

    return {"name": "未知房间", "desc": "还没探索过的空间", "importance": 1, "emoji": "❓"}


def resolve_room(path: str) -> dict[str, Any]:
    """把一个文件路径解析为房间信息。

    精确匹配 ROOM_MAP，匹配不到则逐级向上回退，
    最终兜底到 guess_room。
    """
    normalized = path.replace("\\", "/").strip("/")

    # 精确匹配
    if normalized in ROOM_MAP:
        return dict(ROOM_MAP[normalized])

    # 逐级回退：nyalume/core/agent.py → nyalume/core/ → nyalume/
    parts = normalized.split("/")
    for i in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:i]) + "/"
        if prefix in ROOM_MAP:
            return dict(ROOM_MAP[prefix])

    return guess_room(path)


def room_narrative(path: str, event_type: str = "visit") -> str:
    """把路径+事件类型翻译成猫娘视角的叙事。"""
    room = resolve_room(path)
    name = room["name"]
    emoji = room["emoji"]

    narratives = {
        "visit": [
            f"走进了{emoji} {name}",
            f"去{emoji} {name}看了看",
            f"路过{emoji} {name}",
        ],
        "modify": [
            f"{emoji} {name}好像被改造了",
            f"有人动了{emoji} {name}",
            f"{emoji} {name}变了样",
        ],
        "create": [
            f"{emoji} {name}多了一个新东西",
            f"在{emoji} {name}放了新物品",
        ],
        "delete": [
            f"{emoji} {name}少了一样东西",
            f"有人从{emoji} {name}拿走了什么",
        ],
    }

    import secrets
    options = narratives.get(event_type, narratives["visit"])
    return secrets.choice(options)