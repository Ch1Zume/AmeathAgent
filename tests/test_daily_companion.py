import json
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from ameath.config import Config
from ameath.focus import FocusClock, Pomodoro, clock_text
from ameath.llm import Completion, ModelError
from ameath.weather import DailyGreetingStore, WeatherError, WeatherReport, WeatherService, parse_report
from test_ui import app, window


def data_for(day):
    return {"daily": {"time": [(day - timedelta(days=1)).isoformat(), day.isoformat()],
                      "temperature_2m_min": [18, 15], "temperature_2m_max": [28, 25],
                      "temperature_2m_mean": [23, 20], "weather_code": [2, 61],
                      "precipitation_probability_max": [10, 75]}}


def test_weather_uses_the_requested_day_and_actual_yesterday_temperature():
    day = date(2026, 10, 25)
    report = parse_report(data_for(day), "郑州", day)
    assert report.low == 15 and report.high == 25
    assert "小雨" in report.facts and "凉了约 3℃" in report.facts
    data = data_for(day)
    data["daily"]["temperature_2m_mean"][0] = None
    assert "昨天" not in parse_report(data, "郑州", day).facts


@pytest.mark.parametrize("bad", ["stale", "missing", "nan", "reversed", "unknown_code"])
def test_invalid_or_stale_weather_is_never_reported_as_today(bad):
    day = date(2026, 10, 25)
    data = data_for(day)
    if bad == "stale":
        data["daily"]["time"] = ["2026-10-23", "2026-10-24"]
    elif bad == "missing":
        data["daily"]["temperature_2m_min"][1] = None
    elif bad == "nan":
        data["daily"]["temperature_2m_max"][1] = float("nan")
    elif bad == "reversed":
        data["daily"]["temperature_2m_max"][1] = 5
    else:
        data["daily"]["weather_code"][1] = 999
    with pytest.raises(WeatherError):
        parse_report(data, "郑州", day)


