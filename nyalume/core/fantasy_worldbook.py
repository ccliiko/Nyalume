"""第一章之前生成的简短世界书；核心规则常驻，条目按关键词取用。"""
import json
import time
from contextlib import nullcontext

from . import llm

MAX_ATTEMPTS = 24
PREPARATION_SECONDS = 20 * 60


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("世界底稿格式不完整")
    limits = {"name": 40, "premise": 320, "boundary_rule": 240,
              "nyalume_goal": 160, "starting_location": 120}
    for key, size in limits.items():
        if not isinstance(data.get(key), str) or not 1 <= len(data[key].strip()) <= size:
            raise ValueError("世界底稿缺少" + key)
    items = data.get("starting_items")
    if not isinstance(items, list) or not 1 <= len(items) <= 3 or any(
            not isinstance(item, str) or not 1 <= len(item.strip()) <= 60 for item in items):
        raise ValueError("请给出一到三件明确的初始物品")
    items = [item.strip() for item in items]
    if len(set(items)) != len(items):
        raise ValueError("初始物品不能重复")
    entries = data.get("entries")
    if not isinstance(entries, list) or not 2 <= len(entries) <= 6:
        raise ValueError("请提供二到六条角色、地点或物件条目")
    saved_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("kind") not in ("character", "place", "object", "faction"):
            raise ValueError("世界书条目类型无效")
        name, content, aliases = entry.get("name"), entry.get("content"), entry.get("aliases")
        if (not isinstance(name, str) or not 2 <= len(name.strip()) <= 60
                or not isinstance(content, str) or not 1 <= len(content.strip()) <= 240
                or not isinstance(aliases, list) or len(aliases) > 3
                or any(not isinstance(alias, str) or not 2 <= len(alias.strip()) <= 40 for alias in aliases)):
            raise ValueError("世界书条目内容不完整")
        saved_entries.append({"kind": entry["kind"], "name": name.strip(), "content": content.strip(),
                              "aliases": [alias.strip() for alias in aliases]})
    if len({entry["name"] for entry in saved_entries}) != len(saved_entries):
        raise ValueError("世界书条目名称不能重复")
    return {**{key: data[key].strip() for key in limits}, "starting_items": items, "entries": saved_entries}


def generate(user_text, cancel_event=None):
    stream = generate_stream(user_text, cancel_event=cancel_event)
    while True:
        try:
            next(stream)
        except StopIteration as done:
            return done.value


def generate_stream(user_text, cancel_event=None, trace=None):
    """只生成底稿；调用者验证版本后再写库，不把无效 JSON 当成故事。"""
    prompt = '''为 Nyalume 的文字奇幻冒险创建一份独特、可延续的世界底稿。
固定前提：破碎月轮连接异域，小窝是可返回的锚点；魔法有条件或代价；角色的死亡、失去和承诺不能靠梦醒抹除。
保留小窝日常作为归处，但开场要有真正的异域、阻力和角色目标。不要总选窗边花园。
这只是开场前的准备，不替用户做选择、不宣称用户已经获得战利品。
如果输入包含已拟定的首章，必须沿用其中的地点与 Nyalume 的目标，不重写已发生的事件。
初始物品为 Nyalume 已随身带着的1–3件普通物品，不赋予无代价万能能力；写明独立名称供背包记账。
只输出一个 JSON 对象，不带 Markdown、说明、章节正文或工具调用：
{"name":"世界名称","premise":"主要处境与冲突","boundary_rule":"本世界最关键的魔法代价/限制",
"nyalume_goal":"Nyalume 自己的目标","starting_location":"开场地点",
"starting_items":["初始物品1"],
"entries":[{"kind":"character","name":"条目名","aliases":["可选触发别名"],"content":"独立、明确的事实"}]}。
每条 kind 从 character、place、object、faction 中选一个，不要照抄示例。
entries 给2–6条，其中至少包含 Nyalume 或开场地点的具体信息；未来只按名称或别名按需调入。
'''
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user_text[:500]}]
    deadline = time.monotonic() + PREPARATION_SECONDS
    last_error, repeated_errors = "", 0
    for attempt in range(MAX_ATTEMPTS):
        if cancel_event is not None and cancel_event.is_set():
            return None
        if time.monotonic() >= deadline:
            break
        yield {"type": "status", "message": "她正在整理世界规则、开场地点和随身物品……" if attempt == 0
               else "她正在补全世界底稿中缺失的细节……"}
        with trace.span('llm', 'worldbook') if trace else nullcontext():
            raw = llm.chat_text(messages, timeout=min(120, max(1, deadline - time.monotonic())), cancel_event=cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return None
        try:
            with trace.span('validation', 'worldbook') if trace else nullcontext({}) as span:
                try:
                    text = raw.strip()
                    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
                    return validate(data)
                except (ValueError, TypeError) as exc:
                    span['reason'] = str(exc)[:240]
                    raise
        except (ValueError, TypeError) as exc:
            signature = (str(exc), raw)
            repeated_errors = repeated_errors + 1 if signature == last_error else 1
            last_error = signature
            if repeated_errors >= 6:
                break
            messages[2:] = [{"role": "assistant", "content": raw[:12000]},
                             {"role": "user", "content": "底稿尚未保存，请修正格式问题并重发完整 JSON：" + str(exc)}]
    raise ValueError("世界底稿未生成成功，请点击开启第一章重试")


def selected(book, focus):
    """从简短的正文与用户回应匹配最多三条，不做额外模型调用。"""
    if not isinstance(book, dict):
        return []
    focus = str(focus or "").casefold()
    matches = [entry for entry in book.get("entries", [])
               if entry["name"].casefold() == "nyalume"
               or any(term.casefold() in focus for term in [entry["name"], *entry["aliases"]])]
    return matches[:3]
