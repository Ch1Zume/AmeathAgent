from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .weather import DailyGreetingStore, WeatherError, WeatherService, greeting_period, search_cities


class WeatherSignals(QObject):
    ready = Signal(int, object, str, str)
    cities = Signal(object, str)


class DailyWeather(QObject):
    def __init__(self, config, pet, chat, parent=None):
        super().__init__(parent)
        self.config, self.pet, self.chat = config, pet, chat
        self.service = WeatherService(config)
        self.store = DailyGreetingStore(config.directory)
        self.signals = WeatherSignals(self)
        self.signals.ready.connect(self.on_ready)
        self.cancelled = threading.Event()
        self.generation = 0
        self.pending = None
        self.busy = False
        self.closing = False
        self.force_delivery = False
        self.delivery_timer = QTimer(self)
        self.delivery_timer.setInterval(1000)
        self.delivery_timer.timeout.connect(self.deliver)
        self.blocked = lambda: False

    def begin(self, *, force=False):
        settings = replace(self.config.settings)
        today = datetime.now().astimezone().date()
        if (self.closing or self.busy or not settings.weather_city
                or (not force and (not settings.weather_enabled or self.store.already_shown(today)))):
            return
        self.cancelled.set()
        self.cancelled = threading.Event()
        cancelled = self.cancelled
        self.generation += 1
        generation = self.generation
        self.busy = True
        self.force_delivery = force
        self.request_day, self.request_city = today, settings.weather_city

        def run():
            report, message, error = None, "", ""
            try:
                report = self.service.fetch(today=today, settings=settings)
                message = self.service.greet(report, cancelled, settings=settings)
            except WeatherError as exc:
                error = str(exc)
            except Exception:
                error = "天气消息暂时没能取到，稍后再看看吧。"
            if not cancelled.is_set() and not self.closing:
                self.signals.ready.emit(generation, report, message, error)

        threading.Thread(target=run, daemon=True, name="ameath-weather").start()

    def on_ready(self, generation, report, message, error):
        if generation != self.generation or self.cancelled.is_set() or self.closing:
            return
        self.busy = False
        if self.request_day != datetime.now().astimezone().date() or self.request_city != self.config.settings.weather_city:
            return
        if error:
            message = greeting_period(datetime.now().astimezone()) + "呀，我的大英雄～天气消息还没送到，今天先让我陪着你。稍后再看看好不好？"
        self.pending = (report, message, error, self.request_day)
        self.deliver()
        if self.pending:
            self.delivery_timer.start()

    def deliver(self):
        if not self.pending or self.closing:
            self.delivery_timer.stop()
            return
        report, message, error, day = self.pending
        if day != datetime.now().astimezone().date():
            self.pending = None
            self.delivery_timer.stop()
            return
        if self.pet.transition or self.pet.is_moving or self.pet._press is not None or self.chat.busy or (self.chat.isVisible() and not self.force_delivery) or self.blocked():
            return
        self.pending = None
        self.delivery_timer.stop()
        self.pet.note_interaction()
        self.pet.play_action("waving")
        self.chat.show_proactive(message, tooltip=report.source_note if report else error)
        if report is not None:
            try:
                self.store.mark_shown(day)
            except OSError:
                pass

    def invalidate(self):
        self.cancelled.set()
        self.generation += 1
        self.busy = False
        self.pending = None
        self.delivery_timer.stop()

    def shutdown(self):
        self.closing = True
        self.invalidate()


class WeatherDialog(QDialog):
    changed = Signal()
    refresh_requested = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("AmeathAgent · 天气与问候")
        self.resize(430, 290)
        self.signals = WeatherSignals(self)
        self.signals.cities.connect(self.on_cities)
        self.search_generation = 0
        self._closed = False
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("每天首次启动，和我聊聊今天的天气")
        self.enabled.setChecked(config.settings.weather_enabled)
        layout.addWidget(self.enabled)
        self.current = QLabel("当前城市：" + (config.settings.weather_city or "尚未设置"))
        self.current.setWordWrap(True)
        layout.addWidget(self.current)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入城市名，例如：郑州")
        row.addWidget(self.query, 1)
        self.search_button = QPushButton("查找")
        self.search_button.clicked.connect(self.search)
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        self.results = QComboBox()
        layout.addWidget(self.results)
        self.status = QLabel("天气来自 Open-Meteo；只查询所选城市，不读取设备位置。\n问候会使用已配置的模型和人设，模型不可用时使用本地措辞。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        bottom = QHBoxLayout()
        preview = QPushButton("听听今天的天气")
        preview.clicked.connect(self.preview)
        bottom.addWidget(preview)
        save = QPushButton("保存")
        save.clicked.connect(self.save_and_close)
        bottom.addWidget(save)
        layout.addLayout(bottom)

    def search(self):
        query = self.query.text().strip()
        if len(query) < 2:
            self.status.setText("请输入至少两个字的城市名称。")
            return
        self.search_button.setEnabled(False)
        self.status.setText("正在查找城市…")
        def run():
            try:
                cities, error = search_cities(query), ""
            except WeatherError as exc:
                cities, error = [], str(exc)
            if not self._closed:
                self.signals.cities.emit(cities, error)
        threading.Thread(target=run, daemon=True, name="ameath-city-search").start()

    def on_cities(self, cities, error):
        self.search_button.setEnabled(True)
        self.results.clear()
        for city in cities:
            self.results.addItem(city["city"], city)
        self.status.setText(error or ("选择对应的城市后保存。" if cities else "没有找到，可以试试不带省份的城市名或拼音。"))

    def save(self):
        settings = self.config.settings
        city = self.results.currentData()
        if city:
            settings.weather_city, settings.weather_latitude, settings.weather_longitude, settings.weather_timezone = (
                city["city"], city["latitude"], city["longitude"], city["timezone"])
        settings.weather_enabled = self.enabled.isChecked()
        try:
            self.config.save()
        except OSError:
            self.status.setText("设置未能保存，请检查数据目录是否可写。")
            return False
        self.changed.emit()
        return True

    def save_and_close(self):
        if self.save():
            self.accept()

    def preview(self):
        if self.save():
            if not self.config.settings.weather_city:
                self.status.setText("先查找并选择城市，再试试天气问候。")
                return
            self.refresh_requested.emit()
            self.accept()

    def done(self, result):
        self._closed = True
        super().done(result)
