"""模型接入层：封装 OpenAI 兼容 API。"""

import os
import threading

from dotenv import load_dotenv
from openai import OpenAI

# 仓库根（nyalume/core/llm.py 的上三级），无论从哪启动都能读到 .env
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


LLM_TIMEOUT_SECONDS = 90.0


def get_client() -> OpenAI:
    """从环境变量读取配置，返回 OpenAI 客户端。"""
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    if not api_key or api_key.startswith("sk-xxxx"):
        raise RuntimeError("请先配置 .env 里的 LLM_API_KEY")
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=2,
    )


def get_model() -> str:
    return os.getenv("LLM_MODEL", "deepseek-chat")


def _effort_options() -> dict:
    """按供应商使用各自兼容的推理等级传参方式。"""
    effort = os.getenv("LLM_REASONING_EFFORT", "").strip()
    model = get_model()
    if effort and model.startswith("deepseek-v4") and "vision" not in model:
        return {"extra_body": {"reasoning_effort": effort}}
    if effort and os.getenv("LLM_PROVIDER", "").lower() == "openai" and model.startswith("gpt-5"):
        return {"reasoning_effort": effort}
    return {}


def chat_once(
    messages: list[dict], tools: list[dict] | None = None, timeout: float | None = None
) -> dict:
    """调用一次模型，返回完整响应对象（dict 形式）。"""
    kwargs = {"model": get_model(), "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if timeout is not None:
        kwargs["timeout"] = max(1.0, float(timeout))
    kwargs.update(_effort_options())
    resp = get_client().chat.completions.create(**kwargs)
    return resp.model_dump()


def chat_stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: float | None = None,
    cancel_event=None,
):
    """流式调用一次模型，逐块产出事件 dict：

    - {"kind": "content", "text": "..."}       正文增量
    - {"kind": "tool_delta", "index", "id", "name", "arguments"}  工具调用增量
    """
    kwargs = {"model": get_model(), "messages": messages, "stream": True}
    if tools:
        kwargs["tools"] = tools
    if timeout is not None:
        kwargs["timeout"] = max(1.0, float(timeout))
    kwargs.update(_effort_options())
    stream = get_client().chat.completions.create(**kwargs)
    watcher_done = threading.Event()

    def close_when_cancelled() -> None:
        while not watcher_done.wait(0.1):
            if cancel_event is not None and cancel_event.is_set():
                stream.close()
                return

    if cancel_event is not None:
        threading.Thread(target=close_when_cancelled, daemon=True).start()
    try:
        for chunk in stream:
            if cancel_event is not None and cancel_event.is_set():
                break
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if reasoning:
                yield {"kind": "reasoning", "text": reasoning}
                continue
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
    except Exception:
        if cancel_event is None or not cancel_event.is_set():
            raise
    finally:
        watcher_done.set()
        try:
            stream.close()
        except Exception:
            pass


def chat_text(messages: list[dict], timeout: float | None = None, cancel_event=None) -> str:
    """Collect a cancellable streaming response for summaries and memory extraction."""
    return "".join(
        event["text"]
        for event in chat_stream(
            messages, timeout=timeout, cancel_event=cancel_event
        )
        if event["kind"] == "content"
    ).strip()
