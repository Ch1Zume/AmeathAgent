from datetime import datetime, timedelta

import pytest

from ameath.chat import ChatReply, build_chat_payload, parse_chat_reply
from ameath.chat_ui import ChatDialog
from ameath.config import Config, Settings
from ameath.schedule import Schedule, next_occurrence, parse_time, schedule_updates
from ameath.schedule_ui import SchedulePanel, ScheduleReminder, reminder_text
from test_ui import app  # noqa: F401

NOW = datetime(2026, 11, 6, 9, 0)


def test_events_are_stored_listed_updated_and_deleted(tmp_path):
    schedule = Schedule(tmp_path)
    first = schedule.add("组会", NOW + timedelta(hours=2), remind_minutes=15)
    schedule.add("交报告", NOW + timedelta(days=3))
    schedule.add("很久以后", NOW + timedelta(days=30))
    assert [e.title for e in schedule.upcoming(NOW)] == ["组会", "交报告"]
    schedule.update(first, "组会（改到下午）", NOW + timedelta(hours=5), remind_minutes=5, repeat="none")
    assert schedule.get(first).title == "组会（改到下午）" and schedule.get(first).remind_minutes == 5
    schedule.set_done(first)
    assert [e.title for e in schedule.upcoming(NOW)] == ["交报告"]
    schedule.delete(first)
    assert schedule.get(first) is None
    with pytest.raises(ValueError):
        schedule.add("记住我的密码是 123", NOW)


def test_reminders_fire_once_and_repeating_events_roll_forward(tmp_path):
    schedule = Schedule(tmp_path)
    schedule.add("站会", NOW + timedelta(minutes=10), remind_minutes=10, repeat="weekdays")
    schedule.add("喝水", NOW + timedelta(minutes=30), remind_minutes=10)
    reminder = ScheduleReminder(schedule)
    fired = []
    reminder.due.connect(fired.append)
    assert [e.title for e in reminder.check(NOW)] == ["站会"]
    assert reminder.check(NOW) == []
    moved = next(e for e in schedule.events() if e.title == "站会")
    assert moved.start == datetime(2026, 11, 9, 9, 10)  # Friday -> Monday for weekdays
    assert [e.title for e in reminder.check(NOW + timedelta(minutes=25))] == ["喝水"]
    assert reminder.check(NOW + timedelta(minutes=40)) == []
    assert [e.title for e in fired] == ["站会", "喝水"]


def test_missed_one_off_reminders_are_still_shown_briefly_but_not_hours_later(tmp_path):
    schedule = Schedule(tmp_path)
    schedule.add("早会", NOW - timedelta(minutes=30))
    schedule.add("昨天的事", NOW - timedelta(days=1))
    assert [e.title for e in schedule.due(NOW)] == ["早会"]
    assert "刚才" in reminder_text(schedule.due(NOW)[0], NOW)


def test_repeat_rules_and_time_parsing():
    assert next_occurrence(NOW, "daily", NOW) == NOW + timedelta(days=1)
    assert next_occurrence(NOW, "weekly", NOW + timedelta(days=10)) == NOW + timedelta(days=14)
    assert next_occurrence(NOW, "none", NOW) is None
    assert parse_time("2026-11-07T15:00:00+08:00") == datetime(2026, 11, 7, 15, 0)
    assert parse_time("2026-11-07") == datetime(2026, 11, 7, 9, 0)
    assert parse_time("明天下午") is None


def test_chat_schedule_updates_are_validated():
    items = schedule_updates([
        {"title": "开会", "start": "2026-11-07 15:00", "remind_minutes": 30, "repeat": "weekly"},
        {"title": "过去的事", "start": "2026-11-01 15:00"},
        {"title": "", "start": "2026-11-07 15:00"},
        {"title": "没有时间"},
    ], now=NOW)
    assert len(items) == 1 and items[0]["start"] == datetime(2026, 11, 7, 15, 0) and items[0]["repeat"] == "weekly"
    assert schedule_updates("x") == ()
    reply = parse_chat_reply('{"reply":["好，记下了。"],"emotion":"happy","memory_updates":[],'
                             '"schedule_updates":[{"title":"开会","start":"2099-01-01 10:00"}]}')
    assert reply.schedules[0]["title"] == "开会" and reply.schedules[0]["remind_minutes"] == 10


def test_chat_payload_carries_upcoming_schedule_as_background():
    payload = build_chat_payload("今天有什么安排", [], Settings(mode="live"),
                                 schedule=[{"title": "组会", "start": "2026-11-06 15:00"}])
    contents = [m["content"] for m in payload["messages"]]
    assert any("组会" in c and "仅作背景" in c for c in contents)
    assert "schedule_updates" in payload["messages"][0]["content"]
    assert not any("日程表中" in c for c in (m["content"] for m in build_chat_payload("你好", [], Settings())["messages"]))


def test_chat_reply_adds_schedule_but_late_replies_do_not(app, tmp_path):
    chat = ChatDialog(Config(tmp_path))
    changed = []
    chat.schedule_changed.connect(lambda: changed.append(1))
    event = {"title": "组会", "start": datetime(2099, 1, 1, 10, 0), "remind_minutes": 10, "repeat": "none", "note": ""}
    chat.generation = 3
    chat.on_reply(2, "提醒我开会", ChatReply("好", schedules=(event,)))
    assert chat.agent.schedule.events() == []
    chat.busy = True
    chat.on_reply(3, "提醒我开会", ChatReply("好", schedules=(event,)))
    assert [e.title for e in chat.agent.schedule.events()] == ["组会"] and changed == [1]
    assert chat.agent.schedule.events()[0].source == "chat"
    chat.shutdown()


def test_schedule_panel_adds_edits_completes_and_deletes(app, tmp_path):
    schedule = Schedule(tmp_path)
    panel = SchedulePanel(schedule)
    panel.title.setText("写周报")
    panel.save()
    assert [e.title for e in schedule.events()] == ["写周报"]
    assert panel.table.rowCount() == 1
    panel.table.selectRow(0)
    assert panel.editing is not None and panel.save_button.text() == "保存修改"
    panel.title.setText("写周报和月报")
    panel.save()
    assert [e.title for e in schedule.events()] == ["写周报和月报"]
    panel.table.selectRow(0)
    panel.mark_done()
    assert schedule.events()[0].done
    panel.table.selectRow(0)
    panel.delete()
    assert schedule.events() == [] and panel.table.rowCount() == 0
