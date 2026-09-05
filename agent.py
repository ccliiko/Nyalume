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
    "5. 每轮回复的最后另起一行，输出内部好感度变化标记，格式 [affection:+N] "
    "（N 为 -10~10 的整数，心情好为正、平常为 0、低落为负）。"
    "标记只用于内部记录、不会显示给主人，正文里不要解释它。"
)

MAX_TOOL_ROUNDS = 5  # 防止模型无限调工具
_AFFECTION_RE = re.compile(r"\[affection\s*:\s*([+-]?\d+)\s*\]\s*$", re.MULTILINE)
_TAIL_BUF = 40  # 流式输出时留出尾部缓冲，等流结束再剥好感度隐藏标记


def _system_prompt(affection: int) -> str:
    """按 PERSONA 环境变量选择人设；好感度只注入内部状态，不展示给用户。"""
    persona = (os.getenv("PERSONA") or "assistant").strip().lower()
    if persona == "catgirl":
        return (
            f"（内部状态：好感度 {affection}，范围 -100~200，仅你可见，勿向主人提及）\n"
            + CATGIRL_SYSTEM_PROMPT
        )
    return DEFAULT_SYSTEM_PROMPT


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

    # 对话历史只取最近 N 条，控制 token；人设需要知道当前好感度
    messages = [{"role": "system", "content": _system_prompt(memory.get_affection(session_id))}]
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
