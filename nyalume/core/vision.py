"""视觉小助手：把图片交给视觉模型，翻译成文字给纯文本模型用。"""

import base64
import mimetypes
import os

from dotenv import load_dotenv
from openai import OpenAI

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

_DEFAULT_MODEL = "deepseek-v4-flash-vision-exp"
_DEFAULT_BASE = "https://api.deepseek.com/v1"


def _vision_settings() -> tuple[str, str, str]:
    """优先使用独立视觉 Key，否则让当前供应商自动选择看图模型。"""
    vision_key = os.getenv("VISION_API_KEY", "")
    if vision_key:
        return (
            vision_key,
            os.getenv("VISION_BASE_URL", "") or _DEFAULT_BASE,
            os.getenv("VISION_MODEL", "") or _DEFAULT_MODEL,
        )
    key = os.getenv("LLM_API_KEY", "")
    base = os.getenv("LLM_BASE_URL", "") or _DEFAULT_BASE
    model = os.getenv("LLM_MODEL", "")
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if provider == "deepseek" or "api.deepseek.com" in base:
        model = model if "vision" in model.lower() else _DEFAULT_MODEL
    elif provider == "siliconflow" and not any(x in model.lower() for x in ("vision", "vl")):
        return "", "", ""
    return key, base, model


def vision_configured() -> bool:
    """视觉可用：独立视觉配置，或当前供应商有可尝试的看图模型。"""
    key, _, model = _vision_settings()
    return bool(key and model and not key.startswith("sk-xxxx"))


def describe_image(path: str, question: str = "") -> str:
    """让视觉模型描述一张本地图片，返回文字（失败返回错误信息）。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return f"图片不存在：{path}"
    key, base, model = _vision_settings()
    if not key or not model or key.startswith("sk-xxxx"):
        return "Nyalume 还不能看这张图喵，请换一个标有“可看图”的模型。"
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    question = (
        (question or "").strip()
        or "用简体中文简洁描述这张图片：主体、文字内容、界面元素、按钮文字，越具体越好。"
    )
    try:
        client = OpenAI(api_key=key, base_url=base, timeout=60)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ],
        )
    except Exception as e:
        return f"看图失败：{type(e).__name__}: {str(e)[:200]}"
    text = (resp.choices[0].message.content or "").strip()
    return text or "（视觉模型没有返回内容）"
