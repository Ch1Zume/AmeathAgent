"""In-app updates for the packaged Windows build, from GitHub Releases.

A release carries `AmeathAgent-<version>-win64.zip` and a matching `.sha256` file. The zip is
downloaded into the data folder, verified, unpacked, and swapped in by a small helper script
after the app exits. User data in `.local/` next to the exe is never touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import __version__

DEFAULT_REPO = "Ch1Zume/AmeathAgent"
API = "https://api.github.com/repos/{repo}/releases/latest"
ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com", "github-releases.githubusercontent.com"}
ASSET = re.compile(r"^AmeathAgent-(\d+\.\d+\.\d+)-win64\.zip$")
EXE_NAME = "AmeathAgent.exe"
MAX_DOWNLOAD = 1_500_000_000
USER_AGENT = "AmeathAgent-updater"


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    notes: str
    page: str
    asset_url: str = ""
    asset_name: str = ""
    size: int = 0
    checksum_url: str = ""

    @property
    def newer(self) -> bool:
        return version_tuple(self.version) > version_tuple(__version__)

    @property
    def installable(self) -> bool:
        return bool(self.asset_url and self.checksum_url)


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_directory() -> Path:
    return Path(sys.executable).resolve().parent


def _checked(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise UpdateError("更新地址不是 GitHub 官方下载地址，已停止。")
    return url


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _checked(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: float = 20):
    opener = urllib.request.build_opener(_CheckedRedirect)
    request = urllib.request.Request(_checked(url), headers={"User-Agent": USER_AGENT,
                                                             "Accept": "application/vnd.github+json"})
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError("还没有找到已发布的版本（仓库没有 Release 或不可公开访问）。") from None
        if error.code == 403:
            raise UpdateError("GitHub 暂时限制了查询次数，请稍后再试。") from None
        raise UpdateError(f"GitHub 返回 HTTP {error.code}。") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("连不上 GitHub，请检查网络后再试。") from None


def parse_release(data: dict) -> UpdateInfo:
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        raise UpdateError("GitHub 返回的版本信息不完整。")
    version = ".".join(map(str, version_tuple(data["tag_name"])))
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    asset = next((a for a in assets if ASSET.match(str(a.get("name") or ""))
                  and version_tuple(a["name"]) == version_tuple(version)), None)
    checksum = next((a for a in assets if asset and a.get("name") == asset["name"] + ".sha256"), None)
    return UpdateInfo(version, str(data.get("body") or "")[:20000], str(data.get("html_url") or ""),
                      str(asset.get("browser_download_url") or "") if asset else "",
                      str(asset.get("name") or "") if asset else "",
                      int(asset.get("size") or 0) if asset else 0,
                      str(checksum.get("browser_download_url") or "") if checksum else "")


def check(repo: str = DEFAULT_REPO) -> UpdateInfo:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise UpdateError("更新仓库名称不正确。")
    with _open(API.format(repo=repo)) as response:
        content = response.read(2_000_001)
    if len(content) > 2_000_000:
        raise UpdateError("版本信息过大，已停止。")
    try:
        return parse_release(json.loads(content))
    except ValueError:
        raise UpdateError("GitHub 返回的版本信息无法识别。") from None


def download(info: UpdateInfo, directory: Path, cancelled: threading.Event, progress=None) -> Path:
    """Download and verify the release zip; returns its path."""
    if not info.installable:
        raise UpdateError("这个版本没有附带 Windows 安装包和校验文件，请到发布页手动下载。")
    directory.mkdir(parents=True, exist_ok=True)
    with _open(info.checksum_url) as response:
        text = response.read(4096).decode("utf-8", "replace")
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not match:
        raise UpdateError("校验文件格式不正确，已停止。")
    expected = match[1].lower()
    target = directory / info.asset_name
    partial = target.with_suffix(".part")
    digest, received = hashlib.sha256(), 0
    with _open(info.asset_url, timeout=60) as response, partial.open("wb") as stream:
        total = int(response.headers.get("Content-Length") or info.size or 0)
        while True:
            if cancelled.is_set():
                stream.close()
                partial.unlink(missing_ok=True)
                raise UpdateError("已取消下载。")
            chunk = response.read(1 << 20)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_DOWNLOAD:
                raise UpdateError("安装包超过大小上限，已停止。")
            digest.update(chunk)
            stream.write(chunk)
            if progress:
                progress(received, total)
    if digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise UpdateError("安装包校验失败（SHA256 不一致），已删除，请稍后重试。")
    partial.replace(target)
    return target


def extract(archive: Path, directory: Path) -> Path:
    """Unpack into a fresh staging folder; returns the folder that holds AmeathAgent.exe."""
    staging = directory / ("staging-" + archive.stem)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    root = staging.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            destination = (staging / member.filename).resolve()
            if root != destination and root not in destination.parents:
                raise UpdateError("安装包包含不安全的路径，已停止。")
        bundle.extractall(staging)
    found = [path.parent for path in staging.rglob(EXE_NAME)]
    if len(found) != 1 or not (found[0] / "_internal").is_dir():
        raise UpdateError("安装包结构不正确，缺少 AmeathAgent.exe 或 _internal。")
    return found[0]


# Paths come in through the environment so non-ASCII folders survive cmd's code page.
APPLY_SCRIPT = r"""@echo off
setlocal EnableExtensions
set tries=0
:wait
tasklist /FI "PID eq %AMEATH_PID%" 2>NUL | find "%AMEATH_PID%" >NUL
if errorlevel 1 goto copy
set /a tries+=1
if %tries% GEQ 120 goto fail
ping -n 2 127.0.0.1 >NUL
goto wait
:copy
robocopy "%AMEATH_STAGING%\_internal" "%AMEATH_APP%\_internal" /MIR /R:5 /W:1 /NP /NFL /NDL >> "%AMEATH_LOG%"
if errorlevel 8 goto fail
copy /Y "%AMEATH_STAGING%\AmeathAgent.exe" "%AMEATH_APP%\AmeathAgent.exe" >> "%AMEATH_LOG%"
if errorlevel 1 goto fail
echo updated >> "%AMEATH_LOG%"
start "" "%AMEATH_APP%\AmeathAgent.exe"
exit /b 0
:fail
echo failed >> "%AMEATH_LOG%"
start "" "%AMEATH_APP%\AmeathAgent.exe"
exit /b 1
"""


def launch_apply(staged: Path, directory: Path) -> None:
    """Start the helper that waits for this process to exit, swaps the files and restarts."""
    if not is_frozen() or sys.platform != "win32":
        raise UpdateError("只有打包后的 Windows 版本可以在应用内安装更新；源码运行请使用 git pull。")
    script = directory / "apply_update.cmd"
    script.write_text(APPLY_SCRIPT, encoding="ascii")
    env = os.environ.copy()
    env.update({"AMEATH_PID": str(os.getpid()), "AMEATH_STAGING": str(staged), "AMEATH_APP": str(app_directory()),
                "AMEATH_LOG": str(directory / "apply_update.log")})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/d", "/c", str(script)], env=env, cwd=str(directory), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
