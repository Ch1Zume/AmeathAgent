import os
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase


def configure_fonts(app):
    """Explicit font loading also supports Windows sessions without registry access."""
    families = set(QFontDatabase.families())
    if "Microsoft YaHei UI" not in families:
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisb.ttf"):
            path = fonts / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei UI", 10))
