from __future__ import annotations

import ctypes
import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    endpoint: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    protocol: str = "chat"
    mode: str = "demo"
    observe_reader: bool = False
    cooldown_seconds: int = 120
    dwell_seconds: int = 35
    thinking: bool = False
    stream_chat: bool = True
    tts_enabled: bool = False
    tts_provider: str = "minimax"
    tts_voice: str = "female-shaonv"
    tts_endpoint: str = "https://api.minimaxi.com/v1/t2a_v2"
    tts_model: str = "speech-2.8-turbo"
    reasoning_effort: str = "high"
    game_thinking: bool = True
    game_endpoint: str = "http://127.0.0.1:8080"
    game_request_limit: int = 200
    game_token_limit: int = 1000000
    game_interval: float = 1.0
    pet_wandering: bool = False
    pet_resting: bool = True
    pet_flying: bool = True
    memory_enabled: bool = True
    session_memory: bool = True
    proactive_enabled: bool = False
    proactive_daily_limit: int = 3
    screen_observation_enabled: bool = False
    screen_observation_interval: int = 15
    vision_endpoint: str = "https://api.openai.com/v1"
    vision_model: str = ""
    use_companion_context: bool = True
    weather_enabled: bool = True
    weather_city: str = ""
    weather_latitude: float | None = None
    weather_longitude: float | None = None
    weather_timezone: str = "Asia/Shanghai"
    focus_work_minutes: int = 50
    focus_rest_minutes: int = 10
    focus_cycles: int = 2


