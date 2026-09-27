"""本地虚拟小计划：每日一小步，选择和陪伴可留作纪念。"""
import datetime
import json
import random
import time

from . import memory

THEMES = {
    "garden": {"title": "窗边花园", "choices": ("薄荷", "向日葵", "Nyalume"),
               "stages": ("一个小念头", "播下种子", "冒出新芽", "窗边的小成果"),
               "art": ("🌰", "🪴", "🌱", "🌻"), "color": "#e9f3df",
               "invitation": "想在窗边种点东西。也可以选 Nyalume，种下一颗幻想的种子。",
               "company": ("你陪我挑了小花盆，窗边已经腾好位置啦。", "一起看着种子住进了花盆。", "小芽还很小，不过今天是一起看的。", "这份小成果，我们一起看到了。")},
    "boat": {"title": "纸船远行", "choices": ("晴空蓝", "落日橙"),
             "stages": ("一个小念头", "折出船身", "添上船帆", "启航纪念"),
             "art": ("📃", "📐", "⛵", "⛵"), "color": "#e0effb",
             "invitation": "想折一艘小纸船，放进小窝的想象海。帮我选个颜色吧。",
             "company": ("有你陪着挑纸，连折痕都开始让人期待了。", "你陪我压平了船身的折痕。", "一起给小船留了一张能迎风的帆。", "你也在岸边，这次出发就有了送行的人。")},
    "stars": {"title": "口袋星光", "choices": ("月光银", "蜜桃粉"),
              "stages": ("一个小念头", "折第一颗星", "攒起星光", "封存星星罐"),
              "art": ("🫙", "⭐", "✨", "🌟"), "color": "#f1e6fa",
              "invitation": "想给小窝攒一罐纸星星。选一种我们喜欢的颜色吧。",
              "company": ("你坐在旁边，我把星星纸也分给你一小摞。", "第一颗有点歪，但你陪我看过啦。", "我们一起把几颗小星星放进罐子。", "封好啦，这一罐里也留着你陪我的时候。")},
}
PLANTS = THEMES["garden"]["choices"]
STAGES = THEMES["garden"]["stages"]


def catalog():
    return [{"kind": kind, "title": theme["title"]} for kind, theme in THEMES.items()]


def _day(now):
    return datetime.datetime.fromtimestamp(now).date().isoformat()


def _view(row):
    if row is None:
        return None
    plan = dict(row)
    theme = THEMES[plan["kind"]]
    choice, stage = plan["plant"], plan["stage"]
    if plan["kind"] == "garden":
        final = "几片清香的薄荷叶" if choice == "薄荷" else "一朵朝向光的小黄花"
        texts = (f"那就种{choice}。我先准备好小花盆，明天再播下种子。",
                 f"我把{choice}的种子种下了，给它留了一个有光的位置。",
                 f"{choice}冒出了小芽，我今天仔细看了看它的新叶。",
                 f"小花园里长出了{final}，这次的小计划完成啦。")
        if choice == "Nyalume":
            texts = ("那就选 Nyalume。留一个小花盆，种下一颗幻想的种子。",
                     "幻想的种子住进花盆啦，里面藏着一点银白色的光。",
                     "小芽旁浮出一对小猫耳的轮廓，越来越像 Nyalume 了。",
                     "这颗幻想的种子，最后开成了 Nyalume 的模样。把我的 2D 形象留给你作纪念。")
    elif plan["kind"] == "boat":
        texts = (f"选好{choice}的纸啦，明天开始折小船。", f"把{choice}的纸对折，船身已经站稳了。",
                 f"给{choice}的小船添了一张帆，摆在窗边等风。", f"{choice}的小纸船在想象海里启航啦，留下这张远行纪念。")
    else:
        texts = (f"选好{choice}的星星纸啦，我先找一个空罐子。", f"折好了第一颗{choice}的纸星星，虽然还有一点歪。",
                 f"罐子里多了几颗{choice}的小星星，摇一摇像在闪光。", f"把{choice}的纸星星封进小罐，给小窝留下一点口袋星光。")
    plan.update(title=theme["title"], stages=theme["stages"], stage_name=theme["stages"][stage],
                color=theme["color"], art=theme["art"][stage], text=texts[stage] if choice else theme["invitation"],
                choices=list(theme["choices"]) if stage == 0 else [],
                accompanied=bool(plan["company"] & (1 << stage)),
                company_count=plan["company"].bit_count(), company_text=theme["company"][stage],
                portrait=stage == 3 and choice == "Nyalume" and plan["kind"] == "garden")
    if plan["kind"] == "garden" and stage == 3 and choice == "薄荷":
        plan["art"] = "🌿"
    plan["color"] = {"落日橙": "#ffdfc1", "月光银": "#e4e9f5", "蜜桃粉": "#ffe2ee",
                     "Nyalume": "#ffe6ee"}.get(choice, plan["color"])
    plan["notice"] = (f"我有个「{theme['title']}」的小念头，点「小计划」一起挑吧。" if stage == 0 else
                      f"「{theme['title']}」完成啦，来小窝看看这份纪念吧。" if stage == 3 else
                      f"「{theme['title']}」有新进展：{plan['stage_name']}啦。")
    return plan


