"""模型接入层：封装 OpenAI 兼容 API。"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


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
