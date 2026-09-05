"""核心 Agent 循环：记忆 + 模型 + 工具调用。"""

import json

import llm
import memory
from tools import TOOL_SCHEMAS, execute_tool

SYSTEM_PROMPT = (
    "你是一个个人 AI 助手。回答简洁、友好。"
    "当需要计算、查时间或记便签时，请调用对应工具。"
    "用户说“记下来 / 记个便签 / 记个标签”时，先确定要记的具体内容："
    "如果是“算完再记”，就把算式和结果一起存成便签（如 (13*78)=1014），"
    "并给一个简短标签（如 计算记录）；保存后直接告诉用户记了什么，不要反问用户。"
)
MAX_TOOL_ROUNDS = 5  # 防止模型无限调工具


def run(session_id: str, user_text: str) -> str:
    """处理一条用户消息，返回助手最终回复。"""
    memory.save_message(session_id, "user", user_text)

    # 对话历史只取最近 N 条，控制 token
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(memory.load_history(session_id))

    # 本轮工具调用产生的中间消息，不写回历史库
    tool_messages: list[dict] = []

    for _ in range(MAX_TOOL_ROUNDS):
        resp = llm.chat_once(messages + tool_messages, tools=TOOL_SCHEMAS)
        choice = resp["choices"][0]["message"]

        if not choice.get("tool_calls"):
            final_text = choice.get("content") or ""
            memory.save_message(session_id, "assistant", final_text)
            return final_text

        # 把带工具调用的 assistant 消息和工具结果追加进本轮上下文
        tool_messages.append(
            {
                "role": "assistant",
                "content": choice.get("content"),
                "tool_calls": choice["tool_calls"],
            }
        )
        for call in choice["tool_calls"]:
            fn_name = call["function"]["name"]
            try:
                fn_args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                fn_args = {}
            result = execute_tool(fn_name, fn_args)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                }
            )

    fallback = "抱歉，我处理这个问题时循环太多次了，请换个说法再试。"
    memory.save_message(session_id, "assistant", fallback)
    return fallback
