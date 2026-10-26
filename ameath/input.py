"""Enter-to-send editing that leaves IME composition and candidate selection intact."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QTextEdit


class MessageInput(QTextEdit):
    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setInputMethodHints(Qt.InputMethodHint.ImhMultiLine)
        self.setAcceptRichText(False)
        self.composing = False
        # Some IMEs deliver the committed text before forwarding the confirming Enter.
        self._commit_guard = QTimer(self)
        self._commit_guard.setSingleShot(True)
        self._commit_guard.setInterval(150)

    def inputMethodEvent(self, event):
        was_composing = self.composing
        super().inputMethodEvent(event)
        self.composing = bool(event.preeditString())
        if self.composing:
            self._commit_guard.stop()
        elif was_composing or event.commitString():
            self._commit_guard.start()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.composing or self._commit_guard.isActive():
                # IME receives native candidate keys first; never turn a forwarded key into a send.
                event.accept()
                return
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                event.accept()
                if not event.isAutoRepeat():
                    self.submitted.emit()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        # A new physical key press after choosing a candidate may send immediately.
        self._commit_guard.stop()
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.composing = False
        self._commit_guard.stop()
