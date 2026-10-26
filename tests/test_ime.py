import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from ameath.app import AskInput
from ameath.chat_ui import ChatInput
from test_ui import app, window


@pytest.fixture(params=[ChatInput, AskInput])
def editor(request, app):
    widget = request.param()
    widget.show()
    widget.setFocus()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def commit(app, editor, text):
    event = QInputMethodEvent()
    event.setCommitString(text)
    app.sendEvent(editor, event)


def test_confirming_candidate_cannot_submit_or_clear_chinese_text(editor, app):
    sent = []
    editor.submitted.connect(lambda: sent.append(editor.toPlainText()))
    app.sendEvent(editor, QInputMethodEvent("nihao", []))
    assert editor.composing
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert not sent
    commit(app, editor, "你好")
    # Also handle IMEs that commit first, then forward the same confirming key.
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert sent == [] and editor.toPlainText() == "你好"
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert sent == ["你好"]


def test_space_selection_then_enter_sends_once_without_waiting(editor, app):
    sent = []
    editor.submitted.connect(lambda: sent.append(editor.toPlainText()))
    app.sendEvent(editor, QInputMethodEvent("zhongwen", []))
    commit(app, editor, "中文")
    QTest.keyRelease(editor, Qt.Key.Key_Space)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert sent == ["中文"]


def test_chinese_shift_enter_newline_and_backspace_remain_editable(editor, app):
    sent = []
    editor.submitted.connect(lambda: sent.append(True))
    commit(app, editor, "你好")
    QTest.keyRelease(editor, Qt.Key.Key_Space)
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert editor.toPlainText() == "你好\n" and not sent
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == "你" and not sent


def test_english_enter_and_autorepeat_do_not_double_submit(editor, app):
    sent = []
    editor.submitted.connect(lambda: sent.append(True))
    QTest.keyClicks(editor, "hello")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r", True)
    app.sendEvent(editor, event)
    assert len(sent) == 1 and editor.toPlainText() == "hello"


def test_empty_composition_cancel_does_not_leave_editor_stuck(editor, app):
    app.sendEvent(editor, QInputMethodEvent("ni", []))
    app.sendEvent(editor, QInputMethodEvent())
    QTest.keyRelease(editor, Qt.Key.Key_Escape)
    assert not editor.composing
    sent = []
    editor.submitted.connect(lambda: sent.append(True))
    QTest.keyClicks(editor, "hello")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert sent == [True]


def test_dialog_buttons_cannot_steal_an_ime_confirmation_enter(window):
    assert all(not button.autoDefault() and not button.isDefault() for button in window.chat.findChildren(QPushButton))
