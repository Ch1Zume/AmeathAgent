"""Preview only Ameath's own chat and persona widgets with synthetic local data."""
import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ameath.app import AmeathWindow
from ameath.companion_ui import CompanionDialog
from ameath.config import Config, ROOT
from ameath.fonts import configure_fonts
from ameath.theme import STYLE


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    configure_fonts(app)
    app.setStyleSheet(STYLE)
    out = ROOT / ".local" / "preview"
    out.mkdir(parents=True, exist_ok=True)
    config = Config(out / "chat-data")
    config.settings.mode = "demo"
    window = AmeathWindow(config, tray=False)
    window.pet.show()
    window.open_chat()
    for text in ("你好呀，Ameath！", "我今天终于完成了第一版，想庆祝一下！"):
        window.chat.input.setPlainText(text)
        window.chat.submit()
        deadline = time.monotonic() + 3
        while window.chat.busy and time.monotonic() < deadline:
            QTest.qWait(10)
    window.chat.agent.memory.remember("回复偏好", "喜欢先给结论，再讲原因。")
    window.chat.agent.memory.remember("项目", "正在开发桌面伙伴 Ameath。")
    app.processEvents()
    window.chat.grab().save(str(out / "chat.png"))
    canvas = QImage(820, 680, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#f1edf8"))
    painter = QPainter(canvas)
    painter.setPen(QColor("#594575"))
    painter.setFont(QFont("Microsoft YaHei UI", 14, QFont.Weight.DemiBold))
    painter.drawText(QRectF(20, 10, 780, 35), Qt.AlignmentFlag.AlignCenter, "单击聊天 · 回复与桌宠动作联动")
    window.chat.render(painter, QPoint(28, 70))
    window.pet.render(painter, QPoint(535, 315))
    painter.end()
    canvas.save(str(out / "chat-and-pet.png"))
    editor = CompanionDialog(config, window.chat)
    editor.show()
    app.processEvents()
    editor.grab().save(str(out / "persona-card.png"))
    editor.tabs.setCurrentIndex(1)
    app.processEvents()
    editor.grab().save(str(out / "long-term-memory.png"))
    editor.close()
    window.shutdown()
    print(str(out / "chat-and-pet.png"))


if __name__ == "__main__":
    main()
