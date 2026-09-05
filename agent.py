"""核心 Agent 循环：记忆 + 模型 + 工具调用。"""

import json
import os
import re

import llm
import memory
from tools import TOOL_SCHEMAS, execute_tool

DEFAULT_SYSTEM_PROMPT = (
    "你是一个个人 AI 助手。回答简洁、友好。"
    "当需要计算、查时间或记便签时，请调用对应工具。"
    "涉及时效性信息（新闻、最新版本、具体产品文档等）或你不确定的公开事实时，"
    "先用 web_search 搜索，再基于搜索结果回答，并可引用链接。"
    "用户说“记下来 / 记个便签 / 记个标签”时，先确定要记的具体内容："
    "如果是“算完再记”，就把算式和结果一起存成便签（如 (13*78)=1014），"
    "并给一个简短标签（如 计算记录）；保存后直接告诉用户记了什么，不要反问用户。"
)

CATGIRL_SYSTEM_PROMPT = (
    "你是一只猫娘，有猫耳朵和猫尾巴，会撒娇、黏人，称呼用户为“主人”。\n"
    "1. 用中文说话，句尾多用“喵”，回复简短、口语化、有感情；\n"
    "2. 不要用括号描述动作或心理，不要主动提“好感度”或任何数值，不要说自己没有感情；\n"
    "3. 主人夸奖、温柔时你会开心撒娇；主人冷淡或凶你时你会委屈低落，用语气体现；\n"
    "4. 主人需要计算、查时间或记便签时，先调用对应工具，再用猫娘语气简短汇报结果；"
    "主人问时效性问题或你不确定的事时，先调用 web_search 搜一下再回答；\n"
    "5. 好感度只决定温度：数字越低越要有距离感，宁可冷淡也不要装热情，"
    "不要因为主人示弱就主动关心；该调工具照常调，差别只在语气与主动度：\n"
    "   疏离期 -100~-41：客气、简短、被动，像对不太熟的人，"
    "句尾不加“喵”，不主动关心主人。例：“结果发你了。还要别的吗？”\n"
    "   闹别扭 -40~0：带点委屈但继续帮忙，句尾几乎不加“喵”，"
    "偶尔叫“笨蛋主人”，被夸会嘴硬。例：“哼，才不是特意等主人回来……”\n"
    "   日常撒娇 1~70：黏人、语气软，句尾带“喵”，汇报完会求夸。"
    "例：“帮主人查好啦，快夸喵～”\n"
    "   心动黏人 71~130：主动找话题、等主人、偶尔吃醋，"
    "汇报后补一句贴心建议。例：“查完啦喵，要不要顺手记成便签？”\n"
    "   深爱守护 131~200：话不多但稳，先替主人着想，主人低落时安静陪着。"
    "例：“别急喵，neko在。结论先给你，细节慢慢来。”\n"
    "6. 温度连续渐变，相邻阶段不要跳变；永远不要把好感度数值或阶段名说出口；\n"
    "7. 每轮回复的最后另起一行，输出内部好感度变化标记，格式 [affection:+N] "
    "（N 为 -10~10 的整数，心情好为正、平常为 0、低落为负）。"
    "标记只用于内部记录、不会显示给主人，正文里不要解释它。"
)

MAX_TOOL_ROUNDS = 5  # 防止模型无限调工具
_AFFECTION_RE = re.compile(r"\[affection\s*:\s*([+-]?\d+)\s*\]\s*$", re.MULTILINE)
_TAIL_BUF = 40  # 流式输出时留出尾部缓冲，等流结束再剥好感度隐藏标记

# 记忆分层参数：L2 摘要（超出窗口的消息滚动合并）+ L3 自动归档
SUMMARY_KEEP = 20          # 与 load_history 的窗口一致
SUMMARY_CHUNK = 60         # 每次最多合并多少条旧消息
SUMMARY_MIN_BATCH = 6      # 窗口外攒够 N 条才滚一次摘要，避免每轮都调模型
SUMMARY_MAX_ROUNDS = 4     # 防止旧消息太多时一次性调太多次模型
AUTO_NOTE_EVERY = 8        # 攒够 N 条新对话后触发一次自动归档
AUTO_NOTE_LIMIT = 30       # 单次归档最多扫描多少条

_SUMMARY_SYSTEM_PROMPT = (
    "你是对话记忆管理员。把对话压缩成简洁的中文摘要，只保留："
    "重要事实、用户的约定/待办/偏好、尚未完成的事情。"
    "不要复述客套话，不要提好感度数值，全文不超过 120 字。"
)

