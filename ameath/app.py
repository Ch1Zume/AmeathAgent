from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter,
                               QStackedWidget, QSystemTrayIcon, QTextBrowser, QTextEdit,
                               QVBoxLayout, QWidget)

from .agent import Agent, AgentError
from .capture import CaptureOverlay
from .config import Config
from .dialogs import CardsView, SettingsDialog
from .models import Answer, Question, Source
from .papers import Paper, make_sample
from .pet import DesktopPet, PetAvatar, draw_pet
from .companion import ProactiveCompanion
from .policy import AttentionPolicy
from .storage import Notebook
from .theme import MARKDOWN_STYLE, STYLE
from .windows import GlobalHotkeys
from .widgets import SafeTextBrowser
from .fonts import configure_fonts
from .instance import SingleInstance
from .game_ui import GamePanel
from .usage_ui import UsagePanel
from .sprites import load_snowcap
from .chat_ui import ChatDialog
from .focus import Pomodoro, PomodoroDialog
from .weather_ui import DailyWeather, WeatherDialog
from .input import MessageInput


class AskInput(MessageInput):
    pass


class JobSignals(QObject):
    done = Signal(int, object, object)
    failed = Signal(int, str)


def button(text, callback, name=""):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


class AmeathWindow(QMainWindow):
    def __init__(self, config: Config, *, tray: bool = True):
        super().__init__()
        self.config = config
        self.agent = Agent(config)
        self.notebook = Notebook(config.directory / "notebook.sqlite3")
        self.policy = AttentionPolicy(enabled=config.settings.observe_reader,
                                      cooldown=config.settings.cooldown_seconds, dwell=config.settings.dwell_seconds)
        self.paper: Paper | None = None
        self.selection = ""
        self.selection_image: bytes | None = None
        self.external = False
        self.history: list[tuple[str, str]] = []
        self.last_answer: Answer | None = None
        self.last_question: Question | None = None
        self.busy = False
        self.generation = 0
        self.cancelled = threading.Event()
        self.cache: dict[str, Answer] = {}
        self.closing = False
        self.exiting = False
        self.overlay = None
        self.signals = JobSignals(self)
        self.signals.done.connect(self.on_answer)
        self.signals.failed.connect(self.on_error)
        self.pet_sheet, self.pet_resource_warning = load_snowcap(config.directory)
        self.pet = DesktopPet(self.pet_sheet, wandering=config.settings.pet_wandering,
                              resting=config.settings.pet_resting, flying=config.settings.pet_flying)
        self.pet.setToolTip(self.pet_resource_warning or "单击聊天，按住拖动；右键打开 Ameath、选择动作或开启散步。")
        self.chat = ChatDialog(config, self.pet_sheet)
        self.chat.bind_pet(self.pet)
        self.focus = Pomodoro(config, self.pet, self.chat, self)
        self.focus_dialog = PomodoroDialog(self.focus, self)
        self.weather = DailyWeather(config, self.pet, self.chat, self)
        self.weather.blocked = lambda: self.focus.breaking or self.busy or self.game_panel.busy
        self.companion = ProactiveCompanion(config, self.pet, self.chat, self)
        self.companion.blocked = lambda: self.focus.clock.active or self.weather.busy or bool(self.weather.pending) or self.busy or self.game_panel.busy
        self.pet.proactive_enabled = config.settings.proactive_enabled
        self.pet.screen_observation_enabled = config.settings.screen_observation_enabled
        self.pet.screen_observation_stop_requested.connect(self.stop_screen_observation)
        self.pet.proactive_changed.connect(self.companion.set_enabled)
        self.pet.quiet_requested.connect(self.on_quiet_requested)
        self.pet.activated.connect(self.companion.on_interaction)
        self.chat.user_activity.connect(self.companion.on_interaction)
        self.chat.message_sent.connect(self.companion.on_user_message)
        self.pet.busy_provider = lambda: self.busy or self.chat.busy or self.chat.isVisible() or self.focus.breaking or bool(self.pet.transition) or (hasattr(self, "game_panel") and self.game_panel.busy)
        self.chat.activity.connect(self.on_chat_activity)
        self.chat.responded.connect(self.on_chat_reply)
        self.chat.user_activity.connect(self.pet.note_interaction)
        self.chat.settings_requested.connect(lambda: self.settings(self.chat))
        self.pet.wandering_changed.connect(self.on_wandering_changed)
        self.pet.resting_changed.connect(lambda enabled: self.save_pet_behavior("pet_resting", enabled))
        self.pet.flying_changed.connect(lambda enabled: self.save_pet_behavior("pet_flying", enabled))
        self.pet.activated.connect(self.open_chat)
        self.pet.app_requested.connect(self.open_app)
        self.pet.memory_requested.connect(self.chat.edit_companion)
        self.pet.clipboard_requested.connect(self.from_clipboard)
        self.pet.capture_requested.connect(self.begin_capture)
        self.pet.pause_requested.connect(self.toggle_observation)
        self.pet.quit_requested.connect(self.request_quit)
        self.pet.lifecycle_finished.connect(self.on_lifecycle_finished)
        self.pet.focus_requested.connect(self.open_focus)
        self.pet.weather_requested.connect(self.open_weather)
        self.pet.game_requested.connect(lambda: self.open_game())
        self.pet.stop_game_requested.connect(lambda: self.game_panel.stop())
        self.setWindowTitle("AmeathAgent · 陪你一起读懂")
        self.setMinimumSize(1080, 730)
        self.resize(1280, 850)
        icon_pixmap = QPixmap(96, 96)
        icon_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon_pixmap)
        draw_pet(painter, QRectF(icon_pixmap.rect()), sheet=self.pet_sheet)
        painter.end()
        self.app_icon = QIcon(icon_pixmap)
        self.setWindowIcon(self.app_icon)
        self.build_ui()
        self.update_mode()
        self.update_observation()
        self.tray = None
        if tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.app_icon, self)
            self.tray.setToolTip("AmeathAgent · 论文阅读伙伴")
            menu = QMenu()
            menu.addAction("和 Ameath 聊天", self.open_chat)
            menu.addAction("打开 Ameath", self.open_app)
            menu.addAction("番茄钟", self.open_focus)
            menu.addAction("天气与问候", self.open_weather)
            menu.addAction("解释已复制的文字", self.from_clipboard)
            menu.addAction("框选屏幕内容", self.begin_capture)
            menu.addSeparator()
            menu.addAction("暂停 / 恢复陪读", self.toggle_observation)
            menu.addAction("退出 Ameath", self.request_quit)
            self.tray_menu = menu
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.activate() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()
        self.hotkeys = GlobalHotkeys(QApplication.instance(), self.from_clipboard, self.begin_capture,
                                     self.game_panel.stop) if tray else None
        if self.hotkeys and len(self.hotkeys.registered) < 3:
            self.progress_label.setText("部分快捷键被其他应用占用；可使用桌宠右键菜单。")
        self.observation_timer = QTimer(self)
        self.observation_timer.timeout.connect(self.maybe_nudge)
        self.observation_timer.start(1500)
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def build_ui(self):
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(root)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(198)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 23, 18, 20)
        side.setSpacing(12)
        brand = QHBoxLayout()
        brand.addWidget(PetAvatar(44, self.pet_sheet))
        logo = QLabel("A M E A T H")
        logo.setObjectName("brand")
        brand.addWidget(logo)
        side.addLayout(brand)
        tagline = QLabel("给好奇心，一个陪伴。")
        tagline.setObjectName("muted")
        side.addWidget(tagline)
        side.addSpacing(25)
        side.addWidget(button("和 Ameath 聊天", self.open_chat, "quiet"))
        side.addWidget(button("＋  打开论文", self.open_pdf, "primary"))
        side.addWidget(button("体验示例论文", self.open_sample, "quiet"))
        side.addSpacing(15)
        self.read_nav = button("◈   阅读空间", lambda: self.navigate(0), "nav")
        self.read_nav.setCheckable(True)
        self.read_nav.setChecked(True)
        side.addWidget(self.read_nav)
        self.cards_nav = button("◇   概念卡片", lambda: self.navigate(1), "nav")
        self.cards_nav.setCheckable(True)
        side.addWidget(self.cards_nav)
        self.game_nav = button("♧   杀戮尖塔 2", self.open_game, "nav")
        self.game_nav.setCheckable(True)
        side.addWidget(self.game_nav)
        self.usage_nav = button("▤   用量记录", lambda: self.navigate(3), "nav")
        self.usage_nav.setCheckable(True)
        side.addWidget(self.usage_nav)
        side.addStretch(1)
        shortcuts = QLabel("随时叫我帮忙\n\n复制文字后  Ctrl + Alt + E\n框选屏幕      Ctrl + Alt + S")
        shortcuts.setObjectName("muted")
        shortcuts.setWordWrap(True)
        side.addWidget(shortcuts)
        side.addSpacing(15)
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        side.addWidget(self.status_label)
        side.addWidget(button("模型设置", self.settings, "quiet"))
        side.addWidget(button("收起到桌宠", self.hide, "quiet"))
        layout.addWidget(sidebar)
        workspace = QWidget()
        workspace.setObjectName("workspace")
        work = QVBoxLayout(workspace)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(0)
        top = QWidget()
        top.setObjectName("topbar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(25, 18, 25, 18)
        headings = QVBoxLayout()
        title = QLabel("让一个问题，慢慢变清楚。")
        title.setObjectName("headline")
        self.headline = title
        headings.addWidget(title)
        subtitle = QLabel("选一段文字，理清一个概念。Ameath 就在旁边。")
        subtitle.setObjectName("muted")
        self.workspace_subtitle = subtitle
        headings.addWidget(subtitle)
        top_layout.addLayout(headings, 1)
        self.observe = button("", self.toggle_observation, "observe")
        self.observe.setCheckable(True)
        self.observe.setToolTip("仅在内置阅读区给出本地提示；不会自动截图或调用模型。")
        top_layout.addWidget(self.observe)
        work.addWidget(top)
        self.stack = QStackedWidget()
        read_panel = QWidget()
        read_layout = QHBoxLayout(read_panel)
        read_layout.setContentsMargins(0, 0, 0, 0)
        read_layout.setSpacing(0)
        from .reader import Reader
        self.reader = Reader()
        self.reader.selected.connect(self.on_selection)
        self.reader.page_changed.connect(self.on_page)
        self.reader.scroll.verticalScrollBar().valueChanged.connect(self.reading_activity)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.addWidget(self.reader)
        splitter.setCollapsible(0, False)
        assistant = self.build_assistant()
        splitter.addWidget(assistant)
        splitter.setCollapsible(1, False)
        splitter.setSizes([640, 380])
        read_layout.addWidget(splitter)
        self.stack.addWidget(read_panel)
        self.cards_view = CardsView(self.notebook)
        self.cards_view.navigate.connect(self.navigate_source)
        self.stack.addWidget(self.cards_view)
        self.game_panel = GamePanel(self.config)
        self.game_panel.comment.connect(self.pet.say)
        self.game_panel.voice_router = self.speak_game_line
        self.game_panel.silenced.connect(self.chat.stop_game_voice)
        self.chat.speech.drained.connect(self.game_panel.voice_finished)
        # Load the local voice model early so the first reply is not a cold start.
        QTimer.singleShot(4000, self.chat.speech.warm_up)
        self.chat.speech.failed.connect(self.game_panel.voice_finished)
        self.game_panel.model_settings_requested.connect(self.settings)
        self.stack.addWidget(self.game_panel)
        self.usage_panel = UsagePanel(self.config)
        self.stack.addWidget(self.usage_panel)
        work.addWidget(self.stack, 1)
        layout.addWidget(workspace, 1)

    def build_assistant(self):
        assistant = QWidget()
        assistant.setObjectName("assistant")
        assistant.setMinimumWidth(350)
        assistant.setMaximumWidth(510)
        column = QVBoxLayout(assistant)
        column.setContentsMargins(19, 18, 19, 18)
        column.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(PetAvatar(55, self.pet_sheet))
        labels = QVBoxLayout()
        name = QLabel("Ameath")
        name.setObjectName("sectionTitle")
        labels.addWidget(name)
        subtitle = QLabel("你的论文阅读伙伴")
        subtitle.setObjectName("muted")
        labels.addWidget(subtitle)
        header.addLayout(labels, 1)
        self.mode_badge = QLabel()
        self.mode_badge.setObjectName("badge")
        header.addWidget(self.mode_badge)
        column.addLayout(header)
        context = QFrame()
        context.setObjectName("contextCard")
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(13, 12, 13, 12)
        self.context_title = QLabel("等待一段值得探索的内容")
        self.context_title.setObjectName("eyebrow")
        context_layout.addWidget(self.context_title)
        self.selection_label = QLabel("打开一篇 PDF，或从其他应用复制一段文字。")
        self.selection_label.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_label.setObjectName("selectionText")
        self.selection_label.setWordWrap(True)
        self.selection_label.setMaximumHeight(74)
        context_layout.addWidget(self.selection_label)
        self.thumbnail = QLabel()
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setMaximumHeight(105)
        self.thumbnail.hide()
        context_layout.addWidget(self.thumbnail)
        column.addWidget(context)
        actions = QHBoxLayout()
        self.explain_selection = button("解释选区", self.explain_current_selection, "primary")
        self.explain_selection.setEnabled(False)
        actions.addWidget(self.explain_selection, 1)
        self.explain_page = button("梳理这一页", self.explain_current_page)
        actions.addWidget(self.explain_page, 1)
        column.addLayout(actions)
        quick = QHBoxLayout()
        quick.addWidget(button("attention 是什么", lambda: self.ask("请解释 attention 的直觉和含义。"), "quiet"))
        quick.addWidget(button("清除上下文", self.clear_context, "quiet"))
        column.addLayout(quick)
        self.response = SafeTextBrowser()
        self.response.setOpenLinks(False)
        self.response.setOpenExternalLinks(False)
        self.response.document().setDefaultStyleSheet(MARKDOWN_STYLE)
        self.response.anchorClicked.connect(self.open_source_link)
        self.response.setMarkdown("### 一起读懂，不必急。\n\n试着框选左侧的一个段落，然后点击 **解释选区**。\n\n我会先讲直觉，再把概念和这篇论文联系起来。\n\n**当前为本地演示。** 内置卡片可以体验操作；开放式解释需要连接模型。")
        column.addWidget(self.response, 1)
        self.source_label = QLabel("解释会保留原文出处，点击页码即可回到论文。")
        self.source_label.setObjectName("muted")
        self.source_label.setWordWrap(True)
        column.addWidget(self.source_label)
        feedback = QHBoxLayout()
        self.useful_button = button("有帮助", lambda: self.feedback(True), "quiet")
        self.useful_button.setEnabled(False)
        feedback.addWidget(self.useful_button)
        feedback.addWidget(button("稍后再说", lambda: self.feedback(False), "quiet"))
        self.save_button = button("保存卡片", self.save_card, "quiet")
        self.save_button.setEnabled(False)
        feedback.addWidget(self.save_button, 1)
        column.addLayout(feedback)
        self.input = AskInput()
        self.input.setPlaceholderText("继续追问，比如：能举一个例子吗？")
        self.input.setFixedHeight(70)
        self.input.submitted.connect(self.submit)
        column.addWidget(self.input)
        bottom = QHBoxLayout()
        self.progress_label = QLabel("Enter 发送 · Shift + Enter 换行")
        self.progress_label.setObjectName("muted")
        self.progress_label.setWordWrap(True)
        bottom.addWidget(self.progress_label, 1)
        self.send = button("发送", self.submit, "primary")
        bottom.addWidget(self.send)
        column.addLayout(bottom)
        return assistant

    def activate(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_chat(self):
        if self.exiting:
            return
        self.chat.open_near(self.pet)

    def on_chat_activity(self, text, state):
        if not self.exiting and not self.focus.breaking:
            self.pet.set_activity(state)

    def on_quiet_requested(self):
        self.companion.quiet()
        self.pet.say("好，我安静两小时，有事点我就行。", "idle")

    def speak_game_line(self, lines, emotion) -> str:
        if self.exiting or self.focus.breaking:
            return ""
        shown = self.chat.show_game_line(lines)
        if shown:
            # Game lines never trigger flight or sleep.
            action = {"happy": "waving", "excited": "jumping", "thoughtful": "review", "curious": "waiting"}.get(emotion, "idle")
            self.pet.respond(" ".join(lines), action, show_bubble=False)
        return shown

    def on_chat_reply(self, text, action):
        if not self.exiting and not self.focus.breaking:
            self.pet.respond(text, action, show_bubble=False)

    def open_focus(self):
        self.focus_dialog.refresh()
        self.focus_dialog.showNormal()
        self.focus_dialog.raise_()
        self.focus_dialog.activateWindow()

    def open_weather(self):
        dialog = WeatherDialog(self.config, self)
        dialog.changed.connect(self.weather.invalidate)
        dialog.refresh_requested.connect(lambda: self.weather.begin(force=True))
        dialog.exec()

    def start_companion(self):
        self.pet.start_transition("enter")

    def on_lifecycle_finished(self, kind):
        if kind == "enter" and not self.exiting:
            self.weather.begin()
        elif kind == "exit" and self.exiting:
            QApplication.instance().quit()

    def request_quit(self):
        if self.exiting or self.closing:
            return
        self.exiting = True
        self.cancel_request()
        self.chat.cancel_request(silent=True)
        self.weather.shutdown()
        self.companion.shutdown()
        self.game_panel.stop()
        self.focus.shutdown()
        self.focus_dialog.hide()
        self.chat.hide()
        self.chat.reply_bubble.hide()
        self.hide()
        if self.overlay:
            self.overlay.close()
        self.pet.start_transition("exit")
        QTimer.singleShot(2600, lambda: QApplication.instance().quit() if self.exiting else None)

    def open_app(self):
        if self.paper is None:
            self.open_sample()
        self.activate()

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        self.read_nav.setChecked(index == 0)
        self.cards_nav.setChecked(index == 1)
        self.game_nav.setChecked(index == 2)
        self.usage_nav.setChecked(index == 3)
        self.headline.setText({2: "一起闯一闯尖塔。", 3: "看看一起用掉了多少。"}.get(index, "让一个问题，慢慢变清楚。"))
        self.workspace_subtitle.setText({2: "先算好这一回合，再一张张打出去。DeepSeek 驱动 Ameath。",
                                         3: "对话、游戏和 Agent 工作共用同一份用量记录。"}.get(
                                            index, "选一段文字，理清一个概念。Ameath 就在旁边。"))
        if index == 1:
            self.cards_view.refresh()

    def open_game(self):
        self.navigate(2)
        self.activate()

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开要一起阅读的论文", "", "PDF 文件 (*.pdf)")
        if path:
            self.load_paper(path)

    def open_sample(self):
        self.load_paper(make_sample(self.config.directory / "ameath-sample.pdf"))

    def load_paper(self, path):
        try:
            if Path(path).stat().st_size > 100 * 1024 * 1024:
                raise ValueError("原型暂支持 100 MB 以内的 PDF，请先拆分这份文件。")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                new_paper = Paper(path)
            finally:
                QApplication.restoreOverrideCursor()
        except Exception:
            QMessageBox.warning(self, "暂时无法打开", "无法读取这份 PDF。请确认文件完整、没有密码保护，且小于 100 MB。")
            return False
        self.cancel_request()
        old = self.paper
        self.paper = new_paper
        self.cache.clear()
        self.history.clear()
        self.reader.set_paper(new_paper)
        if old:
            old.close()
        self.navigate(0)
        self.pet.say("论文准备好了。选一段内容，我们一起看看。")
        return True

    def reading_activity(self, value=0):
        self.policy.page_since = time.monotonic()

    def on_page(self, page):
        self.clear_context()
        if self.paper:
            self.policy.visit(f"{self.paper.id}:{page}")
            self.context_title.setText(f"当前论文 · 第 {page + 1} 页")
            self.selection_label.setText("框选一段文字，或点击「梳理这一页」。")

    def on_selection(self, text: str, image: bytes):
        self.clear_context(preserve_highlight=True)
        self.selection, self.selection_image = text[:8000], image
        self.external = False
        self.context_title.setText(f"已选中 · 第 {self.reader.page + 1} 页")
        self.selection_label.setText(text[:180] or "这是图像或公式区域，将结合选区图片解释。")
        self.show_thumbnail(image if not text else None)
        self.explain_selection.setEnabled(True)
        if self.policy.enabled:
            self.pet.say("选区准备好了，点击「解释选区」就能聊聊。", "waiting")
        self.reading_activity()

    def show_thumbnail(self, image: bytes | None):
        if image:
            pixmap = QPixmap()
            pixmap.loadFromData(image)
            self.thumbnail.setPixmap(pixmap.scaled(290, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))
            self.thumbnail.show()
        else:
            self.thumbnail.clear()
            self.thumbnail.hide()

    def clear_context(self, preserve_highlight=False):
        self.cancel_request()
        self.selection = ""
        self.selection_image = None
        self.external = False
        self.history.clear()
        self.last_answer = None
        self.last_question = None
        self.explain_selection.setEnabled(False)
        self.useful_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.show_thumbnail(None)
        if not preserve_highlight:
            self.reader.canvas.selection = QRect()
            self.reader.canvas.update()
        self.response.setMarkdown("### 从这里开始探索\n\n框选内容，或直接输入一个问题。解释只在你发起时生成。")
        self.source_label.setText("尚未生成解释。")
        if self.paper:
            self.context_title.setText(f"当前论文 · 第 {self.reader.page + 1} 页")
            self.selection_label.setText("未选择段落，将使用当前页及相关原文。")
        else:
            self.context_title.setText("等待内容")
            self.selection_label.setText("打开 PDF、复制文字，或框选屏幕中的内容。")

    def from_clipboard(self):
        text = QApplication.clipboard().text().strip()
        self.activate()
        self.navigate(0)
        if not text:
            self.pet.say("先复制想了解的文字，再叫我解释。")
            self.progress_label.setText("剪贴板没有文字。请先在阅读器中选中并复制。")
            return
        self.clear_context()
        self.selection = text[:8000]
        self.external = True
        self.context_title.setText("外部选文 · 未关联论文")
        self.selection_label.setText(self.selection[:180])
        self.explain_selection.setEnabled(True)
        self.pet.say("文字准备好了。点击解释后才会发送给模型。")

    def begin_capture(self):
        if self.overlay is not None:
            return
        self.cancel_request()
        self._before_capture_visible = self.isVisible()
        self._capture_screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self.hide()
        self.pet.hide()
        QTimer.singleShot(220, self.show_capture)

    def show_capture(self):
        self.overlay = CaptureOverlay(self._capture_screen)
        self.overlay.captured.connect(self.on_capture)
        self.overlay.cancelled.connect(self.capture_cancelled)
        self.overlay.show()
        self.overlay.activateWindow()
        self.overlay.setFocus()

    def capture_cancelled(self):
        self.overlay = None
        self.pet.show()
        if self._before_capture_visible:
            self.activate()

    def on_capture(self, image):
        self.overlay = None
        self.clear_context()
        self.selection_image = image
        self.external = True
        self.context_title.setText("屏幕选区 · 未关联论文")
        self.selection_label.setText("只发送这块选区。请确认预览后，点击「解释选区」。")
        self.show_thumbnail(image)
        self.explain_selection.setEnabled(True)
        self.pet.show()
        self.navigate(0)
        self.activate()

    def explain_current_selection(self):
        if self.selection or self.selection_image:
            self.ask("请解释所选内容：先讲直觉，再说明概念或图表关系；涉及公式时逐项解释符号。")

    def explain_current_page(self):
        if not self.paper:
            self.open_pdf()
            return
        self.clear_context()
        self.ask("请梳理当前页：主要在讲什么、关键概念是什么、与前后文有什么联系？请区分原文和补充讲解。", page_image=True)

    def submit(self):
        if self.busy:
            self.cancel_request()
            self.progress_label.setText("已停止等待，本次返回的内容不会显示。")
            return
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.ask(text)

    def ask(self, text: str, *, page_image=False):
        if self.busy:
            return
        sources = []
        image = self.selection_image
        if self.paper and not self.external:
            sources = self.paper.context(text + " " + self.selection, self.reader.page)
            if page_image or (not sources and not image):
                image = self.paper.image(self.reader.page, 1.4).tobytes("png")
            if image and not any(source.page == self.reader.page + 1 for source in sources):
                sources.insert(0, Source(self.paper.id, self.paper.title, self.reader.page + 1,
                                         "此页为图片内容，请根据附图核对；没有提取到可验证的文字。"))
        question = Question(text=text, selection=self.selection, sources=sources, image_png=image,
                            history=self.history[-6:], external=self.external)
        self.generation += 1
        generation = self.generation
        self.cancelled = threading.Event()
        cancelled = self.cancelled
        self.busy = True
        self.send.setText("停止")
        self.explain_selection.setEnabled(False)
        self.explain_page.setEnabled(False)
        self.save_button.setEnabled(False)
        self.useful_button.setEnabled(False)
        self.progress_label.setText("Ameath 正在整理这段内容…")
        self.pet.say("让我把这段内容和上下文连起来。", "thinking")
        self.response.setMarkdown("### 正在一起想…\n\n我会核对提供的材料，再组织解释。")
        settings = self.config.settings
        cache_key = hashlib.sha256(json.dumps({"text": text, "selection": self.selection,
                                                "sources": [asdict(s) for s in sources],
                                                "history": question.history,
                                                "image": hashlib.sha256(image or b"").hexdigest(),
                                                "settings": asdict(settings)}, ensure_ascii=False).encode()).hexdigest()
        cached = self.cache.get(cache_key)
        if cached:
            QTimer.singleShot(60, lambda: self.on_answer(generation, question, cached))
            return

        def run():
            try:
                answer = self.agent.answer(question, cancelled)
                if not cancelled.is_set() and not self.closing:
                    self.signals.done.emit(generation, question, (cache_key, answer))
            except AgentError as error:
                if not cancelled.is_set() and not self.closing:
                    self.signals.failed.emit(generation, str(error))
            except Exception:
                if not cancelled.is_set() and not self.closing:
                    self.signals.failed.emit(generation, "这次解释没有完成。请缩小选区，或检查模型设置后重试。")

        threading.Thread(target=run, daemon=True, name="ameath-explanation").start()

    def cancel_request(self):
        self.cancelled.set()
        self.generation += 1
        self.busy = False
        if hasattr(self, "send"):
            self.send.setText("发送")
            self.explain_page.setEnabled(True)
            self.explain_selection.setEnabled(bool(self.selection or self.selection_image))
        self.pet.state = "idle" if self.policy.enabled else "paused"

    def on_answer(self, generation: int, question: Question, result):
        if generation != self.generation or self.cancelled.is_set():
            return
        if isinstance(result, tuple):
            cache_key, answer = result
            if len(self.cache) >= 16:
                self.cache.pop(next(iter(self.cache)))
            self.cache[cache_key] = answer
        else:
            answer = result
        self.busy = False
        self.last_question, self.last_answer = question, answer
        self.history.extend([("user", question.text), ("assistant", answer.text)])
        self.history = self.history[-6:]
        self.response.setMarkdown(answer.text)
        if answer.sources:
            pages = "、".join(str(source.page) for source in answer.sources)
            self.source_label.setText(f"提供给解释的原文：第 {pages} 页 · 请结合原文核对")
        else:
            self.source_label.setText("外部内容未关联论文页码。" if question.external else "没有可引用的论文原文。")
        self.send.setText("发送")
        self.progress_label.setText("本地演示 · 预置卡片" if answer.demo else f"已完成 · {self.config.settings.model}")
        self.explain_selection.setEnabled(bool(self.selection or self.selection_image))
        self.explain_page.setEnabled(True)
        self.useful_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.pet.say("整理好了。我们也可以再换个例子。", "happy")

    def on_error(self, generation: int, message: str):
        if generation != self.generation:
            return
        self.busy = False
        self.response.setMarkdown("### 暂时没有完成解释\n\n" + message)
        self.progress_label.setText("可以修改设置后重试，或切换本地演示。")
        self.send.setText("发送")
        self.explain_selection.setEnabled(bool(self.selection or self.selection_image))
        self.explain_page.setEnabled(True)
        self.pet.say("暂时没有连上，我们稍后再试。", "failed")

    def open_source_link(self, url):
        if url.scheme() != "source" or not self.last_answer:
            return
        try:
            index = int(url.path()) - 1
        except ValueError:
            return
        if 0 <= index < len(self.last_answer.sources):
            source = self.last_answer.sources[index]
            self.navigate_source(source.paper_id, source.page)

    def navigate_source(self, paper_id: str, page: int):
        if not self.paper or self.paper.id != paper_id:
            QMessageBox.information(self, "打开对应论文", "请先打开这张卡片对应的 PDF，再返回原文页。")
            return
        self.navigate(0)
        previous_answer, previous_question = self.last_answer, self.last_question
        previous_markdown = self.response.toMarkdown()
        self.reader.go(page - 1)
        if previous_answer is not None:
            self.last_answer, self.last_question = previous_answer, previous_question
            self.response.setMarkdown(previous_markdown)
            self.useful_button.setEnabled(True)
            self.save_button.setEnabled(True)

    def save_card(self):
        if not self.last_answer or not self.last_question:
            return
        source = self.last_answer.sources[0] if self.last_answer.sources else None
        self.notebook.save(source.paper_id if source else "external", source.title if source else "外部内容",
                           source.page if source else 0, self.last_question.selection or self.last_question.text,
                           self.last_answer.text, self.last_answer.sources)
        self.save_button.setEnabled(False)
        self.progress_label.setText("已保存到本机概念卡片。")
        self.pet.say("记下来了，下次可以回来看看。", "happy")

    def feedback(self, useful: bool):
        self.policy.feedback(useful)
        self.config.settings.cooldown_seconds = round(self.policy.cooldown)
        self.config.save()
        self.useful_button.setEnabled(False)
        if useful:
            self.progress_label.setText("已记录反馈，会保留适当的提示间隔。")
            self.pet.say("收到，我们就按这个节奏。", "happy")
        else:
            self.progress_label.setText(f"已减少打扰，至少 {round(self.policy.cooldown / 60)} 分钟内不再主动提示。")
            self.pet.say("好，我先安静陪着。", "paused")

    def toggle_observation(self):
        if self.policy.enabled:
            self.policy.pause()
        else:
            self.policy.resume()
        self.config.settings.observe_reader = self.policy.enabled
        self.config.save()
        self.update_observation()

    def update_observation(self):
        self.observe.setChecked(self.policy.enabled)
        self.observe.setText("●  陪读提示已开启" if self.policy.enabled else "○  陪读提示已暂停")
        self.status_label.setText("陪读中\n仅关注内置阅读区\n不自动调用模型" if self.policy.enabled else "安静陪伴\n仅手动发起解释\n不在后台截图")
        self.pet.say("我会在合适的时候，轻轻提醒你。" if self.policy.enabled else "我先安静陪着，需要时点我。",
                     "idle" if self.policy.enabled else "paused")

    def maybe_nudge(self):
        active = (self.isActiveWindow() and self.stack.currentIndex() == 0 and self.paper is not None
                  and not self.input.hasFocus())
        if self.policy.candidate(active, self.busy):
            self.policy.prompted()
            self.pet.say("需要的话，我可以梳理这一页的概念。", "waiting")
            self.progress_label.setText("需要梳理当前页时，点击上方按钮即可。")

    def settings(self, parent=None):
        self.cancel_request()
        self.game_panel.stop()
        self.chat.cancel_request()
        self.companion.invalidate()
        dialog = SettingsDialog(self.config, parent if isinstance(parent, QWidget) else self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.companion.settings_changed()
            self.pet.screen_observation_enabled = self.config.settings.screen_observation_enabled
            self.chat.speech.refresh_settings()
            self.chat.speech.warm_up()
            self.cache.clear()
            self.clear_context()
            self.update_mode()
            self.chat.refresh_mode()

    def stop_screen_observation(self):
        self.config.settings.screen_observation_enabled = False
        self.config.save()
        self.pet.screen_observation_enabled = False
        self.companion.settings_changed()

    def on_wandering_changed(self, enabled: bool):
        self.config.settings.pet_wandering = enabled
        self.config.save()

    def save_pet_behavior(self, name: str, enabled: bool):
        setattr(self.config.settings, name, enabled)
        self.config.save()

    def update_mode(self):
        self.mode_badge.setText("本地演示" if self.config.settings.mode == "demo" else "模型模式")
        self.mode_badge.setToolTip("预置教学卡片，不调用模型。" if self.config.settings.mode == "demo"
                                   else "已配置模型；连接状态以实际请求结果为准。")

    def closeEvent(self, event: QCloseEvent):
        if self.tray is not None and not self.closing:
            self.hide()
            event.ignore()
        else:
            if self.closing:
                event.accept()
            else:
                self.request_quit()
                event.ignore()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.cancelled.set()
        self.observation_timer.stop()
        self.weather.shutdown()
        self.companion.shutdown()
        self.focus.shutdown()
        self.focus_dialog.close()
        self.chat.shutdown()
        self.game_panel.shutdown()
        if self.hotkeys:
            self.hotkeys.close()
        if self.tray:
            self.tray.hide()
        self.pet.close()
        if self.paper:
            self.paper.close()
        self.notebook.close()


def main():
    parser = argparse.ArgumentParser(description="AmeathAgent desktop reading companion")
    parser.add_argument("pdf", nargs="?", help="PDF to open")
    parser.add_argument("--no-tray", action="store_true", help="Close exits instead of minimizing to tray")
    parser.add_argument("--game", action="store_true", help="Open the Slay the Spire 2 skill")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("AmeathAgent")
    app.setOrganizationName("Ameath")
    configure_fonts(app)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    config = Config()
    instance = SingleInstance(config.directory)
    if not instance.primary:
        return 0
    app.aboutToQuit.connect(instance.close)
    window = AmeathWindow(config, tray=not args.no_tray)
    screen = app.primaryScreen().availableGeometry()
    width, height = min(1280, screen.width() - 50), min(850, screen.height() - 60)
    window.resize(max(1080, width), max(730, height))
    window.move(screen.center() - window.rect().center())
    window.pet.move(screen.right() - window.pet.width() - 18, screen.bottom() - window.pet.height() - 18)
    window.pet.set_activity("idle")
    window.start_companion()
    if args.game:
        QTimer.singleShot(0, window.open_game)
    elif args.pdf:
        window.show()
        QTimer.singleShot(0, lambda: window.load_paper(args.pdf))
    elif args.no_tray:
        window.show()
        QTimer.singleShot(0, window.open_sample)
    return app.exec()
