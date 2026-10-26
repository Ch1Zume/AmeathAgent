"""Render the game skill on an isolated Qt surface; never captures the desktop."""
import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ameath.app import AmeathWindow
from ameath.config import Config, ROOT
from ameath.fonts import configure_fonts
from ameath.theme import STYLE

app = QApplication([])
app.setStyle("Fusion")
configure_fonts(app)
app.setStyleSheet(STYLE)
out = ROOT / ".local" / "preview"
out.mkdir(parents=True, exist_ok=True)
window = AmeathWindow(Config(out / "game-data"), tray=False)
window.resize(1340, 850)
window.show()
window.open_game()
app.processEvents()
window.game_panel.start("suggest")
deadline = time.monotonic() + 5
while window.game_panel.busy and time.monotonic() < deadline:
    QTest.qWait(25)
app.processEvents()
window.grab().save(str(out / "spire-skill.png"))
window.shutdown()
print(str(out / "spire-skill.png"))
