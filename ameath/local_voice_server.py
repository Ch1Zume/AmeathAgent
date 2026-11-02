"""Local AemeathVoice adapter with bounded decoding and offline speech checks."""
from __future__ import annotations

import io
import logging
import os
import re
import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import HTTPException
from fastapi.responses import Response
from pypinyin import lazy_pinyin
from scipy.signal import resample_poly

from .voice_quality import excessive_silence, transcript_matches

ROOT = Path(os.environ["AMEATH_VOICE_ROOT"])
sys.path.insert(0, str(ROOT / "source" / "api"))
import inference_api as upstream

app = upstream.app
app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/tts"]
# GPU synthesis and the CPU recognition check use separate locks, so the next
# phrase can synthesize while the previous one is being checked.
_lock = threading.Lock()
_asr_lock = threading.Lock()
_recognizer = None
_engine = None


class CandidateError(Exception):
    pass


def syllables(text):
    text = re.sub(r"[^\w\u3400-\u9fff]+", " ", text.lower(), flags=re.UNICODE)
    result = []
    for item in lazy_pinyin(text):
        result.extend(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", item.lower()))
    return result


def recognizer():
    global _recognizer
    if _recognizer is None:
        # Import after the first GPU inference so PyTorch initializes its CUDA DLLs
        # before the CPU-only recognizer loads its native libraries on Windows.
        from faster_whisper import WhisperModel
        preferred = ROOT / "asr-check-small"
        model = preferred if (preferred / "model.bin").is_file() else ROOT / "asr-check"
        if not (model / "model.bin").is_file():
            raise RuntimeError("缺少本地语音校验模型。")
        _recognizer = WhisperModel(str(model), device="cpu", compute_type="int8", cpu_threads=4)
    return _recognizer


def check_audio(audio, sr, text):
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
        raise CandidateError("invalid_audio")
    window = max(1, sr // 4)
    levels = [float(np.sqrt(np.mean(np.square(audio[i:i+window])))) for i in range(0, len(audio), window)]
    if excessive_silence(levels):
        raise CandidateError("silence")
    divisor = __import__('math').gcd(sr, 16000)
    data = resample_poly(audio, 16000 // divisor, sr // divisor).astype(np.float32)
    with _asr_lock:
        segments, _ = recognizer().transcribe(data, language="zh", beam_size=5,
                                             without_timestamps=True, vad_filter=True)
        actual = "".join(segment.text for segment in segments)
    if not transcript_matches(syllables(text), syllables(actual)):
        raise CandidateError("recognition_mismatch")
    return audio


@app.post("/tts")
def synthesize(req: upstream.TTSRequest):
    global _engine
    if upstream._tts_model is None:
        raise HTTPException(503, "语音模型尚未加载。")
    if not req.text.strip():
        raise HTTPException(400, "语音文本为空。")
    import inference
    from .local_voice_inference import DecodeLimit, LocalInference
    for attempt, (top_k, top_p, temperature) in enumerate(((5, 0.95, 0.7), (20, 0.6, 0.6), (10, 0.9, 0.8)), 1):
        try:
            with _lock:
                if _engine is None:
                    _engine = LocalInference(inference, upstream.REF_AUDIO)
                sr, audio = _engine.synthesize(req.text, top_k=top_k, top_p=top_p, temperature=temperature)
            audio = check_audio(audio, sr, req.text)
            buffer = io.BytesIO()
            sf.write(buffer, audio, sr, format="WAV", subtype="PCM_16")
            logging.info("Local speech check passed on attempt %s", attempt)
            return Response(content=buffer.getvalue(), media_type="audio/wav")
        except (CandidateError, DecodeLimit) as error:
            logging.warning("Local speech candidate rejected: %s", error)
    raise HTTPException(422, "本地语音多次生成不完整，请重试或切换其他音色。")
