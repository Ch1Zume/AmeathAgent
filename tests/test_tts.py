import io
import json
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtTest import QTest

from ameath.chat import ChatReply
from ameath.chat_ui import ChatDialog
from ameath.config import Config
from ameath.tts import (CustomSpeechProvider, SpeechAudio, SpeechQueue, TTSError, create_provider,
                        register_provider, speakable_text, wav_seconds)
from test_ui import app


def wav(seconds=0.05, rate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * round(seconds * rate))
    return buffer.getvalue()


class Provider:
    def __init__(self, gate=None, fail_on=None):
        self.gate, self.fail_on, self.texts, self.made = gate, fail_on, [], {}

    def synthesize(self, text, cancelled):
        if self.gate is not None:
            self.gate.wait(2)
        if text == self.fail_on:
            raise TTSError("合成失败")
        self.texts.append(text)
        audio = SpeechAudio(wav())
        self.made[id(audio)] = text
        return audio


class Backend:
    def __init__(self):
        self.played, self.stopped = [], 0

    def play(self, audio):
        self.played.append(audio)
        return wav_seconds(audio.data)

    def stop(self):
        self.stopped += 1


def wait_for(condition, seconds=3):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(10)
    return condition()


@pytest.fixture
def config(tmp_path):
    config = Config(tmp_path)
    config.settings.tts_enabled = True
    return config


def queue_with(config, provider, backend):
    return SpeechQueue(config, provider_factory=lambda _config: provider, backend=backend)


def test_speakable_text_drops_markup_and_does_not_read_code():
    assert speakable_text("**好呀**，看[这里](https://x)。") == "好呀，看这里。"
    assert speakable_text("步骤：\n```py\nprint(1)\n```") == "步骤： （代码在气泡里）"
    assert speakable_text("| a | b |\n|---|---|") == ""
    assert speakable_text("- 第一\n- 第二") == "第一 第二"


def test_placeholder_provider_explains_where_to_plug_in_an_api(config):
    config.settings.tts_provider = "custom"
    assert isinstance(create_provider(config), CustomSpeechProvider)
    with pytest.raises(TTSError, match="CustomSpeechProvider"):
        create_provider(config).synthesize("你好", threading.Event())
    config.settings.tts_provider = "missing"
    with pytest.raises(TTSError):
        create_provider(config)
    register_provider("fake", lambda _config: Provider())
    config.settings.tts_provider = "fake"
    assert isinstance(create_provider(config), Provider)
    with pytest.raises(TTSError):
        wav_seconds(b"not audio")


