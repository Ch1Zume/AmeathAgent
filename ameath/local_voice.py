"""Manage the optional local AemeathVoice server for one desktop session."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .llm import NoRedirect


class LocalVoiceError(Exception):
    pass


class LocalVoiceService:
    def __init__(self, directory: Path):
        self.root = directory / "aemeath-voice"
        self.lock = threading.Lock()
        self.process = None
        self.port = None
        self.closed = False

    def _start(self):
        python = self.root / "venv" / "Scripts" / "python.exe"
        api = self.root / "source" / "api"
        if not python.is_file() or not (api / "inference_api.py").is_file():
            raise LocalVoiceError("未找到本地爱弥斯语音环境，请检查安装目录。")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                    "AMEATH_VOICE_ROOT": str(self.root)})
        self.root.mkdir(parents=True, exist_ok=True)
        log = (self.root / "service.log").open("w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                [str(python), "-m", "uvicorn", "ameath.local_voice_server:app", "--app-dir", str(Path(__file__).resolve().parent.parent),
                 "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log"],
                cwd=str(self.root / "source"), env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log.close()

    def address(self, cancelled: threading.Event) -> str:
        if cancelled.is_set():
            raise LocalVoiceError("本地语音已取消。")
        with self.lock:
            if self.closed:
                raise LocalVoiceError("本地语音已关闭。")
            if self.process is None or self.process.poll() is not None:
                self._start()
            process, port = self.process, self.port
        deadline = time.monotonic() + 180
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        while time.monotonic() < deadline and not cancelled.is_set():
            if process.poll() is not None:
                raise LocalVoiceError("本地语音启动失败，请查看 .local/aemeath-voice/service.log。")
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    data = json.load(response)
                if data.get("status") == "ok" and data.get("model") == "aemeath-e20":
                    return f"http://127.0.0.1:{port}/tts"
            except (OSError, ValueError, TypeError):
                pass
            cancelled.wait(0.25)
        raise LocalVoiceError("本地语音已取消。" if cancelled.is_set() else "本地语音启动超时。")

    def close(self):
        with self.lock:
            self.closed = True
            process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
