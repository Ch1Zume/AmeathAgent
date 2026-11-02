"""Usage overview page: every model call, by day / week / month and by purpose."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .usage import CATEGORIES, PERIODS, PeriodSummary, UsageLedger

# One hue per purpose, picked to stay distinguishable on the cream theme.
COLORS = {"chat": "#c5497c", "game": "#4f8fbf", "agent": "#d18b2c", "companion": "#5aa37a",
          "memory": "#8a6fc0", "other": "#a99aa2"}
DETAIL_LABELS = {"sts2:turn_plan": "杀戮尖塔 2 · 回合规划", "sts2:replan": "杀戮尖塔 2 · 中途重算",
                 "sts2:step": "杀戮尖塔 2 · 单步决策", "sts2:review": "杀戮尖塔 2 · 阶段回顾",
                 "episodes": "往事整理", "return": "久别问候", "share": "闲时分享", "weather": "天气问候",
                 "screen": "屏幕观察", "explain": "论文讲解与解释", "chat": "聊天回复", "": "—"}
HISTORY = {"day": 14, "week": 8, "month": 6}


def compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 10_000:
        return f"{value / 1000:.1f}k"
    return f"{value:,}"


class TrendChart(QWidget):
    """Stacked columns of reported tokens per period, coloured by purpose."""
    def __init__(self):
        super().__init__()
        self.summaries: list[PeriodSummary] = []
        self.setMinimumHeight(180)

    def set_data(self, summaries):
        self.summaries = summaries
        tips = []
        for summary in summaries:
            parts = [f"{CATEGORIES[key]} {compact(t.total)}" for key, t in summary.by_category.items() if t.total]
            tips.append(f"{summary.label}：{compact(summary.totals.total)}" + (f"（{'，'.join(parts)}）" if parts else ""))
        self.setToolTip("\n".join(tips[-8:]))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(46, 10, -8, -24)
        peak = max([s.totals.total for s in self.summaries] + [1])
        painter.setPen(QPen(QColor("#efd3df"), 1))
        for fraction in (0, 0.5, 1):
            y = rect.bottom() - rect.height() * fraction
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            painter.setPen(QColor("#886777"))
            painter.drawText(QRectF(0, y - 8, 42, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             compact(int(peak * fraction)))
            painter.setPen(QPen(QColor("#efd3df"), 1))
        if not self.summaries:
            return
        slot = rect.width() / len(self.summaries)
        width = max(4.0, min(28.0, slot * 0.6))
        for index, summary in enumerate(self.summaries):
            x = rect.left() + slot * index + (slot - width) / 2
            y = rect.bottom()
            for key in CATEGORIES:
                total = summary.by_category.get(key)
                if not total or not total.total:
                    continue
                height = rect.height() * total.total / peak
                painter.fillRect(QRectF(x, y - height, width, height), QColor(COLORS[key]))
                y -= height
            if len(self.summaries) <= 8 or index % 2 == len(self.summaries) % 2 or index == len(self.summaries) - 1:
                painter.setPen(QColor("#886777"))
                painter.drawText(QRectF(x - slot / 2, rect.bottom() + 4, width + slot, 16),
                                 Qt.AlignmentFlag.AlignHCenter, summary.label)


class CategoryBars(QWidget):
    """Share of the selected period per purpose; the colour key for the chart as well."""
    ROW = 26

    def __init__(self):
        super().__init__()
        self.summary: PeriodSummary | None = None
        self.setMinimumHeight(self.ROW * len(CATEGORIES) + 4)

    def set_data(self, summary):
        self.summary = summary
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        totals = self.summary.by_category if self.summary else {}
        peak = max([t.total for t in totals.values()] + [1])
        whole = max(1, self.summary.totals.total if self.summary else 1)
        label_width, value_width = 96, 210
        track = max(40, self.width() - label_width - value_width - 16)
        for row, key in enumerate(CATEGORIES):
            y = row * self.ROW + 4
            total = totals.get(key)
            painter.setPen(QColor(COLORS[key]))
            painter.setBrush(QColor(COLORS[key]))
            painter.drawEllipse(QRectF(0, y + 6, 9, 9))
            painter.setPen(QColor("#583a49"))
            painter.drawText(QRectF(16, y, label_width - 16, 20), Qt.AlignmentFlag.AlignVCenter, CATEGORIES[key])
            painter.fillRect(QRectF(label_width, y + 6, track, 9), QColor("#f6e6ed"))
            if total and total.total:
                painter.fillRect(QRectF(label_width, y + 6, track * total.total / peak, 9), QColor(COLORS[key]))
            text = "—"
            if total:
                text = f"{compact(total.total)} · {total.total * 100 / whole:.0f}% · {total.requests} 次"
                if total.unknown:
                    text += f"（{total.unknown} 次未知）"
            painter.setPen(QColor("#886777"))
            painter.drawText(QRectF(label_width + track + 10, y, value_width, 20), Qt.AlignmentFlag.AlignVCenter, text)


class UsagePanel(QWidget):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.ledger = UsageLedger(config.directory)
        self.period = "day"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 22, 25, 20)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        labels = QVBoxLayout()
        title = QLabel("用量记录")
        title.setObjectName("headline")
        labels.addWidget(title)
        desc = QLabel("所有模型调用按服务方报告的 Token 记账：对话、游戏、Agent 工作（论文讲解等）、陪伴与观察、后台整理。")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        labels.addWidget(desc)
        heading.addLayout(labels, 1)
        self.setStyleSheet("QPushButton:checked { background: #f7cede; color: #96365f; border-color: #c5497c; font-weight: 600; }")
        self.group = QButtonGroup(self)
        for key, label in PERIODS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self.period)
            button.setMinimumWidth(46)
            button.clicked.connect(lambda _checked=False, key=key: self.set_period(key))
            self.group.addButton(button)
            heading.addWidget(button)
        layout.addLayout(heading)
        self.headline = QLabel()
        self.headline.setObjectName("sectionTitle")
        layout.addWidget(self.headline)
        self.subline = QLabel()
        self.subline.setObjectName("muted")
        self.subline.setWordWrap(True)
        layout.addWidget(self.subline)
        self.bars = CategoryBars()
        layout.addWidget(self.bars)
        self.chart = TrendChart()
        layout.addWidget(self.chart, 1)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["用途", "细项", "请求", "Token", "其中缓存命中"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(150)
        layout.addWidget(self.table)
        note = QLabel("只保存次数、Token 数、模型名和用途，不保存提问、回复或密钥。部分服务不返回用量时计为“未知”，不当作 0；这不是账单金额。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)

    def set_period(self, period):
        self.period = period
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()

    def refresh(self):
        try:
            history = self.ledger.history(self.period, HISTORY[self.period])
        except Exception:
            self.headline.setText("暂时读不到用量记录")
            return
        current = history[-1]
        totals = current.totals
        names = {"day": "今天", "week": "本周", "month": "本月"}
        self.headline.setText(f"{names[self.period]} · {compact(totals.total)} Token · {totals.requests} 次请求")
        detail = [f"输入 {compact(totals.prompt)}", f"输出 {compact(totals.completion)}"]
        if totals.reasoning:
            detail.append(f"其中思考 {compact(totals.reasoning)}")
        if totals.cached and totals.prompt:
            detail.append(f"输入缓存命中 {totals.cached * 100 / totals.prompt:.0f}%")
        if totals.unknown:
            detail.append(f"{totals.unknown} 次未报告用量")
        previous = history[-2].totals.total if len(history) > 1 else 0
        if previous:
            detail.append({"day": "昨天", "week": "上周", "month": "上月"}[self.period] + " " + compact(previous))
        self.subline.setText(" · ".join(detail))
        self.bars.set_data(current)
        self.chart.set_data(history)
        rows = sorted(current.by_detail.items(), key=lambda item: -item[1].total)
        self.table.setRowCount(len(rows))
        for row, ((category, name), total) in enumerate(rows):
            values = [CATEGORIES.get(category, category), DETAIL_LABELS.get(name, name), str(total.requests)
                      + (f"（{total.unknown} 未知）" if total.unknown else ""), f"{total.total:,}", f"{total.cached:,}"]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
