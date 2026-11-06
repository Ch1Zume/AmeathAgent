# PyInstaller spec for the Windows build: `pyinstaller packaging/AmeathAgent.spec`.
# One folder (AmeathAgent.exe + _internal) so updates can replace program files while
# user data stays in `.local` beside the exe.
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent

datas = [
    (str(root / "assets"), "assets"),
    (str(root / "CHANGELOG.md"), "."),
    (str(root / "LICENSE"), "."),
    (str(root / "THIRD-PARTY-NOTICES.md"), "."),
    (str(root / "licenses"), "licenses"),
    (str(root / "docs" / "ROADMAP.md"), "docs"),
    (str(root / "docs" / "sts2.md"), "docs"),
    (str(root / "integrations" / "sts2" / "bridge-release.json"), "integrations/sts2"),
    (str(root / "integrations" / "sts2" / "UPSTREAM-LICENSE.txt"), "integrations/sts2"),
    # The optional local voice server runs in its own Python environment and imports these
    # as source files with --app-dir pointing at _internal.
    (str(root / "ameath" / "__init__.py"), "ameath"),
    (str(root / "ameath" / "local_voice_server.py"), "ameath"),
    (str(root / "ameath" / "local_voice_inference.py"), "ameath"),
    (str(root / "ameath" / "voice_quality.py"), "ameath"),
]
# Dependency metadata carries their license files (PyMuPDF's COPYING, among others).
datas += copy_metadata("PyMuPDF") + copy_metadata("PySide6_Essentials") + copy_metadata("shiboken6")

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=[],
    excludes=["tkinter", "pytest", "numpy", "scipy", "torch", "fastapi", "uvicorn"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AmeathAgent",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AmeathAgent")
