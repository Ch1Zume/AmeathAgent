from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .config import Config
from .memory import CompanionMemory, Persona, PersonaCard, local_date
from .theme import STYLE


class CompanionDialog(QDialog):
    changed = Signal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE)
        self.setWindowTitle("AmeathAgent · 记忆与人设")
        self.resize(620, 700)
        self.config = config
        self.card = PersonaCard(config.directory)
        self.memory = CompanionMemory(config.directory)
        self.selected_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        title = QLabel("让爱弥斯更懂你")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.use_context = QCheckBox("聊天时使用人设与相关记忆（随消息发送到模型服务）")
        self.use_context.setChecked(config.settings.use_companion_context)
        self.use_context.toggled.connect(self.save_preferences)
        layout.addWidget(self.use_context)
        destination = QLabel("模型服务：" + config.settings.endpoint)
        destination.setObjectName("muted")
        destination.setWordWrap(True)
        layout.addWidget(destination)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        persona_tab = QWidget()
        persona_layout = QVBoxLayout(persona_tab)
        form = QFormLayout()
        self.persona_fields = {}
        for name, label in [("name", "角色名称"), ("identity", "身份"), ("personality", "性格"),
                            ("speaking_style", "说话风格"), ("relationship", "相处方式"), ("background", "背景")]:
            if name == "name":
                field = QLineEdit()
                field.setMaxLength(40)
            else:
                field = QTextEdit()
                field.setFixedHeight(66)
                field.setAcceptRichText(False)
            self.persona_fields[name] = field
            form.addRow(label, field)
        persona_layout.addLayout(form)
        self.populate_persona(self.card.load())
        persona_buttons = QHBoxLayout()
        for label, callback in [("导入人设卡", self.import_persona), ("导出人设卡", self.export_persona), ("保存人设", self.save_persona)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            persona_buttons.addWidget(button)
        persona_layout.addLayout(persona_buttons)
        persona_layout.addStretch(1)
        tabs.addTab(persona_tab, "人设卡")

        memory_tab = QWidget()
        memory_layout = QVBoxLayout(memory_tab)
        self.auto_memory = QCheckBox("自动记住我明确说过的长期事实和偏好")
        self.auto_memory.setChecked(config.settings.memory_enabled)
        self.auto_memory.toggled.connect(self.save_preferences)
        memory_layout.addWidget(self.auto_memory)
        hint = QLabel("长期记忆会跨重启保留。关闭自动记忆后，仍可手动管理已有内容。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        memory_layout.addWidget(hint)
        self.memory_list = QListWidget()
        self.memory_list.currentItemChanged.connect(self.select_memory)
        memory_layout.addWidget(self.memory_list, 1)
        self.count = QLabel()
        self.count.setObjectName("muted")
        memory_layout.addWidget(self.count)
        memory_form = QFormLayout()
        self.memory_key = QLineEdit()
        self.memory_key.setMaxLength(80)
        self.memory_key.setPlaceholderText("例如：称呼、喜欢的游戏、回复偏好")
        memory_form.addRow("记忆名称", self.memory_key)
        self.memory_value = QTextEdit()
        self.memory_value.setAcceptRichText(False)
        self.memory_value.setFixedHeight(80)
        self.memory_value.setPlaceholderText("只记录你希望 Ameath 长期记住的事。")
        memory_form.addRow("内容", self.memory_value)
        memory_layout.addLayout(memory_form)
        memory_buttons = QHBoxLayout()
        new_button = QPushButton("新建")
        new_button.clicked.connect(self.new_memory)
        memory_buttons.addWidget(new_button)
        save_button = QPushButton("保存记忆")
        save_button.clicked.connect(self.save_memory)
        memory_buttons.addWidget(save_button)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.clicked.connect(self.delete_memory)
        self.delete_button.setEnabled(False)
        memory_buttons.addWidget(self.delete_button)
        memory_layout.addLayout(memory_buttons)
        tabs.addTab(memory_tab, "长期记忆")

        episode_tab = QWidget()
        episode_layout = QVBoxLayout(episode_tab)
        self.session_memory = QCheckBox("跨会话记住聊过的事和她自己讲的日常")
        self.session_memory.setChecked(config.settings.session_memory)
        self.session_memory.toggled.connect(self.save_preferences)
        episode_layout.addWidget(self.session_memory)
        episode_hint = QLabel("离开当前上下文的聊天会由模型整理成简短摘要，整理后删除原文。"
                              "「往事」是真实聊过的内容，「日常」是角色原创的小故事；两者都不会写进长期记忆。")
        episode_hint.setObjectName("muted")
        episode_hint.setWordWrap(True)
        episode_layout.addWidget(episode_hint)
        self.episode_list = QListWidget()
        self.episode_list.currentItemChanged.connect(lambda current, previous=None: self.delete_episode_button.setEnabled(current is not None))
        episode_layout.addWidget(self.episode_list, 1)
        episode_buttons = QHBoxLayout()
        self.delete_episode_button = QPushButton("删除选中")
        self.delete_episode_button.clicked.connect(self.delete_episode)
        self.delete_episode_button.setEnabled(False)
        episode_buttons.addWidget(self.delete_episode_button)
        clear_button = QPushButton("全部清空")
        clear_button.clicked.connect(self.clear_episodes)
        episode_buttons.addWidget(clear_button)
        episode_layout.addLayout(episode_buttons)
        tabs.addTab(episode_tab, "往事与日常")
        self.tabs = tabs
        self.status = QLabel("人设与长期记忆保存在本机；聊天全文不会写入长期记忆。")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_memories()
        self.refresh_episodes()

    def populate_persona(self, persona: Persona):
        for name, field in self.persona_fields.items():
            if isinstance(field, QLineEdit):
                field.setText(getattr(persona, name))
            else:
                field.setPlainText(getattr(persona, name))

    def edited_persona(self):
        return Persona.from_dict({name: field.text() if isinstance(field, QLineEdit) else field.toPlainText()
                                  for name, field in self.persona_fields.items()})

    def save_persona(self):
        try:
            self.card.save(self.edited_persona())
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.status.setText("人设已保存，将用于之后的聊天回复。" if self.config.settings.use_companion_context else "人设已保存在本机；启用上方开关后才用于模型回复。")
        self.changed.emit()

    def import_persona(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入人设卡", "", "人设卡 (*.json)")
        if path:
            try:
                self.populate_persona(PersonaCard.read(Path(path)))
                self.status.setText("已载入人设卡，点击「保存人设」生效。")
            except (OSError, ValueError) as error:
                self.status.setText(str(error))

    def export_persona(self):
        try:
            persona = self.edited_persona()
        except ValueError as error:
            self.status.setText(str(error))
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出人设卡", "ameath-persona.json", "人设卡 (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(persona.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                self.status.setText("人设卡已导出。")
            except OSError:
                self.status.setText("无法写入该位置，请选择其他文件夹。")

    def save_preferences(self):
        self.config.settings.use_companion_context = self.use_context.isChecked()
        self.config.settings.memory_enabled = self.auto_memory.isChecked()
        self.config.settings.session_memory = self.session_memory.isChecked()
        try:
            if not self.config.settings.session_memory:
                # Opting out discards raw turns that were still waiting to be summarized.
                self.memory.clear_pending()
            self.config.save()
            self.status.setText("偏好已保存。关闭使用开关后，后续请求不会携带已保存的人设和长期记忆。")
            self.changed.emit()
        except OSError:
            self.status.setText("暂时无法保存设置，请检查数据目录。")

    def refresh_memories(self):
        self.memory_list.clear()
        records = self.memory.all()
        for record in records:
            source = "自动" if record["source"] == "auto" else "手动"
            item = QListWidgetItem(f"{record['key']} · {source}\n{record['value']}")
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.memory_list.addItem(item)
        self.count.setText(f"{len(records)} / 200 条长期记忆")
        self.new_memory()

    def select_memory(self, current, previous=None):
        if current is None:
            self.selected_id = None
            self.delete_button.setEnabled(False)
            return
        record = current.data(Qt.ItemDataRole.UserRole)
        self.selected_id = record["id"]
        self.memory_key.setText(record["key"])
        self.memory_value.setPlainText(record["value"])
        self.delete_button.setEnabled(True)

    def new_memory(self):
        self.memory_list.setCurrentRow(-1)
        self.selected_id = None
        self.memory_key.clear()
        self.memory_value.clear()
        self.delete_button.setEnabled(False)

    def save_memory(self):
        try:
            self.memory.remember(self.memory_key.text(), self.memory_value.toPlainText(), identifier=self.selected_id)
            self.refresh_memories()
            self.status.setText("记忆已保存，重启后仍会保留。")
            self.changed.emit()
        except (ValueError, OSError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def refresh_episodes(self):
        self.episode_list.clear()
        for record in self.memory.episodes():
            if record["kind"] == "shared":
                label = f"往事 · {local_date(record['created'])}"
            else:
                label = f"日常 · {record['topic']}" + (" · 告一段落" if record.get("status") == "closed" else "")
            item = QListWidgetItem(f"{label}\n{record['summary']}")
            item.setData(Qt.ItemDataRole.UserRole, record["id"])
            self.episode_list.addItem(item)
        self.delete_episode_button.setEnabled(False)

    def delete_episode(self):
        item = self.episode_list.currentItem()
        if item is not None:
            self.memory.delete_episode(item.data(Qt.ItemDataRole.UserRole))
            self.refresh_episodes()
            self.status.setText("已删除，之后的聊天不再携带这条内容。")
            self.changed.emit()

    def clear_episodes(self):
        self.memory.clear_episodes()
        self.refresh_episodes()
        self.status.setText("往事、日常和待整理的聊天都已清空。")
        self.changed.emit()

    def delete_memory(self):
        if self.selected_id is not None:
            self.memory.delete(self.selected_id)
            self.refresh_memories()
            self.status.setText("这条记忆已删除，后续请求不再携带它；当前聊天中已提到的内容仍在对话上下文里，可点击「新对话」清空。")
            self.changed.emit()
