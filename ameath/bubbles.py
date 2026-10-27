"""Small, anchored speech surfaces; only their painted area covers the desktop."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .widgets import SafeTextBrowser


BUBBLE_STYLE = """
QDialog#petBubble { background: transparent; color: #633d50; }
QDialog#petBubble QLabel { background: transparent; color: #95677d; font-size: 11px; }
QDialog#petBubble QTextEdit, QDialog#petBubble QTextBrowser {
    background: transparent; color: #583a49; border: none; padding: 0; font-size: 14px;
    selection-background-color: #f3c0d6; selection-color: #63344b;
}
QDialog#petBubble QPushButton { background: transparent; color: #ad4d77; border: none;
    border-radius: 10px; padding: 4px 7px; min-height: 20px; font-size: 12px; }
QDialog#petBubble QPushButton:hover { background: #fbdce9; }
QDialog#petBubble QPushButton#bubbleSend { background: #c5497c; color: white; border-radius: 13px; }
QDialog#petBubble QScrollBar:vertical { width: 5px; background: transparent; }
QDialog#petBubble QScrollBar::handle:vertical { background: #dca2bc; border-radius: 2px; min-height: 20px; }
QDialog#petBubble QScrollBar::add-line:vertical, QDialog#petBubble QScrollBar::sub-line:vertical { height: 0; }
QDialog#petBubble QScrollArea, QWidget#speechStack { background: transparent; border: none; }
QFrame#speechCard { background: #fffafc; border: 1px solid #edbfd3; border-radius: 17px; }
QFrame#speechHeader { background: #fffafc; border: 1px solid #edbfd3; border-radius: 13px; }
"""


class BubbleFrame(QDialog):
    def __init__(self, *, passive=False):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("petBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, passive)
        self.setStyleSheet(BUBBLE_STYLE)
        self.tail = "bottom"
        self.tail_x = 0.5
        self.fill = QColor("#fffafc")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(4, 4, self.width() - 8, self.height() - 19)
        path = QPainterPath()
        path.addRoundedRect(body, 20, 20)
        x = min(body.right() - 26, max(body.left() + 26, self.width() * self.tail_x))
        tail = QPainterPath(QPointF(x - 9, body.bottom() - 1))
        tail.lineTo(x + 1, self.height() - 4)
        tail.lineTo(x + 13, body.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        painter.setPen(QPen(QColor("#edbfd3"), 1.2))
        painter.setBrush(self.fill)
        painter.drawPath(path)


class ReplyBubble(BubbleFrame):
    dismissed = Signal()
    previous_requested = Signal(int)
    geometry_changed = Signal()
    playback_finished = Signal()
    FADE_MS = 650
    MIN_PAUSE_MS = 550
    MAX_PAUSE_MS = 1700

    def __init__(self):
        super().__init__(passive=True)
        self.setWindowTitle("AmeathAgent · 回复气泡")
        self.resize(370, 150)
        self.playing = False
        self.streaming = False
        self._sequence_mode = False
        self._segments = ()
        self._max_height = 440
        self.speech_cards = []
        self.speech_texts = []
        self._next_timer = QTimer(self)
        self._next_timer.setSingleShot(True)
        self._next_timer.timeout.connect(self._show_next)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._scroll_bottom)
        self._fade = QPropertyAnimation(self)
        self._fade.setPropertyName(b"opacity")
        self._fade.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._fade.finished.connect(self._after_fade)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 25)
        layout.setSpacing(5)
        self.header = QFrame()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(6, 0, 6, 0)
        self.speaker = QLabel("爱弥斯")
        header.addWidget(self.speaker, 1)
        self.previous = QPushButton("‹")
        self.previous.setToolTip("上一条消息")
        self.previous.clicked.connect(lambda: self.previous_requested.emit(-1))
        header.addWidget(self.previous)
        self.next = QPushButton("›")
        self.next.setToolTip("下一条消息")
        self.next.clicked.connect(lambda: self.previous_requested.emit(1))
        header.addWidget(self.next)
        self.reveal_all = QPushButton("显示全部")
        self.reveal_all.setToolTip("跳过停顿，立即显示本次完整回复")
        self.reveal_all.clicked.connect(self.finish_playback)
        self.reveal_all.hide()
        header.addWidget(self.reveal_all)
        close = QPushButton("×")
        close.setToolTip("收起气泡")
        close.clicked.connect(self.dismiss)
        header.addWidget(close)
        layout.addWidget(self.header)
        self.text = SafeTextBrowser()
        self.text.setOpenExternalLinks(False)
        self.text.setOpenLinks(False)
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text.document().setDefaultStyleSheet("p { margin: 0 0 7px; } a { color: #b33c70; } pre { white-space: pre-wrap; }")
        layout.addWidget(self.text)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.verticalScrollBar().rangeChanged.connect(lambda _minimum, _maximum: self._scroll_bottom())
        self.stack = QWidget()
        self.stack.setObjectName("speechStack")
        self.stack_layout = QVBoxLayout(self.stack)
        self.stack_layout.setContentsMargins(0, 0, 5, 0)
        self.stack_layout.setSpacing(8)
        self.stack_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.stack)
        self.scroll.hide()
        layout.addWidget(self.scroll)

    def paintEvent(self, event):
        if not self._sequence_mode:
            super().paintEvent(event)

    def set_message(self, text, *, speaker="爱弥斯", plain=False, max_height=330):
        self.stop_playback()
        self.streaming = False
        self._sequence_mode = False
        self.header.setObjectName("")
        self.header.setStyleSheet("")
        self.layout().setContentsMargins(20, 12, 20, 25)
        self.scroll.hide()
        self.text.show()
        self.speaker.setText(speaker)
        self.text.setPlainText(text) if plain else self.text.setMarkdown(text)
        self.text.document().setTextWidth(self.width() - 48)
        height = round(self.text.document().size().height()) + 78
        self.resize(self.width(), max(110, min(max_height, height)))
        self.text.verticalScrollBar().setValue(0)
        self.geometry_changed.emit()
        self.update()

    def set_sequence(self, segments, *, speaker="爱弥斯", animate=True, max_height=440, streaming=False):
        self.stop_playback()
        # While streaming, playback waits after the last received bubble instead of finishing.
        self.streaming = streaming
        self._sequence_mode = True
        self._segments = tuple(segments)
        self._max_height = max(130, max_height)
        self.speaker.setText(speaker)
        self.header.setObjectName("speechHeader")
        # Refresh the dynamic object-name selector when switching presentation.
        self.header.setStyleSheet("QFrame#speechHeader { background: #fffafc; border: 1px solid #edbfd3; border-radius: 13px; }")
        self.layout().setContentsMargins(4, 4, 4, 8)
        self.text.setMarkdown("\n\n".join(self._segments))
        self.text.hide()  # Canonical document for copying/history; cards render it.
        while self.stack_layout.count():
            item = self.stack_layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        self.speech_cards.clear()
        self.speech_texts.clear()
        self.scroll.show()
        self.playing = bool(animate and (self._segments or streaming))
        self.reveal_all.setVisible(self.playing)
        if self.playing:
            self._show_next()
        else:
            for part in self._segments:
                self._append_card(part, fade=False)
        self.update()

    def extend_sequence(self, segments, *, final=False) -> bool:
        """Append newly arrived bubbles; False when they no longer continue what is shown."""
        segments = tuple(segments)
        if not self._sequence_mode or segments[:len(self._segments)] != self._segments:
            return False
        self._segments = segments
        self.text.setMarkdown("\n\n".join(segments))
        if final:
            self.streaming = False
        if not self.playing:
            for part in segments[len(self.speech_cards):]:
                self._append_card(part, fade=False)
        elif self._fade.state() != QPropertyAnimation.State.Running and not self._next_timer.isActive():
            if len(self.speech_cards) < len(segments):
                self._show_next()
            elif not self.streaming:
                self.stop_playback()
                self.playback_finished.emit()
        return True

    def _append_card(self, part, *, fade):
        card = QFrame()
        card.setObjectName("speechCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 9, 14, 9)
        text = SafeTextBrowser()
        text.setOpenExternalLinks(False)
        text.setOpenLinks(False)
        text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        text.setMarkdown(part)
        text.setToolTip(self.text.toolTip())
        text.document().setTextWidth(self.width() - 53)
        height = round(text.document().size().height()) + 6
        text.setFixedHeight(max(32, min(self._max_height - 92, height)))
        card_layout.addWidget(text)
        card.setFixedHeight(text.height() + 20)
        self.stack_layout.addWidget(card)
        self.speech_cards.append(card)
        self.speech_texts.append(text)
        if fade:
            effect = QGraphicsOpacityEffect(text)
            effect.setOpacity(0.0)
            text.setGraphicsEffect(effect)
            self._fade.setTargetObject(effect)
            self._fade.setDuration(self.FADE_MS)
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        height = sum(widget.height() for widget in self.speech_cards) + max(0, len(self.speech_cards) - 1) * 8 + 56
        self.resize(self.width(), max(130, min(self._max_height, height)))
        self.geometry_changed.emit()
        self._scroll_timer.start(0)

    def _scroll_bottom(self):
        # The viewport width is final only after Qt lays out the new card.
        # Measure again here so wrapping cannot clip the last line of a bubble.
        for card, text in zip(self.speech_cards, self.speech_texts):
            text.document().setTextWidth(text.viewport().width())
            height = round(text.document().size().height()) + 10
            text.setFixedHeight(max(32, min(self._max_height - 92, height)))
            card.setFixedHeight(text.height() + 20)
        if self._sequence_mode:
            height = sum(card.height() for card in self.speech_cards) + max(0, len(self.speech_cards) - 1) * 8 + 56
            self.resize(self.width(), max(130, min(self._max_height, height)))
            self.geometry_changed.emit()
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _show_next(self):
        if self.playing and len(self.speech_cards) < len(self._segments):
            self._append_card(self._segments[len(self.speech_cards)], fade=True)

    def _after_fade(self):
        if not self.playing:
            return
        if len(self.speech_cards) == len(self._segments):
            if self.streaming:
                return
            self.stop_playback()
            self.playback_finished.emit()
        else:
            length = len(self._segments[len(self.speech_cards) - 1])
            self._next_timer.start(min(self.MAX_PAUSE_MS, max(self.MIN_PAUSE_MS, length * 28)))

    def finish_playback(self):
        if not self.playing:
            return
        self.stop_playback()
        for part in self._segments[len(self.speech_cards):]:
            self._append_card(part, fade=False)
        self.playback_finished.emit()

    def stop_playback(self):
        self.playing = False
        self.streaming = False
        self._next_timer.stop()
        self._scroll_timer.stop()
        self._fade.stop()
        for text in self.speech_texts:
            effect = text.graphicsEffect()
            if effect is not None:
                effect.setOpacity(1.0)
        self._fade.setTargetObject(None)
        self.reveal_all.hide()

    def hideEvent(self, event):
        self.stop_playback()
        super().hideEvent(event)

    def dismiss(self):
        self.hide()
        self.dismissed.emit()

    def reject(self):
        self.dismiss()