def current():
    with memory._conn() as conn:
        return _view(conn.execute("SELECT * FROM pet_plans ORDER BY id DESC LIMIT 1").fetchone())


def completed_after(plan_id=0):
    with memory._conn() as conn:
        return [_view(row) for row in conn.execute(
            "SELECT * FROM pet_plans WHERE stage=3 AND id>? ORDER BY id LIMIT 10", (plan_id,))]


def collection(before=0):
    with memory._conn() as conn:
        return [_view(row) for row in conn.execute(
            "SELECT * FROM pet_plans WHERE stage=3 AND (?=0 OR id<?) ORDER BY id DESC LIMIT 20", (before, before))]


def tick(now=None, *, restart=False, kind=None):
    now = time.time() if now is None else now
    today = _day(now)
    if kind is not None and kind not in THEMES:
        raise ValueError("请选择已有的小计划主题")
    with memory._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM pet_plans ORDER BY id DESC LIMIT 1").fetchone()
        if row is None or (restart and row["stage"] == 3):
            if kind is None:
                options = [k for k in THEMES if row is None or k != row["kind"]]
                liked = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM pet_plans WHERE liked=1")}
                kind = random.choices(options, weights=[3 if k in liked else 1 for k in options])[0]
            pid = conn.execute("INSERT INTO pet_plans(kind,stage_day,created_at,updated_at) VALUES (?,?,?,?)",
                               (kind, today, now, now)).lastrowid
        else:
            pid = row["id"]
            if not row["paused"] and row["stage"] < 3 and today > row["stage_day"]:
                choice = row["plant"] or random.choice(THEMES[row["kind"]]["choices"])
                conn.execute("UPDATE pet_plans SET stage=stage+1,plant=?,chosen_by=?,stage_day=?,updated_at=? WHERE id=?",
                             (choice, row["chosen_by"] or "pet", today, now, pid))
                if row["stage"] == 2:
                    who = "你选的" if row["chosen_by"] == "user" else "她自己选的"
                    content = f"桌宠完成了虚拟小计划「{THEMES[row['kind']]['title']}」：{who}{choice}，留下了一份成长纪念。"
                    if row["company"]:
                        content += f"你在其中 {row['company'].bit_count()} 个阶段点过陪伴。"
                    conn.execute(
                        "INSERT OR IGNORE INTO companion_entries(kind,state,content,source,created_at,updated_at,event_key) "
                        "VALUES ('event','recorded',?,'pet_plan',?,?,?)", (content, now, now, f"pet_plan:{pid}"))
        return _view(conn.execute("SELECT * FROM pet_plans WHERE id=?", (pid,)).fetchone())


