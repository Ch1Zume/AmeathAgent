from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest

from ameath.capture import CaptureOverlay
from test_ui import app


class FakeScreen:
    """A synthetic HiDPI display; tests never capture the real desktop."""
    def geometry(self):
        return QRect(-800, 0, 800, 600)

    def grabWindow(self, identifier):
        pixmap = QPixmap(1600, 1200)
        pixmap.fill(QColor("#aabbcc"))
        pixmap.setDevicePixelRatio(2)
        return pixmap


def test_crop_uses_display_ratio_and_discards_full_frame(app):
    overlay = CaptureOverlay(FakeScreen())
    captured = []
    overlay.captured.connect(captured.append)
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(10, 20))
    QTest.mouseMove(overlay, QPoint(110, 70))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(110, 70))
    image = QImage.fromData(captured[0])
    assert image.width() == 202 and image.height() == 102
    assert image.pixelColor(10, 10).name() == "#aabbcc"
    assert overlay.background.isNull()


def test_closing_capture_cancels_and_releases_frame(app):
    overlay = CaptureOverlay(FakeScreen())
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.close()
    assert cancelled == [True]
    assert overlay.background.isNull()