def test_minimax_http_request_and_errors(config):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/unexpected")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            data = {"base_resp": {"status_code": 1004}, "data": None} if self.path == "/error" else {
                "base_resp": {"status_code": 0}, "data": {"audio": wav().hex()}}
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config.settings.tts_endpoint = f"http://127.0.0.1:{server.server_port}/v1/t2a_v2"
        config.set_tts_key("test-tts-secret", False)
        config.set_key("different-chat-key", False)
        result = create_provider(config).synthesize("你好", threading.Event())
        assert result.data == wav()
        path, authorization, body = requests[0]
        assert path == "/v1/t2a_v2" and authorization == "Bearer test-tts-secret"
        assert body["text"] == "你好" and body["model"] == config.settings.tts_model
        assert body["voice_setting"]["voice_id"] == config.settings.tts_voice
        assert body["audio_setting"]["format"] == "wav" and body["output_format"] == "hex"
        config.settings.tts_endpoint = f"http://127.0.0.1:{server.server_port}/error"
        with pytest.raises(TTSError, match="1004"):
            create_provider(config).synthesize("你好", threading.Event())
        config.settings.tts_endpoint = f"http://127.0.0.1:{server.server_port}/redirect"
        with pytest.raises(TTSError, match="302"):
            create_provider(config).synthesize("你好", threading.Event())
        assert len(requests) == 3
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(TTSError, match="取消"):
            create_provider(config).synthesize("你好", cancelled)
        assert len(requests) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_voice_provider_requests_wav_and_honours_cancellation():
    from ameath.tts import AemeathLocalSpeechProvider
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(wav())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    class LocalService:
        def address(self, cancelled):
            return f"http://127.0.0.1:{server.server_port}/tts"
    try:
        provider = AemeathLocalSpeechProvider(LocalService())
        assert provider.synthesize("你好", threading.Event()).data == wav()
        assert requests == [{"text": "你好", "text_language": "zh"}]
        combined = provider.synthesize("你好！我们走吧。", threading.Event())
        assert [r['text'] for r in requests[1:]] == ["你好！", "我们走吧。"]
        assert wav_seconds(combined.data) == pytest.approx(0.1)
        stopped = threading.Event()
        stopped.set()
        with pytest.raises(TTSError, match="取消"):
            provider.synthesize("你好", stopped)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_voice_rejects_long_silent_audio(monkeypatch):
    from ameath.tts import AemeathLocalSpeechProvider
    class Service:
        def address(self, cancelled):
            return "http://127.0.0.1:12345/tts"
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return wav(6)
    class Opener:
        calls = 0
        def open(self, request, timeout):
            self.calls += 1
            return Response()
    opener = Opener()
    monkeypatch.setattr('ameath.tts.urllib.request.build_opener', lambda *args: opener)
    with pytest.raises(TTSError, match="异常静音"):
        AemeathLocalSpeechProvider(Service()).synthesize("测试", threading.Event())
    assert opener.calls == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows account-bound encryption")
def test_speech_settings_and_independent_encrypted_key_roundtrip(app, tmp_path, monkeypatch):
    from ameath.dialogs import SettingsDialog
    monkeypatch.delenv("AMEATH_TTS_API_KEY", raising=False)
    monkeypatch.delenv("AMEATH_API_KEY", raising=False)
    monkeypatch.delenv("VIOLET_API_KEY", raising=False)
    config = Config(tmp_path)
    config.set_key("chat-secret", True)
    dialog = SettingsDialog(config)
    dialog.tts_enabled.setChecked(True)
    dialog.tts_endpoint.setText("https://api.minimax.io/v1/t2a_v2")
    dialog.tts_model.setText("speech-2.8-hd")
    dialog.tts_voice.setText("my-voice")
    dialog.tts_api_key.setText("tts-secret")
    dialog.tts_remember.setChecked(True)
    dialog.save()
    restored = Config(tmp_path)
    assert restored.settings.tts_enabled and restored.settings.tts_provider == "minimax"
    assert restored.settings.tts_endpoint == "https://api.minimax.io/v1/t2a_v2"
    assert restored.settings.tts_model == "speech-2.8-hd" and restored.settings.tts_voice == "my-voice"
    assert restored.tts_key() == "tts-secret" and restored.key() == "chat-secret"
    assert b"tts-secret" not in restored.tts_key_path.read_bytes()
    assert "tts-secret" not in restored.path.read_text(encoding="utf-8")


def test_bubbles_are_spoken_in_order_and_streamed_bubbles_are_not_repeated(app, config):
    provider, backend = Provider(), Backend()
    queue = queue_with(config, provider, backend)
    queue.extend(["第一段。"])
    queue.extend(["第一段。", "第二段。"])
    queue.extend(["第一段。", "第二段。"])
    assert wait_for(lambda: len(backend.played) == 2 and not queue.active)
    assert provider.texts == ["第一段。", "第二段。"]


