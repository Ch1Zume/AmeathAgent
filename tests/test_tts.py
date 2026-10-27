import io
import threading
import time
import wave

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


def test_bubbles_are_spoken_in_order_and_streamed_bubbles_are_not_repeated(app, config):
    provider, backend = Provider(), Backend()
    queue = queue_with(config, provider, backend)
    queue.extend(["第一段。"])
    queue.extend(["第一段。", "第二段。"])
    queue.extend(["第一段。", "第二段。"])
    assert wait_for(lambda: len(backend.played) == 2 and not queue.active)
    assert provider.texts == ["第一段。", "第二段。"]


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
