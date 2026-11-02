"""Optional read-aloud for chat bubbles: a pluggable synthesis provider plus an ordered, interruptible queue.

Text bubbles never wait for speech. If synthesis or playback fails, the reply stays readable and
speech for that reply stops.
"""
from __future__ import annotations

import io
import json
import math
import re
import struct
import sys
import threading
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from .config import validate_endpoint
from .llm import NoRedirect
from .local_voice import LocalVoiceError, LocalVoiceService


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


def create_provider(config, local_voice=None) -> SpeechProvider:
    if config.settings.tts_provider == "aemeath_local":
        if local_voice is None:
            raise TTSError("本地爱弥斯语音需要通过桌宠朗读队列启动。")
        return AemeathLocalSpeechProvider(local_voice)
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


class MiniMaxSpeechProvider:
    """MiniMax synchronous T2A; return the WAV bytes required by the Windows player."""

    def __init__(self, config):
        self.endpoint = validate_endpoint(config.settings.tts_endpoint)
        self.model = config.settings.tts_model.strip()
        self.voice = config.settings.tts_voice.strip()
        self.key = config.tts_key()

    def synthesize(self, text: str, cancelled: threading.Event) -> SpeechAudio:
        if cancelled.is_set():
            raise TTSError("语音请求已取消。")
        if not self.model or not self.voice or not self.key:
            raise TTSError("请在模型设置中填写 MiniMax 语音模型、音色 ID 和语音 API 密钥。")
        payload = {"model": self.model, "text": text, "stream": False, "output_format": "hex",
                   "voice_setting": {"voice_id": self.voice, "speed": 1, "vol": 1, "pitch": 0},
                   "audio_setting": {"sample_rate": 32000, "format": "wav", "channel": 1}}
        request = urllib.request.Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
                                         method="POST")
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
                raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise TTSError("语音服务返回的数据过大。")
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("base_resp"), dict):
                raise ValueError("Invalid response")
            status = data["base_resp"].get("status_code")
            if status != 0:
                raise TTSError(f"MiniMax 语音请求失败（状态码 {status if isinstance(status, int) else '未知'}），请检查语音设置及账户权限。")
            audio = bytes.fromhex(data["data"]["audio"])
            if not audio:
                raise ValueError("Empty audio")
            wav_seconds(audio)
        except urllib.error.HTTPError as error:
            raise TTSError(f"MiniMax 语音请求失败（HTTP {error.code}），请检查语音设置。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TTSError("无法连接语音服务，请检查网络和服务地址。") from None
        except (ValueError, TypeError, KeyError):
            raise TTSError("语音服务返回的数据格式不正确。") from None
        if cancelled.is_set():
            raise TTSError("语音请求已取消。")
        return SpeechAudio(audio)


register_provider("minimax", MiniMaxSpeechProvider)


class AemeathLocalSpeechProvider:
    def __init__(self, service):
        self.service = service

    def synthesize(self, text: str, cancelled: threading.Event) -> SpeechAudio:
        if cancelled.is_set():
            raise TTSError("语音请求已取消。")
        try:
            address = self.service.address(cancelled)
            if cancelled.is_set():
                raise TTSError("语音请求已取消。")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
            phrases = [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*", text) if part.strip()]
            params = None
            clips = []
            for phrase in phrases:
                for attempt in range(2):
                    if cancelled.is_set():
                        raise TTSError("语音请求已取消。")
                    request = urllib.request.Request(address, data=json.dumps({"text": phrase, "text_language": "zh"}, ensure_ascii=False).encode("utf-8"),
                                                     headers={"Content-Type": "application/json"}, method="POST")
                    with opener.open(request, timeout=120) as response:
                        audio = response.read(20_000_001)
                    if len(audio) > 20_000_000:
                        raise TTSError("本地语音返回的数据过大。")
                    duration = wav_seconds(audio)
                    with wave.open(io.BytesIO(audio), "rb") as wav:
                        current = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype())
                        frames = wav.readframes(wav.getnframes())
                    if duration >= 5 and current[1] == 2 and current[3] == "NONE":
                        count = len(frames) // 2
                        rms = math.sqrt(sum(sample[0] ** 2 for sample in struct.iter_unpack('<h', frames)) / max(count, 1)) / 32768
                        if rms < 0.002:
                            if attempt == 0:
                                continue
                            raise TTSError("本地音色生成了异常静音，请稍后重试或切换 MiniMax 语音。")
                    if params is None:
                        params = current
                    elif params != current:
                        raise TTSError("本地音色返回了不一致的音频格式。")
                    clips.append(frames)
                    break
            if params is None:
                raise TTSError("没有可以朗读的文字。")
            output = io.BytesIO()
            with wave.open(output, "wb") as result:
                result.setnchannels(params[0])
                result.setsampwidth(params[1])
                result.setframerate(params[2])
                for frames in clips:
                    result.writeframes(frames)
            audio = output.getvalue()
        except LocalVoiceError as error:
            raise TTSError(str(error)) from None
        except urllib.error.HTTPError as error:
            if error.code == 422:
                raise TTSError("本地语音多次生成不完整，已停止朗读；可以重试或切换其他音色。") from None
            raise TTSError("本地语音合成失败，请查看 .local/aemeath-voice/service.log。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TTSError("本地语音合成失败，请查看 .local/aemeath-voice/service.log。") from None
        if cancelled.is_set():
            raise TTSError("语音请求已取消。")
        return SpeechAudio(audio)


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


def speech_phrases(text: str, max_chars: int = 45, first_chars: int = 0) -> tuple[str, ...]:
    """Split a finished bubble into bounded, complete phrases for incremental playback.

    `first_chars` shortens only the opening phrase at a natural comma, so the first
    audio is ready sooner; synthesis time grows with phrase length.
    """
    parts = re.split(r"(?<=[。！？!?；;])\s*", text)
    if first_chars and parts and len(parts[0].strip()) > first_chars:
        head = parts[0].strip()
        marks = [m for m in re.finditer(r"[，,、]|——|……|…", head[:first_chars + 2]) if m.end() >= 4]
        if marks and len(head) - marks[-1].end() > 4:
            parts[0:1] = [head[:marks[-1].end()], head[marks[-1].end():]]
    result = []
    for part in parts:
        part = part.strip()
        while len(part) > max_chars:
            cut = max(part.rfind(mark, 0, max_chars + 1) for mark in ("，", ",", "、", "：", ":", " "))
            cut = cut + 1 if cut >= max_chars // 3 else max_chars
            result.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            result.append(part)
    return tuple(part for part in result if part)


FIRST_PHRASE_CHARS = 16


def speech_units(segments) -> tuple[tuple[str, str], ...]:
    """Pair displayed text with its spoken form without splitting Markdown markup."""
    units = []
    for segment in segments:
        # Splitting across markup can break code blocks, links and emphasis.
        first = FIRST_PHRASE_CHARS if not units else 0
        parts = (segment,) if any(token in segment for token in ('`', '[', '*', '_', '|')) else speech_phrases(segment, first_chars=first)
        units.extend((part, speakable_text(part)) for part in parts)
    return tuple(units)


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
    started = Signal(int)
    drained = Signal()
    GAP_MS = 150
    WORKERS = 2       # synthesize the next phrase while the current one is checked or played
    CACHE_SIZE = 48   # short repeated lines (pauses, greetings, game remarks) skip synthesis

    def __init__(self, config, parent=None, *, provider_factory=None, backend=None):
        super().__init__(parent)
        self.config = config
        self.local_voice = LocalVoiceService(config.directory)
        self.provider_factory = provider_factory or (lambda config: create_provider(config, self.local_voice))
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
        self._working = 0  # running workers; guarded by _lock, like _segments and _produced
        self._produced = 0
        self._cache: dict[tuple, SpeechAudio] = {}
        self._warming = False
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
        spoken = tuple(spoken for _display, spoken in speech_units(segments))
        if spoken[:len(self._segments)] != self._segments:
            self.stop()
        if len(spoken) <= len(self._segments):
            return
        with self._lock:
            self._segments = spoken
            # Running workers pick up the new bubbles; add workers up to the limit.
            wanted = min(self.WORKERS, len(spoken) - self._produced) - self._working
            if wanted <= 0:
                return
            self._working += wanted
        self._start_worker(wanted)

    def _voice_key(self, text):
        settings = self.config.settings
        return (settings.tts_provider, settings.tts_voice, settings.tts_model, settings.tts_endpoint, text)

    def _start_worker(self, count=1):
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
                        self._working -= 1
                        return
                    # Claim an index; playback still follows index order in _play_next.
                    index = self._produced
                    self._produced += 1
                    text = self._segments[index]
                    key = self._voice_key(text)
                    audio = self._cache.get(key)
                error = ""
                if text and audio is None:
                    try:
                        audio = provider.synthesize(text, cancelled)
                    except TTSError as exc:
                        error = str(exc)
                    except Exception:
                        error = "语音合成失败，回复仍会以文字显示。"
                    if audio is not None and len(text) <= 40:
                        with self._lock:
                            self._cache[key] = audio
                            while len(self._cache) > self.CACHE_SIZE:
                                self._cache.pop(next(iter(self._cache)))
                with self._lock:
                    if cancelled.is_set() or generation != self.generation:
                        return
                self.signals.ready.emit(generation, index, audio, error)
                if error:
                    return

        for _ in range(count):
            threading.Thread(target=run, daemon=True, name="ameath-tts").start()

    def warm_up(self):
        """Load the local voice model in the background so the first reply is not a cold start."""
        if not self.enabled or self.config.settings.tts_provider != "aemeath_local" or self._warming:
            return
        self._warming = True
        cancelled = self.cancelled

        def run():
            try:
                provider = self.provider_factory(self.config)
                if not cancelled.is_set():
                    provider.synthesize("嗯，好。", cancelled)
            except Exception:
                pass  # A real reply reports problems; warming stays silent.
            finally:
                self._warming = False

        threading.Thread(target=run, daemon=True, name="ameath-tts-warm").start()

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
            index = self._next
            audio = self._ready.pop(index)
            self._next += 1
            if audio is None:
                self.started.emit(index)
                continue  # Nothing speakable in this bubble.
            try:
                seconds = self.backend.play(audio)
            except (TTSError, OSError, RuntimeError) as error:
                self.failed.emit(str(error) if isinstance(error, TTSError) else "语音播放失败，回复仍会以文字显示。")
                self.stop()
                return
            self._playing = True
            self.started.emit(index)
            self._timer.start(round(seconds * 1000) + self.GAP_MS)
        if not self.active:
            self.drained.emit()

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
            self._working = 0
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
        self.local_voice.close()
        if self.backend is not None and hasattr(self.backend, "close"):
            self.backend.close()

    def refresh_settings(self):
        if not self.enabled or self.config.settings.tts_provider != "aemeath_local":
            self.local_voice.close()
            self.local_voice = LocalVoiceService(self.config.directory)
