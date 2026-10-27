from __future__ import annotations
import json
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .config import Config, Settings, validate_endpoint
from .theme import MARKDOWN_STYLE, STYLE
from .widgets import SafeTextBrowser


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.config = config
        self.setWindowTitle("AmeathAgent · 模型设置")
        self.setMinimumWidth(530)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(17)
        title = QLabel("爱弥斯 · 模型设置")
        title.setObjectName("headline")
        layout.addWidget(title)
        desc = QLabel("DeepSeek 是 Ameath 的统一模型核心，供阅读、截图解释和游戏技能共用。\n论文解释由你发起；开启游戏接管后，会根据局面连续调用同一模型。\n本地演示无需密钥。")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        preset = QPushButton("使用 DeepSeek 预设 · Ameath 的统一模型核心")
        preset.clicked.connect(self.deepseek_preset)
        layout.addWidget(preset)
        form = QFormLayout()
        form.setSpacing(14)
        self.mode = QComboBox()
        self.mode.addItem("本地演示 · 预置概念卡片", "demo")
        self.mode.addItem("连接模型 · 真实解释", "live")
        self.mode.setCurrentIndex(1 if config.settings.mode == "live" else 0)
        form.addRow("工作模式", self.mode)
        self.endpoint = QLineEdit(config.settings.endpoint)
        self.endpoint.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("服务地址", self.endpoint)
        self.protocol = QComboBox()
        self.protocol.addItem("Responses · OpenAI", "responses")
        self.protocol.addItem("Chat Completions · 兼容服务", "chat")
        self.protocol.setCurrentIndex(1 if config.settings.protocol == "chat" else 0)
        form.addRow("接口类型", self.protocol)
        self.model = QLineEdit(config.settings.model)
        form.addRow("模型名称", self.model)
        self.api_key = QLineEdit(config.key())
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("API 密钥；本地模型可留空")
        form.addRow("API 密钥", self.api_key)
        self.thinking = QCheckBox("阅读与日常对话使用深度思考")
        self.thinking.setChecked(config.settings.thinking)
        form.addRow("日常模式", self.thinking)
        self.stream_chat = QCheckBox("聊天回复边生成边显示（仅 Chat Completions 接口）")
        self.stream_chat.setChecked(config.settings.stream_chat)
        form.addRow("流式显示", self.stream_chat)
        self.tts_enabled = QCheckBox("朗读回复（需先在 ameath/tts.py 接入语音接口）")
        self.tts_enabled.setChecked(config.settings.tts_enabled)
        form.addRow("语音", self.tts_enabled)
        self.tts_voice = QLineEdit(config.settings.tts_voice)
        self.tts_voice.setMaxLength(80)
        self.tts_voice.setPlaceholderText("音色名称，由语音接口自行解释")
        form.addRow("音色", self.tts_voice)
        self.game_thinking = QCheckBox("游戏决策使用深度思考")
        self.game_thinking.setChecked(config.settings.game_thinking)
        form.addRow("游戏模式", self.game_thinking)
        self.effort = QComboBox()
        for label, value in [("低 · 更快", "low"), ("高 · 默认", "high"), ("最大", "max")]:
            self.effort.addItem(label, value)
        self.effort.setCurrentIndex(self.effort.findData(config.settings.reasoning_effort))
        form.addRow("思考强度", self.effort)
        layout.addLayout(form)
        self.remember = QCheckBox("在此 Windows 账户下加密保存密钥")
        self.remember.setChecked(config.key_path.exists())
        layout.addWidget(self.remember)
        info = QLabel("陪读提示只在内置阅读区生效，不自动调用模型。\n截图不写入磁盘；对话默认只保留在内存，点击保存才生成本地卡片。\n服务方的数据保留规则以其政策为准。")
        info.setObjectName("muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def deepseek_preset(self):
        if "api.deepseek.com" not in self.endpoint.text():
            self.api_key.clear()
            self.remember.setChecked(False)
        self.endpoint.setText("https://api.deepseek.com")
        self.model.setText("deepseek-flash")
        self.protocol.setCurrentIndex(1)
        self.mode.setCurrentIndex(1)

    def save(self):
        try:
            endpoint = validate_endpoint(self.endpoint.text())
            if not self.model.text().strip():
                raise ValueError("请填写模型名称。")
            self.config.set_key(self.api_key.text(), self.remember.isChecked())
            current = self.config.settings
            self.config.settings = replace(current, endpoint=endpoint, model=self.model.text().strip(),
                                            mode=self.mode.currentData(), protocol=self.protocol.currentData(),
                                            thinking=self.thinking.isChecked(), stream_chat=self.stream_chat.isChecked(), tts_enabled=self.tts_enabled.isChecked(), tts_voice=self.tts_voice.text().strip(), game_thinking=self.game_thinking.isChecked(),
                                            reasoning_effort=self.effort.currentData())
            self.config.save()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "设置未保存", str(error))
            return
        self.accept()


class CardsView(QWidget):
    navigate = Signal(str, int)

    def __init__(self, notebook):
        super().__init__()
        self.notebook = notebook
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        title = QLabel("一点一点，积累理解。")
        title.setObjectName("headline")
        layout.addWidget(title)
        desc = QLabel("只有你主动保存的解释会留在这里。卡片保存在这台电脑。")
        desc.setObjectName("muted")
        layout.addWidget(desc)
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(240)
        self.list.setMaximumWidth(340)
        self.list.currentRowChanged.connect(self.show_card)
        row.addWidget(self.list, 1)
        self.detail = SafeTextBrowser()
        self.detail.setOpenLinks(False)
        self.detail.document().setDefaultStyleSheet(MARKDOWN_STYLE)
        self.detail.anchorClicked.connect(self.go)
        row.addWidget(self.detail, 2)
        layout.addLayout(row, 1)
        self.delete_button = QPushButton("删除这张卡片")
        self.delete_button.clicked.connect(self.delete)
        layout.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignRight)
        self.rows = []
        self.refresh()

    def refresh(self):
        self.rows = self.notebook.cards()
        self.list.clear()
        for card in self.rows:
            title = (card["selection"] or "这一页的理解").replace("\n", " ")[:28]
            location = f"第 {card['page']} 页" if card["page"] else "无页码"
            self.list.addItem(f"{title}\n{card['title'][:28]} · {location}")
        if self.rows:
            self.list.setCurrentRow(0)
        else:
            self.detail.setMarkdown("### 还没有概念卡片\n\n解释一段内容后，点击「保存卡片」，把理解和出处一起留下。")
        self.delete_button.setEnabled(bool(self.rows))

    def show_card(self, index):
        if 0 <= index < len(self.rows):
            row = self.rows[index]
            location = f" · [返回第 {row['page']} 页](card:page)" if row["page"] else " · 未关联论文"
            self.detail.setMarkdown(row["explanation"] + f"\n\n---\n\n{row['title']}" + location)

    def go(self, url):
        index = self.list.currentRow()
        if 0 <= index < len(self.rows):
            row = self.rows[index]
            if url.scheme() == "card" and row["page"]:
                self.navigate.emit(row["paper_id"], row["page"])
            elif url.scheme() == "source":
                try:
                    index = int(url.path()) - 1
                    references = json.loads(row["sources"])
                    if 0 <= index < len(references):
                        source = references[index]
                        self.navigate.emit(source["paper_id"], source["page"])
                except (ValueError, KeyError, TypeError):
                    pass

    def delete(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.rows):
            self.notebook.delete(self.rows[index]["id"])
            self.refresh()
