"""Snowcap's original artwork and generated supplementary animation atlases."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QImage, QPainter

CELL_WIDTH = 192
CELL_HEIGHT = 208
MAX_ASSET_BYTES = 20 * 1024 * 1024
_GENERATED_CACHE: dict[tuple, tuple[dict, dict]] = {}


@dataclass(frozen=True)
class Clip:
    row: int
    frames: int
    frame_ms: int


CLIPS = {
    "idle": Clip(0, 6, 160),
    "running-right": Clip(1, 8, 85),
    "running-left": Clip(2, 8, 85),
    "waving": Clip(3, 4, 160),
    "jumping": Clip(4, 5, 110),
    "failed": Clip(5, 8, 150),
    "waiting": Clip(6, 6, 180),
    "running": Clip(7, 6, 110),
    "review": Clip(8, 6, 160),
}

DIRECTIONS = ("right", "left", "up", "down", "up-right", "up-left", "down-right", "down-left")
MOTION_CLIPS = {f"running-{direction}": Clip(row, 8, 90) for row, direction in enumerate(DIRECTIONS)}
REST_CLIPS = {name: Clip(row, 8, delay) for row, (name, delay) in enumerate([
    ("sitting-down", 110), ("sitting", 180), ("standing-up", 110),
    ("sleeping-down", 140), ("sleeping", 220), ("waking-up", 120)])}
FLIGHT_CLIPS = {name: Clip(row, 8, delay) for row, (name, delay) in enumerate([
    ("takeoff", 95), ("hover", 120), ("flying-right", 95), ("flying-left", 95), ("landing", 95)])}
ALL_CLIPS = CLIPS | {name: clip for name, clip in MOTION_CLIPS.items() if name not in CLIPS} | REST_CLIPS | FLIGHT_CLIPS
FACING = {"down": 0, "down-right": 1, "right": 2, "up-right": 3,
          "up": 4, "up-left": 5, "left": 6, "down-left": 7}


class SpriteError(ValueError):
    pass


def _has_pixels(image: QImage) -> bool:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    alpha = rgba.constBits()[3::4]
    return any(alpha)


class SpriteSheet:
    def __init__(self, image: QImage, *, source: Path | None = None):
        if image.isNull() or image.width() != 1536 or image.height() not in (1872, 2288):
            raise SpriteError("动画图需要为 1536×1872 或 1536×2288 的透明 PNG / WebP。")
        if not image.hasAlphaChannel():
            raise SpriteError("动画图需要保留透明背景。")
        self.source = source
        self.version = 1 if image.height() == 1872 else 2
        self.image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        self._frames: dict[str, tuple[QImage, ...]] = {}
        self.clips = dict(ALL_CLIPS)
        self.generated_packs: list[str] = []
        self.supplement_warning = ""
        for name, clip in CLIPS.items():
            frames = []
            for column in range(8):
                frame = self.image.copy(column * CELL_WIDTH, clip.row * CELL_HEIGHT, CELL_WIDTH, CELL_HEIGHT)
                visible = _has_pixels(frame)
                if column < clip.frames:
                    if not visible:
                        raise SpriteError(f"动作 {name} 的第 {column + 1} 帧为空。")
                    frames.append(frame)
                elif visible:
                    raise SpriteError(f"动作 {name} 的空余格包含图像，会造成串帧。")
            self._frames[name] = tuple(frames)
        for direction in DIRECTIONS:
            name = f"running-{direction}"
            if name not in self._frames:
                self._frames[name] = self._frames["running-left" if "left" in direction else "running-right"]
        for name in REST_CLIPS | FLIGHT_CLIPS:
            self._frames[name] = self._frames["waiting" if name in REST_CLIPS else "jumping"]

    @classmethod
    def from_file(cls, path: str | Path):
        path = Path(path)
        if path.suffix.lower() not in (".png", ".webp") or path.stat().st_size > MAX_ASSET_BYTES:
            raise SpriteError("请选择 20 MiB 以内的透明 PNG / WebP 动画图。")
        return cls(QImage(str(path)), source=path)

    def frame(self, action: str, index: int) -> QImage:
        frames = self._frames[action]
        return frames[index % len(frames)]

    def add_generated_atlas(self, path: Path, names: list[str], timings: list[int]):
        """Adapt an evenly tiled generated atlas to the same logical canvas and foot anchor."""
        info = path.stat()
        if info.st_size > MAX_ASSET_BYTES:
            raise SpriteError("补充动画图过大。")
        key = (str(path.resolve()), info.st_mtime_ns, info.st_size, tuple(names), tuple(timings))
        cached = _GENERATED_CACHE.get(key)
        if cached:
            self._frames.update(cached[0])
            self.clips.update(cached[1])
            self.generated_packs.append(str(path))
            return
        atlas = QImage(str(path))
        if atlas.isNull() or not atlas.hasAlphaChannel() or atlas.width() < 640 or atlas.height() < len(names) * 80:
            raise SpriteError("补充动画图需要为包含透明通道的完整帧表。")
        atlas = atlas.convertToFormat(QImage.Format.Format_RGBA8888)
        x_cuts, y_cuts = atlas_cuts(atlas, 8, len(names))
        frames_by_name = {}
        for row, name in enumerate(names):
            frames = []
            for column in range(8):
                left, right = x_cuts[column], x_cuts[column + 1]
                top, bottom = y_cuts[row], y_cuts[row + 1]
                cell = atlas.copy(left, top, right - left, bottom - top)
                bounds = alpha_bounds(cell)
                if bounds.isEmpty():
                    raise SpriteError(f"补充动作 {name} 包含空帧。")
                frames.append(cell)
            frames_by_name[name] = frames
        reference = frames_by_name[names[0]]
        reference_heights = [alpha_bounds(reference[0]).height()] if names[0] in ("sitting-down", "takeoff") else sorted(alpha_bounds(frame).height() for frame in reference)
        uniform_scale = 190 / reference_heights[len(reference_heights) // 2]
        for row, (name, frames) in enumerate(frames_by_name.items()):
            # Keep each clip's body anchor fixed; do not normalize every frame's bob or jump away.
            bounds = [alpha_bounds(frame) for frame in frames]
            # Generated cells have unequal gutters. Register each head separately;
            # using one row-wide offset made those gutter differences look like shaking.
            anchors = [body_center_x(frame) for frame in frames]
            centers = sorted(bound.center().x() for bound in bounds)
            bottoms = sorted(bound.bottom() for bound in bounds)
            anchor = bounds[0].bottom() if name in ("jumping", "takeoff", "sitting-down") else bounds[-1].bottom() if name in ("standing-up", "waking-up", "landing") else bottoms[len(bottoms) // 2]
            topmost, bottommost = min(bound.top() for bound in bounds), max(bound.bottom() for bound in bounds)
            center = centers[len(centers) // 2]
            half_width = max(center - min(bound.left() for bound in bounds), max(bound.right() for bound in bounds) - center)
            scale = min(uniform_scale, 204 / max(1, bottommost - topmost + 1), 90 / max(1, half_width))
            dy = 201 - anchor * scale
            if name in ("hover", "flying-right", "flying-left"):
                tops = sorted(bound.top() for bound in bounds)
                dy = 8 - tops[len(tops) // 2] * scale
            dy = min(207 - bottommost * scale, max(2 - topmost * scale, dy))
            aligned = []
            for frame, center in zip(frames, anchors):
                dx = 96 - center * scale
                canvas = QImage(CELL_WIDTH, CELL_HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
                canvas.fill(Qt.GlobalColor.transparent)
                painter = QPainter(canvas)
                painter.drawImage(QRectF(dx, dy, frame.width() * scale, frame.height() * scale), frame)
                painter.end()
                aligned.append(canvas)
            self._frames[name] = tuple(aligned)
            self.clips[name] = Clip(row, 8, timings[row])
        self.generated_packs.append(str(path))
        _GENERATED_CACHE[key] = ({name: self._frames[name] for name in names}, {name: self.clips[name] for name in names})
        if len(_GENERATED_CACHE) > 8:
            _GENERATED_CACHE.pop(next(iter(_GENERATED_CACHE)))


def alpha_bounds(image: QImage) -> QRect:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = rgba.constBits()
    left, top, right, bottom = rgba.width(), rgba.height(), -1, -1
    for y in range(rgba.height()):
        row = pixels[y * rgba.bytesPerLine() + 3:y * rgba.bytesPerLine() + rgba.width() * 4:4]
        visible = [x for x, alpha in enumerate(row) if alpha >= 16]
        if visible:
            left, right = min(left, visible[0]), max(right, visible[-1])
            top, bottom = min(top, y), y
    return QRect(left, top, right - left + 1, bottom - top + 1) if right >= left else QRect()


def body_center_x(image: QImage) -> float:
    """Locate the head's dense silhouette, ignoring hands, feet and cape motion."""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    bounds = alpha_bounds(rgba)
    if bounds.isEmpty():
        return image.width() / 2
    pixels = rgba.constBits()
    rows = []
    for y in range(bounds.top(), bounds.top() + max(1, round(bounds.height() * 0.55))):
        row = pixels[y * rgba.bytesPerLine() + 3:y * rgba.bytesPerLine() + rgba.width() * 4:4]
        visible = [x for x, alpha in enumerate(row) if alpha >= 128]
        if visible:
            rows.append((visible[-1] - visible[0] + 1, (visible[0] + visible[-1]) / 2))
    if not rows:
        return bounds.center().x()
    widest = max(width for width, _ in rows)
    centers = sorted(center for width, center in rows if width >= widest * 0.7)
    return centers[len(centers) // 2]


def atlas_cuts(image: QImage, columns: int, rows: int) -> tuple[list[int], list[int]]:
    """Find transparent gutters near nominal grid lines; generators do not place exact grids."""
    width, height = image.width(), image.height()
    alpha = bytes(image.constBits())[3::4]
    horizontal = [sum(value >= 16 for value in alpha[y * width:(y + 1) * width]) for y in range(height)]
    vertical = [sum(value >= 16 for value in alpha[x::width]) for x in range(width)]

    def cuts(profile, count):
        length = len(profile)
        result = [0]
        for index in range(1, count):
            ideal = index * length / count
            radius = length / count * 0.32
            low, high = max(result[-1] + 2, round(ideal - radius)), min(length - 2, round(ideal + radius))
            minimum = min(profile[low:high + 1])
            candidates = [position for position in range(low, high + 1) if profile[position] <= minimum + 1]
            groups = []
            for position in candidates:
                if groups and position == groups[-1][-1] + 1:
                    groups[-1].append(position)
                else:
                    groups.append([position])
            group = min(groups, key=lambda values: abs((values[0] + values[-1]) / 2 - ideal))
            result.append(round((group[0] + group[-1]) / 2))
        return result + [length]

    return cuts(vertical, columns), cuts(horizontal, rows)


def supplement_snowcap(sheet: SpriteSheet) -> SpriteSheet:
    folder = Path(__file__).resolve().parent.parent / "assets" / "snowcap-motion-v2"
    if (folder / "movement.png").is_file():
        try:
            sheet.add_generated_atlas(folder / "movement.png", list(MOTION_CLIPS), [90] * 8)
        except (OSError, SpriteError):
            sheet.supplement_warning = "移动补充帧暂时无法载入，使用原版动作。"
    if (folder / "actions.png").is_file():
        try:
            sheet.add_generated_atlas(folder / "actions.png", ["idle", "waving", "jumping", "turning"], [150, 105, 95, 60])
            # Curate generated poses: one hand throughout the greeting, neutral endpoints.
            wave = sheet._frames["waving"]
            sheet._frames["waving"] = tuple(wave[index] for index in (0, 1, 2, 3, 2, 1, 0, 0))
            turn = sheet._frames["turning"]
            # The generator's profile views were out of order; select the correct orientations.
            sheet._frames["turning"] = (turn[0], turn[1], sheet._frames["running-right"][3], turn[5],
                                         turn[4], turn[3], turn[2], sheet._frames["running-down-left"][3])
        except (OSError, SpriteError):
            sheet.supplement_warning = "表情补充帧暂时无法载入，使用原版动作。"
    for filename, clips in [("rest.png", REST_CLIPS), ("flight.png", FLIGHT_CLIPS)]:
        if (folder / filename).is_file():
            try:
                sheet.add_generated_atlas(folder / filename, list(clips), [clip.frame_ms for clip in clips.values()])
                if filename == "flight.png":
                    right, left = sheet._frames["flying-right"], sheet._frames["flying-left"]
                    sheet._frames["flying-right"] = tuple(right[index] for index in (3, 4, 5, 6, 5, 4, 3, 4))
                    sheet._frames["flying-left"] = tuple(left[index] for index in (0, 1, 2, 3, 2, 1, 0, 1))
            except (OSError, SpriteError):
                sheet.supplement_warning = "部分休息或飞行动画未能载入，使用基础动作。"
    return sheet


def load_snowcap(data_directory: Path, source_directory: Path | None = None) -> tuple[SpriteSheet | None, str]:
    """Keep a byte-identical, private local copy so Codex updates cannot remove the skin."""
    folder = data_directory / "pets" / "snowcap"
    cached = folder / "spritesheet.webp"
    try:
        if cached.is_file():
            sheet = supplement_snowcap(SpriteSheet.from_file(cached))
            return sheet, sheet.supplement_warning
        source_directory = source_directory or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "pets" / "snowcap"
        manifest = json.loads((source_directory / "pet.json").read_text(encoding="utf-8"))
        filename = manifest.get("spritesheetPath")
        if not isinstance(filename, str):
            raise SpriteError("Snowcap 的资源描述缺少动画图路径。")
        source = (source_directory / filename).resolve()
        if not source.is_relative_to(source_directory.resolve()):
            raise SpriteError("Snowcap 的动画路径超出资源目录。")
        sheet = SpriteSheet.from_file(source)
        content = source.read_bytes()
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "spritesheet.tmp"
        temporary.write_bytes(content)
        temporary.replace(cached)
        (folder / "source.json").write_text(json.dumps({
            "name": "Snowcap", "source": str(source), "sha256": hashlib.sha256(content).hexdigest(),
            "sprite_version": sheet.version, "cell_size": [CELL_WIDTH, CELL_HEIGHT],
            "note": "Existing local artwork, copied unchanged for this private Ameath installation.",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        sheet.source = cached
        supplement_snowcap(sheet)
        return sheet, sheet.supplement_warning
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return None, "Snowcap 暂时无法载入，使用简易外观。" + (str(error) if isinstance(error, SpriteError) else "")


class SpriteAnimator:
    """Clock-based frame selection; movement > one-shot reaction > task activity."""
    def __init__(self, clips: dict[str, Clip] | None = None):
        self.clips = clips or ALL_CLIPS
        self.base = "idle"
        self.motion: str | None = None
        self.temporary: str | None = None
        self.queued: list[str] = []
        self.action = "idle"
        self.elapsed_ms = 0.0

    @property
    def frame_index(self):
        clip = self.clips[self.action]
        return min(clip.frames - 1, int(self.elapsed_ms // clip.frame_ms))

    def _sync(self, restart=False, preserve_phase=False):
        desired = self.motion or self.temporary or self.base
        if restart or desired != self.action:
            previous = self.clips[self.action]
            phase = self.elapsed_ms / (previous.frames * previous.frame_ms)
            self.action = desired
            clip = self.clips[desired]
            self.elapsed_ms = (phase % 1) * clip.frames * clip.frame_ms if preserve_phase else 0.0

    def set_base(self, action: str, *, clear_reaction=False):
        if action not in self.clips:
            raise ValueError("Unknown animation")
        self.base = action
        if clear_reaction:
            self.temporary = None
            self.queued.clear()
        self._sync()

    def set_motion(self, direction: str | None, *, flying=False):
        if direction is not None and direction not in DIRECTIONS:
            raise ValueError("Unknown direction")
        was_moving = self.motion is not None
        self.motion = ("flying-left" if "left" in direction else "flying-right" if "right" in direction else "hover") if direction and flying else f"running-{direction}" if direction else None
        self._sync(preserve_phase=was_moving and self.motion is not None)

    def play(self, action: str, *, restart=True, finish_current=False):
        if action not in self.clips:
            raise ValueError("Unknown animation")
        if finish_current and self.temporary and self.temporary != action and self.motion is None:
            self.queued = [action]
            return
        self.queued.clear()
        already_playing = self.temporary == action
        self.temporary = action
        self._sync(restart=(restart or not already_playing) and self.motion is None)

    def play_sequence(self, actions: list[str]):
        if not actions or any(action not in self.clips for action in actions):
            raise ValueError("Unknown animation sequence")
        self.temporary = actions[0]
        self.queued = list(actions[1:])
        self._sync(restart=True)

    def advance(self, milliseconds: float):
        remaining = max(0.0, float(milliseconds))
        while remaining > 0:
            clip = self.clips[self.action]
            duration = clip.frames * clip.frame_ms
            if self.temporary == self.action and self.motion is None:
                step = min(remaining, duration - self.elapsed_ms)
                self.elapsed_ms += step
                remaining -= step
                if self.elapsed_ms >= duration:
                    self.temporary = self.queued.pop(0) if self.queued else None
                    self._sync(restart=True)
                else:
                    break
            else:
                self.elapsed_ms = (self.elapsed_ms + remaining) % duration
                break


class FacingAnimator:
    """Use generated standing views for the shortest turn, preserving the foot anchor."""
    def __init__(self):
        self.current = self.target = 0
        self.elapsed_ms = 0.0

    @property
    def turning(self):
        return self.current != self.target

    def face(self, direction: str | None):
        self.target = FACING[direction] if direction else 0
        if not self.turning:
            self.elapsed_ms = 0.0

    def advance(self, milliseconds: float) -> float:
        remaining = max(0.0, milliseconds)
        while self.turning and remaining > 0:
            step = min(remaining, 60 - self.elapsed_ms)
            remaining -= step
            self.elapsed_ms += step
            if self.elapsed_ms >= 60:
                distance = (self.target - self.current) % 8
                self.current = (self.current + (1 if distance <= 4 else -1)) % 8
                self.elapsed_ms = 0.0
        return remaining
