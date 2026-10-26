from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


def png_bytes(pixmap: QPixmap, max_size: int = 1800) -> bytes:
    if max(pixmap.width(), pixmap.height()) > max_size:
        pixmap = pixmap.scaled(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


class CaptureOverlay(QWidget):
    captured = Signal(bytes)
    cancelled = Signal()

    def __init__(self, screen):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setGeometry(screen.geometry())
        self.background = screen.grabWindow(0)
        self.start = QPoint()
        self.selection = QRect()
        self.dragging = False
        self.finished = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.background)
        painter.fillRect(self.rect(), QColor(17, 14, 27, 145))
        if not self.selection.isEmpty():
            painter.save()
            painter.setClipRect(self.selection)
            painter.drawPixmap(self.rect(), self.background)
            painter.restore()
            painter.setPen(QPen(QColor("#c7a8ff"), 2))
            painter.drawRect(self.selection)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Microsoft YaHei UI", 12))
        painter.drawText(QRectF(20, 22, self.width() - 40, 45), Qt.AlignmentFlag.AlignCenter,
                         "拖动框选要解释的内容 · 松开预览 · Esc 取消")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.dragging = True

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.selection = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.dragging:
            return
        self.dragging = False
        self.finished = True
        self.selection = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
        if self.selection.width() < 12 or self.selection.height() < 12:
            self.cancelled.emit()
        else:
            scale = self.background.devicePixelRatio()
            rect = QRect(round(self.selection.x() * scale), round(self.selection.y() * scale),
                         round(self.selection.width() * scale), round(self.selection.height() * scale))
            self.captured.emit(png_bytes(self.background.copy(rect)))
        self.background = QPixmap()
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.finished = True
            self.cancelled.emit()
            self.background = QPixmap()
            self.close()

    def closeEvent(self, event):
        self.background = QPixmap()
        if not self.finished:
            self.finished = True
            self.cancelled.emit()
        event.accept()
