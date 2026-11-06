from __future__ import annotations

import sqlite3
import threading
import time

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QTextEdit, QVBoxLayout

from .chat import ChatAgent, ChatReply, speech_segments
from .config import Config
from .llm import ModelError
from .pet import clamp_position
from .bubbles import BubbleFrame, ReplyBubble
from .companion_ui import CompanionDialog
from .input import MessageInput
from .tts import SpeechQueue, speech_units


class ChatInput(MessageInput):
    pass


class ChatSignals(QObject):
    done = Signal(int, str, object)
    failed = Signal(int, str)
    progress = Signal(int, str)
    partial = Signal(int, object)


class ChatDialog(BubbleFrame):
    AUTO_HIDE_SECONDS = 8.0
    # Pending rounds are folded into episodes once this many accumulate, keeping
    # the rounds still inside the 12-message live history out of the summary.
    EPISODE_BATCH_ROUNDS = 12
    LIVE_ROUNDS = 6
    user_activity = Signal()
    settings_requested = Signal()
    activity = Signal(str, str)
    responded = Signal(str, str)
    message_sent = Signal(str)
    schedule_changed = Signal()

    def __init__(self, config: Config, sheet=None):
        super().__init__()
        self.setWindowTitle("AmeathAgent · 输入气泡")
        self.resize(330, 158)
        self.anchor = None
        self._last_interaction = time.monotonic()
        self._message_index = -1
        self.config = config
        self.agent = ChatAgent(config)
        self.history: list[tuple[str, str]] = []
        self.messages: list[tuple[str, str]] = []
        self.last_reply: ChatReply | None = None
        self.busy = False
        self.closing = False
        self.generation = 0
        self.cancelled = threading.Event()
        self.episode_cancelled = threading.Event()
        self.signals = ChatSignals(self)
        self.signals.done.connect(self.on_reply)
        self.signals.failed.connect(self.on_error)
        self.signals.progress.connect(self.on_progress)
        self.signals.partial.connect(self.on_partial)
        self.speech = SpeechQueue(config, self)
        self.speech.failed.connect(self.on_speech_failed)
        self.speech.started.connect(self.reply_voice_started)
        self.speech.drained.connect(self.reply_voice_finished)
        self._voice_layout = self.speech.enabled
        self._voice_sync = self.speech.enabled
        self._game_speech_generation = -1
        self.streamed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(19, 13, 19, 25)
        layout.setSpacing(3)
        self.title = QLabel(self)
        self.title.hide()
        self.mode_badge = QLabel(self)
        self.mode_badge.hide()
        self.reply_bubble = ReplyBubble()
        self.reply_bubble.setParent(self, self.reply_bubble.windowFlags())
        self.reply_bubble.previous_requested.connect(self.browse_message)
        self.reply_bubble.geometry_changed.connect(self.reposition)
        self.reply_bubble.playback_finished.connect(self._reset_auto_hide)
        self.reply_bubble.dismissed.connect(self._dismiss_reply)
        self.transcript = self.reply_bubble.text
        self.sent = QLabel()
        self.sent.hide()
        layout.addWidget(self.sent)
        self.input = ChatInput()
        self.input.setPlaceholderText("想和你说…")
        self.input.setFixedHeight(66)
        self.input.submitted.connect(self.submit)
        self.input.textChanged.connect(self.user_activity.emit)
        self.input.textChanged.connect(self._reset_auto_hide)
        layout.addWidget(self.input)
        bottom = QHBoxLayout()
        self.new_button = QPushButton("新对话", self)
        self.new_button.hide()
        self.new_button.clicked.connect(self.new_conversation)
        more = QPushButton("···")
        more.setToolTip("新对话、记忆与人设、模型设置")
        menu = QMenu(more)
        menu.addAction("新对话", self.new_conversation)
        menu.addAction("回看消息", lambda: self.browse_message(0))
        menu.addAction("记忆与人设", self.edit_companion)
        menu.addAction("模型设置", self.settings_requested.emit)
        more.setMenu(menu)
        bottom.addWidget(more)
        self.status = QLabel(self)
        self.status.hide()
        bottom.addStretch(1)
        close = QPushButton("收起")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        self.send = QPushButton("发送")
        self.send.setObjectName("bubbleSend")
        self.send.clicked.connect(self.submit)
        bottom.addWidget(self.send)
        layout.addLayout(bottom)
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)
            control.setDefault(False)
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setInterval(250)
        self.dismiss_timer.timeout.connect(self._check_auto_hide)
        self.refresh_mode()
        self.refresh_transcript()
        # Turns left over from the previous run become episodes in the background.
        self.consolidate_episodes(keep_recent=0)

    def consolidate_episodes(self, *, keep_recent: int):
        if self.closing or not self.agent.keeps_episodes():
            return
        try:
            if len(self.agent.memory.pending_turns()) <= keep_recent:
                return
        except (OSError, sqlite3.Error):
            return
        cancelled = self.episode_cancelled

        def run():
            try:
                self.agent.consolidate(cancelled, keep_recent=keep_recent)
            except Exception:
                pass  # Pending turns stay for the next attempt; chat must not depend on this.

        threading.Thread(target=run, daemon=True, name="ameath-episodes").start()

    def refresh_mode(self):
        use_context = self.config.settings.use_companion_context
        context_disabled = getattr(self, "_used_context", False) and not use_context
        if context_disabled:
            # Earlier replies may repeat a saved fact; do not retransmit them after opting out.
            self.history.clear()
        self._used_context = use_context
        self.persona_name = self.agent.persona.load().name if self.config.settings.use_companion_context else "Ameath"
        self.title.setText("和 " + self.persona_name + " 聊聊")
        demo = self.config.settings.mode == "demo"
        self.mode_badge.setText("本地演示" if demo else "模型模式")
        self.mode_badge.setToolTip("固定回复，不调用模型。" if demo else self.config.settings.model)
        if not self.busy:
            self.status.setText("已停用人设与记忆，下条消息将从新上下文开始。" if context_disabled else
                                "演示模式 · 固定回复与动作" if demo else "Enter 发送 · Shift + Enter 换行")

    def open_near(self, pet):
        self.bind_pet(pet)
        self.user_activity.emit()
        self.refresh_mode()
        self.showNormal()
        self._reset_auto_hide()
        self.refresh_transcript()
        self.reposition()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def bind_pet(self, pet):
        if self.anchor is pet:
            return
        if self.anchor is not None:
            self.anchor.position_changed.disconnect(self.reposition)
        self.anchor = pet
        pet.position_changed.connect(self.reposition)

    def reposition(self):
        if self.anchor is None:
            return
        bounds = self.anchor.geometry()
        screen = QGuiApplication.screenAt(bounds.center()) or self.anchor.screen()
        available = screen.availableGeometry().adjusted(6, 6, -6, -6)
        reply = self.reply_bubble
        target = QPoint(bounds.center().x() - reply.width() // 2, bounds.top() + 78 - reply.height())
        reply.move(clamp_position(target, available, reply.size()))
        reply.tail_x = (bounds.center().x() - reply.x()) / reply.width()
        x = bounds.right() - 20
        if x + self.width() > available.right():
            x = bounds.left() - self.width() + 20
        target = QPoint(x, bounds.top() + 115)
        self.move(clamp_position(target, available, self.size()))
        # On small screens, place the input immediately below the response if necessary.
        if reply.isVisible() and self.geometry().intersects(reply.geometry()):
            self.move(clamp_position(QPoint(self.x(), reply.geometry().bottom() + 5), available, self.size()))
        self.tail_x = max(0.1, min(0.9, (bounds.center().x() - self.x()) / self.width()))
        reply.update()
        self.update()

    def refresh_transcript(self):
        if self.reply_bubble.playing and (not self.busy or self.streamed):
            return
        self._message_index = len(self.messages) - 1
        if self.busy:
            self.show_text("让我想想呀…", self.persona_name)
        elif self.messages:
            self.browse_message(0)
        else:
            self.transcript.clear()
            self.reply_bubble.hide()

    def show_text(self, text, speaker=None, *, plain=False):
        self.reply_bubble.text.setToolTip("")
        self.reply_bubble.set_message(text, speaker=speaker or self.persona_name, plain=plain)
        self.reply_bubble.show()
        self._reset_auto_hide()
        self.reposition()

    def show_speech(self, segments, *, animate=False, streaming=False, voice_sync=False):
        screen = self.anchor.screen() if self.anchor is not None else QGuiApplication.primaryScreen()
        max_height = min(440, screen.availableGeometry().height() - 190) if screen else 440
        self.reply_bubble.set_sequence(segments, speaker=self.persona_name, animate=animate, max_height=max_height,
                                       streaming=streaming, voice_sync=voice_sync)
        self.reply_bubble.show()
        self._reset_auto_hide()
        self.reposition()

    def _dismiss_reply(self):
        self.cancel_request()
        self.reply_bubble.hide()
        self._reset_auto_hide()

    def browse_message(self, delta):
        if not self.messages:
            return
        self._message_index = max(0, min(len(self.messages) - 1, self._message_index + delta))
        role, text = self.messages[self._message_index]
        if role == "assistant":
            self.show_speech(speech_segments(text))
        else:
            self.show_text(text, "你" if role == "user" else "提示", plain=True)
        self.reply_bubble.previous.setEnabled(self._message_index > 0)
        self.reply_bubble.next.setEnabled(self._message_index < len(self.messages) - 1)

    def show_proactive(self, text, *, tooltip=""):
        self.refresh_mode()
        if self.agent.keeps_episodes():
            try:
                self.agent.memory.note_mentions(text)
            except (OSError, sqlite3.Error):
                pass
        self.history.append(("assistant", text))
        self.history = self.history[-12:]
        self.messages.append(("assistant", text))
        self.messages = self.messages[-40:]
        segments = speech_segments(text)
        self.speech.stop()
        self._voice_layout = self.speech.enabled
        self._voice_sync = self.speech.enabled
        if self._voice_sync:
            self.show_speech(self._voice_segments(segments), animate=True, voice_sync=True)
        else:
            self.refresh_transcript()
        self.speech.extend(segments)
        self.reply_bubble.text.setToolTip(tooltip)
        self._reset_auto_hide()

    def show_game_line(self, segments) -> str:
        """A game commentary line in the reply bubble, voiced when TTS is on.

        Returns "voice", "text", or "" when chat is in use and the line was not shown.
        Game lines stay out of chat history so play-by-play never becomes chat context.
        """
        segments = tuple(segment for segment in segments if segment.strip())
        owns_voice = self.speech.generation == self._game_speech_generation
        if not segments or self.closing or self.busy or self.input.composing or (self.speech.active and not owns_voice):
            return ""
        self.speech.stop()
        self._game_speech_generation = self.speech.generation
        voiced = self.speech.enabled and self.speech.backend is not None
        self._voice_layout = self._voice_sync = voiced
        self.show_speech(self._voice_segments(segments) if voiced else segments, animate=True, voice_sync=voiced)
        self.speech.extend(segments)
        self._reset_auto_hide()
        return "voice" if voiced else "text"

    def stop_game_voice(self):
        if self.speech.generation == self._game_speech_generation:
            self.speech.stop()
            self.reply_bubble.stop_playback()

    def _reset_auto_hide(self):
        self._last_interaction = time.monotonic()
        if not self.closing:
            self.dismiss_timer.start()

    def _pointer_in_chat(self):
        position = QCursor.pos()
        return any(widget is not None and widget.isVisible()
                   and widget.rect().contains(widget.mapFromGlobal(position))
                   for widget in (self.anchor, self, self.reply_bubble))

    def _check_auto_hide(self):
        if not (self.isVisible() or self.reply_bubble.isVisible()):
            self.dismiss_timer.stop()
            return
        # Focus alone must not pin the bubble forever. Protect active composition,
        # pending replies and menus/dialogs; typing and hovering renew the grace period.
        if (self.busy or self.reply_bubble.playing or self.speech.active or self.input.composing or self._pointer_in_chat()
                or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None):
            self._last_interaction = time.monotonic()
            return
        if time.monotonic() - self._last_interaction >= self.AUTO_HIDE_SECONDS:
            self.hide()
            self.reply_bubble.hide()
            self.dismiss_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._reset_auto_hide()

    def submit(self):
        if self.busy:
            self.cancel_request()
            return
        if self.input.composing:
            QGuiApplication.inputMethod().commit()
            if self.input.composing:
                return
        text = self.input.toPlainText().strip()
        if not text:
            return
        if len(text) > 6000:
            self.status.setText("这条消息有些长，请控制在 6000 字以内。")
            self.show_text(self.status.text(), "提示")
            return
        self.input.clear()
        self.speech.stop()
        self._voice_layout = self.speech.enabled
        self._voice_sync = self.speech.enabled
        self.message_sent.emit(text)
        self.reply_bubble.stop_playback()
        self.sent.setText("你：" + (text[:29] + "…" if len(text) > 29 else text))
        self.sent.setToolTip(text)
        self.sent.show()
        self.resize(330, 177)
        self.dismiss_timer.stop()
        self.generation += 1
        generation = self.generation
        self.cancelled = threading.Event()
        cancelled = self.cancelled
        history = list(self.history)
        self.busy = True
        self.send.setText("停止")
        self.messages.append(("user", text))
        self.messages = self.messages[-40:]
        self.refresh_transcript()
        self.status.setText("Ameath 正在回复…")
        self.activity.emit("我在想怎么回答你。", "thinking")

        def run():
            try:
                reply = self.agent.answer(text, history, cancelled,
                                          on_retry=lambda message: self.signals.progress.emit(generation, message),
                                          on_partial=lambda bubbles: self.signals.partial.emit(generation, bubbles))
                if not cancelled.is_set() and not self.closing:
                    self.signals.done.emit(generation, text, reply)
            except ModelError as error:
                if not cancelled.is_set() and not self.closing:
                    self.signals.failed.emit(generation, str(error))
            except Exception:
                if not cancelled.is_set() and not self.closing:
                    self.signals.failed.emit(generation, "这次回复没有完成，请稍后重试。")

        threading.Thread(target=run, daemon=True, name="ameath-chat").start()

    def on_reply(self, generation: int, text: str, reply: ChatReply):
        if generation != self.generation or self.cancelled.is_set() or self.closing:
            return
        self.busy = False
        self.last_reply = reply
        self.history.extend([("user", text), ("assistant", reply.text)])
        self.history = self.history[-12:]
        self.messages.append(("assistant", reply.text))
        self.messages = self.messages[-40:]
        self.send.setText("发送")
        self._message_index = len(self.messages) - 1
        # Continue a streamed reply in place; a retried or reformatted reply replays from the start.
        display_segments = self._voice_segments(reply.segments)
        if not (self.streamed and self.reply_bubble.extend_sequence(display_segments, final=True)):
            self.show_speech(display_segments, animate=True, voice_sync=self._voice_sync)
        self.streamed = False
        if not self._voice_layout or self._voice_sync:
            self.speech.extend(reply.segments)
        if self._voice_sync and not self.speech.active:
            self.reply_bubble.voice_finished()
        self.reply_bubble.previous.setEnabled(self._message_index > 0)
        self.reply_bubble.next.setEnabled(False)
        self.status.setText("本地演示 · 固定回复与动作" if reply.demo else f"已回复 · {self.config.settings.model}")
        if reply.retried:
            self.status.setText(self.status.text() + " · 自动重试 1 次")
        saved = 0
        if self.config.settings.memory_enabled:
            for key, value in reply.memories:
                try:
                    self.agent.memory.remember(key, value, source="auto")
                    saved += 1
                except (ValueError, OSError, sqlite3.Error):
                    self.status.setText("已回复；部分记忆未能保存，可到「记忆与人设」整理。")
        if saved:
            self.status.setText(self.status.text() + f" · 记住了 {saved} 条")
        scheduled = 0
        for item in reply.schedules:
            try:
                self.agent.schedule.add(item["title"], item["start"], remind_minutes=item["remind_minutes"],
                                        repeat=item["repeat"], note=item["note"], source="chat")
                scheduled += 1
            except (ValueError, OSError, sqlite3.Error):
                pass
        if scheduled:
            self.status.setText(self.status.text() + f" · 加入日程 {scheduled} 条")
            self.schedule_changed.emit()
        if not reply.demo and self.agent.keeps_episodes():
            try:
                self.agent.memory.note_mentions(reply.text)
                self.agent.memory.log_turn(text, reply.text)
                if len(self.agent.memory.pending_turns()) >= self.EPISODE_BATCH_ROUNDS:
                    self.consolidate_episodes(keep_recent=self.LIVE_ROUNDS)
            except (OSError, sqlite3.Error):
                pass
        self.reply_bubble.text.setToolTip(self.status.text())
        if reply.demo:
            self.reply_bubble.speaker.setText(self.persona_name + " · 本地演示")
        self.responded.emit(reply.text, reply.action)

    def on_partial(self, generation: int, bubbles):
        if generation != self.generation or not self.busy or self.cancelled.is_set() or self.closing:
            return
        display_segments = self._voice_segments(bubbles)
        if not (self.streamed and self.reply_bubble.extend_sequence(display_segments)):
            self.streamed = True
            self.show_speech(display_segments, animate=True, streaming=True, voice_sync=self._voice_sync)
        if not self._voice_layout or self._voice_sync:
            self.speech.extend(bubbles)

    def _voice_segments(self, segments):
        return tuple(display for display, _spoken in speech_units(segments)) if self._voice_layout else segments

    def reply_voice_started(self, index):
        self.reply_bubble.voice_started(index)

    def reply_voice_finished(self):
        self.reply_bubble.voice_finished()

    def on_speech_failed(self, message: str):
        self._voice_sync = False
        if self.reply_bubble.voice_sync:
            self.reply_bubble.finish_playback()
        self.status.setText(message)
        self.reply_bubble.text.setToolTip(message)

    def on_progress(self, generation: int, message: str):
        if generation == self.generation and self.busy and not self.cancelled.is_set() and not self.closing:
            self.streamed = False
            self.speech.stop()
            self._voice_layout = self.speech.enabled
            self._voice_sync = self.speech.enabled
            self.status.setText(message)
            self.show_text("这次回复没生成完整，我再试一下。")

    def on_error(self, generation: int, message: str):
        if generation != self.generation or self.cancelled.is_set() or self.closing:
            return
        self.busy = False
        self.streamed = False
        self.speech.stop()
        self.reply_bubble.stop_playback()
        self.send.setText("发送")
        self.messages.append(("notice", message))
        self.refresh_transcript()
        self.status.setText("可以检查模型设置后重试。")
        self.activity.emit("暂时没能回复，请检查一下模型设置。", "failed")

    def cancel_request(self, *, silent=False):
        was_busy = self.busy
        self.streamed = False
        self.speech.stop()
        self.reply_bubble.stop_playback()
        self.cancelled.set()
        self.generation += 1
        self.busy = False
        self.send.setText("发送")
        if was_busy:
            self.refresh_transcript()
            self.status.setText("已停止等待这次回复。")
            if not silent:
                self.activity.emit("好，我先停下来。", "idle")

    def new_conversation(self):
        self.cancel_request()
        self.consolidate_episodes(keep_recent=0)
        self.history.clear()
        self.messages.clear()
        self.last_reply = None
        self.sent.hide()
        self.resize(330, 158)
        self.refresh_transcript()
        self.refresh_mode()
        self.input.setFocus()

    def edit_companion(self):
        self.cancel_request()
        dialog = CompanionDialog(self.config, self)
        dialog.changed.connect(self.refresh_mode)
        dialog.exec()
        self.refresh_mode()

    def hideEvent(self, event):
        # Closing or pressing Escape cancels pending work without erasing the conversation.
        self.cancel_request(silent=self.closing)
        self.reply_bubble.hide()
        self.dismiss_timer.stop()
        super().hideEvent(event)

    def shutdown(self):
        self.closing = True
        self.episode_cancelled.set()
        self.cancel_request(silent=True)
        self.speech.shutdown()
        self.dismiss_timer.stop()
        self.reply_bubble.close()
        self.close()
