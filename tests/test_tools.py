"""工具注册表与纯函数工具。"""

import pytest

from mini_agent.core import memory, tools


def test_tool_schemas_include_reminder_tools():
    names = [t["function"]["name"] for t in tools.TOOL_SCHEMAS]
    assert "create_reminder" in names
    assert "remind_me_in" in names
    assert "cancel_reminder" in names
    assert "delete_note" in names
    assert "web_search" in names


def test_remind_me_in_is_one_shot_not_every_minute():
    """“X 分钟后提醒一次”必须落成固定时刻 cron，不能变成 */1 每分钟。"""
    result = tools.execute_tool(
        "remind_me_in", {"content": "喝水", "minutes": 1}
    )
    assert "已设好一次性提醒" in result
    rows = tools.list_reminders()
    assert rows, "提醒应已入库"
    cron = rows[0]["cron"]
    assert "*/1" not in cron and cron.split()[0] != "*"
    assert cron.count(" ") == 4
    assert rows[0]["one_shot"] == 1, "remind_me_in 必须标记为一次性"


def test_create_reminder_stays_recurring():
    rows_before = tools.list_reminders()
    result = tools.execute_tool(
        "create_reminder", {"content": "周期性测试", "cron": "0 9 * * *"}
    )
    assert "已设置定时提醒" in result
    rows = tools.list_reminders()
    newest = rows[0]
    assert newest["one_shot"] == 0, "create_reminder 是周期提醒，不能标记一次性"
    tools.execute_tool("cancel_reminder", {"reminder_id": newest["id"]})


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