def update(plan_id, *, choice=None, paused=None, kind=None, accompany=None, liked=None, now=None):
    now = time.time() if now is None else now
    if all(value is None for value in (choice, paused, kind, accompany, liked)):
        raise ValueError("缺少小计划操作")
    if kind is not None and kind not in THEMES:
        raise ValueError("请选择已有的小计划主题")
    with memory._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM pet_plans WHERE id=?", (plan_id,)).fetchone()
        latest = conn.execute("SELECT MAX(id) FROM pet_plans").fetchone()[0]
        archive_like = liked is not None and all(v is None for v in (choice, paused, kind, accompany))
        if not row or (latest != plan_id and not archive_like):
            raise ValueError("小计划已变化，请刷新后再试")
        if kind is not None and kind != row["kind"]:
            if row["stage"] != 0:
                raise ValueError("已经开始制作啦，完成后再换主题吧")
            conn.execute("UPDATE pet_plans SET kind=?,plant='',chosen_by='',company=0,liked=0,noticed_stage=-1 WHERE id=?", (kind, plan_id))
        if choice is not None:
            if choice not in THEMES[kind or row["kind"]]["choices"]:
                raise ValueError("请选择这个主题提供的选项")
            if row["stage"] != 0:
                raise ValueError("已经播种或开始制作啦，下次再选另一种吧")
            conn.execute("UPDATE pet_plans SET plant=?,chosen_by='user' WHERE id=?", (choice, plan_id))
        if paused is not None:
            # 恢复当天不赶进度；系统时钟回拨也不回退。
            conn.execute("UPDATE pet_plans SET paused=?,stage_day=? WHERE id=?",
                         (int(paused), max(row["stage_day"], _day(now)), plan_id))
        if accompany:
            conn.execute("UPDATE pet_plans SET company=company | ? WHERE id=?", (1 << row["stage"], plan_id))
        if liked is not None:
            conn.execute("UPDATE pet_plans SET liked=? WHERE id=?", (int(liked), plan_id))
        conn.execute("UPDATE pet_plans SET updated_at=? WHERE id=?", (now, plan_id))
        return _view(conn.execute("SELECT * FROM pet_plans WHERE id=?", (plan_id,)).fetchone())


def claim_notice(plan, *, commit=True):
    with memory._conn() as conn:
        if not commit:
            row = conn.execute('SELECT 1 FROM pet_plans WHERE id=? AND stage=? AND noticed_stage < ? AND paused=0',
                               (plan['id'], plan['stage'], plan['stage'])).fetchone()
            return plan['notice'] if row else ''
        changed = conn.execute("UPDATE pet_plans SET noticed_stage=? WHERE id=? AND stage=? "
                               "AND noticed_stage < ? AND paused=0",
                               (plan["stage"], plan["id"], plan["stage"], plan["stage"])).rowcount
    return plan["notice"] if changed else ""


def prompt_context():
    plan = current()
    if not plan:
        return ""
    facts = {k: plan[k] for k in ("id", "kind", "stage", "choices", "title", "stage_name", "plant", "chosen_by", "paused", "text", "company_count", "liked")}
    with memory._conn() as conn:
        facts["liked_themes"] = [THEMES[r[0]]["title"] for r in conn.execute("SELECT DISTINCT kind FROM pet_plans WHERE liked=1")]
    return ("\n\n【她的小计划】这是本地虚拟生活进度，不是真实植物或用户的任务。"
            "只引用已发生的阶段；chosen_by=user 才能说用户选过。company_count 是用户点过陪伴的阶段数，"
            "不是陪伴时长；liked_themes 是用户明确喜欢的主题。不要擅自宣布下一阶段，"
            "用户明确要选择、陪伴或暂停时，可调用 pet_plan 保存；也可点击聊天顶栏的「小计划」。\n" + json.dumps(facts, ensure_ascii=False))
