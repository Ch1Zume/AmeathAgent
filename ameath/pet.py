from __future__ import annotations

import math
import random
import re
from datetime import datetime

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .sprites import CELL_HEIGHT, CELL_WIDTH, CLIPS, FacingAnimator, SpriteAnimator, SpriteSheet
from .lifecycle import load_resonance, paint_resonance


def direction_for_vector(dx: float, dy: float, previous: str | None = None) -> str:
    directions = ("right", "down-right", "down", "down-left", "left", "up-left", "up", "up-right")
    angle = math.atan2(dy, dx)
    if previous in directions:
        previous_angle = directions.index(previous) * math.pi / 4
        difference = abs((angle - previous_angle + math.pi) % (2 * math.pi) - math.pi)
        if difference < math.radians(30):
            return previous
    return directions[round(angle / (math.pi / 4)) % 8]


def draw_pet(painter: QPainter, area: QRectF, phase: float = 0, state: str = "idle",
             sheet: SpriteSheet | None = None, action: str = "idle", frame: int = 0):
    if sheet is not None:
        scale = min(area.width() / CELL_WIDTH, area.height() / CELL_HEIGHT)
        width, height = CELL_WIDTH * scale, CELL_HEIGHT * scale
        target = QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(target, sheet.frame(action, frame))
        painter.restore()
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(area.center().x(), area.center().y() + math.sin(phase) * 2)
    painter.scale(area.width() / 160, area.width() / 160)
    painter.translate(-75, -70)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(117, 90, 180, 28))
    painter.drawEllipse(QRectF(20, 128, 108, 12))
    tail = QPainterPath(QPointF(111, 107))
    tail.cubicTo(150, 112, 155, 74, 137, 81)
    painter.setPen(QPen(QColor("#9f88d9"), 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawPath(tail)
    painter.setPen(Qt.PenStyle.NoPen)
    gradient = QLinearGradient(30, 10, 115, 128)
    gradient.setColorAt(0, QColor("#e7dafa"))
    gradient.setColorAt(1, QColor("#ab91db"))
    painter.setBrush(gradient)
    body = QPainterPath(QPointF(30, 58))
    body.cubicTo(16, 17, 29, 2, 52, 30)
    body.cubicTo(63, 26, 82, 26, 93, 30)
    body.cubicTo(117, 2, 131, 18, 117, 60)
    body.cubicTo(135, 90, 121, 130, 104, 131)
    body.cubicTo(91, 138, 85, 126, 73, 130)
    body.cubicTo(58, 129, 52, 138, 39, 131)
    body.cubicTo(18, 120, 12, 85, 30, 58)
    painter.drawPath(body)
    painter.setBrush(QColor("#c5a5dc"))
    for x in (33, 100):
        ear = QPainterPath(QPointF(x, 43))
        ear.lineTo(x + 2, 24)
        ear.lineTo(x + 14, 42)
        ear.closeSubpath()
        painter.drawPath(ear)
    painter.setBrush(QColor(255, 255, 255, 155))
    painter.drawEllipse(QRectF(40, 80, 63, 43))
    blink = state == "paused" or math.sin(phase * 0.6) > 0.994
    painter.setPen(QPen(QColor("#45355f"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for x in (49, 91):
        if blink or state == "happy":
            painter.drawArc(QRectF(x - 5, 58, 12, 10), 10 * 16, 160 * 16)
        else:
            painter.setBrush(QColor("#45355f"))
            painter.drawEllipse(QRectF(x, 59, 5, 10))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(226, 135, 168, 120))
    painter.drawEllipse(QRectF(33, 73, 14, 7))
    painter.drawEllipse(QRectF(100, 73, 14, 7))
    painter.setPen(QPen(QColor("#6b4f7c"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawArc(QRectF(65, 72, 9, 9), 180 * 16, 180 * 16)
    painter.drawArc(QRectF(73, 72, 9, 9), 180 * 16, 180 * 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#fff7d5"))
    star = QPainterPath(QPointF(74, 37))
    for point in [(77, 44), (84, 47), (77, 50), (74, 57), (71, 50), (64, 47), (71, 44)]:
        star.lineTo(*point)
    star.closeSubpath()
    painter.drawPath(star)
    if state == "thinking":
        painter.setBrush(QColor("#8969c9"))
        for i in range(3):
            painter.drawEllipse(QRectF(122 + i * 8, 28 - math.sin(phase * 3 + i) * 3, 4, 4))
    painter.restore()


class PetAvatar(QWidget):
    def __init__(self, size=68, sheet: SpriteSheet | None = None):
        super().__init__()
        self.sheet = sheet
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        draw_pet(painter, QRectF(0, 0, self.width(), self.height()), sheet=self.sheet)


def clamp_position(position: QPoint, available: QRect, size: QSize) -> QPoint:
    """Logical screen coordinates, including monitors to the left/above the primary."""
    return QPoint(max(available.left(), min(position.x(), max(available.left(), available.right() - size.width() + 1))),
                  max(available.top(), min(position.y(), max(available.top(), available.bottom() - size.height() + 1))))


class DesktopPet(QWidget):
    activated = Signal()
    app_requested = Signal()
    memory_requested = Signal()
    capture_requested = Signal()
    clipboard_requested = Signal()
    pause_requested = Signal()
    quit_requested = Signal()
    game_requested = Signal()
    stop_game_requested = Signal()
    wandering_changed = Signal(bool)
    resting_changed = Signal(bool)
    flying_changed = Signal(bool)
    animation_changed = Signal(str)
    position_changed = Signal()
    lifecycle_finished = Signal(str)
    focus_requested = Signal()
    weather_requested = Signal()

    def __init__(self, sheet: SpriteSheet | None = None, *, wandering=False, resting=True, flying=True, time_provider=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setWindowTitle("AmeathAgent · 桌宠")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(248, 316)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sheet = sheet
        self.resonance_frames = load_resonance()
        self.transition = None
        self._transition_elapsed_ms = 0.0
        self.external_bubbles = False
        self.animator = SpriteAnimator(sheet.clips if sheet else None)
        self.facing = FacingAnimator()
        self._direction: str | None = None
        self._state = "idle"
        self.message = "我是 Ameath，陪你一起读懂。"
        self._bubble_remaining_ms = 0.0
        self._bubble_age_ms = 0.0
        self.phase = 0.0
        self._press = None
        self._origin = QPoint()
        self._dragged = False
        self._drag_motion_ms = 0.0
        self._drag_last = QPoint()
        self._external_motion_ms = 0.0
        self._target: QPoint | None = None
        self._walk_position = QPointF()
        self._speed = 95.0
        self._walk_start = QPointF()
        self._walk_time_ms = 0.0
        self._walk_duration_ms = 0.0
        self._movement_mode = "walk"
        self._flight_phase: str | None = None
        self._flight_destination: QPoint | None = None
        self._flight_speed = 160.0
        self.resting_enabled = bool(resting)
        self.flying_enabled = bool(flying)
        self._now = time_provider or (lambda: datetime.now().astimezone())
        self.busy_provider = lambda: False
        self._idle_elapsed_ms = 0.0
        self._travel_elapsed_ms = 0.0
        self._rest_remaining_ms = 0.0
        self._idle_rest_taken = False
        self.wandering = wandering
        self._wander_in_ms = random.uniform(8000, 14000)
        self._clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.setInterval(16)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self.set_activity(value)

    @property
    def animation(self):
        return self.animator.action

    @property
    def frame_index(self):
        return self.animator.frame_index

    @property
    def is_moving(self):
        return self._target is not None or self._flight_phase is not None

    @property
    def bubble_visible(self):
        return self._bubble_remaining_ms > 0

    @property
    def bubble_opacity(self):
        return min(1.0, self._bubble_age_ms / 120, self._bubble_remaining_ms / 220) if self.bubble_visible else 0.0

    @property
    def visual_pose(self):
        if self.sheet and "turning" in self.sheet.clips and self.facing.turning and self._movement_mode != "fly" and self._flight_phase is None and self._state not in ("sitting", "sleeping"):
            return "turning", self.facing.current
        return self.animation, self.frame_index

    def _set_motion(self, direction: str | None):
        self._direction = direction
        self.animator.set_motion(direction, flying=self._movement_mode == "fly")
        self.facing.face(direction)

    def showEvent(self, event):
        self._clock.start()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        self._press = None
        self.stop_moving()
        super().hideEvent(event)

    def tick(self):
        elapsed = self._clock.restart()
        self.advance(min(250, elapsed), idle_ms=elapsed)

    def advance(self, milliseconds: float, *, idle_ms=None):
        """Advance both motion and frames using elapsed time, also usable by deterministic previews."""
        elapsed = max(0.0, milliseconds)
        if self.transition:
            self._transition_elapsed_ms = min(1800, self._transition_elapsed_ms + elapsed)
            if self._transition_elapsed_ms >= 1800:
                completed = self.transition
                self.transition = None
                if completed == "exit":
                    self.hide()
                else:
                    self.play_action("waving")
                self.lifecycle_finished.emit(completed)
            self.update()
            return
        previous = self.animation
        self.phase += elapsed / 600
        self._idle_elapsed_ms += elapsed if idle_ms is None else max(0, idle_ms)
        self._bubble_remaining_ms = max(0.0, self._bubble_remaining_ms - elapsed)
        self._bubble_age_ms += elapsed
        if self._target is not None:
            self._travel_elapsed_ms += min(elapsed, self._walk_duration_ms - self._walk_time_ms)
            self._walk_time_ms = min(self._walk_duration_ms, self._walk_time_ms + elapsed)
            progress = self._walk_time_ms / self._walk_duration_ms
            eased = progress ** 3 * (10 - 15 * progress + 6 * progress ** 2)
            self._walk_position = self._walk_start + (QPointF(self._target) - self._walk_start) * eased
            if progress >= 1:
                target = self._target
                self._target = None
                self.move(target)
                self._set_motion(None)
                if self._movement_mode == "fly":
                    self._begin_landing()
            else:
                self.move(self._walk_position.toPoint())
        if self._press is not None and self._dragged:
            self._drag_motion_ms += elapsed
            if self._drag_motion_ms > 180:
                self._set_motion(None)
        elif self._target is None and self._external_motion_ms > 0:
            self._external_motion_ms = max(0.0, self._external_motion_ms - elapsed)
            if self._external_motion_ms == 0:
                self._set_motion(None)
        turning = self.facing.turning
        remainder = self.facing.advance(elapsed)
        turn_visible = self.sheet and "turning" in self.sheet.clips and turning and self._flight_phase is None and self._state not in ("sitting", "sleeping")
        self.animator.advance(remainder if turn_visible and self.animator.motion is None else elapsed)
        if self._flight_phase == "takeoff" and self.animator.temporary != "takeoff":
            self._flight_phase = "cruise"
            self._prepare_path(self._flight_destination or self.pos(), self._flight_speed)
            if self._target is None:
                self._begin_landing()
        elif self._flight_phase == "landing" and self.animator.temporary != "landing":
            self._flight_phase = None
            self._state = "idle"
            self._movement_mode = "walk"
        self._advance_rest(elapsed)
        if self.wandering and not self.busy_provider() and self._state in ("idle", "happy") and self._press is None and not self.is_moving and self.animator.temporary is None:
            self._wander_in_ms -= elapsed
            if self._wander_in_ms <= 0:
                if self.flying_enabled and random.random() < 0.25:
                    self.fly_once()
                else:
                    self.walk_once()
        if previous != self.animation:
            self.animation_changed.emit(self.animation)
        self.update()

    def start_transition(self, kind="enter"):
        if kind not in ("enter", "exit"):
            raise ValueError("Unknown lifecycle transition")
        self.stop_moving()
        self.set_activity("idle")
        self._bubble_remaining_ms = 0
        self.transition = kind
        self._transition_elapsed_ms = 0
        self.show()
        self.raise_()
        self.update()

    def set_activity(self, state: str, *, keep_reaction=False):
        if state == "sitting":
            self.sit()
            return
        if state == "sleeping":
            self.sleep()
            return
        self._idle_elapsed_ms = 0.0
        self._idle_rest_taken = False
        was_resting = self._state in ("sitting", "sleeping")
        asleep = self._state == "sleeping"
        self._state = state
        if state in ("idle", "paused"):
            self._bubble_remaining_ms = 0.0
        if state != "idle" and self._press is None:
            self.stop_moving()
        base = {"thinking": "running", "running": "running", "waiting": "waiting", "review": "review"}.get(state, "idle")
        waking = self.animator.temporary in ("waking-up", "standing-up")
        self.animator.set_base(base, clear_reaction=not keep_reaction and not waking and state not in ("happy", "failed", "error"))
        if was_resting:
            self.animator.play("waking-up" if asleep else "standing-up")
        if state == "happy":
            self.animator.play("waving", finish_current=True)
        elif state in ("failed", "error"):
            self.animator.play("failed", restart=False, finish_current=True)
        self._wander_in_ms = random.uniform(8000, 14000)
        self.animation_changed.emit(self.animation)
        self.update()

    def say(self, text: str, state: str = "idle"):
        self.message = text
        self.set_activity(state)
        self._show_bubble(text)

    def _show_bubble(self, text: str):
        self._bubble_age_ms = 0.0
        self._bubble_remaining_ms = min(8000, max(3500, len(text) * 90)) if text.strip() and not self.external_bubbles else 0.0
        self.update()

    def respond(self, text: str, action: str = "idle", *, show_bubble=True):
        """A reply can select an expression, while an active drag retains priority."""
        plain = re.sub(r"[*`#_]+", "", " ".join(text.split()))
        self.message = plain[:48] + ("…" if len(plain) > 48 else "")
        if self._press is None:
            self.stop_moving()
        self.set_activity("idle", keep_reaction=True)
        if action == "sleeping":
            self.sleep()
        elif action == "sitting":
            self.sit()
        elif action == "flight":
            self.fly_once()
        elif action in CLIPS and action not in ("idle", "running-left", "running-right"):
            self.animator.play(action, finish_current=True)
        if show_bubble:
            self._show_bubble(self.message)
        self.animation_changed.emit(self.animation)
        self.update()

    def play_action(self, action: str):
        self.note_interaction()
        self.stop_moving()
        self.animator.play(action)
        self.animation_changed.emit(self.animation)
        self.update()

    def stop_moving(self):
        self._target = None
        self._external_motion_ms = 0.0
        self._set_motion(None)
        self._flight_destination = None
        self._flight_phase = None
        self._movement_mode = "walk"
        if self._state == "flying":
            self._state = "idle"
            self.animator.set_base("idle", clear_reaction=True)

    def move_to(self, position: QPoint, *, speed=95.0):
        if self._press is not None:
            return
        self.stop_moving()
        self._prepare_path(position, speed)

    def _prepare_path(self, position: QPoint, speed: float):
        screen = QGuiApplication.screenAt(self.geometry().center()) or self.screen()
        target = clamp_position(position, screen.availableGeometry(), self.size())
        if target == self.pos():
            return
        self._target = target
        self._walk_position = QPointF(self.pos())
        self._walk_start = QPointF(self.pos())
        self._walk_time_ms = 0.0
        self._speed = max(20.0, min(500.0, speed))
        delta = QPointF(target) - self._walk_start
        self._walk_duration_ms = max(300.0, math.hypot(delta.x(), delta.y()) / self._speed * 1000)
        self._set_motion(direction_for_vector(delta.x(), delta.y(), self._direction))
        self.animation_changed.emit(self.animation)

    def _random_destination(self):
        screen = QGuiApplication.screenAt(self.geometry().center()) or self.screen()
        available = screen.availableGeometry()
        destination = self.pos()
        for _ in range(12):
            angle = random.uniform(-math.pi, math.pi)
            radius = random.uniform(110, 240)
            candidate = self.pos() + QPoint(round(math.cos(angle) * radius), round(math.sin(angle) * radius))
            destination = clamp_position(candidate, available.adjusted(8, 8, -8, -8), self.size())
            if (destination - self.pos()).manhattanLength() >= 70:
                break
        self._wander_in_ms = random.uniform(8000, 14000)
        return destination

    def walk_once(self):
        self.move_to(self._random_destination())

    def fly_once(self):
        self.fly_to(self._random_destination())

    def fly_to(self, position: QPoint, *, speed=160.0):
        if self._press is not None:
            return
        self.stop_moving()
        self._state = "flying"
        self._movement_mode = "fly"
        self._flight_destination = QPoint(position)
        self._flight_speed = speed
        self._flight_phase = "takeoff"
        self.animator.set_base("hover", clear_reaction=True)
        self.animator.play("takeoff")
        self.update()

    def _begin_landing(self):
        self._target = None
        self._flight_phase = "landing"
        self._movement_mode = "walk"
        self.facing.current = self.facing.target = 0
        self.animator.set_motion(None)
        self.animator.set_base("idle", clear_reaction=True)
        self.animator.play("landing")

    def note_interaction(self):
        self._idle_elapsed_ms = 0.0
        self._idle_rest_taken = False
        if self._state in ("sitting", "sleeping"):
            self.wake()

    def sit(self, *, automatic=False):
        if self._state == "sitting":
            return
        self.stop_moving()
        self._state = "sitting"
        self._bubble_remaining_ms = 0.0
        self._rest_remaining_ms = 15000 if automatic else 0
        self._idle_rest_taken = True
        self._travel_elapsed_ms = 0
        self.animator.set_base("sitting", clear_reaction=True)
        self.animator.play("sitting-down")
        self.update()

    def sleep(self):
        if self._state == "sleeping":
            return
        seated = self._state == "sitting"
        self.stop_moving()
        self._state = "sleeping"
        self._bubble_remaining_ms = 0.0
        self.animator.set_base("sleeping", clear_reaction=True)
        self.animator.play_sequence(["sleeping-down"] if seated else ["sitting-down", "sleeping-down"])
        self.update()

    def wake(self, *, reset_idle=True):
        asleep = self._state == "sleeping"
        if self._state not in ("sitting", "sleeping"):
            return
        self._state = "idle"
        self._rest_remaining_ms = 0
        if reset_idle:
            self._idle_elapsed_ms = 0
            self._idle_rest_taken = False
        self.animator.set_base("idle", clear_reaction=True)
        self.animator.play("waking-up" if asleep else "standing-up")
        self._wander_in_ms = random.uniform(8000, 14000)
        self.update()

    def _advance_rest(self, elapsed):
        if not self.resting_enabled or self.busy_provider() or self._press is not None or self.is_moving or self._state in ("thinking", "running", "flying"):
            return
        if self._state == "sleeping":
            return
        night = self._now().hour >= 23 or self._now().hour < 6
        if self._idle_elapsed_ms >= 300000 or (night and self._idle_elapsed_ms >= 45000):
            self.sleep()
            return
        if self._state == "sitting":
            if self._rest_remaining_ms > 0:
                self._rest_remaining_ms = max(0.0, self._rest_remaining_ms - elapsed)
                if self._rest_remaining_ms == 0:
                    self.wake(reset_idle=False)
            return
        if self.animator.temporary is None and (self._travel_elapsed_ms >= 8000 or (self._idle_elapsed_ms >= 120000 and not self._idle_rest_taken)):
            self.sit(automatic=True)

    def set_resting(self, enabled: bool):
        self.resting_enabled = bool(enabled)
        if not enabled:
            self.wake()
        self.resting_changed.emit(self.resting_enabled)

    def set_flying(self, enabled: bool):
        self.flying_enabled = bool(enabled)
        self.flying_changed.emit(self.flying_enabled)

    def set_wandering(self, enabled: bool):
        self.wandering = bool(enabled)
        self._wander_in_ms = random.uniform(8000, 14000)
        if not enabled:
            self.stop_moving()
        self.wandering_changed.emit(self.wandering)

    def moveEvent(self, event):
        if self.isVisible() and self._press is None and self._target is None and self._flight_phase is None:
            delta = event.pos() - event.oldPos()
            if delta.manhattanLength() > 0:
                self._set_motion(direction_for_vector(delta.x(), delta.y(), self._direction))
                self._external_motion_ms = 180
        super().moveEvent(event)
        self.position_changed.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.transition:
            paint_resonance(painter, self.resonance_frames, self._transition_elapsed_ms / 1800,
                            exiting=self.transition == "exit", idle=self.sheet.frame("idle", 0) if self.sheet else None)
            return
        if self.bubble_opacity > 0:
            painter.save()
            painter.setOpacity(self.bubble_opacity)
            painter.setPen(QPen(QColor("#ddd3ed"), 1))
            painter.setBrush(QColor("#faf7ff"))
            painter.drawRoundedRect(QRectF(5, 3, 238, 65), 16, 16)
            painter.setPen(QColor("#594575"))
            painter.setFont(QFont("Microsoft YaHei UI", 9))
            painter.drawText(QRectF(18, 10, 212, 50), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            painter.restore()
        action, frame = self.visual_pose
        draw_pet(painter, QRectF(28, 82, 192, 208), self.phase, self.state, sheet=self.sheet,
                 action=action, frame=frame)
        painter.setPen(QColor("#796589"))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(QRectF(0, 294, 248, 20), Qt.AlignmentFlag.AlignCenter, "A M E A T H")

    def mousePressEvent(self, event):
        if self.transition:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.note_interaction()
            self.stop_moving()
            self._press = event.globalPosition().toPoint()
            self._origin = self.pos()
            self._drag_last = self._press
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press is not None:
            position = event.globalPosition().toPoint()
            delta = position - self._press
            if delta.manhattanLength() > 5:
                self._dragged = True
                movement = position - self._drag_last
                if movement.manhattanLength() > 0:
                    self._set_motion(direction_for_vector(movement.x(), movement.y(), self._direction))
                    self._drag_motion_ms = 0.0
                self.move(self._origin + delta)
                self._drag_last = position
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            displacement = event.globalPosition().toPoint() - self._press
            dragged = self._dragged or displacement.manhattanLength() > 5
            self._press = None
            self._set_motion(None)
            if not dragged:
                self.animator.play("waving", finish_current=True)
                self.activated.emit()
            else:
                screen = QGuiApplication.screenAt(event.globalPosition().toPoint()) or self.screen()
                target = clamp_position(self._origin + displacement, screen.availableGeometry(), self.size())
                self.move(target)
                self._external_motion_ms = 0.0
                self._set_motion(None)
                self.animator.play("jumping")
            self._wander_in_ms = random.uniform(8000, 14000)
            self.update()

    def build_context_menu(self):
        menu = QMenu(self)
        menu.addAction("和 Ameath 聊天", self.activated.emit)
        menu.addAction("打开 Ameath", self.app_requested.emit)
        menu.addAction("记忆与人设", self.memory_requested.emit)
        menu.addAction("番茄钟", self.focus_requested.emit)
        menu.addAction("天气与问候", self.weather_requested.emit)
        menu.addSeparator()
        actions = menu.addMenu("动作")
        for label, action in [("打个招呼", "waving"), ("跳一下", "jumping"), ("等你回应", "waiting"),
                              ("认真工作", "running"), ("检查结果", "review"), ("失败反应", "failed")]:
            actions.addAction(label, lambda checked=False, name=action: self.play_action(name))
        actions.addAction("散步一下", self.walk_once)
        actions.addAction("坐下休息", self.sit)
        actions.addAction("睡一会儿", self.sleep)
        actions.addAction("起床 / 站起来", self.wake)
        actions.addAction("飞一圈", self.fly_once)
        wandering = menu.addAction("允许自己散步")
        wandering.setCheckable(True)
        wandering.setChecked(self.wandering)
        wandering.toggled.connect(self.set_wandering)
        resting = menu.addAction("自动休息与睡眠")
        resting.setCheckable(True)
        resting.setChecked(self.resting_enabled)
        resting.toggled.connect(self.set_resting)
        flying = menu.addAction("散步时偶尔飞行")
        flying.setCheckable(True)
        flying.setChecked(self.flying_enabled)
        flying.toggled.connect(self.set_flying)
        menu.addSeparator()
        for text, signal in [("玩杀戮尖塔 2", self.game_requested),
                             ("暂停游玩", self.stop_game_requested), ("解释已复制的文字", self.clipboard_requested),
                             ("框选屏幕内容", self.capture_requested), ("暂停 / 恢复陪读", self.pause_requested),
                             ("退出 Ameath", self.quit_requested)]:
            menu.addAction(text, signal.emit)
        return menu

    def contextMenuEvent(self, event):
        if self.transition == "exit":
            return
        self.note_interaction()
        self.stop_moving()
        menu = self.build_context_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()
