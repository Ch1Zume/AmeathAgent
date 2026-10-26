import time

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ameath.app import AmeathWindow
from ameath.capture import png_bytes
from ameath.config import Config
from ameath.models import Answer, Question
from ameath.theme import STYLE
from ameath.fonts import configure_fonts


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    configure_fonts(instance)
    instance.setStyle("Fusion")
    instance.setStyleSheet(STYLE)
    yield instance


@pytest.fixture
def window(app, tmp_path):
    instance = AmeathWindow(Config(tmp_path), tray=False)
    instance.show()
    app.processEvents()
    instance.open_sample()
    app.processEvents()
    yield instance
    instance.shutdown()
    instance.hide()
    instance.deleteLater()
    app.processEvents()


def wait_answer(window):
    deadline = time.monotonic() + 4
    while window.busy and time.monotonic() < deadline:
        QTest.qWait(20)
    assert not window.busy
    assert window.last_answer is not None


def test_select_explain_save_and_follow_source(window, app):
    canvas = window.reader.canvas
    scale = canvas.scale
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(45 * scale), round(335 * scale)))
    QTest.mouseMove(canvas, QPoint(round(545 * scale), round(425 * scale)))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(round(545 * scale), round(425 * scale)))
    assert "Attention" in window.selection
    assert window.selection_image
    assert window.explain_selection.isEnabled()
    window.explain_current_selection()
    wait_answer(window)
    assert "注意力机制" in window.response.toPlainText()
    assert window.last_answer.sources[0].page == 1
    window.save_card()
    assert len(window.notebook.cards()) == 1
    window.open_source_link(QUrl("source:1"))
    assert window.reader.page == 0
    window.navigate(1)
    assert window.cards_view.list.count() == 1


def test_external_clipboard_has_no_unrelated_paper_context(window, app):
    app.clipboard().setText("softmax from another application")
    window.from_clipboard()
    assert window.external
    assert not window.busy
    window.explain_current_selection()
    wait_answer(window)
    assert window.last_question.external
    assert not window.last_question.sources
    assert not window.last_answer.sources


def test_screen_preview_is_explicit_and_cleared_in_memory(window):
    image = QPixmap(320, 160)
    image.fill(QColor("#ab91db"))
    window.on_capture(png_bytes(image))
    assert not window.busy
    assert window.selection_image
    assert window.external
    assert window.thumbnail.isVisible()
    window.clear_context()
    assert window.selection_image is None
    assert not window.thumbnail.isVisible()


def test_old_responses_cannot_overwrite_new_document_or_context(window):
    old_generation = window.generation
    window.clear_context()
    window.on_answer(old_generation, Question("old"), Answer("STALE RESPONSE", []))
    assert "STALE" not in window.response.toPlainText()
    assert window.last_answer is None


def test_pause_is_explicit_and_does_not_auto_explain(window):
    window.toggle_observation()
    assert window.policy.enabled
    window.toggle_observation()
    assert not window.policy.enabled
    window.policy.page_since = 0
    window.maybe_nudge()
    assert not window.busy
    assert "暂停" in window.observe.text()


def test_model_markdown_cannot_load_local_or_remote_images(window):
    assert window.response.loadResource(2, QUrl("file:///C:/private.png")) is None
    assert window.response.loadResource(2, QUrl("https://example.com/image.png")) is None


def test_saved_card_keeps_each_source_page_mapping(window):
    from ameath.models import Source
    sources = [Source(window.paper.id, window.paper.title, 1, "one"),
               Source(window.paper.id, window.paper.title, 3, "three")]
    window.notebook.save(window.paper.id, window.paper.title, 1, "concept", "[Third page](source:2)", sources)
    window.cards_view.refresh()
    navigated = []
    window.cards_view.navigate.connect(lambda paper_id, page: navigated.append((paper_id, page)))
    window.cards_view.go(QUrl("source:2"))
    assert navigated[-1] == (window.paper.id, 3)
    assert window.reader.page == 2


def test_page_number_entry_is_visible_and_navigates(window):
    field = window.reader.page_number
    assert field.width() > 30
    assert field.text() == "1"
    field.setText("2")
    window.reader.enter_page()
    assert window.reader.page == 1


def wait_pilot(panel):
    deadline = time.monotonic() + 6
    while panel.busy and time.monotonic() < deadline:
        QTest.qWait(20)
    assert not panel.busy


def test_game_panel_suggests_then_executes_only_on_request(window):
    window.open_game()
    panel = window.game_panel
    panel.start("suggest")
    wait_pilot(panel)
    assert panel.decision is not None
    assert not panel.bridge.executed
    panel.start("apply")
    wait_pilot(panel)
    assert len(panel.bridge.executed) == 1
    assert panel.session.budget.requests == 0


def test_game_panel_autoplay_demo_stops_at_run_end(window):
    panel = window.game_panel
    panel.start("auto")
    wait_pilot(panel)
    assert len(panel.bridge.executed) == 4
    assert "本局已经结束" in panel.status.text()
    assert "本局结束" in panel.state_view.toPlainText()
    assert panel.auto_button.isEnabled()


def test_model_settings_preserve_game_configuration_and_offer_deepseek(window):
    from ameath.dialogs import SettingsDialog
    window.config.settings.game_request_limit = 77
    dialog = SettingsDialog(window.config, window)
    dialog.deepseek_preset()
    dialog.save()
    assert window.config.settings.endpoint == "https://api.deepseek.com"
    assert window.config.settings.model == "deepseek-flash"
    assert window.config.settings.game_request_limit == 77
    assert window.config.settings.protocol == "chat"
