"""Render Ameath's real sprite player and movement into a GIF; never capture the desktop."""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from ameath.config import ROOT
from ameath.fonts import configure_fonts
from ameath.pet import DesktopPet
from ameath.sprites import CLIPS, load_snowcap


def drag_event(pet, kind, position):
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, QPointF(position - pet.pos()), QPointF(position), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: pet.mousePressEvent,
     QEvent.Type.MouseMove: pet.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: pet.mouseReleaseEvent}[kind](event)


def main():
    app = QApplication([])
    configure_fonts(app)
    out = ROOT / ".local" / "preview"
    out.mkdir(parents=True, exist_ok=True)
    sheet, warning = load_snowcap(ROOT / ".local")
    if sheet is None:
        raise SystemExit(warning)
    pet = DesktopPet(sheet)
    pet.move(100, 72)
    pet.show()
    app.processEvents()
    pet.timer.stop()
    pet.stop_moving()
    frames = []
    snapshots = []

    def segment(label, milliseconds, start, update=None):
        start()
        count = round(milliseconds / 60)
        for index in range(count):
            if update:
                update(index / max(1, count - 1))
            pet.advance(60)
            canvas = QImage(680, 416, QImage.Format.Format_RGBA8888)
            canvas.fill(QColor("#f1edf8"))
            painter = QPainter(canvas)
            painter.setPen(QColor("#594575"))
            painter.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.DemiBold))
            painter.drawText(QRectF(20, 8, 640, 28), Qt.AlignmentFlag.AlignCenter, label)
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(20, 37, 640, 20), Qt.AlignmentFlag.AlignCenter,
                             f"{pet.animation}  ·  {pet.frame_index + 1}/{pet.animator.clips[pet.animation].frames}")
            pet.render(painter, pet.pos())
            painter.end()
            frame = Image.frombytes("RGBA", (canvas.width(), canvas.height()), bytes(canvas.constBits())).convert("RGB")
            frames.append(frame)
            if index == count // 2:
                snapshots.append(frame.copy())

    segment("Ameath · 日常待机", 1080, lambda: pet.say("我是 Ameath，陪你一起读懂。"))
    segment("点击 · 挥手打招呼", 780, lambda: pet.play_action("waving"))
    segment("阅读 / 游戏思考 · 工作动作", 1200, lambda: pet.say("让我把这段内容和上下文连起来。", "thinking"))
    segment("等你操作 · 等待动作", 1200, lambda: pet.say("选区准备好了，点击解释就能聊聊。", "waiting"))
    segment("检查结果 · 回顾动作", 1080, lambda: pet.say("我再检查一下这一步。", "review"))
    segment("请求失败 · 反应播完回到待机", 1440, lambda: pet.say("暂时没有连上，我们稍后再试。", "failed"))
    segment("向右散步 · 方向与步速联动", 1860,
            lambda: (pet.say("散步一下。"), pet.move_to(QPoint(340, 72), speed=140)))
    segment("向左散步 · 方向切换", 1860, lambda: pet.move_to(QPoint(100, 72), speed=140))
    press = pet.pos() + QPoint(110, 160)
    segment("按住拖动 · 向右跑动", 720,
            lambda: drag_event(pet, QEvent.Type.MouseButtonPress, press),
            lambda progress: drag_event(pet, QEvent.Type.MouseMove, press + QPoint(round(180 * progress), 0)))
    segment("拖动改变方向 · 向左跑动", 720, lambda: None,
            lambda progress: drag_event(pet, QEvent.Type.MouseMove, press + QPoint(180 - round(100 * progress), 0)))
    segment("松手 · 跳一下再恢复待机", 1020,
            lambda: drag_event(pet, QEvent.Type.MouseButtonRelease, press + QPoint(80, 0)))
    pet.close()
    destination = out / "ameath-snowcap.gif"
    frames[0].save(destination, save_all=True, append_images=frames[1:], duration=60, loop=0, optimize=False)
    contact = Image.new("RGB", (680 * 3, 416 * 4), "#f1edf8")
    for index, frame in enumerate(snapshots):
        contact.paste(frame, ((index % 3) * 680, (index // 3) * 416))
    contact.save(out / "pet-motion-contact.png")
    print(f"{destination}\n{len(frames)} frames · {len(frames) * 60 / 1000:.2f} seconds")


if __name__ == "__main__":
    main()