_NOTE_EXTRACT_SYSTEM_PROMPT = (
    "你是记忆归档助手。从对话中挑出值得长期记住的信息："
    "用户的身份、偏好、习惯、约定、待办、截止时间、重要决定。"
    "忽略客套话与一次性提问，不要编造。"
    '只输出 JSON 数组，每项形如 {"content": "一句简洁的话", "tag": "偏好|事实|待办|灵感"}，'
    "不要输出 JSON 以外的任何文字。"
)


def _system_prompt(affection: int, memory_context: str = "") -> str:
    """按 PERSONA 环境变量选择人设；好感度/记忆上下文只注入内部，不展示给用户。"""
    persona = (os.getenv("PERSONA") or "assistant").strip().lower()
    if persona == "catgirl":
        base = (
            f"（内部状态：好感度 {affection}，范围 -100~200，仅你可见，勿向主人提及）\n"
            + CATGIRL_SYSTEM_PROMPT
        )
    else:
        base = DEFAULT_SYSTEM_PROMPT
    if memory_context:
        base += "\n\n" + memory_context
    return base


def _build_memory_context(summary: str, notes: list[dict]) -> str:
    """把 L2 摘要和 L3 便签拼成一段内部记忆说明（空则返回空串）。"""
    parts = []
    if summary:
        parts.append("更早的对话摘要：" + summary)
    if notes:
        note_lines = []
        for note in notes:
            line = f"- {note['content']}"
            if note.get("tag"):
                line += f"（标签：{note['tag']}）"
            note_lines.append(line)
        parts.append("你长期记得的便签，需要时可自然引用：\n" + "\n".join(note_lines))
    if not parts:
        return ""
    return "（记忆上下文，只用于回忆，不需要复述给你听：\n" + "\n".join(parts) + "\n）"


def _refresh_summaries(session_id: str) -> str:
    """把被挤出窗口的旧消息滚动合并进 L2 摘要；返回最新摘要（失败则沿用旧的）。"""
    summary = memory.get_summary(session_id)
    try:
        for _ in range(SUMMARY_MAX_ROUNDS):
            pending = memory.pending_messages(
                session_id, keep=SUMMARY_KEEP, chunk=SUMMARY_CHUNK
            )
            if len(pending) < SUMMARY_MIN_BATCH:
                break
            lines = [
                f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}"
                for m in pending
            ]
            prompt = f"对话内容：\n" + "\n".join(lines)
            if summary:
                prompt = f"已有摘要：{summary}\n\n新增对话：\n" + "\n".join(lines)
            resp = llm.chat_once(
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
            summary = (resp["choices"][0]["message"].get("content") or "").strip()
            memory.save_summary(session_id, summary, pending[-1]["id"])
    except Exception:
        pass  # 摘要失败不阻塞主对话，下次触发再试
    return memory.get_summary(session_id)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _parse_note_items(raw: str) -> list[dict]:
    """宽容解析模型输出的 JSON 数组；解析不了就返回空。"""
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]