def test_weather_greeting_keeps_facts_even_when_model_fails_or_invents_numbers(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    service = WeatherService(config)
    report = parse_report(data_for(date(2026, 10, 25)), "郑州", date(2026, 10, 25))
    class Client:
        def request(self, *args, **kwargs):
            return Completion('{"opening":"早上好，气温99度","ending":"今天99度，明天也99度，别出门了。"}', {}, "test")
    service.client = Client()
    message = service.greet(report, threading.Event(), now=datetime(2026, 10, 25, 8))
    assert message.startswith("早上好") and "99" not in message and report.facts in message
    class Failing:
        def request(self, *args, **kwargs):
            raise ModelError("服务余额不足")
    service.client = Failing()
    assert report.facts in service.greet(report, threading.Event())


def test_weather_daily_mark_survives_restart_and_allows_tomorrow(tmp_path):
    today = date(2026, 10, 25)
    store = DailyGreetingStore(tmp_path)
    assert not store.already_shown(today)
    store.mark_shown(today)
    assert DailyGreetingStore(tmp_path).already_shown(today)
    assert not store.already_shown(today + timedelta(days=1))


def test_daily_weather_defers_during_chat_and_marks_only_after_display(window):
    weather = window.weather
    today = datetime.now().astimezone().date()
    report = parse_report(data_for(today), "郑州", today)
    window.config.settings.weather_city = "郑州"
    weather.request_day, weather.request_city = today, "郑州"
    window.open_chat()
    weather.on_ready(0, report, "早上好，我看过今天的天气啦。", "")
    assert weather.pending and not weather.store.already_shown(today)
    window.chat.hide()
    weather.deliver()
    assert weather.pending is None and weather.store.already_shown(today)
    assert window.chat.reply_bubble.isVisible()
    assert not window.chat.isVisible()
    weather.begin()
    assert not weather.busy


def test_weather_shutdown_and_city_change_ignore_late_results(window):
    weather = window.weather
    today = datetime.now().astimezone().date()
    weather.request_day, weather.request_city = today, "郑州"
    window.config.settings.weather_city = "北京"
    weather.on_ready(0, None, "late city", "")
    assert weather.pending is None and not window.chat.messages
    weather.shutdown()
    weather.on_ready(0, None, "late result", "")
    assert not window.chat.messages


def test_focus_runs_exactly_two_work_rest_cycles_and_finishes_after_final_rest():
    now = [100.0]
    clock = FocusClock(lambda: now[0])
    clock.start(50, 10, 2)
    for expected, step in [("rest", 3000), ("work", 600), ("rest", 3000), ("complete", 600)]:
        now[0] += step
        clock.tick()
        assert clock.phase == expected
    assert clock.cycle == 2 and not clock.active


def test_focus_pause_postpone_and_sleep_do_not_skip_the_break():
    now = [0.0]
    clock = FocusClock(lambda: now[0])
    clock.start(1, 1, 1)
    now[0] = 20
    clock.toggle_pause()
    now[0] = 800
    clock.tick()
    assert clock.phase == "work" and clock.remaining == 40
    clock.toggle_pause()
    now[0] += 40000
    clock.tick()
    assert clock.phase == "rest" and clock.remaining == 60
    clock.postpone()
    assert clock.phase == "snooze" and clock.remaining == 300 and clock.cycle == 1
    now[0] += 300
    clock.tick()
    assert clock.phase == "rest" and clock.remaining == 60
    clock.stop()
    now[0] += 1000
    clock.tick()
    assert clock.phase == "idle"


def test_focus_flies_to_center_and_stop_clears_obstruction(window, app):
    now = [0.0]
    focus = window.focus
    focus.clock = FocusClock(lambda: now[0])
    window.pet.show()
    window.pet.timer.stop()
    original = QPoint(30, 100)
    window.pet.move(original)
    focus.start(1, 1, 2)
    now[0] = 60
    focus.tick()
    assert focus.break_bubble.isVisible() and window.pet.is_moving
    for _ in range(40):
        window.pet.advance(250)
    screen = window.pet.screen().availableGeometry()
    assert abs(window.pet.x() + 124 - screen.center().x()) < 3
    assert abs(window.pet.y() + 186 - screen.center().y()) < 3
    focus.postpone()
    assert not focus.break_bubble.isVisible() and focus.clock.phase == "snooze"
    for _ in range(40):
        window.pet.advance(250)
    assert window.pet.pos() == original
    focus.stop()
    assert not focus.timer.isActive() and not focus.break_bubble.isVisible()


def test_input_and_reply_are_separate_frameless_bubbles_and_follow_pet(window, app):
    window.hide()
    window.pet.move(400, 270)
    window.pet.show()
    window.open_chat()
    assert window.chat.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.chat.reply_bubble.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not window.chat.reply_bubble.isVisible()
    window.chat.show_proactive("我在这里，想和你说说话。")
    previous = window.chat.reply_bubble.pos()
    window.pet.move(window.pet.pos() + QPoint(-100, 30))
    app.processEvents()
    assert window.chat.reply_bubble.pos() != previous
    assert window.chat.reply_bubble.text.toPlainText() == "我在这里，想和你说说话。"
    assert not window.isVisible()
    window.chat.hide()
    assert not window.chat.reply_bubble.isVisible()


def test_entry_finishes_before_weather_and_exit_hides_pet(window):
    events = []
    window.weather.begin = lambda: events.append("weather")
    window.pet.timer.stop()
    window.start_companion()
    window.pet.timer.stop()
    assert window.pet.transition == "enter" and events == []
    window.pet.advance(1799)
    assert events == []
    window.pet.advance(1)
    assert events == ["weather"] and window.pet.animation == "waving"
    window.pet.start_transition("exit")
    window.pet.timer.stop()
    window.pet.advance(1800)
    assert not window.pet.isVisible()


def test_weather_and_focus_settings_persist_without_replacing_model_config(tmp_path):
    config = Config(tmp_path)
    config.settings.model = "test-model"
    config.settings.weather_city = "郑州"
    config.settings.weather_latitude, config.settings.weather_longitude = 34.75778, 113.64861
    config.settings.focus_work_minutes, config.settings.focus_rest_minutes, config.settings.focus_cycles = 50, 10, 2
    config.save()
    saved = Config(tmp_path).settings
    assert saved.model == "test-model" and saved.weather_city == "郑州"
    assert (saved.focus_work_minutes, saved.focus_rest_minutes, saved.focus_cycles) == (50, 10, 2)


def test_pomodoro_controls_start_pause_resume_and_stop_without_hidden_timer(window, app):
    dialog = window.focus_dialog
    window.open_focus()
    QTest.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
    assert window.focus.clock.phase == "work" and window.focus.timer.isActive()
    window.open_focus()
    QTest.mouseClick(dialog.pause_button, Qt.MouseButton.LeftButton)
    assert window.focus.clock.paused
    QTest.mouseClick(dialog.pause_button, Qt.MouseButton.LeftButton)
    assert not window.focus.clock.paused
    QTest.mouseClick(dialog.stop_button, Qt.MouseButton.LeftButton)
    assert not window.focus.clock.active and not window.focus.timer.isActive()


def test_final_rest_returns_pet_before_ending_the_session(window):
    now = [0.0]
    focus = window.focus
    focus.clock = FocusClock(lambda: now[0])
    window.pet.show()
    window.pet.timer.stop()
    original = QPoint(40, 120)
    window.pet.move(original)
    focus.start(1, 1, 1)
    now[0] = 60
    focus.tick()
    for _ in range(40):
        window.pet.advance(250)
    now[0] = 120
    focus.tick()
    assert focus.clock.phase == "complete" and not focus.break_bubble.isVisible()
    assert window.pet.is_moving
    for _ in range(40):
        window.pet.advance(250)
    assert window.pet.pos() == original


def test_manual_weather_preview_can_show_beside_an_open_input_bubble(window):
    weather = window.weather
    today = datetime.now().astimezone().date()
    report = parse_report(data_for(today), "郑州", today)
    window.config.settings.weather_city = "郑州"
    weather.request_day, weather.request_city, weather.force_delivery = today, "郑州", True
    window.open_chat()
    weather.on_ready(0, report, "今天的预报已经查到啦。", "")
    assert weather.pending is None and window.chat.isVisible()
    assert window.chat.transcript.toPlainText() == "今天的预报已经查到啦。"
