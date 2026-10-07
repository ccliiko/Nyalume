"""每日一次的 nyalume 人格抽取。"""

import datetime
import os
import secrets

from . import memory

RECORD_PATH = os.path.join(memory._PROJECT_ROOT, "nyalume_blessings.md")

from .daily_content import PROFILES, BLESSINGS, profile_for_score, profile_by_key


def _today() -> str:
    return datetime.datetime.now().astimezone().date().isoformat()


def _public(record: dict, new: bool = False) -> dict:
    profile = profile_by_key(record["profile"]) or profile_for_score(record["score"])
    keyword = (record.get("keyword") or "").strip() or profile["name"].replace(" Nyalume", "")
    return {
        "drawn": True, "date": record["day"], "score": record["score"],
        "profile": profile["key"], "name": profile["name"],
        "tone": profile["tone"], "blessing": record["blessing"], "new": new,
        "finish": profile["finish"],
        "keyword": keyword, "source": record.get("source") or "Nyalume",
        "liked": bool(record.get("liked")), "collected": bool(record.get("collected")),
        "viewed": bool(record.get("viewed")), "obtained_at": record["created_at"],
        "card_version": record.get("card_version") or "v1",
        "portrait_url": f"/api/daily-nyalume/portrait/{profile['key']}",
    }


def _pick_blessing() -> dict:
    used = {row.get("keyword") for row in memory.list_daily_nyalume()}
    available = [item for item in BLESSINGS if item["keyword"] not in used]
    pool = available or list(BLESSINGS)
    chinese = [item for item in pool if item.get("language", "zh") == "zh"]
    other = [item for item in pool if item.get("language", "zh") != "zh"]
    # 非中文保留为偶遇彩蛋，常规抽取约 95% 显示中文。
    if other and secrets.randbelow(100) < 5:
        return secrets.choice(other)
    return secrets.choice(chinese or other)


def _record(record: dict) -> None:
    """根目录保留易读记录；以日期去重，异常退出后也能补写。"""
    marker = f"| {record['day']} |"
    old = ""
    try:
        if os.path.isfile(RECORD_PATH):
            with open(RECORD_PATH, encoding="utf-8") as f:
                old = f.read()
        if marker in old:
            return
        profile = profile_by_key(record["profile"]) or profile_for_score(record["score"])
        if not old:
            old = "# Nyalume 的祝福记录\n\n| 日期 | 分数 | 今日 Nyalume | 祝福 |\n| --- | ---: | --- | --- |\n"
        with open(RECORD_PATH, "w", encoding="utf-8") as f:
            f.write(old + f"| {record['day']} | {record['score']} | {profile['name']} | {record['blessing']} |\n")
    except OSError:
        pass


def get_today() -> dict | None:
    record = memory.get_daily_nyalume(_today())
    return _public(record) if record else None


def draw_today() -> dict:
    day = _today()
    existing = memory.get_daily_nyalume(day)
    if existing:
        _record(existing)
        return _public(existing)
    score = secrets.randbelow(101)
    profile = profile_for_score(score)
    blessing = _pick_blessing()
    version = f"{profile['finish']}-v2"
    record, inserted = memory.save_daily_nyalume(
        day, score, profile["key"], blessing["text"],
        blessing["keyword"], blessing["source"], version,
    )
    _record(record)
    return _public(record, inserted)


def collection() -> list[dict]:
    return [_public(record) for record in memory.list_daily_nyalume()]


def update_card(day: str, **flags: bool | None) -> dict | None:
    record = memory.update_daily_nyalume(day, **flags)
    return _public(record) if record else None


def style_prompt() -> str:
    """当天抽过才生效；只调整措辞，不改变能力、事实标准和安全边界。"""
    record = memory.get_daily_nyalume(_today())
    if not record:
        return ""
    profile = profile_by_key(record["profile"])
    if not profile:
        return ""
    return (
        f"\n\n【今日 Nyalume：{profile['name']}】{profile['style']}"
        "这只影响表达风格，不得降低任务质量、事实准确性、工具纪律或安全标准；"
        "不要主动透露今日分数，除非用户询问。"
    )
