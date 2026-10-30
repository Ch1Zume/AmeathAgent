from __future__ import annotations
import json
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

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
        self.tts_enabled = QCheckBox("朗读回复")
        self.tts_enabled.setChecked(config.settings.tts_enabled)
        form.addRow("语音", self.tts_enabled)
        self.tts_provider = QComboBox()
        self.tts_provider.addItem("爱弥斯本地音色 · GPT-SoVITS", "aemeath_local")
        self.tts_provider.addItem("MiniMax · 同步语音合成", "minimax")
        self.tts_provider.addItem("自定义接口（代码扩展）", "custom")
        if self.tts_provider.findData(config.settings.tts_provider) < 0:
            self.tts_provider.addItem(config.settings.tts_provider, config.settings.tts_provider)
        self.tts_provider.setCurrentIndex(self.tts_provider.findData(config.settings.tts_provider))
        form.addRow("语音接口", self.tts_provider)
        self.tts_endpoint = QLineEdit(config.settings.tts_endpoint)
        self.tts_endpoint.setPlaceholderText("https://api.minimaxi.com/v1/t2a_v2")
        form.addRow("语音地址", self.tts_endpoint)
        self.tts_model = QLineEdit(config.settings.tts_model)
        self.tts_model.setPlaceholderText("speech-2.8-turbo")
        form.addRow("语音模型", self.tts_model)
        self.tts_api_key = QLineEdit(config.tts_key())
        self.tts_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.tts_api_key.setPlaceholderText("MiniMax API 密钥，与聊天模型密钥分开")
        form.addRow("语音密钥", self.tts_api_key)
        self.tts_remember = QCheckBox("在此 Windows 账户下加密保存语音密钥")
        self.tts_remember.setChecked(config.tts_key_path.exists())
        form.addRow("", self.tts_remember)
        self.tts_voice = QLineEdit(config.settings.tts_voice)
        self.tts_voice.setMaxLength(80)
        self.tts_voice.setPlaceholderText("MiniMax 音色 ID，例如 female-shaonv")
        form.addRow("语音音色 ID", self.tts_voice)
        self.tts_provider.currentIndexChanged.connect(self.update_speech_fields)
        self.update_speech_fields()
        self.screen_enabled = QCheckBox("定时读取屏幕并主动交流（默认关闭）")
        self.screen_enabled.setChecked(config.settings.screen_observation_enabled)
        form.addRow("屏幕观察", self.screen_enabled)
        self.screen_interval = QSpinBox()
        self.screen_interval.setRange(5, 180)
        self.screen_interval.setSuffix(" 分钟")
        self.screen_interval.setValue(config.settings.screen_observation_interval)
        form.addRow("观察间隔", self.screen_interval)
        self.vision_endpoint = QLineEdit(config.settings.vision_endpoint)
        self.vision_endpoint.setPlaceholderText("兼容 Chat Completions 的视觉服务地址")
        form.addRow("视觉服务地址", self.vision_endpoint)
        self.vision_model = QLineEdit(config.settings.vision_model)
        self.vision_model.setPlaceholderText("支持图片输入的模型名称")
        form.addRow("视觉模型", self.vision_model)
        self.vision_api_key = QLineEdit(config.vision_key())
        self.vision_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.vision_api_key.setPlaceholderText("视觉服务密钥；本地服务可留空")
        form.addRow("视觉密钥", self.vision_api_key)
        self.vision_remember = QCheckBox("在此 Windows 账户下加密保存视觉密钥")
        self.vision_remember.setChecked(config.vision_key_path.exists())
        form.addRow("", self.vision_remember)
        screen_info = QLabel("切换到连接模型模式并启用后，按间隔截取鼠标所在屏幕的完整画面并发送至视觉服务，可能包含聊天、账号等私密内容；截图不写入本地文件；夜间、静默和任务中暂停，并与主动交流共用每天上限和 45 分钟冷却。可能唤醒睡着的桌宠。")
        screen_info.setWordWrap(True)
        form.addRow("", screen_info)
        self.game_thinking = QCheckBox("游戏决策使用深度思考")
        self.game_thinking.setChecked(config.settings.game_thinking)
        form.addRow("游戏模式", self.game_thinking)
        self.effort = QComboBox()
        for label, value in [("低 · 更快", "low"), ("高 · 默认", "high"), ("最大", "max")]:
            self.effort.addItem(label, value)
        self.effort.setCurrentIndex(self.effort.findData(config.settings.reasoning_effort))
        form.addRow("思考强度", self.effort)
        form_widget = QWidget()
        form_widget.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        scroll.setMinimumHeight(280)
        layout.addWidget(scroll, 1)
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

    def update_speech_fields(self):
        is_minimax = self.tts_provider.currentData() == "minimax"
        for widget in (self.tts_endpoint, self.tts_model, self.tts_api_key, self.tts_remember):
            widget.setEnabled(is_minimax)
        self.tts_voice.setEnabled(self.tts_provider.currentData() != "aemeath_local")

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
            tts_endpoint = validate_endpoint(self.tts_endpoint.text())
            vision_endpoint = validate_endpoint(self.vision_endpoint.text())
            if self.screen_enabled.isChecked() and not self.vision_model.text().strip():
                raise ValueError("启用屏幕观察前请填写支持图片输入的视觉模型。")
            if not self.model.text().strip():
                raise ValueError("请填写模型名称。")
            if self.tts_enabled.isChecked() and self.tts_provider.currentData() == "minimax":
                if not self.tts_model.text().strip() or not self.tts_voice.text().strip() or not self.tts_api_key.text().strip():
                    raise ValueError("请填写 MiniMax 语音模型、音色 ID 和语音 API 密钥。")
            self.config.set_vision_key(self.vision_api_key.text(), self.vision_remember.isChecked())
            self.config.set_key(self.api_key.text(), self.remember.isChecked())
            self.config.set_tts_key(self.tts_api_key.text(), self.tts_remember.isChecked())
            current = self.config.settings
            self.config.settings = replace(current, endpoint=endpoint, model=self.model.text().strip(),
                                            mode=self.mode.currentData(), protocol=self.protocol.currentData(),
                                            thinking=self.thinking.isChecked(), stream_chat=self.stream_chat.isChecked(),
                                            tts_enabled=self.tts_enabled.isChecked(), tts_provider=self.tts_provider.currentData(),
                                            tts_endpoint=tts_endpoint, tts_model=self.tts_model.text().strip(),
                                            tts_voice=self.tts_voice.text().strip(), game_thinking=self.game_thinking.isChecked(),
                                            reasoning_effort=self.effort.currentData(),
                                            screen_observation_enabled=self.screen_enabled.isChecked(),
                                            screen_observation_interval=self.screen_interval.value(),
                                            vision_endpoint=vision_endpoint, vision_model=self.vision_model.text().strip())
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
