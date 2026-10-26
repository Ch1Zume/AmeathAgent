"""Generated resonance sprites, played on a fixed body anchor in both directions."""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from .sprites import alpha_bounds, atlas_cuts

ASSET = Path(__file__).resolve().parent.parent / "assets/snowcap-lifecycle-v1/entrance.png"
_FRAMES = None


def load_resonance():
    global _FRAMES
    if _FRAMES is not None:
        return _FRAMES
    image = QImage(str(ASSET))
    if image.isNull() or not image.hasAlphaChannel():
        return ()
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    xs, ys = atlas_cuts(image, 4, 2)
    cells = [image.copy(xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r]) for r in range(2) for c in range(4)]
    bounds = [alpha_bounds(cell) for cell in cells]
    if any(bound.isEmpty() for bound in bounds):
        return ()
    scale = 190 / bounds[-1].height()
    frames = []
    for index, (cell, bound) in enumerate(zip(cells, bounds)):
        factor = min(scale, 238 / bound.width())
        canvas = QImage(248, 250, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        x = 124 - (bound.left() + bound.width() / 2) * factor
        y = 210 - bound.bottom() * factor
        if index < 2:
            y = 125 - (bound.top() + bound.height() / 2) * factor
        p.drawImage(QRectF(x, y, cell.width() * factor, cell.height() * factor), cell)
        p.end()
        frames.append(canvas)
    _FRAMES = tuple(frames)
    return _FRAMES


def paint_resonance(painter, frames, progress, *, exiting=False, idle=None):
    t = 1 - progress if exiting else progress
    if not frames:
        if idle is not None:
            painter.setOpacity(max(0, min(1, t)))
            painter.drawImage(QRectF(28, 82, 192, 208), idle)
        return
    position = min(7.0, max(0.0, t * 7))
    index = min(6, int(position))
    mix = position - index
    idle_mix = max(0, (t - .87) / .13) if idle is not None else 0
    painter.setOpacity(min(1, t * 7) * (1 - mix) * (1 - idle_mix))
    painter.drawImage(QRectF(0, 72, 248, 250), frames[index])
    painter.setOpacity(min(1, t * 7) * mix * (1 - idle_mix))
    painter.drawImage(QRectF(0, 72, 248, 250), frames[index + 1])
    if idle is not None and t > .87:
        # Blend to the exact installed idle sprite to avoid a hard asset boundary.
        painter.setOpacity(idle_mix)
        painter.drawImage(QRectF(28, 82, 192, 208), idle)
