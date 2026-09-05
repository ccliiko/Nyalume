"""工具定义与执行：当前时间 / 安全计算器 / 便签存取。"""

import ast
import datetime
import operator

from memory import note_list, note_save

# 模型看到的工具描述（OpenAI 函数调用格式）
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前日期和时间",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行四则运算表达式，如 (1+2)*3",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "数学表达式"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "保存一条带标签的便签（作为长期记忆）。"
            "若用户只说“记下来/记个便签/记个标签”而没有说明要记的内容，"
            "就把上下文中最该记的内容整理成 content，例如计算结果记为 (13*78)=1014，"
            "并自动取一个简短 tag（如 计算记录），不要留空或含糊。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要保存的具体便签内容，例如 (13*78)=1014",
                    },
                    "tag": {
                        "type": "string",
                        "description": "可选标签，用于归类，例如 计算记录/待办/灵感",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "查看最近保存的便签，可按标签筛选",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "可选：只查看该标签下的便签；不填则查看全部最近便签",
                    }
                },
            },
        },
    },
]


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
        return str(result)
    except Exception as e:
        return f"计算失败：{e}"


def execute_tool(name: str, arguments: dict) -> str:
    """根据函数名执行对应工具，返回字符串结果。"""
    if name == "get_current_time":
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if name == "calculator":
        return _safe_calc(arguments.get("expression", ""))
    if name == "save_note":
        return note_save(arguments.get("content", ""), arguments.get("tag", ""))
    if name == "list_notes":
        return note_list(arguments.get("tag", ""))
    return f"未知工具：{name}"
