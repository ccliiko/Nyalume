"""工具注册表 + 工具实现。

设计：每加一个工具只需用 @register 注册一次（名字、描述、参数声明），
模型可见的 function calling schema 由注册表自动生成，执行时按名字查表分发。
缺参数 / 未知工具 / 执行异常都以字符串返回，由模型自行向用户解释，不会中断对话。
"""

import ast
import datetime
import operator
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from memory import note_list, note_save

_REGISTRY: dict[str, dict] = {}


def register(
    name: str,
    description: str,
    parameters: dict,
    required: list[str] | None = None,
):
    """注册一个工具：description/parameters 会生成模型可见的 schema。"""

    def wrap(func):
        if name in _REGISTRY:
            raise ValueError(f"工具重名：{name}")
        _REGISTRY[name] = {
            "func": func,
            "description": description,
            "parameters": parameters,
            "required": list(required or []),
        }
        return func

    return wrap


def tool_schemas() -> list[dict]:
    """由注册表自动生成 OpenAI function calling 格式的工具列表。"""
    schemas = []
    for name, spec in _REGISTRY.items():
        params: dict = {"type": "object", "properties": spec["parameters"]}
        if spec["required"]:
            params["required"] = spec["required"]
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": params,
                },
            }
        )
    return schemas


def execute_tool(name: str, arguments: dict) -> str:
    """按名字查注册表执行工具，返回字符串结果（错误也转成字符串）。"""
    spec = _REGISTRY.get(name)
    if not spec:
        return f"未知工具：{name}（可用工具：{', '.join(_REGISTRY)}）"
    missing = [
        key for key in spec["required"] if arguments.get(key) in (None, "")
    ]
    if missing:
        return f"工具 {name} 缺少必要参数：{'、'.join(missing)}"
    try:
        result = spec["func"](**arguments)
    except Exception as e:
        return f"工具 {name} 执行失败：{type(e).__name__}: {e}"
    return result if isinstance(result, str) else str(result)


# ---------- 工具 1：当前时间 ----------


@register(
    "get_current_time",
    "获取当前日期和时间，用于回答“现在几点”“今天几号”等问题。",
    {},
)
def _get_current_time() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 工具 2：安全计算器 ----------


def _safe_calc(expression: str) -> str:
    """只允许数字和四则运算的表达式求值，避免任意代码执行。"""
    allowed_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_ops:
            return allowed_ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_ops:
            return allowed_ops[type(node.op)](_eval(node.operand))
        raise ValueError("表达式包含不支持的语法")

    try:
        result = _eval(ast.parse(expression, mode="eval"))
    except Exception as e:
        return f"计算失败：{e}"
    return str(result)


@register(
    "calculator",
    "执行数学表达式求值（四则运算、幂、取模等），如 (13*78)+5。"
    "只做数学计算，不会执行其他代码。",
    {
        "expression": {
            "type": "string",
            "description": "要计算的数学表达式，如 (13*78)+5",
        }
    },
    required=["expression"],
)
def _calculator(expression: str) -> str:
    return _safe_calc(expression)


# ---------- 工具 3/4：带标签的便签 ----------


@register(
    "save_note",
    "保存一条带标签的便签（作为长期记忆）。"
    "若用户只说“记下来/记个便签/记个标签”而没有说明要记的内容，"
    "就把上下文中最该记的内容整理成 content，例如计算结果记成 (13*78)=1014，"
    "并自动取一个简短 tag（如 计算记录），不要留空或含含糊。",
    {
        "content": {
            "type": "string",
            "description": "要保存的具体便签内容，例如 (13*78)=1014",
        },
        "tag": {
            "type": "string",
            "description": "可选标签，用于归类，例如 计算记录/待办/灵感",
        },
    },
    required=["content"],
)
def _save_note(content: str, tag: str = "") -> str:
    return note_save(content, tag)


@register(
    "list_notes",
    "查看最近保存的便签，可按标签筛选。",
    {
        "tag": {
            "type": "string",
            "description": "可选：只看该标签下的便签；不填则看全部最近便签",
        }
    },
)
def _list_notes(tag: str = "") -> str:
    return note_list(tag)


# ---------- 工具 5：网页搜索（必应，免密钥） ----------

_SEARCH_TIMEOUT = 12
_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _html_to_text(raw: str) -> str:
    """去掉 HTML 标签并折叠空白，只留可见文本。"""
    text = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"\s+", " ", text).strip()


def _bing_search(query: str, limit: int) -> list[dict]:
    """走必应 RSS 接口：无需 key、返回干净 XML，国内可直接访问。"""
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"format": "rss", "q": query}
    )
    req = urllib.request.Request(url, headers=_SEARCH_HEADERS)
    with urllib.request.urlopen(req, timeout=_SEARCH_TIMEOUT) as resp:
        xml_bytes = resp.read()
    root = ET.fromstring(xml_bytes)

    results: list[dict] = []
    seen_titles: set[str] = set()
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        snippet = _html_to_text(item.findtext("description") or "")
        if (
            not title
            or title in seen_titles
            or not link.startswith(("http://", "https://"))
        ):
            continue
        seen_titles.add(title)
        results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


@register(
    "web_search",
    "在互联网上搜索公开信息（必应网页搜索，无需 API key）。"
    "适合回答时效性强、模型训练数据里没有或不确定的问题：新闻、最新版本、"
    "第三方文档、某个人/公司/产品的最新情况等。搜索后请基于结果回答，可引用链接。",
    {
        "query": {
            "type": "string",
            "description": "搜索关键词，尽量具体，中英文均可，例如 DeepSeek API 文档",
        },
        "max_results": {
            "type": "integer",
            "description": "最多返回几条结果，范围 1~8，默认 5",
        },
    },
    required=["query"],
)
def _web_search(query: str, max_results: int = 5) -> str:
    try:
        limit = max(1, min(8, int(max_results)))
    except (TypeError, ValueError):
        limit = 5
    query = (query or "").strip()[:150]
    if not query:
        return "搜索失败：缺少查询关键词"

    try:
        results = _bing_search(query, limit)
    except Exception as e:
        return f"搜索失败（网络或解析错误）：{type(e).__name__}: {e}"

    if not results:
        return f"没有搜到与「{query}」相关的结果，可以换个关键词再试。"
    lines = [f"「{query}」的搜索结果："]
    for index, item in enumerate(results, 1):
        lines.append(
            f"{index}. {item['title']}\n"
            f"   {item['url']}\n"
            f"   {item['snippet']}"
        )
    return "\n".join(lines)


# 供 agent.py 使用的模型可见工具列表（注册完成后生成一次）
TOOL_SCHEMAS = tool_schemas()
