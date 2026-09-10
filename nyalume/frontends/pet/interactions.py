"""桌宠互动反馈：按触摸部位从本地台词库即时回应。"""

import random

_LINES: dict[str, list[str]] = {
    "head": ["喵呜～摸头好舒服。", "再揉揉耳朵也可以喵～", "主人的手好暖喵。"],
    "body": ["呀，好痒的喵！", "呼噜呼噜…这里是痒痒肉喵。", "摸摸可以，不许挠太久喵～"],
    "legs": ["腿腿被摸会没力气的喵…", "喂，差点站不稳啦喵！", "腿腿也要温柔一点喵。"],
    "miss": ["喵？戳到空气啦，我在这里～", "要摸哪里呀？", "差一点点就摸到啦喵。"],
}


def pick_line(region: str, session_id: str = "") -> str:
    """本地即时反馈，不改变任何关系数值。"""
    return random.choice(_LINES.get(region, _LINES["miss"]))
