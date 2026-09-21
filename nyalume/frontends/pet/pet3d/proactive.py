"""桌宠的「主动搭话」层：本地规则判"值得说" → 只在这时候调一次小模型。

省钱的关键都在这个文件里：

* 采集、判定、预算、去重全在本地做，**不花 token**；
* 只有真的决定开口时才调一次模型，一次约 0.5k token（输入 ~500 / 输出 ~60）；
* 搭话间隔由用户选档，间隔内不会再主动说话；全屏游戏 / 开会 /
  机器满载 / 刚被碰过都不打扰；被无视两次就静默一小时。
  （深夜**不**挡：她本来就没声音，半夜搭一句话反而更像陪着你。）

使用与主 App 相同的 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 环境变量
（默认 DeepSeek）。没配 key 或没装 openai 就整个关掉，不影响其它功能。
"""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
import time

TALK_MODES = {
    "quiet": None,
    "reserved": (45 * 60, 75 * 60),
    "normal": (20 * 60, 40 * 60),
    "chatty": (10 * 60, 20 * 60),
    "talkative": (3 * 60, 8 * 60),
}
TALK_LABELS = {"quiet": "安静", "reserved": "寡言", "normal": "正常",
               "chatty": "健谈", "talkative": "话痨"}
RECENT_TOUCH = 180.0  # 刚被碰过就别插嘴
IGNORE_AFTER = 600.0  # 提议后这么久还没人理 → 记一次"被无视"
IGNORE_LIMIT = 2  # 连续被无视这么多次 → 静默
SILENCE = 3600.0  # 静默时长
ACTIVITY_MIN = 25 * 60  # 同一件事连着做这么久才值得提一句
BATTERY_LOW = 20  # 电量低于这个数（且没插电）提醒一次
SPECIAL_GAP = 7200.0  # "电量低""你还在吗"这类最多两小时提一次

ALLOWED_ACTIONS = {"say", "face", "idle", "look"}
FACE_EMOTIONS = {"happy", "shy", "surprise", "angry", "sad", "sleepy", "calm", "love"}
LOOK_DIRECTIONS = {"左", "右", "上", "下"}

PERSONA = """你是{name}，住在用户桌面上的 3D 桌宠，像熟悉的伙伴一样陪着用户。
说话规则（必须遵守）：
1. 一次只说一句话，≤25 个字，轻松可爱，像熟人闲聊，不要播音腔。
2. 有提议时只提一个，用"要不要…"这类邀请口吻；不必每次都提议。
3. 可以参考活动类别和媒体标题，但不要念窗口名、技术细节或说"检测到"。
4. 用户不理你就算了，绝对不要追问、不要道歉、不要重复刚才的话。
5. 可以用语气词和颜文字，但一次最多一个。

你只能从这些动作里挑一个：
- say：说这句话（text 必填）
- face：做个表情，text 填 happy/shy/surprise/angry/sad/sleepy/calm/love
- idle：什么也不做
- look：看向某处，text 填 "左"/"右"/"上"/"下"

只输出 JSON，不要任何解释：
{{"action": "say", "text": "要不要歇会儿？我陪你发呆～"}}"""


def llm_ready() -> bool:
    """有没有可用的模型配置（跟主 App 共用一套环境变量）。"""
    if not os.getenv("LLM_API_KEY"):
        return False
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def default_chat(messages: list[dict], timeout: float = 25.0) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["LLM_API_KEY"],
                    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
                    timeout=timeout)
    reply = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "deepseek-chat"), messages=messages)
    return reply.choices[0].message.content or ""


def estimate_tokens(text: str) -> int:
    """粗估 token（中英混排按 1 token ≈ 3 个字符算），只用来记日志。"""
    return max(1, int(len(text) / 3))


def parse_reply(text: str) -> dict | None:
    """从模型回复里抠出 JSON；抠不出来或动作不合法就返回 None。"""
    raw = (text or "").strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip().lower()
    if action not in ALLOWED_ACTIONS:
        return None
    say = str(data.get("text") or "").strip()
    if action == "say" and not say:
        return None
    if action == "face" and say not in FACE_EMOTIONS:
        return None
    if action == "look" and say not in LOOK_DIRECTIONS:
        return None
    return {"action": action, "text": say[:25] if action == "say" else say}


