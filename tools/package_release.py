"""Zip a PyInstaller build as AmeathAgent-<version>-win64.zip and write its .sha256 file.

Usage: python tools/package_release.py [dist/AmeathAgent] [--out dist]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def version() -> str:
    text = (ROOT / "ameath" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text)[1]


def package(build: Path, out: Path, platform: str = "win64") -> Path:
    exe = "AmeathAgent.exe" if platform == "win64" else "AmeathAgent"
    if not (build / exe).is_file() or not (build / "_internal").is_dir():
        raise SystemExit(f"{build} 不是完整的 PyInstaller 目录（缺少 {exe} 或 _internal）。")
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"AmeathAgent-{version()}-{platform}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(build.rglob("*")):
            if path.is_file():
                archive.write(path, Path("AmeathAgent") / path.relative_to(build))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out / (target.name + ".sha256")).write_text(f"{digest}  {target.name}\n", encoding="ascii")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", nargs="?", default=str(ROOT / "dist" / "AmeathAgent"))
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--platform", default="win64", help="win64 for releases; linux64 only for local spec checks")
    args = parser.parse_args()
    result = package(Path(args.build), Path(args.out), args.platform)
    print(result)
    sys.exit(0)
