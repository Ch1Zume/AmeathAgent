"""Preview actual generated sprites, 2D travel and rest/flight state transitions."""
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from ameath.config import ROOT
from ameath.fonts import configure_fonts
from ameath.pet import DesktopPet
from ameath.sprites import load_snowcap


def main():
    app = QApplication([])
    configure_fonts(app)
    sheet, warning = load_snowcap(ROOT / ".local")
    if sheet is None or len(sheet.generated_packs) != 4:
        raise SystemExit(warning or "Missing generated animation atlases")
    out = ROOT / ".local" / "preview"
    out.mkdir(parents=True, exist_ok=True)
    pet = DesktopPet(sheet, time_provider=lambda: datetime(2026, 10, 25, 12))
    pet.move(240, 180)
    pet.show()
    app.processEvents()
    pet.timer.stop()
    pet.stop_moving()
    frames, snapshots = [], []

    def segment(label, duration, start):
        start()
        count = round(duration / 40)
        for index in range(count):
            pet.advance(40)
            canvas = QImage(800, 700, QImage.Format.Format_RGBA8888)
            canvas.fill(QColor("#f2edf7"))
            painter = QPainter(canvas)
            painter.setPen(QColor("#594575"))
            painter.setFont(QFont("Microsoft YaHei UI", 14, QFont.Weight.DemiBold))
            painter.drawText(QRectF(20, 12, 760, 38), Qt.AlignmentFlag.AlignCenter, label)
            pet.render(painter, pet.pos())
            painter.end()
            image = Image.frombytes("RGBA", (800, 700), bytes(canvas.constBits())).convert("RGB")
            frames.append(image)
            if index == count // 2:
                snapshots.append(image.copy())

    segment("安静待机 · 没有常驻气泡", 1000, lambda: pet.set_activity("idle"))
    segment("挥手 · 同一只手抬起和放下", 1100, lambda: pet.play_action("waving"))
    segment("向右上移动 · 自然加速和转身", 1700, lambda: pet.move_to(QPoint(420, 90), speed=140))
    segment("向下移动 · 自然减速", 1900, lambda: pet.move_to(QPoint(420, 330), speed=140))
    segment("向左上移动 · 步态相位连续", 2100, lambda: pet.move_to(QPoint(200, 180), speed=140))
    segment("坐下 · 抱着小猫休息", 2600, lambda: pet.sit())
    segment("睡觉 · 呼吸循环与闭眼", 3000, lambda: pet.sleep())
    segment("被叫醒 · 起身恢复待机", 1400, lambda: pet.wake())
    segment("飞行 · 起飞、斜向飞行和落地", 3800, lambda: pet.fly_to(QPoint(420, 90), speed=160))
    segment("短暂回复 · 气泡随后自动收起", 4400, lambda: pet.respond("我在这里，准备好继续陪你啦。", "waving"))
    pet.close()
    destination = out / "ameath-motion-v2.gif"
    frames[0].save(destination, save_all=True, append_images=frames[1:], duration=40, loop=0, optimize=True)
    contact = Image.new("RGB", (800 * 3, 700 * 4), "#f2edf7")
    for index, image in enumerate(snapshots):
        contact.paste(image, ((index % 3) * 800, (index // 3) * 700))
    contact.save(out / "pet-v2-contact.png")
    print(f"{destination}\n{len(frames)} frames, {len(frames) * 40 / 1000:.2f} seconds")


if __name__ == "__main__":
    main()