def validate_endpoint(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("服务地址不能包含密钥、查询参数或账号信息。")
    if not parsed.hostname or parsed.scheme not in ("http", "https"):
        raise ValueError("请输入完整的 HTTPS 服务地址。")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程服务请使用 HTTPS；本地服务可以使用 HTTP。")
    return value


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data: bytes, decrypt: bool = False) -> bytes:
    """DPAPI, bound to the signed-in Windows account. No plaintext key files."""
    if sys.platform != "win32":
        raise OSError("此系统仅支持会话内使用密钥或使用环境变量。")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_wchar_p, ctypes.POINTER(_Blob),
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
    crypt.CryptProtectData.restype = ctypes.c_int
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.POINTER(ctypes.c_wchar_p),
                                        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_uint32, ctypes.POINTER(_Blob)]
    crypt.CryptUnprotectData.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), "Ameath API key", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise OSError("Windows 无法读取或保存加密密钥。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class Config:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(os.environ.get("AMEATH_DATA_DIR", os.environ.get("VIOLET_DATA_DIR", ROOT / ".local")))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "settings.json"
        self.key_path = self.directory / "credential.dpapi"
        self.tts_key_path = self.directory / "tts-credential.dpapi"
        self.vision_key_path = self.directory / "vision-credential.dpapi"
        self.settings = Settings()
        self._session_key = ""
        self._session_tts_key = ""
        self._session_vision_key = ""
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                names = {f.name for f in fields(Settings)}
                self.settings = Settings(**{k: v for k, v in data.items() if k in names})
                if not isinstance(self.settings.observe_reader, bool):
                    raise ValueError("Invalid observation setting")
                if not isinstance(self.settings.pet_wandering, bool):
                    raise ValueError("Invalid pet setting")
                if not isinstance(self.settings.pet_resting, bool) or not isinstance(self.settings.pet_flying, bool):
                    raise ValueError("Invalid pet behavior setting")
                if not isinstance(self.settings.memory_enabled, bool) or not isinstance(self.settings.use_companion_context, bool):
                    raise ValueError("Invalid memory setting")
                if not isinstance(self.settings.session_memory, bool) or not isinstance(self.settings.proactive_enabled, bool):
                    raise ValueError("Invalid companion setting")
                if not isinstance(self.settings.screen_observation_enabled, bool) or not isinstance(self.settings.vision_model, str):
                    raise ValueError("Invalid screen observation setting")
                self.settings.screen_observation_interval = max(5, min(180, int(self.settings.screen_observation_interval)))
                self.settings.vision_endpoint = validate_endpoint(self.settings.vision_endpoint)
                self.settings.proactive_daily_limit = max(1, min(10, int(self.settings.proactive_daily_limit)))
                self.settings.endpoint = validate_endpoint(self.settings.endpoint)
                if self.settings.mode not in ("demo", "live") or self.settings.protocol not in ("responses", "chat"):
                    raise ValueError("Invalid settings")
                self.settings.cooldown_seconds = max(30, min(1800, int(self.settings.cooldown_seconds)))
                self.settings.dwell_seconds = max(15, min(300, int(self.settings.dwell_seconds)))
                if self.settings.reasoning_effort not in ("low", "high", "max"):
                    raise ValueError("Invalid reasoning effort")
                if (not isinstance(self.settings.tts_enabled, bool) or not isinstance(self.settings.tts_provider, str)
                        or not isinstance(self.settings.tts_voice, str) or not isinstance(self.settings.tts_model, str)):
                    raise ValueError("Invalid speech setting")
                self.settings.tts_endpoint = validate_endpoint(self.settings.tts_endpoint)
                if not isinstance(self.settings.stream_chat, bool):
                    raise ValueError("Invalid streaming setting")
                if not isinstance(self.settings.thinking, bool) or not isinstance(self.settings.game_thinking, bool):
                    raise ValueError("Invalid thinking mode")
                # Migrate the unconfigured v0.1 demo; preserve any existing live provider or key.
                if (self.settings.mode == "demo" and self.settings.endpoint == "https://api.openai.com/v1"
                        and self.settings.model == "gpt-4.1-mini" and not self.key_path.exists()
                        and not os.environ.get("AMEATH_API_KEY") and not os.environ.get("VIOLET_API_KEY")):
                    self.settings.endpoint = "https://api.deepseek.com"
                    self.settings.model = "deepseek-flash"
                    self.settings.protocol = "chat"
                self.settings.game_request_limit = max(1, min(2000, int(self.settings.game_request_limit)))
                self.settings.game_token_limit = max(1000, min(10000000, int(self.settings.game_token_limit)))
                self.settings.game_interval = max(0.5, min(10, float(self.settings.game_interval)))
                self.settings.focus_work_minutes = max(1, min(240, int(self.settings.focus_work_minutes)))
                self.settings.focus_rest_minutes = max(1, min(120, int(self.settings.focus_rest_minutes)))
                self.settings.focus_cycles = max(1, min(20, int(self.settings.focus_cycles)))
                if not isinstance(self.settings.weather_enabled, bool) or not isinstance(self.settings.weather_city, str):
                    raise ValueError("Invalid weather settings")
                for value, bound in [(self.settings.weather_latitude, 90), (self.settings.weather_longitude, 180)]:
                    if value is not None and (not isinstance(value, (int, float)) or not -bound <= value <= bound):
                        raise ValueError("Invalid weather coordinates")
            except (ValueError, TypeError, OSError):
                self.settings = Settings()

    def save(self):
        self.settings.endpoint = validate_endpoint(self.settings.endpoint)
        self.settings.tts_endpoint = validate_endpoint(self.settings.tts_endpoint)
        self.settings.vision_endpoint = validate_endpoint(self.settings.vision_endpoint)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self.settings), indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def key(self) -> str:
        if self._session_key:
            return self._session_key
        if os.environ.get("AMEATH_API_KEY"):
            return os.environ["AMEATH_API_KEY"]
        if os.environ.get("VIOLET_API_KEY"):
            return os.environ["VIOLET_API_KEY"]
        if urlparse(self.settings.endpoint).hostname == "api.deepseek.com" and os.environ.get("DEEPSEEK_API_KEY"):
            return os.environ["DEEPSEEK_API_KEY"]
        if self.key_path.exists():
            try:
                return _protect(self.key_path.read_bytes(), decrypt=True).decode("utf-8")
            except (OSError, UnicodeError):
                return ""
        return ""

    def set_key(self, key: str, remember: bool):
        self._session_key = key.strip()
        if remember and self._session_key:
            self.key_path.write_bytes(_protect(self._session_key.encode("utf-8")))
        elif self.key_path.exists():
            self.key_path.unlink()

    def tts_key(self) -> str:
        if self._session_tts_key:
            return self._session_tts_key
        if os.environ.get("AMEATH_TTS_API_KEY"):
            return os.environ["AMEATH_TTS_API_KEY"]
        if self.tts_key_path.exists():
            try:
                return _protect(self.tts_key_path.read_bytes(), decrypt=True).decode("utf-8")
            except (OSError, UnicodeError):
                return ""
        return ""

    def set_tts_key(self, key: str, remember: bool):
        self._session_tts_key = key.strip()
        if remember and self._session_tts_key:
            self.tts_key_path.write_bytes(_protect(self._session_tts_key.encode("utf-8")))
        elif self.tts_key_path.exists():
            self.tts_key_path.unlink()

    def vision_key(self) -> str:
        if self._session_vision_key:
            return self._session_vision_key
        if os.environ.get("AMEATH_VISION_API_KEY"):
            return os.environ["AMEATH_VISION_API_KEY"]
        if self.vision_key_path.exists():
            try:
                return _protect(self.vision_key_path.read_bytes(), decrypt=True).decode("utf-8")
            except (OSError, UnicodeError):
                return ""
        return ""

    def set_vision_key(self, key: str, remember: bool):
        self._session_vision_key = key.strip()
        if remember and self._session_vision_key:
            self.vision_key_path.write_bytes(_protect(self._session_vision_key.encode("utf-8")))
        elif self.vision_key_path.exists():
            self.vision_key_path.unlink()
