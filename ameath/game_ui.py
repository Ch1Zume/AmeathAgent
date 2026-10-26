from __future__ import annotations

import html
import json
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)

from .game import DemoBridge, GameError, GameFinished, GameSession, GameWaiting, SpireBridge, choices_for, validate_game_endpoint
from .llm import ModelError
from .widgets import SafeTextBrowser


class PilotSignals(QObject):
    update = Signal(int, str, object)
    finished = Signal(int)


class GamePanel(QWidget):
    comment = Signal(str, str)
    model_settings_requested = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.bridge = DemoBridge()
        self.session = None
        self.decision = None
        self.busy = False
        self.generation = 0
        self.stopping = False
        self.signals = PilotSignals(self)
        self.signals.update.connect(self.receive)
        self.signals.finished.connect(self.finished)
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 22, 25, 20)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        labels = QVBoxLayout()
        title = QLabel("杀戮尖塔 2")
        title.setObjectName("headline")
        labels.addWidget(title)
        desc = QLabel("Ameath 看局面、做决定、执行一步，再观察结果。")
        desc.setObjectName("muted")
        labels.addWidget(desc)
        heading.addLayout(labels, 1)
        settings = QPushButton("DeepSeek / 模型设置")
        settings.clicked.connect(self.model_settings_requested.emit)
        heading.addWidget(settings)
        layout.addLayout(heading)
        connection = QHBoxLayout()
        self.source = QComboBox()
        self.source.addItem("演示局面 · 不操作真实游戏", "demo")
        self.source.addItem("连接真实游戏", "live")
        self.source.currentIndexChanged.connect(self.source_changed)
        connection.addWidget(self.source)
        self.endpoint = QLineEdit(self.config.settings.game_endpoint)
        self.endpoint.setPlaceholderText("F8 面板显示的本机接口")
        connection.addWidget(self.endpoint, 1)
        self.connect_button = QPushButton("读取局面")
        self.connect_button.clicked.connect(lambda: self.start("read"))
        connection.addWidget(self.connect_button)
        layout.addLayout(connection)
        controls = QHBoxLayout()
        self.suggest_button = QPushButton("只给建议")
        self.suggest_button.clicked.connect(lambda: self.start("suggest"))
        controls.addWidget(self.suggest_button)
        self.apply_button = QPushButton("执行这条建议")
        self.apply_button.clicked.connect(lambda: self.start("apply"))
        self.apply_button.setEnabled(False)
        controls.addWidget(self.apply_button)
        self.step_button = QPushButton("决策并执行一步")
        self.step_button.clicked.connect(lambda: self.start("step"))
        controls.addWidget(self.step_button)
        self.auto_button = QPushButton("开始自主游玩")
        self.auto_button.setObjectName("primary")
        self.auto_button.clicked.connect(lambda: self.start("auto"))
        controls.addWidget(self.auto_button)
        self.stop_button = QPushButton("暂停 · Ctrl+Alt+P")
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        budgets = QHBoxLayout()
        budgets.addWidget(QLabel("本次调用上限"))
        self.request_limit = QSpinBox()
        self.request_limit.setRange(1, 2000)
        self.request_limit.setValue(self.config.settings.game_request_limit)
        self.request_limit.setMinimumWidth(90)
        budgets.addWidget(self.request_limit)
        budgets.addWidget(QLabel("Token 上限"))
        self.token_limit = QSpinBox()
        self.token_limit.setRange(1000, 10000000)
        self.token_limit.setSingleStep(10000)
        self.token_limit.setValue(self.config.settings.game_token_limit)
        self.token_limit.setMinimumWidth(120)
        budgets.addWidget(self.token_limit)
        self.reset_button = QPushButton("新游玩会话")
        self.reset_button.clicked.connect(self.reset)
        budgets.addWidget(self.reset_button)
        budgets.addStretch(1)
        layout.addLayout(budgets)
        self.status = QLabel("先体验演示局面；切换真实游戏后，读取局面会检查模组状态。")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.state_view = SafeTextBrowser()
        self.state_view.setOpenLinks(False)
        self.state_view.setMinimumWidth(290)
        self.state_view.setMarkdown("### 等待局面\n\n手牌、敌人意图、血量、路线和可执行动作会显示在这里。\n\n真实游戏需要安装桥接模组，并在游戏中开启一局单人游戏。")
        split.addWidget(self.state_view)
        self.decisions_view = SafeTextBrowser()
        self.decisions_view.setOpenLinks(False)
        self.decisions_view.setMinimumWidth(310)
        self.decisions_view.setMarkdown("### Ameath 的游玩记录\n\n模型会给出简短的动作理由和评论。\n\n读取局面不调用模型；只有请求建议、单步或自主游玩才会调用 DeepSeek。\n\n开始后可收起窗口，让桌宠陪你看它玩。")
        split.addWidget(self.decisions_view)
        layout.addWidget(split, 1)
        self.metrics = QLabel("尚未调用模型 · 游戏技能与论文阅读共用 Ameath 的模型设置")
        self.metrics.setObjectName("muted")
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        note = QLabel("暂停会阻止下一步；已提交的动作可能完成。模组内置自动游玩请保持暂停。日志只记录游戏决策，不保存密钥或模型内部思考。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

    def source_changed(self):
        if self.busy:
            return
        self.session = None
        self.decision = None
        self.bridge = DemoBridge() if self.source.currentData() == "demo" else None
        self.apply_button.setEnabled(False)
        self.set_controls(True)
        self.status.setText("演示局面，不会调用模型或操作真实游戏。" if self.source.currentData() == "demo"
                            else "请启动带模组的游戏，在 F8 面板查看本机端口；Ameath 只接管单人对局。")

    def reset(self):
        if self.busy:
            return
        self.session = None
        self.decision = None
        if self.source.currentData() == "demo":
            self.bridge = DemoBridge()
        self.apply_button.setEnabled(False)
        self.set_controls(True)
        self.metrics.setText("新会话 · 调用计数已重置")
        self.status.setText("新会话准备好了，开始前请确认局面。")

    def stop(self):
        if self.session:
            self.session.stop()
        self.stopping = True
        self.decision = None
        self.apply_button.setEnabled(False)
        self.status.setText("已暂停，不会继续提交下一步；正在返回的请求或已提交动作可能仍会完成。")
        self.comment.emit("好，我先停下来，把操作交还给你。", "paused")

    def start(self, mode):
        if self.busy:
            return
        demo = self.source.currentData() == "demo"
        try:
            if not demo:
                endpoint = validate_game_endpoint(self.endpoint.text())
                if self.bridge is None or self.bridge.endpoint != endpoint:
                    self.bridge = SpireBridge(endpoint)
                    self.session = None
                    self.decision = None
                self.config.settings.game_endpoint = endpoint
            if self.session is None:
                self.config.settings.game_request_limit = self.request_limit.value()
                self.config.settings.game_token_limit = self.token_limit.value()
                self.config.save()
                self.session = GameSession(self.config, self.bridge, demo=demo)
            else:
                self.session.cancelled.clear()
                if self.session.budget.requests == 0 and not self.session.history:
                    self.session.budget.request_limit = self.request_limit.value()
                    self.session.budget.token_limit = self.token_limit.value()
            if mode == "apply" and self.decision is None:
                raise GameError("请先生成一条建议。")
        except GameError as error:
            self.status.setText(str(error))
            return
        self.busy = True
        self._mode = mode
        self.stopping = False
        self.generation += 1
        generation = self.generation
        session = self.session
        decision_to_apply = self.decision
        self.set_controls(False)
        self.status.setText("正在读取并核对游戏局面…" if mode == "read" else "Ameath 正在观察局面并决定下一步…")

        def emit(kind, data):
            self.signals.update.emit(generation, kind, data)

        def run():
            try:
                if mode == "read":
                    emit("snapshot", session.bridge.snapshot())
                    return
                if mode == "apply":
                    emit("applied", (decision_to_apply, session.apply(decision_to_apply)))
                    return
                snapshot = session.wait_next()
                while not session.cancelled.is_set():
                    emit("snapshot", snapshot)
                    emit("thinking", None)
                    decision = session.propose(snapshot)
                    emit("proposed", decision)
                    if mode == "suggest":
                        return
                    result = session.apply(decision)
                    emit("applied", (decision, result))
                    if mode != "auto":
                        emit("snapshot", session.bridge.snapshot())
                        return
                    snapshot = session.wait_next(decision.snapshot)
            except (GameError, ModelError) as error:
                if isinstance(error, GameFinished):
                    emit("snapshot", session.bridge.snapshot())
                    emit("completed", str(error))
                else:
                    emit("error", str(error))
            except Exception:
                emit("error", "本次游玩没有完成，已暂停。请重新读取局面后检查模型和模组配置。")
            finally:
                self.signals.finished.emit(generation)

        threading.Thread(target=run, daemon=True, name="ameath-spire").start()

    def set_controls(self, enabled):
        for control in (self.source, self.endpoint, self.connect_button, self.suggest_button,
                        self.step_button, self.auto_button, self.reset_button):
            control.setEnabled(enabled)
        editable = enabled and (self.session is None or (self.session.budget.requests == 0 and not self.session.history))
        self.request_limit.setEnabled(editable)
        self.token_limit.setEnabled(editable)
        self.apply_button.setEnabled(enabled and self.decision is not None and not self.stopping)

    def receive(self, generation, kind, data):
        if generation != self.generation or self.stopping:
            return
        if kind == "snapshot":
            snapshot = data
            self.state_view.setMarkdown(render_state(snapshot))
            prefix = "演示局面" if self.source.currentData() == "demo" else "真实游戏"
            self.status.setText(f"{prefix} · {snapshot.screen} · {len(choices_for(snapshot))} 个当前支持的动作")
        elif kind == "proposed":
            self.decision = data
            prefix = "固定策略演示" if data.demo else self.config.settings.model
            self.decisions_view.append(f"<p><b>{html.escape(prefix)} · 建议</b><br>{html.escape(data.choice.label)}<br>{html.escape(data.reason)}</p>")
            self.comment.emit("建议：" + (data.comment or data.reason)[:80], "waiting" if self._mode == "suggest" else "review")
        elif kind == "thinking":
            self.comment.emit("我在看看这一手该怎么打。", "thinking")
        elif kind == "applied":
            decision, result = data
            self.decision = None
            label = "已完成" if result.get("status") == "completed" else "已提交，等待结果"
            self.decisions_view.append(f"<p><b>{label}</b> · {html.escape(decision.choice.label)}<br>{html.escape(decision.comment)}</p>")
            self.comment.emit(decision.comment or "这一手已提交，我再看看局面。", "happy")
        elif kind == "error":
            self.decision = None
            self.status.setText(str(data))
            self.decisions_view.append("<p><b>已停止</b><br>" + html.escape(str(data)) + "</p>")
            self.comment.emit("这一手先停下来，我需要检查一下。", "failed")
        elif kind == "completed":
            self.decision = None
            self.status.setText(str(data))
            self.decisions_view.append("<p><b>本局结束</b><br>" + html.escape(str(data)) + "</p>")
            self.comment.emit("这一局结束了，我们可以回顾一下。", "happy")
        self.update_metrics()

    def update_metrics(self):
        if not self.session:
            return
        budget = self.session.budget
        qualifier = f"；另有 {budget.unknown_usage} 次用量未知" if budget.unknown_usage else ""
        source = "本地演示，不调用模型" if self.session.demo else self.config.settings.model
        self.metrics.setText(f"{source} · 请求 {budget.requests}/{budget.request_limit} · 已报告 Token {budget.tokens:,}/{budget.token_limit:,}{qualifier}")

    def finished(self, generation):
        if generation == self.generation:
            self.busy = False
            self.set_controls(True)
            self.update_metrics()

    def shutdown(self):
        self.stop()
        self.generation += 1


def render_state(snapshot):
    state = snapshot.state
    labels = {"COMBAT": "战斗局面", "MAP": "选择路线", "REST": "休息点", "EVENT": "事件",
              "REWARD": "奖励", "CHEST": "宝箱", "SHOP": "商店", "GAME_OVER": "本局结束",
              "MAIN_MENU": "游戏主菜单", "CARD_SELECTION": "选择卡牌", "BUNDLE_SELECTION": "选择卡包"}
    lines = ["### " + labels.get(snapshot.screen, "当前游戏局面")]
    run = state.get("run") or {}
    if run:
        lines.append(f"第 {run.get('floor', '—')} 层 · 金币 {run.get('gold', '—')}")
    combat = state.get("combat") or {}
    player = combat.get("player") or {}
    if player:
        hp = player.get("hp", f"{player.get('current_hp', '—')}/{player.get('max_hp', '—')}")
        lines += ["**我方**", f"生命 {hp} · 格挡 {player.get('block', 0)} · 能量 {player.get('energy', '—')}"]
    if combat.get("hand"):
        lines.append("**手牌**")
        for card in combat["hand"]:
            text = card.get("line") or card.get("name") or card.get("card_id", "卡牌")
            lines.append("- " + text + ("" if card.get("playable") else "（当前不可打出）"))
    if combat.get("enemies"):
        lines.append("**敌人**")
        for enemy in combat["enemies"]:
            hp = enemy.get("hp", f"{enemy.get('current_hp', '—')}/{enemy.get('max_hp', '—')}")
            intents = "、".join(str(intent.get("label") or intent.get("intent_type") or "未知") for intent in enemy.get("intents", []))
            lines.append(f"- {enemy.get('name', '敌人')} · 生命 {hp}" + (f" · 意图 {intents}" if intents else ""))
    event = state.get("event") or {}
    if event.get("description"):
        lines += ["**事件描述**", event["description"]]
    choices = choices_for(snapshot)
    if choices:
        lines += ["**此刻可选择**"] + ["- " + choice.label for choice in choices[:30]]
    else:
        lines.append("当前没有支持的可执行动作。")
    return "\n\n".join(lines)
