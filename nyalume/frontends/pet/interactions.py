"""桌边生活的即时反馈；复用表情，不从点击推测关系与偏好。"""

import random

_REACTIONS = {
    "head": [("摸头可以，发型乱了你负责梳哦。", "happy"),
             ("刚整理好的头发……算啦，给你摸。", "love"),
             ("再摸一下就好。好吧，两下也行。", "love"),
             ("嗯，舒服。差点忘了我还在站岗。", "happy"),
             ("轻一点嘛，我还要保持漂亮的发型。", "happy")],
    "body": [("呀，偷袭！猫猫差点原地打结。", "happy"),
             ("这里不是门铃，别把我戳开机了。", "happy"),
             ("好痒！我的威严漏气了。", "happy"),
             ("主人再戳，可爱就要溢出来了。", "love"),
             ("猫猫防御失败……偷笑算反击！", "happy")],
    "legs": [("轻一点，我不是桌面的承重柱。", "grumpy"),
             ("猫步还没练熟，别加考试题呀。", "neutral"),
             ("腿在站岗，脑袋已经下班。", "happy"),
             ("呀，差点把猫步走成鸭步。", "happy"),
             ("轻一点啦，优雅还在试用期呢。", "neutral")],
    "lift": [("猫猫搬家中，禁止倒放。", "happy"),
             ("主人，随身猫猫不支持托运！", "happy"),
             ("喵？我还没做好起飞的准备呢！", "happy"),
             ("搬我可以，零食也得跟上。", "love"),
             ("换个窝？这次我要挑风水。", "happy")],
    "settle": [("落地成功！优雅那部分下次补。", "happy"),
               ("新窝验收：能摸鱼，合格！", "happy"),
               ("这个位置不错，先占了再说。", "happy"),
               ("主人放好了？猫猫开始假装乖巧。", "love"),
               ("猫猫已就位，发型也安全着陆。", "happy")],
    "peek": [("我在巡逻。顺便趴着。", "happy"),
             ("只是探个头，没打算捣乱。真的。", "happy"),
             ("占领桌边！面积小，野心大。", "happy"),
             ("探个头，摸鱼小队还缺一个主人。", "love"),
             ("躲好了！头发你别出卖我呀。", "happy")],
    "cheer": [("做完啦！本猫宣布进入摸鱼环节。", "happy"),
              ("猫猫交卷！奖励一条虚拟小鱼干？", "happy"),
              ("今天很能干，先偷偷骄傲一下。", "happy"),
              ("一起搞定！可爱和靠谱我都要。", "love"),
              ("认真营业结束，装乖还剩两秒。", "happy")],
    "miss": [("你戳的是空气，猫猫谢绝代签。", "neutral"),
             ("偏了偏了！我在这边，不在空气里。", "happy"),
             ("这一下算空气的，猫猫先不认账。", "neutral"),
             ("差一点！再准一点就能命中可爱。", "happy"),
             ("猫在这里，刚才那边只有想象力。", "happy")],
}


# 有互动边界时使用不带称呼、不调情的反馈；不猜测用户拒绝的程度。
_RESERVED = {
    "head": ["轻一点，头发刚整理好。", "嗯，摸头收到啦。", "发型还在，问题不大。"],
    "body": ["有点痒，威严先借我一下。", "这里不是门铃哦。", "猫猫正在努力站得端庄。"],
    "legs": ["轻一点，让我先站稳。", "猫步差点走成鸭步。", "优雅还在练习中。"],
    "lift": ["换个窝，头发也要整理一下。", "猫猫搬家中，轻拿轻放。", "让我挑个能摸鱼的位置。"],
    "settle": ["落地成功，优雅下次补。", "新窝验收，合格！", "好了，开始假装乖巧。"],
    "peek": ["我在巡逻，顺便趴着。", "头发露出来了，藏不住了。", "这个角落归猫猫了。"],
    "cheer": ["做完啦，先偷偷骄傲一下。", "一起搞定，可以歇一会儿。", "猫猫交卷，请查收。"],
    "miss": ["戳到空气啦，我在这边。", "偏了偏了，猫猫在这里。", "这一下归空气，我先不认账。"],
}


def pick_reaction(region: str, session_id: str = "", *, previous: str = "", preferences=None) -> dict:
    """台词与现有表情配对；称呼和边界只来自已确认的用户记忆。"""
    preferences = preferences or {}
    region = region if region in _REACTIONS else "miss"
    boundaries = preferences.get("boundaries", [])
    if boundaries:
        lines = _RESERVED[region]
        if any(word in boundary for boundary in boundaries for word in ("摸", "碰", "触摸", "拖", "拎")):
            lines = ["好，我在这里陪着。", "嗯，先安静待着。", "我就在桌边。"]
        choices = [(line, "neutral") for line in lines]
    else:
        address = preferences.get("address") or "主人"
        choices = [(line.replace("主人", address), mood) for line, mood in _REACTIONS[region]]
    text, mood = random.choice([item for item in choices if item[0] != previous] or choices)
    return {"text": text, "mood": mood, "reserved": bool(boundaries)}


def pick_line(region: str, session_id: str = "") -> str:
    """保留字符串接口；不改变任何关系数值。"""
    return pick_reaction(region, session_id)["text"]
