"""cron 解析 + 提醒落库去重。"""

import datetime

from mini_agent.core import reminders


def test_cron_daily_at_nine():
    assert reminders.cron_matches(
        "0 9 * * *", datetime.datetime(2026, 9, 5, 9, 0)
    )
    assert not reminders.cron_matches(
        "0 9 * * *", datetime.datetime(2026, 9, 5, 10, 0)
    )


def test_cron_weekday_only():
    assert reminders.cron_matches(
        "0 9 * * 1", datetime.datetime(2026, 9, 7, 9, 0)  # 周一
    )
    assert not reminders.cron_matches(
        "0 9 * * 1", datetime.datetime(2026, 9, 6, 9, 0)  # 周日
    )


def test_cron_every_30_minutes_and_steps():
    assert reminders.cron_matches(
        "*/30 * * * *", datetime.datetime(2026, 9, 5, 14, 0)
    )
    assert reminders.cron_matches(
        "*/30 * * * *", datetime.datetime(2026, 9, 5, 14, 30)
    )
    assert not reminders.cron_matches(
        "*/30 * * * *", datetime.datetime(2026, 9, 5, 14, 20)
    )
    assert reminders.cron_matches(
        "0 9 * * 1-5", datetime.datetime(2026, 9, 4, 9, 0)  # 周五
    )


def test_one_shot_reminder_auto_deleted_after_fire():
    rid = reminders.add_reminder("喝水", "0 10 * * *", one_shot=True)
    assert reminders.list_reminders(), "一次性提醒应已入库"
    due = reminders.check_due(now=datetime.datetime(2026, 9, 5, 10, 0))
    assert [d["id"] for d in due] == [rid]
    assert not any(r["id"] == rid for r in reminders.list_reminders()), \
        "一次性提醒触发后应自动删除"


def test_recurring_reminder_kept_after_fire():
    rid = reminders.add_reminder("每天喝水", "0 10 * * *", one_shot=False)
    reminders.check_due(now=datetime.datetime(2026, 9, 5, 10, 0))
    assert any(r["id"] == rid for r in reminders.list_reminders())


def test_cron_bad_expression_raises():
    try:
        reminders.cron_matches("0 9 * *")
    except ValueError:
        return
    raise AssertionError("应拒绝 4 段 cron")


def test_reminder_crud_and_dedupe():
    rid = reminders.add_reminder("测试提醒", "0 9 * * *")
    assert any(r["id"] == rid for r in reminders.list_reminders())

    due1 = reminders.check_due(datetime.datetime(2026, 9, 5, 9, 0, 30))
    assert [r["id"] for r in due1] == [rid]
    # 同一分钟第二次检查不再触发
    due2 = reminders.check_due(datetime.datetime(2026, 9, 5, 9, 0, 50))
    assert due2 == []

    assert reminders.delete_reminder(rid)
    assert not any(r["id"] == rid for r in reminders.list_reminders())


def test_add_reminder_rejects_empty_content():
    try:
        reminders.add_reminder("  ", "0 9 * * *")
    except ValueError:
        return
    raise AssertionError("空内容应被拒绝")
