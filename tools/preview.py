"""Render only Ameath's widgets for visual inspection. No desktop capture."""
import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ameath.app import AmeathWindow
from ameath.config import Config, ROOT
from ameath.theme import STYLE
from ameath.fonts import configure_fonts

app = QApplication([])
app.setStyle("Fusion")
configure_fonts(app)
app.setStyleSheet(STYLE)
out = ROOT / ".local" / "preview"
out.mkdir(parents=True, exist_ok=True)
window = AmeathWindow(Config(out / "data"), tray=False)
window.resize(1280, 850)
window.show()
app.processEvents()
window.open_sample()
app.processEvents()
window.on_selection("Attention builds a context-dependent summary of a sequence. Instead of treating every token equally, it assigns a weight to each value vector and takes their weighted sum.", b"")
window.reader.canvas.selection = QRect(round(46 * window.reader.canvas.scale), round(335 * window.reader.canvas.scale),
                                       round(501 * window.reader.canvas.scale), round(82 * window.reader.canvas.scale))
window.explain_current_selection()
deadline = time.monotonic() + 4
while window.busy and time.monotonic() < deadline:
    QTest.qWait(30)
app.processEvents()
window.grab().save(str(out / "reading-space.png"))
window.pet.show()
app.processEvents()
window.pet.grab().save(str(out / "desktop-pet.png"))
window.shutdown()
print(str(out / "reading-space.png"))