class Proposer:
    """决定"什么时候说、说什么"的那一层。chat 可注入，方便测试。"""

    def __init__(self, chat=None, model_name: str = "Nyalume", mode: str = "normal") -> None:
        self.chat = chat or default_chat
        self.model_name = " ".join(str(model_name or "Nyalume").split())[:30]
        self.mode = mode if mode in TALK_MODES else "normal"
        self.next_allowed_at = 0.0
        self.last_proposal = 0.0
        self.last_reply_at = 0.0
        self.ignored = 0
        self.silenced_until = 0.0
        self.pending_ack = False
        self.sent = 0
        self.tokens = 0
        self.special_at: dict[str, float] = {}  # 特殊触发各自的冷却

    # ---- 触发条件（本地判断，全部不花 token）----

    def worth_saying(self, desk: dict, state: dict, activity: str, now: float) -> str:
        # 电量低且没插电：只提醒一次，两小时内不重复
        batt = desk.get("battery") or {}
        if (batt.get("present") and batt.get("percent", 100) <= BATTERY_LOW
                and not batt.get("charging")
                and now - self.special_at.get("battery", 0) > SPECIAL_GAP):
            return "battery"
        # 很久没动：关心一下（这条本来就发生在"人不在"的时候，所以要放行 idle 那道闸）
        if (desk.get("idle_sec", 0) >= ACTIVITY_MIN
                and now - self.special_at.get("idle_care", 0) > SPECIAL_GAP):
            return "idle_care"
        if (state.get("energy", 1.0) < 0.3
                and now - self.special_at.get("tired", 0) > SPECIAL_GAP):
            return "tired"
        if activity:
            return activity
        return "陪伴"  # 没有特别事件也能在所选间隔后轻声搭话

    def blocked(self, desk: dict, state: dict, now: float, allow_idle: bool = False) -> str:
        """被什么挡着了（返回原因字符串，空串=可以说话）。

        allow_idle=True 时放行"人不在"那条闸——"你还在吗""电量低"本来就是
        发生在人不在的时候。
        """
        if self.mode == "quiet":
            return "主动搭话已关闭"
        if now < self.silenced_until:
            return "静默中"
        if now < self.next_allowed_at:
            return "还没到下次搭话的间隔（或刚说过）"
        if desk.get("quiet"):
            return "手动安静模式"
        if desk.get("category") == "会议" or desk.get("busy"):
            return "会议/满载"
        if desk.get("fullscreen"):
            return "全屏（游戏/视频）"
        if not allow_idle and desk.get("idle_sec", 0) > 900:
            return "人不在（空闲太久）"
        if now - state.get("last_interaction", 0) < RECENT_TOUCH:
            return "刚被碰过"
        return ""

    def note_touch(self, now: float) -> None:
        """用户互动了：算"理过它"，把被无视计数清掉。"""
        if self.pending_ack or now - self.last_reply_at < IGNORE_AFTER:
            self.ignored = 0
            self.pending_ack = False

    def tick_ignored(self, now: float) -> None:
        if self.pending_ack and now - self.last_reply_at > IGNORE_AFTER:
            self.pending_ack = False
            self.ignored += 1
            if self.ignored >= IGNORE_LIMIT:
                self.silenced_until = now + SILENCE
                self.ignored = 0

    # ---- 真的开口 ----

    def build_prompt(self, desk: dict, state: dict, reason: str) -> list[dict]:
        digest = {
            "现在": time.strftime("%H:%M"),
            "我在做的事": reason if reason != "tired" else "没事（只是有点累）",
            "桌宠状态": {"心情": state.get("mood"), "体力": state.get("energy"),
                         "被戳次数": state.get("taps")},
            "桌面": {"类别": desk.get("category"), "在放": str(desk.get("media", {}).get("title", ""))[:80],
                     "空闲秒": desk.get("idle_sec")},
        }
        return [
            {"role": "system", "content": PERSONA.format(name=self.model_name)},
            {"role": "user", "content": json.dumps(digest, ensure_ascii=False)},
        ]

    def propose(self, desk: dict, state: dict, reason: str, now: float | None = None) -> dict | None:
        now = now or time.time()
        messages = self.build_prompt(desk, state, reason)
        try:
            reply = self.chat(messages)
        except Exception as e:
            raise RuntimeError(f"模型调用失败：{type(e).__name__}: {e}") from e
        self.last_proposal = now
        span = TALK_MODES[self.mode]
        self.next_allowed_at = now + random.uniform(*span) if span else float("inf")
        if reason in ("battery", "idle_care", "tired"):
            self.special_at[reason] = now
        self.sent += 1
        self.tokens += sum(estimate_tokens(m["content"]) for m in messages)
        action = parse_reply(reply)
        self.pending_ack = bool(action and action["action"] != "idle")
        if self.pending_ack:
            self.last_reply_at = now
        return action


