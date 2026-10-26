"""A deterministic work/rest clock and the desktop companion's break invitation."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout

from .bubbles import BubbleFrame


class FocusClock:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.phase = "idle"
        self.cycle = 0
        self.cycles = 2
        self.work = 3000
        self.rest = 600
        self.deadline = 0
        self.paused = False
        self._remaining = 0

    @property
    def active(self):
        return self.phase in ("work", "rest", "snooze")

    @property
    def remaining(self):
        return max(0, self._remaining if self.paused else self.deadline - self.now()) if self.active else 0

    def start(self, work, rest, cycles):
        if not (1 <= work <= 240 and 1 <= rest <= 120 and 1 <= cycles <= 20):
            raise ValueError("Invalid focus durations")
        self.work, self.rest, self.cycles = work * 60, rest * 60, cycles
        self.cycle = 1
        self._enter("work", self.work)

    def _enter(self, phase, seconds):
        self.phase, self.paused = phase, False
        self.deadline = self.now() + seconds

    def tick(self):
        if self.active and not self.paused and self.remaining <= 0:
            if self.phase in ("work", "snooze"):
                self._enter("rest", self.rest)
            else:
                self.finish_rest()

    def finish_rest(self):
        if self.phase != "rest":
            return
        if self.cycle >= self.cycles:
            self.phase, self.paused = "complete", False
        else:
            self.cycle += 1
            self._enter("work", self.work)

    def postpone(self):
        if self.phase == "rest":
            self._enter("snooze", 300)

    def toggle_pause(self):
        if not self.active:
            return
        if self.paused:
            self.deadline = self.now() + self._remaining
        else:
            self._remaining = self.remaining
        self.paused = not self.paused

    def stop(self):
        self.phase, self.paused = "idle", False


def clock_text(seconds):
    seconds = max(0, math.ceil(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class BreakBubble(BubbleFrame):
    postpone_requested = Signal()
    skip_requested = Signal()
    stop_requested = Signal()

    def __init__(self):
        super().__init__(passive=True)
        self.setWindowTitle("AmeathAgent · 休息一下")
        self.fill = QColor("#fbf3ff")
        self.resize(490, 218)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 20, 25, 30)
        self.line = QLabel("大英雄，先把手里的事情放一放嘛。\n现在是休息时间，陪我伸个懒腰，好不好？")
        self.line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.line.setStyleSheet("font-size: 16px; color: #88506a;")
        layout.addWidget(self.line)
        self.countdown = QLabel()
        self.countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdown.setStyleSheet("font-size: 25px; color: #c5497c; font-weight: 600;")
        layout.addWidget(self.countdown)
        self.cycle_label = QLabel()
        self.cycle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.cycle_label)
        row = QHBoxLayout()
        for text, signal in [("稍后 5 分钟", self.postpone_requested), ("跳过本次休息", self.skip_requested), ("结束番茄钟", self.stop_requested)]:
            button = QPushButton(text)
            button.clicked.connect(signal.emit)
            row.addWidget(button)
        layout.addLayout(row)

    def reject(self):
        self.postpone_requested.emit()


class Pomodoro(QObject):
    changed = Signal()

    def __init__(self, config, pet, chat, parent=None, *, clock=None):
        super().__init__(parent)
        self.config, self.pet, self.chat = config, pet, chat
        self.clock = clock or FocusClock()
        self.break_bubble = BreakBubble()
        self.break_bubble.setParent(pet, self.break_bubble.windowFlags())
        self.break_bubble.postpone_requested.connect(self.postpone)
        self.break_bubble.skip_requested.connect(self.skip)
        self.break_bubble.stop_requested.connect(self.stop)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.return_position = None
        self._presented = False
        self._last_phase = "idle"
        self.closing = False

    @property
    def breaking(self):
        return self.clock.phase == "rest" and not self.clock.paused

    def start(self, work, rest, cycles):
        self.stop(silent=True)
        self.clock.start(work, rest, cycles)
        self._last_phase = "work"
        self.timer.start()
        self.pet.note_interaction()
        self.chat.show_proactive(f"好呀，那就认真忙 {work} 分钟，再一起歇 {rest} 分钟。我们走 {cycles} 轮，时间交给我来记～")
        self.pet.play_action("waving")
        self.changed.emit()

    def tick(self):
        if self.closing:
            return
        before = self.clock.phase
        self.clock.tick()
        if before != self.clock.phase:
            if before == "rest":
                self._leave_break()
            if self.clock.phase == "work":
                self.chat.show_proactive("歇好啦？那我们继续下一轮。你认真忙，我就在旁边陪着你。")
            elif self.clock.phase == "complete":
                self.timer.stop()
                self.chat.show_proactive(f"约好的 {self.clock.cycles} 轮都完成啦！辛苦了，我的大英雄。接下来想做什么，我陪你呀～")
                if not self.pet.is_moving:
                    self.pet.play_action("waving")
        if self.breaking and not self._presented and self.pet._press is None and not self.pet.transition:
            self._show_break()
        self.break_bubble.countdown.setText(clock_text(self.clock.remaining))
        self.break_bubble.cycle_label.setText(f"第 {self.clock.cycle} / {self.clock.cycles} 轮 · 休息")
        self._last_phase = self.clock.phase
        self.changed.emit()

    def _show_break(self):
        self._presented = True
        self.return_position = QPoint(self.pet.pos())
        screen = QGuiApplication.screenAt(self.pet.geometry().center()) or self.pet.screen()
        area = screen.availableGeometry()
        center = area.center()
        # The character's visible center, rather than its transparent window, lands at screen center.
        target = QPoint(center.x() - self.pet.width() // 2, center.y() - 186)
        self.break_bubble.move(center.x() - self.break_bubble.width() // 2,
                               max(area.top() + 8, center.y() - self.break_bubble.height() - 105))
        self.break_bubble.show()
        self.break_bubble.raise_()
        self.chat.reply_bubble.hide()
        self.pet.note_interaction()
        self.pet.fly_to(target, speed=420)
        self.pet.raise_()

    def _leave_break(self):
        self.break_bubble.hide()
        self._presented = False
        if self.return_position is not None:
            self.pet.fly_to(self.return_position, speed=420)
            self.return_position = None

    def postpone(self):
        if self.clock.phase != "rest":
            return
        self.clock.postpone()
        self._leave_break()
        self.chat.show_proactive("好，再给你五分钟收个尾。说好啦，到时候要陪我歇一会儿哦。")
        self.changed.emit()

    def skip(self):
        if self.clock.phase != "rest":
            return
        self.clock.deadline = self.clock.now()
        self.clock.paused = False
        self.tick()

    def pause(self):
        self.clock.toggle_pause()
        if self.clock.paused:
            self._leave_break()
        self.tick()

    def stop(self, *, silent=False):
        active = self.clock.active
        self.clock.stop()
        self.timer.stop()
        self._leave_break()
        if active and not silent:
            self.chat.show_proactive("这次的番茄钟就先到这里。辛苦啦，剩下的按你的节奏来。")
        self.changed.emit()

    def shutdown(self):
        self.closing = True
        self.stop(silent=True)
        self.break_bubble.close()


class PomodoroDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("AmeathAgent · 番茄钟")
        self.resize(380, 310)
        layout = QVBoxLayout(self)
        description = QLabel("你认真忙，我替你记时间。\n休息时我会飞到屏幕中央来找你。")
        layout.addWidget(description)
        form = QFormLayout()
        settings = controller.config.settings
        self.work = QSpinBox()
        self.work.setRange(1, 240)
        self.work.setSuffix(" 分钟")
        self.work.setValue(settings.focus_work_minutes)
        self.rest = QSpinBox()
        self.rest.setRange(1, 120)
        self.rest.setSuffix(" 分钟")
        self.rest.setValue(settings.focus_rest_minutes)
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 20)
        self.cycles.setValue(settings.focus_cycles)
        form.addRow("工作", self.work)
        form.addRow("休息", self.rest)
        form.addRow("循环次数", self.cycles)
        layout.addLayout(form)
        note = QLabel("一轮 = 工作 + 休息；最后一轮也会休息。\n关闭本窗口后继续计时；退出 Ameath 会结束计时。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.start_button = QPushButton("开始")
        self.start_button.clicked.connect(self.start)
        row.addWidget(self.start_button)
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(controller.pause)
        row.addWidget(self.pause_button)
        self.stop_button = QPushButton("结束")
        self.stop_button.clicked.connect(controller.stop)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        controller.changed.connect(self.refresh)
        self.refresh()

    def start(self):
        settings = self.controller.config.settings
        settings.focus_work_minutes, settings.focus_rest_minutes, settings.focus_cycles = self.work.value(), self.rest.value(), self.cycles.value()
        try:
            self.controller.config.save()
        except OSError:
            self.status.setText("设置未能保存，请检查数据目录。")
            return
        self.controller.start(self.work.value(), self.rest.value(), self.cycles.value())
        self.hide()

    def refresh(self):
        clock = self.controller.clock
        label = {"idle": "尚未开始", "work": "专注", "rest": "休息", "snooze": "稍后提醒", "complete": "已完成"}[clock.phase]
        self.status.setText(("已暂停 · " if clock.paused else "") + label + (f" · 第 {clock.cycle}/{clock.cycles} 轮 · {clock_text(clock.remaining)}" if clock.active else ""))
        self.pause_button.setText("继续" if clock.paused else "暂停")
        self.pause_button.setEnabled(clock.active)
        self.stop_button.setEnabled(clock.active)
        self.start_button.setEnabled(not clock.active)
        for widget in (self.work, self.rest, self.cycles):
            widget.setEnabled(not clock.active)