def test_local_voice_plays_first_sentence_while_second_is_still_synthesizing(app, config):
    class BlockingProvider(Provider):
        def __init__(self):
            super().__init__()
            self.second_started = threading.Event()
            self.release_second = threading.Event()

        def synthesize(self, text, cancelled):
            if text == "第二句。":
                self.second_started.set()
                self.release_second.wait(3)
            return super().synthesize(text, cancelled)

    config.settings.tts_provider = "aemeath_local"
    provider, backend = BlockingProvider(), Backend()
    queue = queue_with(config, provider, backend)
    try:
        queue.extend(["第一句。第二句。"])
        assert wait_for(lambda: provider.second_started.is_set() and len(backend.played) == 1)
        assert provider.texts == ["第一句。"]
        queue.extend(["第一句。第二句。", "第三句。"])
        provider.release_second.set()
        assert wait_for(lambda: len(backend.played) == 3 and not queue.active)
        assert provider.texts == ["第一句。", "第二句。", "第三句。"]
    finally:
        provider.release_second.set()
        queue.shutdown()


def test_local_voice_sentence_split_limits_long_phrases_without_losing_text():
    from ameath.tts import speech_phrases
    text = "先把事情整理好。" + "一起" * 30 + "，再喝茶！"
    phrases = speech_phrases(text)
    assert "".join(phrases) == text
    assert phrases[0] == "先把事情整理好。"
    assert all(len(phrase) <= 45 for phrase in phrases)


def test_local_voice_quality_detects_silence_omissions_and_reference_leak():
    from ameath.voice_quality import excessive_silence, transcript_matches
    assert not excessive_silence([0.05] * 8 + [0.0001] * 2 + [0.05] * 8)
    assert excessive_silence([0.05] * 4 + [0.0001] * 10 + [0.05] * 4)
    assert excessive_silence([0.0001] * 12)
    expected = ['jin', 'tian', 'ye', 'xin', 'ku', 'le', 'xian', 'xiu', 'xi', 'yi', 'xia', 'ba']
    assert transcript_matches(expected, expected)
    assert not transcript_matches(expected, expected[:5])
    assert not transcript_matches(expected, ['wu', 'biao', 'jie', 'lu'] + expected)
    assert transcript_matches(expected, ['en', 'en'] + expected)
    assert transcript_matches(expected, ['n', 'n'] + expected)
    assert not transcript_matches(expected, ['en', 'wu', 'biao', 'jie', 'lu'] + expected)
    assert not transcript_matches(expected, ['wu', 'biao', 'jie', 'luo'] + expected)
    assert not transcript_matches(expected, ['wu', 'biao', 'qian', 'luo'] + expected)
    assert not transcript_matches(expected, ['yan', 'lu'] + expected)
    assert not transcript_matches(expected, expected[2:])
    assert not transcript_matches(expected, expected[:6] + expected[8:])


def test_disabled_speech_does_nothing_and_a_new_reply_interrupts_the_old_one(app, config):
    gate = threading.Event()
    provider, backend = Provider(gate), Backend()
    queue = queue_with(config, provider, backend)
    config.settings.tts_enabled = False
    queue.extend(["不会读"])
    assert not queue.active
    config.settings.tts_enabled = True
    queue.extend(["旧回复"])
    queue.extend(["新回复"])  # Not a continuation: restart.
    gate.set()
    assert wait_for(lambda: len(backend.played) == 1 and not queue.active)
    QTest.qWait(100)
    assert len(backend.played) == 1 and provider.made[id(backend.played[0])] == "新回复"


def test_switching_away_releases_local_service(config):
    queue = SpeechQueue(config, provider_factory=lambda _config: Provider(), backend=Backend())
    old = queue.local_voice
    config.settings.tts_provider = "minimax"
    queue.refresh_settings()
    assert old.closed and queue.local_voice is not old
    queue.shutdown()


def test_failure_reports_once_and_stops_without_affecting_text(app, config):
    backend, errors = Backend(), []
    queue = queue_with(config, Provider(fail_on="坏的"), backend)
    queue.failed.connect(errors.append)
    queue.extend(["好的", "坏的", "后面的"])
    assert wait_for(lambda: errors)
    QTest.qWait(150)
    assert errors == ["合成失败"] and len(backend.played) <= 1 and not queue.active
    no_player = SpeechQueue(config, provider_factory=lambda _config: Provider(), backend=None)
    no_player.backend = None
    no_player.failed.connect(errors.append)
    no_player.extend(["你好"])
    assert "播放器" in errors[-1]


