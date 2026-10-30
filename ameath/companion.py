"""Proactive companionship: deterministic rules for when to stay quiet, model text for what to say."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from PySide6.QtCore import QObject, QTimer, Signal

from .screen_observation import capture_screen, observe

# Only explicit requests; a false match merely keeps her quiet, which is the safe direction.
QUIET_REQUEST = re.compile(r"安静(?:一?会儿?|一下|点|些)|让我(?:静静|静一静)|别(?:说话|吵|打扰|烦我)|不要(?:说话|打扰|吵)|闭嘴|少说(?:点|两句)|先别找我")
QUIET_SECONDS = 2 * 3600
RETURN_GAP = 3 * 3600
SHARE_AFTER_CHAT = 25 * 60
PRESENT_WITHIN = 90 * 60
COOLDOWN = 45 * 60


def asks_for_quiet(text: str) -> bool:
    return bool(QUIET_REQUEST.search(text))


def quiet_hours(moment: datetime) -> bool:
    return moment.hour >= 23 or moment.hour < 7


@dataclass
class CompanionPolicy:
    """All times are monotonic seconds except the wall clock used for quiet hours and the daily cap."""
    enabled: bool = False
    daily_limit: int = 3
    quiet_until: float = 0
    last_spoken: float = float("-inf")
    last_chat: float = field(default_factory=time.monotonic)
    last_interaction: float = field(default_factory=time.monotonic)
    spoken_day: date | None = None
    spoken_count: int = 0

    def note_interaction(self, now: float) -> float:
        """Returns how long the user had been away."""
        gap = now - self.last_interaction
        self.last_interaction = now
        return gap

    def note_chat(self, text: str, now: float):
        self.last_chat = self.last_interaction = now
        if asks_for_quiet(text):
            self.quiet_until = now + QUIET_SECONDS

    def quiet_for(self, seconds: float, now: float):
        self.quiet_until = now + seconds

    def _count(self, today: date) -> int:
        return self.spoken_count if self.spoken_day == today else 0

    def allowed(self, now: float, wall: datetime) -> bool:
        return (self.enabled and now >= self.quiet_until and not quiet_hours(wall)
                and self._count(wall.date()) < self.daily_limit and now - self.last_spoken >= COOLDOWN)

    def share_due(self, now: float, wall: datetime) -> bool:
        return (self.allowed(now, wall) and now - self.last_chat >= SHARE_AFTER_CHAT
                and now - self.last_interaction <= PRESENT_WITHIN)

    def return_due(self, gap: float, now: float, wall: datetime) -> bool:
        return gap >= RETURN_GAP and self.allowed(now, wall)

    def attempted(self, now: float):
        # A model that chose silence still uses the cooldown, so it is not asked again every tick.
        self.last_spoken = now

    def spoke(self, now: float, today: date):
        self.last_spoken = now
        self.spoken_count = self._count(today) + 1
        self.spoken_day = today


class CompanionSignals(QObject):
    ready = Signal(int, object)
    failed = Signal(int, str)


class ProactiveCompanion(QObject):
    TICK_MS = 30000

    def __init__(self, config, pet, chat, parent=None, *, clock=time.monotonic, wall=None):
        super().__init__(parent)
        self.config, self.pet, self.chat = config, pet, chat
        self.clock = clock
        self.wall = wall or (lambda: datetime.now().astimezone())
        self.policy = CompanionPolicy(enabled=(config.settings.proactive_enabled or config.settings.screen_observation_enabled),
                                      daily_limit=config.settings.proactive_daily_limit,
                                      last_chat=clock(), last_interaction=clock())
        self.signals = CompanionSignals(self)
        self.signals.ready.connect(self.on_ready)
        self.signals.failed.connect(self.on_failure)
        self.last_screen_attempt = clock()
        self.pending_kind = ""
        self.capture_screen = capture_screen
        self.observe_screen = observe
        self.cancelled = threading.Event()
        self.generation = 0
        self.pending = False
        self.closing = False
        self.blocked = lambda: False
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def set_enabled(self, enabled: bool):
        self.config.settings.proactive_enabled = enabled
        self.policy.enabled = enabled or self.config.settings.screen_observation_enabled
        self.config.save()
        if not enabled:
            self.invalidate()

    def quiet(self, seconds=QUIET_SECONDS):
        self.policy.quiet_for(seconds, self.clock())
        self.invalidate()

    def on_interaction(self):
        now = self.clock()
        gap = self.policy.note_interaction(now)
        if self.config.settings.proactive_enabled and not self.pending and self.policy.return_due(gap, now, self.wall()):
            self.begin("return", gap)

    def on_user_message(self, text: str):
        # Whatever was being prepared is now stale: the user has started the conversation.
        self.invalidate()
        self.policy.note_chat(text, self.clock())

    def _busy(self, *, allow_sleep=False) -> bool:
        return (self.chat.busy or self.chat.isVisible() or self.pet.transition or (self.pet.state == "sleeping" and not allow_sleep)
                or self.blocked())

    def tick(self):
        if self.closing or self.pending:
            return
        now = self.clock()
        if self._busy(allow_sleep=True) or self.config.settings.mode == "demo":
            return
        settings = self.config.settings
        if (settings.screen_observation_enabled and settings.vision_model
                and now - self.last_screen_attempt >= settings.screen_observation_interval * 60
                and self.policy.allowed(now, self.wall())):
            self.last_screen_attempt = now
            image = self.capture_screen()
            if image:
                self.begin("screen", 0, image)
            return
        if settings.proactive_enabled and not self._busy() and self.policy.share_due(now, self.wall()):
            self.begin("share", now - self.policy.last_chat)

    def begin(self, kind: str, gap: float, image=None):
        agent = self.chat.agent
        if self.closing or self.config.settings.mode == "demo":
            return
        self.policy.attempted(self.clock())
        self.cancelled.set()
        self.cancelled = threading.Event()
        cancelled = self.cancelled
        self.generation += 1
        generation = self.generation
        self.pending = True
        self.pending_kind = kind
        history = list(self.chat.history)

        def run():
            try:
                reply = (self.observe_screen(agent, self.config, image, cancelled) if kind == "screen"
                         else agent.proactive(kind, gap, history, cancelled))
            except Exception:
                if not cancelled.is_set() and not self.closing:
                    self.signals.failed.emit(generation, "服务连接失败或响应格式不正确" if kind == "screen" else "")
                return
            if not cancelled.is_set() and not self.closing:
                self.signals.ready.emit(generation, reply)

        threading.Thread(target=run, daemon=True, name="ameath-proactive").start()

    def on_ready(self, generation, reply):
        if generation != self.generation or self.cancelled.is_set() or self.closing:
            return
        self.pending = False
        now, wall = self.clock(), self.wall()
        # Re-check at delivery: quiet requests, typing or a new task may have started meanwhile.
        enabled = (self.config.settings.screen_observation_enabled if self.pending_kind == "screen"
                   else self.config.settings.proactive_enabled)
        if (reply is None or not enabled or now < self.policy.quiet_until or quiet_hours(wall)
                or self.chat.busy or (self.pending_kind == "screen" and self.chat.isVisible()) or self.chat.input.toPlainText().strip()
                or self.pet.transition or (self.pet.state == "sleeping" and self.pending_kind != "screen") or self.blocked()):
            return
        self.policy.spoke(now, wall.date())
        self.chat.show_proactive(reply.text)
        if not self.pet.is_moving:
            self.pet.respond(reply.text, reply.action, show_bubble=False)

    def on_failure(self, generation, message):
        if generation != self.generation or self.cancelled.is_set() or self.closing:
            return
        self.pending = False
        if message and hasattr(self.pet, "setToolTip"):
            self.pet.setToolTip("屏幕观察失败：" + message + "；请检查视觉模型设置。")

    def settings_changed(self):
        self.invalidate()
        self.policy.enabled = (self.config.settings.proactive_enabled or self.config.settings.screen_observation_enabled)
        self.last_screen_attempt = self.clock()

    def invalidate(self):
        self.cancelled.set()
        self.generation += 1
        self.pending = False

    def shutdown(self):
        self.closing = True
        self.timer.stop()
        self.invalidate()
