"""Compare original whole-frame playback with horizontal registration only."""
import copy
import json
import os
import sys
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication
from ameath.config import ROOT
from ameath.fonts import configure_fonts
from ameath.pet import DesktopPet
from ameath.sprites import alpha_bounds, atlas_cuts, body_center_x, load_snowcap


def original_idle_frames():
    atlas = QImage(str(ROOT / 'assets/snowcap-motion-v2/actions.png')).convertToFormat(QImage.Format.Format_RGBA8888)
    xs, ys = atlas_cuts(atlas, 8, 4)
    cells = [atlas.copy(xs[i], ys[0], xs[i+1]-xs[i], ys[1]-ys[0]) for i in range(8)]
    bounds = [alpha_bounds(frame) for frame in cells]
    center = sorted(b.center().x() for b in bounds)[4]
    anchor = sorted(b.bottom() for b in bounds)[4]
    top, bottom = min(b.top() for b in bounds), max(b.bottom() for b in bounds)
    half_width = max(center - min(b.left() for b in bounds), max(b.right() for b in bounds) - center)
    scale = min(190 / sorted(b.height() for b in bounds)[4], 204 / (bottom-top+1), 90 / half_width)
    dy = min(207 - bottom * scale, max(2 - top * scale, 201 - anchor * scale))
    frames = []
    for cell in cells:
        canvas = QImage(192, 208, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage(QRectF(96-center*scale, dy, cell.width()*scale, cell.height()*scale), cell)
        painter.end()
        frames.append(canvas)
    return tuple(frames)


def main():
    app = QApplication([])
    configure_fonts(app)
    sheet, warning = load_snowcap(ROOT / '.local')
    if sheet is None:
        raise SystemExit(warning)
    before = copy.copy(sheet)
    before._frames = dict(sheet._frames)
    before._frames['idle'] = original_idle_frames()
    pets = [DesktopPet(s, resting=False) for s in (before, sheet)]
    for pet in pets:
        pet.show()
    app.processEvents()
    for pet in pets:
        pet.timer.stop()
        pet.stop_moving()
    frames, restored = [], []
    for i in range(8):
        canvas = QImage(640, 365, QImage.Format.Format_RGBA8888)
        canvas.fill(QColor('#fff4f8'))
        painter = QPainter(canvas)
        painter.setFont(QFont('Microsoft YaHei UI', 11))
        painter.setPen(QColor('#8c3159'))
        for j, label in enumerate(('最初的逐帧动画', '相同原画 · 仅对齐横向位置')):
            painter.drawText(QRectF(j*320, 8, 320, 30), Qt.AlignmentFlag.AlignCenter, label)
            pets[j].render(painter, QPoint(j*320+36, 40))
        painter.end()
        frames.append(Image.frombytes('RGBA', (640, 365), bytes(canvas.constBits())).convert('RGB'))
        restored.append(frames[-1].crop((320, 0, 640, 365)))
        for pet in pets:
            pet.advance(150)
    out = ROOT / '.local' / 'preview'
    out.mkdir(parents=True, exist_ok=True)
    for name, sequence in [('ameath-restored-comparison.gif', frames), ('ameath-restored-idle.gif', restored)]:
        palette = sequence[0].quantize(colors=255)
        indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in sequence]
        indexed[0].save(out / name, save_all=True, append_images=indexed[1:], duration=150, loop=0, optimize=False)
    contact = Image.new('RGB', (320*4, 365*2), '#fff4f8')
    for i, frame in enumerate(restored):
        contact.paste(frame, ((i%4)*320, (i//4)*365))
    contact.save(out / 'restored-idle-contact.png')
    centers = [[body_center_x(s.frame('idle', i)) for i in range(8)] for s in (before, sheet)]
    report = {'original_head_x_range_px': max(centers[0])-min(centers[0]),
              'stabilized_head_x_range_px': max(centers[1])-min(centers[1]),
              'idle_frames': 8, 'frame_ms': 150, 'whole_frame_rendering': True}
    (out / 'restored-idle-validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    for pet in pets:
        pet.close()
    print(out / 'ameath-restored-idle.gif')


if __name__ == '__main__':
    main()
