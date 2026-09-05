"""模型接入层：封装 OpenAI 兼容 API。"""

import os

from dotenv import load_dotenv
from openai import OpenAI

# 仓库根（mini_agent/core/llm.py 的上三级），无论从哪启动都能读到 .env
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


def get_client() -> OpenAI:
    """从环境变量读取配置，返回 OpenAI 客户端。"""
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    if not api_key or api_key.startswith("sk-xxxx"):
        raise RuntimeError("请先配置 .env 里的 LLM_API_KEY")
    return OpenAI(api_key=api_key, base_url=base_url)


def get_model() -> str:
    return os.getenv("LLM_MODEL", "deepseek-chat")


def chat_once(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """调用一次模型，返回完整响应对象（dict 形式）。"""
    kwargs = {"model": get_model(), "messages": messages}
    if tools:
        kwargs["tools"] = tools
    resp = get_client().chat.completions.create(**kwargs)
    return resp.model_dump()


def chat_stream(messages: list[dict], tools: list[dict] | None = None):
    """流式调用一次模型，逐块产出事件 dict：

    - {"kind": "content", "text": "..."}       正文增量
    - {"kind": "tool_delta", "index", "id", "name", "arguments"}  工具调用增量
    """
    kwargs = {"model": get_model(), "messages": messages, "stream": True}
    if tools:
        kwargs["tools"] = tools
    stream = get_client().chat.completions.create(**kwargs)
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            yield {"kind": "content", "text": delta.content}
        if delta.tool_calls:
            for tc in delta.tool_calls:
                fn = tc.function
                yield {
                    "kind": "tool_delta",
                    "index": tc.index if tc.index is not None else 0,
                    "id": tc.id or "",
                    "name": fn.name if fn and fn.name else "",
                    "arguments": fn.arguments if fn and fn.arguments else "",
                }