@pytest.fixture
def chat(app, config):
    dialog = ChatDialog(config)
    dialog.speech.provider_factory = lambda _config: Provider()
    dialog.speech.backend = Backend()
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.shutdown()
    dialog.deleteLater()
    app.processEvents()


def test_chat_speaks_replies_and_stops_on_cancel_or_new_message(chat):
    backend = chat.speech.backend
    chat.busy = True
    chat.on_reply(chat.generation, "你好", ChatReply("一。\n\n二。", bubbles=("一。", "二。")))
    assert wait_for(lambda: len(backend.played) == 2)
    chat.speech.extend(["很长的一段。"])
    chat.cancel_request()
    assert backend.stopped and not chat.speech.active
    chat.show_proactive("该休息啦。")
    assert wait_for(lambda: len(backend.played) == 3)
    chat.input.setPlainText("在吗")
    chat.submit()
    assert not chat.speech.active


def test_chat_reveals_each_sentence_only_when_its_audio_starts(chat):
    first_gate, second_gate = threading.Event(), threading.Event()
    class GatedProvider(Provider):
        def synthesize(self, text, cancelled):
            (first_gate if text == "第一句。" else second_gate).wait(3)
            return super().synthesize(text, cancelled)
    provider = GatedProvider()
    chat.speech.provider_factory = lambda _config: provider
    bubble = chat.reply_bubble
    reply = ChatReply("第一句。第二句。", bubbles=("第一句。第二句。",))
    try:
        chat.on_reply(chat.generation, "测试", reply)
        QTest.qWait(100)
        assert bubble.voice_sync and bubble.voice_waiting.isVisible()
        assert len(bubble.speech_cards) == 0
        first_gate.set()
        assert wait_for(lambda: len(bubble.speech_cards) == 1)
        assert bubble.speech_texts[0].toPlainText() == "第一句。"
        QTest.qWait(300)
        assert len(bubble.speech_cards) == 1
        second_gate.set()
        assert wait_for(lambda: len(bubble.speech_cards) == 2 and not chat.speech.active)
        assert bubble.speech_texts[1].toPlainText() == "第二句。"
        assert not bubble.playing
    finally:
        first_gate.set()
        second_gate.set()


def test_voice_failure_reveals_all_text_without_waiting(chat):
    chat.speech.provider_factory = lambda _config: Provider(fail_on="第一句。")
    chat.on_reply(chat.generation, "测试", ChatReply("第一句。第二句。", bubbles=("第一句。第二句。",)))
    assert wait_for(lambda: len(chat.reply_bubble.speech_cards) == 2)
    assert not chat.reply_bubble.voice_sync and not chat.speech.active
    assert [w.toPlainText() for w in chat.reply_bubble.speech_texts] == ["第一句。", "第二句。"]


def test_voice_sync_keeps_markdown_intact_and_spoken_filler():
    from ameath.tts import speech_units
    parts = speech_units(["嗯……我在呢。", "**重要。内容**", "```python\nprint('a。b')\n```"])
    assert parts[0] == ("嗯……我在呢。", "嗯……我在呢。")
    assert parts[1][0] == "**重要。内容**"
    assert parts[2][0] == "```python\nprint('a。b')\n```"


def test_streamed_voice_waits_for_final_text_after_audio_drains(chat):
    chat.busy = True
    chat.on_partial(chat.generation, ("第一句。",))
    assert wait_for(lambda: len(chat.reply_bubble.speech_cards) == 1 and not chat.speech.active)
    assert chat.reply_bubble.playing and chat.reply_bubble.streaming
    chat.on_reply(chat.generation, "测试", ChatReply("第一句。", bubbles=("第一句。",)))
    assert not chat.reply_bubble.playing
    assert len(chat.reply_bubble.speech_cards) == 1


