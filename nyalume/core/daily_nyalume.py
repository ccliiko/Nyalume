"""每日一次的 nyalume 人格抽取。"""

import datetime
import os
import secrets

from . import memory

RECORD_PATH = os.path.join(memory._PROJECT_ROOT, "nyalume_blessings.md")

PROFILES = (
    {
        "key": "quiet", "low": 0, "high": 9, "name": "低电量 Nyalume",
        "portrait": ("distant", 0), "tone": "quiet", "finish": "matte",
        "style": "语气安静、短句、少用感叹号；直接给结论，不额外闲聊。",
        "blessings": ("今天适合把脚步放慢一点，安稳完成一件事就很棒喵。",),
    },
    {
        "key": "dreamy", "low": 10, "high": 19, "name": "迷糊 Nyalume",
        "portrait": ("grumpy", 0), "tone": "dreamy", "finish": "satin",
        "style": "语气有一点迷糊可爱，但事实、路径和参数必须核对清楚；不拿错误开玩笑。",
        "blessings": ("偶尔绕一点路也没关系，今天仍会遇见小小的惊喜喵。",),
    },
    {
        "key": "slow", "low": 20, "high": 29, "name": "慢热 Nyalume",
        "portrait": ("idle", 3), "tone": "slow", "finish": "pearl",
        "style": "语气克制耐心，先观察再回答；内容简洁，不强行热络。",
        "blessings": ("不用急着一下子做好，今天的进展会慢慢长出来喵。",),
    },
    {
        "key": "focused", "low": 30, "high": 39, "name": "认真 Nyalume",
        "portrait": ("working", 0), "tone": "focused", "finish": "mica",
        "style": "语气认真有条理；任务分步时使用短清单，并明确验证结果。",
        "blessings": ("今天的认真会有回音，手边的难题会一项项变清楚喵。",),
    },
    {
        "key": "everyday", "low": 40, "high": 49, "name": "日常 Nyalume",
        "portrait": ("neutral", 0), "tone": "everyday", "finish": "opal",
        "style": "语气自然、平衡、口语化；先结论后细节，不刻意卖萌。",
        "blessings": ("愿今天普通却顺利，想做的事都稳稳向前一点喵。",),
    },
    {
        "key": "sunny", "low": 50, "high": 59, "name": "元气 Nyalume",
        "portrait": ("idle", 1), "tone": "sunny", "finish": "prism",
        "style": "语气轻快积极，可适量使用感叹号；仍保持简洁，不用空泛打气代替答案。",
        "blessings": ("今天的能量正在上线，先迈出第一步，好运就会跟上来喵！",),
    },
    {
        "key": "caring", "low": 60, "high": 69, "name": "贴心 Nyalume",
        "portrait": ("happy", 0), "tone": "caring", "finish": "aurora",
        "style": "语气温柔体贴；完成核心请求后，只补充一个真正有用的下一步。",
        "blessings": ("愿你今天被温柔接住，也别忘了给自己留一点喘息喵。",),
    },
    {
        "key": "creative", "low": 70, "high": 79, "name": "灵感 Nyalume",
        "portrait": ("idle", 2), "tone": "creative", "finish": "stardust",
        "style": "先给稳妥答案，再在有价值时补充一个简短的新角度或创意选项。",
        "blessings": ("今天会有一个亮晶晶的念头冒出来，记得把它接住喵。",),
    },
    {
        "key": "reliable", "low": 80, "high": 88, "name": "可靠 Nyalume",
        "portrait": ("idle", 0), "tone": "reliable", "finish": "linear",
        "style": "语气沉稳笃定，以证据和实际结果为主；不确定处必须明确说明并验证。",
        "blessings": ("今天适合放心向前，你做的每一步都会成为可靠的积累喵。",),
    },
    {
        "key": "lucky", "low": 89, "high": 99, "name": "幸运 Nyalume",
        "portrait": ("love", 0), "tone": "lucky", "finish": "holo",
        "style": "语气明亮、有庆祝感；不夸大结果，不把随机分数说成现实预测。",
        "blessings": ("幸运正在悄悄靠近，今天值得期待一个好消息喵！",),
    },
    {
        "key": "perfect", "low": 100, "high": 100, "name": "满分 Nyalume",
        "portrait": ("eating", 1), "tone": "perfect", "finish": "crystal",
        "style": "语气特别温暖从容，偶尔使用星星符号；答案仍然准确、克制、可执行。",
        "blessings": ("满分不是要求，是今天送给主人独一份的好运——愿所愿皆有回应喵 ✦",),
    },
)