def run_loop(api, interval: float = 60.0) -> None:
    """采集→判定→开口 的主循环。api 只需提供 _desk / _pet_state / _pending / _proactive。"""
    proposer = Proposer(model_name=os.path.basename(getattr(api, "_model_dir", "")),
                        mode=getattr(api, "_talk_mode", "normal"))
    span = TALK_MODES[proposer.mode]
    proposer.next_allowed_at = time.time() + random.uniform(*span) if span else float("inf")
    api._proposer = proposer
    activity_since = 0.0
    last_category = ""
    last_block = ""
    while True:
        time.sleep(interval)
        try:
            now = time.time()
            desk = api._desk
            state = api._pet_state.snapshot()
            proposer.tick_ignored(now)
            category = desk.get("category", "")
            if category != last_category:
                last_category = category
                activity_since = now
            mode = getattr(api, "_talk_mode", "normal")
            if mode != proposer.mode:
                proposer.mode = mode
                span = TALK_MODES[mode]
                proposer.next_allowed_at = max(now, proposer.last_proposal) + random.uniform(*span) if span else float("inf")
            if not getattr(api, "_proactive", True) or mode == "quiet":
                continue
            long_enough = now - activity_since > ACTIVITY_MIN
            reason = proposer.worth_saying(
                desk, state, f"{category}（已经 {int((now - activity_since) / 60)} 分钟）"
                if long_enough and category not in ("", "其他") else "",
                now,
            )
            if not reason:
                continue
            block = proposer.blocked(desk, state, now,
                                     allow_idle=reason in ("battery", "idle_care"))
            if block:
                # 为什么没开口：变了一次就记一笔，排查"配了 API 却从不搭话"用
                if block != last_block:
                    last_block = block
                    _log(f"主动搭话：先不说（{block}）")
                continue
            last_block = ""
            action = proposer.propose(desk, state, reason, now)
            if not action:
                continue
            if action["action"] == "say":
                api._pending = {"kind": "say", "text": action["text"]}
            elif action["action"] == "face":
                api._pending = {"kind": "face", "emotion": action["text"] or "happy"}
            elif action["action"] == "look":
                dx = {"左": -0.5, "右": 0.5}.get(action["text"], 0.0)
                dy = {"上": -0.4, "下": 0.4}.get(action["text"], 0.0)
                api._pending = {"kind": "look", "x": dx, "y": dy}
            _log(f"propose[{reason}] → {action['action']} {action['text']!r} "
                 f"（累计 {proposer.sent} 次，估算 {proposer.tokens} token）")
        except Exception as e:
            _log(f"propose 失败 {type(e).__name__}: {e}")


def _log(msg: str) -> None:
    """直接写主日志。

    别 import pet3d_win：那份会变成**第二份模块实例**，而 `ctypes.windll` 是
    进程级单例，于是它把 `UpdateLayeredWindow.argtypes` 换成它自己的
    `_BlendFunction`，主实例从此每帧都 `ArgumentError`（实测 63 帧/秒全失败）
    → 推帧停 20 秒 → 自动重启，循环间隔 80 秒，主动搭话永远等不到间隔。
    """
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


_LOG_PATH = os.path.join(tempfile.gettempdir(), "nyalume_pet3d.log")
