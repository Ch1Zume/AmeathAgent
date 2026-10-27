"""Optional read-aloud for chat bubbles: a pluggable synthesis provider plus an ordered, interruptible queue.

Text bubbles never wait for speech. If synthesis or playback fails, the reply stays readable and
speech for that reply stops.
"""
from __future__ import annotations

import io
import re
import sys
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtCore import QObject, QTimer, Signal


class TTSError(Exception):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    data: bytes
    format: str = "wav"


class SpeechProvider(Protocol):
    def synthesize(self, text: str, cancelled: threading.Event) -> SpeechAudio:
        """Return audio for one bubble. Check `cancelled` around network calls and raise TTSError on failure."""


PROVIDERS: dict[str, Callable] = {}


def register_provider(name: str, factory: Callable):
    """factory(config) -> SpeechProvider; the settings field `tts_provider` selects it by name."""
    PROVIDERS[name] = factory


def create_provider(config) -> SpeechProvider:
    factory = PROVIDERS.get(config.settings.tts_provider)
    if factory is None:
        raise TTSError(f"没有名为「{config.settings.tts_provider}」的语音接口。")
    return factory(config)


class CustomSpeechProvider:
    """Placeholder for your own TTS API.

    Implement `synthesize` to call the service and return `SpeechAudio(wav_bytes)`. The default
    Windows player accepts PCM WAV. `self.config.settings.tts_voice` holds the voice name chosen in
    the model settings; keep API keys in an environment variable rather than in settings.json.
    """

    def __init__(self, config):
        self.config = config

    def synthesize(self, text: str, cancelled: threading.Event) -> SpeechAudio:
        raise TTSError("语音接口尚未接入：请在 ameath/tts.py 的 CustomSpeechProvider.synthesize 中实现。")


register_provider("custom", CustomSpeechProvider)


def speakable_text(segment: str, limit=500) -> str:
    """Markdown bubbles as plain spoken text; code and tables are referred to, not read out."""
    text = re.sub(r"```.*?(```|$)|~~~.*?(~~~|$)", "（代码在气泡里）", segment, flags=re.S)
    text = re.sub(r"\$\$.*?\$\$", "（公式在气泡里）", text, flags=re.S)
    text = re.sub(r"^\s*\|.*$", "", text, flags=re.M)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(?:#{1,6}|>|[-*+]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"[*_`~]+", "", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as audio:
            return audio.getnframes() / float(audio.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError):
        raise TTSError("语音接口返回的不是可播放的 WAV 音频。") from None


class WinsoundBackend:
    """Windows' built-in asynchronous WAV playback; two alternating files so a new clip never overwrites the one playing."""

    def __init__(self, directory: Path):
        self.directory = directory / "tts-cache"
        self._slot = 0

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def play(self, audio: SpeechAudio) -> float:
        import winsound
        if audio.format != "wav":
            raise TTSError("当前播放器只支持 WAV 音频。")
        seconds = wav_seconds(audio.data)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._slot = 1 - self._slot
        path = self.directory / f"speech-{self._slot}.wav"
        path.write_bytes(audio.data)
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return seconds

    def stop(self):
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)

    def close(self):
        self.stop()
        for path in self.directory.glob("speech-*.wav"):
            try:
                path.unlink()
            except OSError:
                pass


class SpeechSignals(QObject):
    ready = Signal(int, int, object, str)


class SpeechQueue(QObject):
    """Speaks bubbles in order. `extend` with a longer tuple continues; anything else restarts."""
    failed = Signal(str)
    GAP_MS = 150

    def __init__(self, config, parent=None, *, provider_factory=create_provider, backend=None):
        super().__init__(parent)
        self.config = config
        self.provider_factory = provider_factory
        self.backend = backend if backend is not None else (WinsoundBackend(config.directory) if WinsoundBackend.available() else None)
        self.signals = SpeechSignals(self)
        self.signals.ready.connect(self._on_ready)
        self.generation = 0
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._segments: tuple[str, ...] = ()
        self._ready: dict[int, SpeechAudio] = {}
        self._next = 0
        self._playing = False
        self._working = False  # guarded by _lock, like _segments and _produced
        self._produced = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._clip_finished)

    @property
    def enabled(self) -> bool:
        return bool(self.config.settings.tts_enabled)

    @property
    def active(self) -> bool:
        return self._playing or self._next < len(self._segments)

    def extend(self, segments):
        if not self.enabled:
            return
        if self.backend is None:
            self.failed.emit("这台电脑上没有可用的语音播放器，回复仍会以文字显示。")
            return
        spoken = tuple(speakable_text(segment) for segment in segments)
        if spoken[:len(self._segments)] != self._segments:
            self.stop()
        if len(spoken) <= len(self._segments):
            return
        with self._lock:
            self._segments = spoken
            if self._working:
                return  # The running worker picks up the new bubbles.
            self._working = True
        self._start_worker()

    def _start_worker(self):
        try:
            provider = self.provider_factory(self.config)
        except TTSError as error:
            self.failed.emit(str(error))
            self.stop()
            return
        generation, cancelled = self.generation, self.cancelled

        def run():
            while not cancelled.is_set():
                with self._lock:
                    if generation != self.generation:
                        return
                    if self._produced >= len(self._segments):
                        self._working = False
                        return
                    index = self._produced
                    text = self._segments[index]
                audio, error = None, ""
                if text:
                    try:
                        audio = provider.synthesize(text, cancelled)
                    except TTSError as exc:
                        error = str(exc)
                    except Exception:
                        error = "语音合成失败，回复仍会以文字显示。"
                with self._lock:
                    if cancelled.is_set() or generation != self.generation:
                        return
                    self._produced = index + 1
                self.signals.ready.emit(generation, index, audio, error)
                if error:
                    return

        threading.Thread(target=run, daemon=True, name="ameath-tts").start()

    def _on_ready(self, generation, index, audio, error):
        if generation != self.generation or self.cancelled.is_set():
            return
        if error:
            self.failed.emit(error)
            self.stop()
            return
        self._ready[index] = audio
        self._play_next()

    def _play_next(self):
        while not self._playing and self._next in self._ready:
            audio = self._ready.pop(self._next)
            self._next += 1
            if audio is None:
                continue  # Nothing speakable in this bubble.
            try:
                seconds = self.backend.play(audio)
            except (TTSError, OSError, RuntimeError) as error:
                self.failed.emit(str(error) if isinstance(error, TTSError) else "语音播放失败，回复仍会以文字显示。")
                self.stop()
                return
            self._playing = True
            self._timer.start(round(seconds * 1000) + self.GAP_MS)

    def _clip_finished(self):
        self._playing = False
        self._play_next()

    def stop(self):
        self.cancelled.set()
        self.cancelled = threading.Event()
        self.generation += 1
        with self._lock:
            self._segments = ()
            self._produced = 0
            self._working = False
        self._ready.clear()
        self._next = 0
        self._timer.stop()
        if self._playing and self.backend is not None:
            try:
                self.backend.stop()
            except (OSError, RuntimeError):
                pass
        self._playing = False

    def shutdown(self):
        self.stop()
        if self.backend is not None and hasattr(self.backend, "close"):
            self.backend.close()