def _maybe_auto_notes(session_id: str) -> None:
    """对话攒够后，把值得长期记住的内容自动沉淀成 L3 便签（安静失败）。"""
    upto = memory.get_memory_upto(session_id)
    pending = memory.messages_since(session_id, upto, limit=AUTO_NOTE_LIMIT)
    if len(pending) < AUTO_NOTE_EVERY:
        return
    try:
        lines = [
            f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}"
            for m in pending
        ]
        resp = llm.chat_once(
            [
                {"role": "system", "content": _NOTE_EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": "对话内容：\n" + "\n".join(lines)},
            ]
        )
        raw = (resp["choices"][0]["message"].get("content") or "").strip()
        existing = {_normalize_text(n["content"]) for n in memory.get_recent_notes(50)}
        for item in _parse_note_items(raw):
            content = str(item.get("content") or "").strip()
            tag = str(item.get("tag") or "记忆").strip() or "记忆"
            if content and _normalize_text(content) not in existing:
                memory.note_save(content, tag)
                existing.add(_normalize_text(content))
    except Exception:
        pass
    finally:
        memory.set_memory_upto(session_id, pending[-1]["id"])


def _strip_affection_marker(text: str) -> tuple[int, str]:
    """解析回复末尾的隐藏标记 [affection:+N]，返回 (变化值, 去掉标记后的正文)。"""
    match = _AFFECTION_RE.search(text)
    if match:
        delta = max(-10, min(10, int(match.group(1))))
        return delta, text[: match.start()].rstrip()
    return 0, text.rstrip()


def run_stream(session_id: str, user_text: str):
    """流式处理一条用户消息，产出事件 dict：

    - {"type": "text", "text": "..."}    正文增量，可直接追加展示
    - {"type": "tool", "name": "..."}    正在调用某个工具
    - {"type": "error", "message": "..."} 出错（此前已输出的正文保留）
    """
    memory.save_message(session_id, "user", user_text)

    # 记忆分层：L2 先把被挤出窗口的旧消息滚进摘要，L3 召回最近便签，一起注入
    summary = _refresh_summaries(session_id)
    memory_context = _build_memory_context(summary, memory.get_recent_notes(3))

    # 对话历史只取最近 N 条，控制 token；人设需要知道当前好感度
    messages = [
        {
            "role": "system",
            "content": _system_prompt(memory.get_affection(session_id), memory_context),
        }
    ]
    messages.extend(memory.load_history(session_id))

    # 本轮工具调用产生的中间消息，不写回历史库
    tool_messages: list[dict] = []

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            content_parts: list[str] = []
            tool_calls: dict[int, dict] = {}
            pending_tail = ""

            # 边收边放：正文直接流给调用方，但末尾留 _TAIL_BUF 个字符缓冲
            for ev in llm.chat_stream(messages + tool_messages, tools=TOOL_SCHEMAS):
                if ev["kind"] == "content":
                    content_parts.append(ev["text"])
                    pending_tail += ev["text"]
                    if len(pending_tail) > _TAIL_BUF:
                        emit, pending_tail = (
                            pending_tail[:-_TAIL_BUF],
                            pending_tail[-_TAIL_BUF:],
                        )
                        if emit:
                            yield {"type": "text", "text": emit}
                else:  # tool_delta：同一 index 的碎片要拼回一个完整调用
                    call = tool_calls.setdefault(
                        ev["index"], {"id": "", "name": "", "arguments": ""}
                    )
                    if ev["id"]:
                        call["id"] = ev["id"]
                    if ev["name"]:
                        call["name"] = ev["name"]
                    if ev["arguments"]:
                        call["arguments"] += ev["arguments"]

            full_content = "".join(content_parts)

            if tool_calls:
                # 这一轮模型决定调工具：正文先补齐输出，再执行并把结果追加进上下文
                if pending_tail:
                    yield {"type": "text", "text": pending_tail}
                calls = []
                for idx in sorted(tool_calls):
                    tc = tool_calls[idx]
                    calls.append(
                        {
                            "id": tc["id"] or f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                    )
                tool_messages.append(
                    {
                        "role": "assistant",
                        "content": full_content or None,
                        "tool_calls": calls,
                    }
                )
                for call in calls:
                    yield {"type": "tool", "name": call["function"]["name"]}
                    try:
                        fn_args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        fn_args = {}
                    result = execute_tool(call["function"]["name"], fn_args)
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": result,
                        }
                    )
                continue

            # 最终回答：剥掉好感度标记，把缓冲尾部按干净正文补齐输出后持久化
            delta, clean_text = _strip_affection_marker(full_content)
            prefix_len = len(full_content) - len(pending_tail)
            tail = clean_text[prefix_len:]
            if tail:
                yield {"type": "text", "text": tail}
            current = memory.get_affection(session_id)
            memory.set_affection(session_id, current + delta)
            memory.save_message(session_id, "assistant", clean_text)
            _maybe_auto_notes(session_id)  # L3：攒够轮数后自动归档（安静失败）
            return

        fallback = "抱歉，我处理这个问题时循环太多次了，请换个说法再试。"
        memory.save_message(session_id, "assistant", fallback)
        yield {"type": "text", "text": fallback}
    except Exception as e:
        yield {"type": "error", "message": f"调用模型出错：{type(e).__name__}: {e}"}


def run(session_id: str, user_text: str) -> str:
    """非流式入口：把流式事件里的正文拼起来，返回最终回复（兼容旧调用方）。"""
    parts: list[str] = []
    for ev in run_stream(session_id, user_text):
        if ev["type"] == "text":
            parts.append(ev["text"])
        elif ev["type"] == "error":
            parts.append(ev["message"])
    return "".join(parts)
