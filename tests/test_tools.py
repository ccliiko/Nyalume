"""工具注册表与纯函数工具。"""

import pytest

from mini_agent.core import memory, tools


def test_tool_schemas_include_reminder_tools():
    names = [t["function"]["name"] for t in tools.TOOL_SCHEMAS]
    assert "create_reminder" in names
    assert "cancel_reminder" in names
    assert "web_search" in names


def test_calculator_safe_math():
    assert tools.execute_tool("calculator", {"expression": "(13*78)+5"}) == "1019"
    assert tools.execute_tool("calculator", {"expression": "2**10"}) == "1024"


def test_calculator_rejects_code():
    result = tools.execute_tool(
        "calculator", {"expression": "__import__('os').system('echo hi')"}
    )
    assert result.startswith("计算失败")


def test_note_save_and_list_with_tag():
    tag = "pytest-tag"
    memory.init_db()
    tools.execute_tool("save_note", {"content": "测试便签内容", "tag": tag})
    rows = memory.note_list(tag)
    assert "测试便签内容" in rows


def test_unknown_tool_returns_error_string():
    assert "未知工具" in tools.execute_tool("not_exist", {})
