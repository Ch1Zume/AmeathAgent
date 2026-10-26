from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QIntValidator, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .capture import png_bytes
from .papers import Paper


class PageCanvas(QWidget):
    selected = Signal(str, bytes)

    def __init__(self):
        super().__init__()
        self.paper: Paper | None = None
        self.page = 0
        self.pixmap = QPixmap()
        self.selection = QRect()
        self.start: QPoint | None = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setObjectName("paperCanvas")

    def display(self, paper: Paper, page: int, width: int):
        self.paper, self.page = paper, page
        pw, ph = paper.sizes[page]
        self.scale = max(0.35, min(2.0, width / pw))
        pixels = paper.image(page, self.scale * self.devicePixelRatioF())
        image = QImage(pixels.samples, pixels.width, pixels.height, pixels.stride, QImage.Format.Format_RGB888).copy()
        self.pixmap = QPixmap.fromImage(image)
        self.pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self.setFixedSize(round(pw * self.scale), round(ph * self.scale))
        self.selection = QRect()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.drawPixmap(self.rect(), self.pixmap)
        if not self.selection.isEmpty():
            painter.fillRect(self.selection, QColor(146, 109, 213, 42))
            painter.setPen(QPen(QColor("#ab83e3"), 1.5))
            painter.drawRect(self.selection)

    def mousePressEvent(self, event):
        if self.paper and event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.selection = QRect()
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.selection = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.selection = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
        self.start = None
        self.update()
        if self.selection.width() < 8 or self.selection.height() < 8:
            return
        rect = self.selection
        coords = (rect.left() / self.scale, rect.top() / self.scale,
                  (rect.right() + 1) / self.scale, (rect.bottom() + 1) / self.scale)
        text = self.paper.text_in_rect(self.page, coords)
        dpr = self.pixmap.devicePixelRatio()
        crop = QRect(round(rect.x() * dpr), round(rect.y() * dpr), round(rect.width() * dpr), round(rect.height() * dpr))
        self.selected.emit(text, png_bytes(self.pixmap.copy(crop)))


class Reader(QWidget):
    selected = Signal(str, bytes)
    page_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.paper = None
        self.page = 0
        self._last_width = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toolbar = QWidget()
        toolbar.setObjectName("readerBar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(22, 13, 22, 13)
        self.title = QLabel("阅读空间")
        self.title.setObjectName("documentTitle")
        self.title.setMinimumWidth(80)
        self.title.setMaximumWidth(360)
        bar.addWidget(self.title, 1)
        self.previous = QPushButton("‹")
        self.previous.setFixedSize(29, 29)
        self.previous.clicked.connect(lambda: self.go(self.page - 1))
        bar.addWidget(self.previous)
        self.page_number = QLineEdit("1")
        self.page_number.setFixedWidth(54)
        self.page_number.setFixedHeight(31)
        self.page_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_validator = QIntValidator(1, 1, self)
        self.page_number.setValidator(self.page_validator)
        self.page_number.editingFinished.connect(self.enter_page)
        bar.addWidget(self.page_number)
        self.total = QLabel("/ 0")
        self.total.setObjectName("muted")
        bar.addWidget(self.total)
        self.next = QPushButton("›")
        self.next.setFixedSize(29, 29)
        self.next.clicked.connect(lambda: self.go(self.page + 1))
        bar.addWidget(self.next)
        layout.addWidget(toolbar)
        hint = QLabel("  拖动框选文字、公式或图表，然后让 Ameath 解释")
        hint.setObjectName("readerHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFixedHeight(36)
        layout.addWidget(hint)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("paperScroll")
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.canvas = PageCanvas()
        self.canvas.selected.connect(self.selected.emit)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, 1)
        self.footer = QLabel("原文由本地读取 · 页面内容不会自动发送")
        self.footer.setObjectName("readerHint")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer.setFixedHeight(32)
        layout.addWidget(self.footer)

    def set_paper(self, paper: Paper):
        self.paper = paper
        self.title.setText(paper.title)
        self.title.setToolTip(paper.title)
        self.total.setText(f"/ {paper.count}")
        self.page_validator.setTop(paper.count)
        self.go(0, force=True)

    def go(self, page: int, force=False):
        if self.paper is None or not 0 <= page < self.paper.count:
            return
        if page == self.page and not force:
            return
        self.page = page
        self.page_number.blockSignals(True)
        self.page_number.setText(str(page + 1))
        self.page_number.blockSignals(False)
        self.render()
        self.scroll.verticalScrollBar().setValue(0)
        self.previous.setEnabled(page > 0)
        self.next.setEnabled(page < self.paper.count - 1)
        self.page_changed.emit(page)

    def enter_page(self):
        text = self.page_number.text()
        if text.isdigit() and self.paper and 1 <= int(text) <= self.paper.count:
            self.go(int(text) - 1)
        else:
            self.page_number.setText(str(self.page + 1))

    def render(self):
        if self.paper:
            width = max(260, self.scroll.viewport().width() - 48)
            self._last_width = width
            self.canvas.display(self.paper, self.page, width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.paper and abs(self.scroll.viewport().width() - 48 - self._last_width) > 15:
            self.render()