def test_streamed_voice_failure_reveals_later_text_without_restarting_speech(chat):
    provider = Provider(fail_on="第一句。")
    chat.speech.provider_factory = lambda _config: provider
    chat.busy = True
    chat.on_partial(chat.generation, ("第一句。",))
    assert wait_for(lambda: len(chat.reply_bubble.speech_cards) == 1 and not chat._voice_sync)
    chat.on_partial(chat.generation, ("第一句。", "第二句。"))
    chat.on_reply(chat.generation, "测试", ChatReply("第一句。\n\n第二句。", bubbles=("第一句。", "第二句。")))
    QTest.qWait(100)
    assert [w.toPlainText() for w in chat.reply_bubble.speech_texts] == ["第一句。", "第二句。"]
    assert not chat.speech.active and not chat.speech.backend.played


def test_voice_sync_cancel_discards_late_audio_and_text(chat):
    gate = threading.Event()
    provider = Provider(gate=gate)
    chat.speech.provider_factory = lambda _config: provider
    chat.busy = True
    chat.on_partial(chat.generation, ("不应出现。",))
    assert chat.reply_bubble.voice_sync and not chat.reply_bubble.speech_cards
    chat.cancel_request()
    gate.set()
    QTest.qWait(150)
    assert not chat.speech.backend.played
    assert not chat.reply_bubble.voice_sync


def test_first_phrase_is_short_so_audio_starts_sooner_without_losing_text():
    from ameath.tts import speech_phrases, speech_units
    text = "嗯……我打它越多对我越不利，得想想能不能靠效果伤害——哎呀，我玩的是战士啊！"
    units = [spoken for _display, spoken in speech_units([text, "第二段，也很长很长很长很长很长很长的一句话。"])]
    assert units[0] == "嗯……我打它越多对我越不利，" and "".join(units[:2]) == text
    assert units[2] == "第二段，也很长很长很长很长很长很长的一句话。"  # only the opening phrase is shortened
    assert speech_phrases("短句，后面。", first_chars=16) == ("短句，后面。",)


def test_repeated_short_lines_reuse_audio_and_two_workers_keep_playback_order(app, config):
    class SlowFirst(Provider):
        def synthesize(self, text, cancelled):
            if text == "慢的第一句。":
                time.sleep(0.3)
            return super().synthesize(text, cancelled)

    provider, backend = SlowFirst(), Backend()
    queue = queue_with(config, provider, backend)
    queue.extend(["慢的第一句。", "快的第二句。"])
    # The second phrase is synthesized while the first is still in progress...
    assert wait_for(lambda: "快的第二句。" in provider.texts)
    assert provider.texts == ["快的第二句。"]
    # ...but playback still follows bubble order.
    assert wait_for(lambda: len(backend.played) == 2 and not queue.active)
    assert [provider.made[id(audio)] for audio in backend.played] == ["慢的第一句。", "快的第二句。"]
    queue.stop()
    queue.extend(["快的第二句。"])
    assert wait_for(lambda: len(backend.played) == 3 and not queue.active)
    assert provider.texts.count("快的第二句。") == 1
    queue.shutdown()


def test_game_lines_use_the_reply_bubble_but_never_enter_chat_history(chat):
    assert chat.show_game_line(["我先算一下伤害。"]) == "voice"
    assert wait_for(lambda: len(chat.speech.backend.played) == 1 and not chat.speech.active)
    assert chat.history == [] and chat.messages == []
    chat.busy = True
    assert chat.show_game_line(["正在聊天时不抢话"]) == ""
    chat.busy = False
    chat.speech.extend(["聊天回复在说话。"])
    assert chat.show_game_line(["游戏不打断聊天"]) == ""
    chat.speech.stop()