BLESSINGS = (
    {"text": "今天不必事事完美，稳稳向前就很好。", "keyword": "稳稳向前", "source": "Nyalume · 现代"},
    {"text": "愿你今天遇见的善意，比预想中多一点。", "keyword": "多一点", "source": "Nyalume · 现代"},
    {"text": "慢一点也没关系，花会按自己的时辰开放。", "keyword": "花有时", "source": "Nyalume · 诗意"},
    {"text": "愿晚风带走疲惫，晨光留下答案。", "keyword": "晨光", "source": "Nyalume · 诗意"},
    {"text": "你认真走过的路，都会在未来留下回声。", "keyword": "回声", "source": "Nyalume · 现代"},
    {"text": "愿君行路有花，归来有灯。", "keyword": "有花有灯", "source": "Nyalume · 文言"},
    {"text": "心有所向，履之不辍，终至所期。", "keyword": "不辍", "source": "Nyalume · 文言"},
    {"text": "且听风吟，静候佳音。", "keyword": "佳音", "source": "Nyalume · 文言"},
    {"text": "山有峰顶，海有彼岸；缓步亦能抵达。", "keyword": "彼岸", "source": "Nyalume · 文言"},
    {"text": "愿今日所念，皆有微光相应。", "keyword": "微光", "source": "Nyalume · 文言"},
    {"text": "天行健，君子以自强不息。", "keyword": "自强不息", "source": "《周易·乾卦·象传》"},
    {"text": "长风破浪会有时，直挂云帆济沧海。", "keyword": "云帆", "source": "李白《行路难·其一》"},
    {"text": "山重水复疑无路，柳暗花明又一村。", "keyword": "柳暗花明", "source": "陆游《游山西村》"},
    {"text": "但行好事，莫问前程。", "keyword": "莫问前程", "source": "《增广贤文》"},
    {"text": "愿你有拨云见日的清醒，也有从容等待的耐心。", "keyword": "拨云见日", "source": "Nyalume · 现代"},
    {"text": "把今天照顾好，明天自然会来。", "keyword": "照顾今天", "source": "Nyalume · 现代"},
    {"text": "May a quiet little miracle find you today.", "keyword": "little miracle", "source": "Nyalume · English", "language": "en"},
    {"text": "You are allowed to grow at your own pace.", "keyword": "own pace", "source": "Nyalume · English", "language": "en"},
    {"text": "Fortune favors the bold.", "keyword": "be bold", "source": "拉丁谚语", "language": "en"},
    {"text": "今日の小さな幸運が、そっと君を見つけますように。", "keyword": "小さな幸運", "source": "Nyalume · 日本語", "language": "ja"},
    {"text": "愿灵感在你需要它的时候，刚好敲门。", "keyword": "灵感敲门", "source": "Nyalume · 灵感"},
    {"text": "今日宜相信一次自己的判断。", "keyword": "相信自己", "source": "Nyalume · 今日签"},
    {"text": "愿答案不必很远，就藏在下一步里。", "keyword": "下一步", "source": "Nyalume · 今日签"},
    {"text": "好运不一定喧闹，也可能只是事情恰好顺利。", "keyword": "恰好顺利", "source": "Nyalume · 今日签"},
)


def profile_for_score(score: int) -> dict:
    return next(p for p in PROFILES if p["low"] <= score <= p["high"])


def profile_by_key(key: str) -> dict | None:
    return next((p for p in PROFILES if p["key"] == key), None)


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
