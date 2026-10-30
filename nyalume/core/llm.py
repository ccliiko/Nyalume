"""模型接入层：封装 OpenAI 兼容 API。"""

import os
import threading
import time
import json
import logging

from dotenv import load_dotenv
from openai import OpenAI

# 仓库根（nyalume/core/llm.py 的上三级），无论从哪启动都能读到 .env
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


LLM_TIMEOUT_SECONDS = 90.0


class IncompleteResponseError(RuntimeError):
    """The provider ended a stream without a complete response."""

    def __init__(self, finish_reason=None):
        self.finish_reason = finish_reason
        reason = {
            "length": "回复达到模型长度上限，尚未完成。可以缩小问题后重试。",
            "content_filter": "回复被模型服务的内容过滤中止，尚未完成。可以调整问题后重试。",
            None: "模型连接已结束，但没有收到完整回复的结束标记。可以重试。",
        }.get(finish_reason, "模型未完整结束这次回复或工具调用。可以重试。")
        super().__init__(reason + " 已显示的部分保留在当前对话中，本次未作为完整回复保存。")


def record_usage(usage, started: float, first_token_ms=None) -> None:
    try:
        _record_usage(usage, started, first_token_ms)
    except Exception as error:
        # Metrics failure must not discard a response or mask cancellation.
        logging.getLogger(__name__).warning('Usage recording failed: %s', type(error).__name__)


def _record_usage(usage, started: float, first_token_ms=None) -> None:
    """Store actual provider counters; unavailable cache counters stay null."""
    from . import memory
    data = usage.model_dump() if hasattr(usage, 'model_dump') else usage or {}
    details = data.get('prompt_tokens_details') or {}
    cached = details.get('cached_tokens', data.get('prompt_cache_hit_tokens'))
    row = {'model': get_model(), 'provider': os.getenv('LLM_PROVIDER', 'configured'),
           'timestamp': time.time(), 'usage': data, 'cached_tokens': cached,
           'duration_ms': round((time.perf_counter() - started) * 1000, 2),
           'first_token_ms': first_token_ms}
    # Bounded diagnostics contain counters only, no prompts or credentials.
    with memory._conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute("SELECT value FROM settings WHERE key='llm_usage'").fetchone()
        rows = json.loads(old['value']) if old else []
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('llm_usage',?)",
                     (json.dumps((rows + [row])[-50:]),))


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
    started = time.perf_counter()
    resp = get_client().chat.completions.create(**kwargs)
    record_usage(getattr(resp, 'usage', None), started)
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
    不完整结束抛出 IncompleteResponseError；已产出的增量不撤回。
    """
    kwargs = {"model": get_model(), "messages": messages, "stream": True,
              "stream_options": {"include_usage": True}}
    if tools:
        kwargs["tools"] = tools
    if timeout is not None:
        kwargs["timeout"] = max(1.0, float(timeout))
    kwargs.update(_effort_options())
    started = time.perf_counter()
    first_token_ms = None
    usage = None
    finish_reason = None
    tool_calls = {}
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
            if getattr(chunk, 'usage', None) is not None:
                usage = chunk.usage
            if cancel_event is not None and cancel_event.is_set():
                break
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason is not None:
                finish_reason = choice.finish_reason
            delta = choice.delta
            reasoning = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if first_token_ms is None and (reasoning or delta.content or delta.tool_calls):
                first_token_ms = round((time.perf_counter() - started) * 1000, 2)
            if reasoning:
                yield {"kind": "reasoning", "text": reasoning}
            if delta.content:
                yield {"kind": "content", "text": delta.content}
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    fn = tc.function
                    index = tc.index if tc.index is not None else 0
                    call = tool_calls.setdefault(index, {"name": "", "arguments": ""})
                    if fn and fn.name:
                        call["name"] = fn.name
                    if fn and fn.arguments:
                        call["arguments"] += fn.arguments
                    yield {
                        "kind": "tool_delta",
                        "index": index,
                        "id": tc.id or "",
                        "name": fn.name if fn and fn.name else "",
                        "arguments": fn.arguments if fn and fn.arguments else "",
                    }
        if cancel_event is None or not cancel_event.is_set():
            complete = finish_reason == "stop" and not tool_calls
            if finish_reason == "tool_calls" and tool_calls:
                try:
                    complete = all(
                        call["name"] and isinstance(json.loads(call["arguments"] or "{}"), dict)
                        for call in tool_calls.values()
                    )
                except (ValueError, TypeError):
                    complete = False
            if not complete:
                raise IncompleteResponseError(finish_reason)
    except Exception:
        if cancel_event is None or not cancel_event.is_set():
            raise
    finally:
        record_usage(usage, started, first_token_ms)
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
