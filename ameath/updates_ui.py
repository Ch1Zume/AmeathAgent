"""Version page: in-app updates, the update plan and the change log."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from . import __version__
from .config import ROOT
from .updater import UpdateError, UpdateInfo, check, download, extract, is_frozen, launch_apply
from .widgets import SafeTextBrowser


def read_document(relative: str) -> str:
    try:
        return (ROOT / relative).read_text(encoding="utf-8")
    except OSError:
        return f"没有找到 `{relative}`。"


def changelog_for_users(text: str) -> str:
    """The change log without the developer-facing preamble and validation links."""
    start = text.find("## Unreleased")
    return text[start:] if start >= 0 else text


class UpdateSignals(QObject):
    checked = Signal(int, object)
    failed = Signal(int, str)
    progress = Signal(int, int, int)
    ready = Signal(int, str)


class UpdatesPanel(QWidget):
    update_available = Signal(object)
    restart_requested = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.info: UpdateInfo | None = None
        self.busy = False
        self.generation = 0
        self.cancelled = threading.Event()
        self.signals = UpdateSignals(self)
        self.signals.checked.connect(self.on_checked)
        self.signals.failed.connect(self.on_failed)
        self.signals.progress.connect(self.on_progress)
        self.signals.ready.connect(self.on_ready)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 22, 25, 20)
        layout.setSpacing(12)
        title = QLabel("版本与更新")
        title.setObjectName("headline")
        layout.addWidget(title)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        version_tab = QWidget()
        column = QVBoxLayout(version_tab)
        column.setContentsMargins(4, 12, 4, 4)
        mode = "打包版，可在这里一键更新" if is_frozen() else "源码运行，请用 git pull 更新；打包版可以在这里一键更新"
        self.version_label = QLabel(f"当前版本 {__version__} · {mode}")
        self.version_label.setObjectName("sectionTitle")
        self.version_label.setWordWrap(True)
        column.addWidget(self.version_label)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("检查更新")
        self.check_button.clicked.connect(self.check_now)
        buttons.addWidget(self.check_button)
        self.install_button = QPushButton("下载并安装")
        self.install_button.setObjectName("primary")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        buttons.addWidget(self.install_button)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        column.addWidget(self.progress)
        self.status = QLabel("更新来自 GitHub Releases，下载后会核对 SHA256；你的设置、记忆和日程保存在 .local，不会被覆盖。")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        column.addWidget(self.status)
        self.notes = SafeTextBrowser()
        self.notes.setOpenLinks(False)
        self.notes.setMarkdown("### 新版本说明\n\n点「检查更新」查看是否有新版本。")
        column.addWidget(self.notes, 1)
        self.tabs.addTab(version_tab, "检查更新")

        self.plan = SafeTextBrowser()
        self.plan.setOpenLinks(False)
        self.plan.setMarkdown(read_document("docs/ROADMAP.md"))
        self.tabs.addTab(self.plan, "更新计划")
        self.log = SafeTextBrowser()
        self.log.setOpenLinks(False)
        self.log.setMarkdown(changelog_for_users(read_document("CHANGELOG.md")))
        self.tabs.addTab(self.log, "更新记录")

    @property
    def directory(self) -> Path:
        return self.config.directory / "updates"

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.check_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.install_button.setEnabled(not busy and self._can_install())

    def _can_install(self) -> bool:
        return bool(self.info and self.info.newer and self.info.installable and is_frozen())

    def check_now(self, *, quiet: bool = False):
        if self.busy:
            return
        self.generation += 1
        generation, repo = self.generation, self.config.settings.update_repo
        self.quiet = quiet
        self.cancelled.clear()
        self._set_busy(True)
        if not quiet:
            self.status.setText("正在查询 GitHub 上的最新版本…")

        def run():
            try:
                self.signals.checked.emit(generation, check(repo))
            except UpdateError as error:
                self.signals.failed.emit(generation, str(error))
            except Exception:
                self.signals.failed.emit(generation, "检查更新没有完成，请稍后再试。")

        threading.Thread(target=run, daemon=True, name="ameath-update-check").start()

    def on_checked(self, generation: int, info: UpdateInfo):
        if generation != self.generation:
            return
        self.info = info
        self._set_busy(False)
        notes = info.notes.strip() or "（这个版本没有写说明）"
        self.notes.setMarkdown(f"### {info.version} 版本说明\n\n{notes}")
        if not info.newer:
            self.status.setText(f"已经是最新版本（最新发布 {info.version}）。")
            return
        if not info.installable:
            self.status.setText(f"发现新版本 {info.version}，但没有附带 Windows 安装包，请到发布页手动下载：{info.page}")
        elif not is_frozen():
            self.status.setText(f"发现新版本 {info.version}。源码运行请用 git pull 更新。")
        else:
            size = f"约 {info.size / 1_048_576:.0f} MB，" if info.size else ""
            self.status.setText(f"发现新版本 {info.version}，{size}点「下载并安装」后会自动重启。")
        self.update_available.emit(info)

    def on_failed(self, generation: int, message: str):
        if generation != self.generation:
            return
        self._set_busy(False)
        self.progress.hide()
        if not getattr(self, "quiet", False) or self.info is not None:
            self.status.setText(message)

    def install(self):
        if self.busy or not self._can_install():
            return
        self.generation += 1
        generation, info = self.generation, self.info
        self.quiet = False
        self.cancelled.clear()
        self._set_busy(True)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText(f"正在下载 {info.version}…")

        def report(received, total):
            self.signals.progress.emit(generation, received, total)

        def run():
            try:
                archive = download(info, self.directory, self.cancelled, report)
                staged = extract(archive, self.directory)
                self.signals.ready.emit(generation, str(staged))
            except UpdateError as error:
                self.signals.failed.emit(generation, str(error))
            except (OSError, ValueError):
                self.signals.failed.emit(generation, "安装包下载或解压失败，请检查磁盘空间后重试。")

        threading.Thread(target=run, daemon=True, name="ameath-update-download").start()

    def on_progress(self, generation: int, received: int, total: int):
        if generation == self.generation and total:
            self.progress.setValue(min(100, int(received * 100 / total)))

    def on_ready(self, generation: int, staged: str):
        if generation != self.generation or self.cancelled.is_set():
            return
        try:
            launch_apply(Path(staged), self.directory)
        except (UpdateError, OSError) as error:
            self._set_busy(False)
            self.status.setText(str(error) if isinstance(error, UpdateError) else "无法启动更新程序。")
            return
        self.status.setText("下载完成，正在关闭并安装新版本，稍后会自动重新打开。")
        self.restart_requested.emit()

    def cancel(self):
        self.cancelled.set()
        self.generation += 1
        self._set_busy(False)
        self.progress.hide()
        self.status.setText("已取消。")
