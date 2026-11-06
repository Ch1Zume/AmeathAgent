"""Schedule page and the timer that lets the pet remind the user."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QDateTime, QObject, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDateTimeEdit, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .schedule import REPEATS, Event, Schedule

RANGES = {"today": "今天", "week": "7 天内", "all": "全部"}
WEEKDAYS = "一二三四五六日"


def qdatetime(value: datetime) -> QDateTime:
    return QDateTime(QDate(value.year, value.month, value.day), QTime(value.hour, value.minute))


def describe(event: Event, now: datetime | None = None) -> str:
    now = now or datetime.now()
    day = event.start.date()
    if day == now.date():
        label = "今天"
    elif day == (now + timedelta(days=1)).date():
        label = "明天"
    else:
        label = f"{event.start.month}月{event.start.day}日（周{WEEKDAYS[event.start.weekday()]}）"
    return f"{label} {event.start:%H:%M}"


class SchedulePanel(QWidget):
    changed = Signal()

    def __init__(self, schedule: Schedule):
        super().__init__()
        self.schedule = schedule
        self.range = "week"
        self.editing: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 22, 25, 20)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        labels = QVBoxLayout()
        title = QLabel("日程表")
        title.setObjectName("headline")
        labels.addWidget(title)
        desc = QLabel("到了提醒时间，桌宠会提醒你。也可以在聊天里直接说“明天下午三点提醒我开会”。")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        labels.addWidget(desc)
        heading.addLayout(labels, 1)
        self.setStyleSheet("QPushButton:checked { background: #f7cede; color: #96365f; border-color: #c5497c; font-weight: 600; }")
        self.group = QButtonGroup(self)
        for key, label in RANGES.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self.range)
            button.clicked.connect(lambda _checked=False, key=key: self.set_range(key))
            self.group.addButton(button)
            heading.addWidget(button)
        layout.addLayout(heading)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "事项", "提醒", "重复"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.load_selected)
        layout.addWidget(self.table, 1)
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setMaxLength(80)
        self.title.setPlaceholderText("要做的事，例如：组会")
        form.addRow("事项", self.title)
        self.start = QDateTimeEdit()
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("yyyy-MM-dd HH:mm")
        form.addRow("时间", self.start)
        row = QHBoxLayout()
        self.remind = QSpinBox()
        self.remind.setRange(0, 1440)
        self.remind.setSuffix(" 分钟前提醒")
        self.remind.setValue(10)
        row.addWidget(self.remind)
        self.repeat = QComboBox()
        for key, label in REPEATS.items():
            self.repeat.addItem(label, key)
        row.addWidget(self.repeat)
        row.addStretch(1)
        form.addRow("提醒", row)
        self.note = QLineEdit()
        self.note.setMaxLength(400)
        self.note.setPlaceholderText("备注（可选）")
        form.addRow("备注", self.note)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.save_button = QPushButton("添加日程")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        self.new_button = QPushButton("新建")
        self.new_button.clicked.connect(self.clear_form)
        buttons.addWidget(self.new_button)
        self.done_button = QPushButton("标为完成")
        self.done_button.clicked.connect(self.mark_done)
        buttons.addWidget(self.done_button)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.clear_form()
        self.refresh()

    def set_range(self, key):
        self.range = key
        self.refresh()

    def visible_events(self) -> list[Event]:
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.range == "today":
            return self.schedule.events(today, today + timedelta(days=1))
        if self.range == "week":
            return self.schedule.events(today, today + timedelta(days=7))
        return self.schedule.events()

    def refresh(self):
        try:
            events = self.visible_events()
        except (OSError, sqlite3.Error):
            self.status.setText("日程表暂时无法读取。")
            return
        self.table.blockSignals(True)
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            title = ("✓ " if event.done else "") + event.title + ("　· 聊天中添加" if event.source == "chat" else "")
            values = [describe(event), title, f"{event.remind_minutes} 分钟前" if event.remind_minutes else "准时",
                      REPEATS.get(event.repeat, "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, event.id)
                if event.note:
                    item.setToolTip(event.note)
                if event.done:
                    item.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        if not events:
            self.status.setText("这段时间没有日程。")
        else:
            pending = sum(not e.done for e in events)
            self.status.setText(f"共 {len(events)} 项，未完成 {pending} 项。")

    def selected_id(self) -> int | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def load_selected(self):
        event_id = self.selected_id()
        event = self.schedule.get(event_id) if event_id is not None else None
        if event is None:
            return
        self.editing = event.id
        self.title.setText(event.title)
        self.start.setDateTime(qdatetime(event.start))
        self.remind.setValue(event.remind_minutes)
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(event.repeat)))
        self.note.setText(event.note)
        self.save_button.setText("保存修改")

    def clear_form(self):
        self.editing = None
        self.table.clearSelection()
        self.title.clear()
        self.note.clear()
        soon = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1)
        self.start.setDateTime(qdatetime(soon.replace(minute=0)))
        self.remind.setValue(10)
        self.repeat.setCurrentIndex(0)
        self.save_button.setText("添加日程")

    def form_start(self) -> datetime:
        value = self.start.dateTime()
        date, time = value.date(), value.time()
        return datetime(date.year(), date.month(), date.day(), time.hour(), time.minute())

    def save(self):
        title = self.title.text().strip()
        if not title:
            self.status.setText("先写下要做的事。")
            return
        try:
            if self.editing is None:
                self.schedule.add(title, self.form_start(), remind_minutes=self.remind.value(),
                                  repeat=self.repeat.currentData(), note=self.note.text())
                self.status.setText("已添加。")
            else:
                self.schedule.update(self.editing, title, self.form_start(), remind_minutes=self.remind.value(),
                                     repeat=self.repeat.currentData(), note=self.note.text())
                self.status.setText("已保存修改。")
        except (ValueError, OSError, sqlite3.Error) as error:
            self.status.setText(str(error) if isinstance(error, ValueError) else "日程没有保存成功。")
            return
        self.clear_form()
        self.refresh()
        self.changed.emit()

    def mark_done(self):
        event_id = self.selected_id()
        if event_id is None:
            self.status.setText("先在列表里选一项。")
            return
        event = self.schedule.get(event_id)
        self.schedule.set_done(event_id, not (event and event.done))
        self.clear_form()
        self.refresh()
        self.changed.emit()

    def delete(self):
        event_id = self.selected_id()
        if event_id is None:
            self.status.setText("先在列表里选一项。")
            return
        self.schedule.delete(event_id)
        self.clear_form()
        self.refresh()
        self.changed.emit()


class ScheduleReminder(QObject):
    """Checks every 30 seconds; each due event is announced once (repeating ones roll forward)."""
    due = Signal(object)

    def __init__(self, schedule: Schedule, parent=None):
        super().__init__(parent)
        self.schedule = schedule
        self.timer = QTimer(self)
        self.timer.setInterval(30_000)
        self.timer.timeout.connect(self.check)

    def start(self):
        try:
            self.schedule.advance_repeats()
        except (OSError, sqlite3.Error):
            pass
        self.timer.start()
        QTimer.singleShot(5000, self.check)

    def check(self, now: datetime | None = None) -> list[Event]:
        try:
            events = self.schedule.due(now)
            for event in events:
                self.schedule.mark_reminded(event, now)
        except (OSError, sqlite3.Error):
            return []
        for event in events:
            self.due.emit(event)
        return events

    def stop(self):
        self.timer.stop()


def reminder_text(event: Event, now: datetime | None = None) -> str:
    now = now or datetime.now()
    minutes = round((event.start - now).total_seconds() / 60)
    if minutes > 1:
        when = f"{minutes} 分钟后" if minutes < 90 else describe(event, now)
        return f"提醒一下：{when}要「{event.title}」啦。"
    if minutes >= -1:
        return f"到时间啦：「{event.title}」。"
    return f"刚才 {event.start:%H:%M} 有「{event.title}」，别忘了哦。"
