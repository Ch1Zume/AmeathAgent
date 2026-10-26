import json
import threading
import time

import pytest
from PySide6.QtTest import QTest

from ameath.chat import ChatAgent, ChatFormatError, ChatReply, build_chat_payload, parse_chat_reply, speech_segments
from ameath.config import Config, Settings
from ameath.llm import Completion, ModelResponseError, tune_payload
from test_chat import app, chat


def test_segmented_reply_preserves_order_emotion_memory_and_legacy_format():
    parts = ["好玩的事啊……我想到了纸飞机。", "哈——你是不是觉得挺无聊？", "那么——你呢？"]
    reply = parse_chat_reply(json.dumps({"reply": parts, "emotion": "happy",
                                        "memory_updates": [{"key": "饮品", "value": "喜欢红茶"}]}))
    assert reply.segments == tuple(parts)
    assert reply.text == "\n\n".join(parts)
    assert reply.action == "waving" and reply.memories == (("饮品", "喜欢红茶"),)
    assert parse_chat_reply('{"reply":"老版本也能接住。"}').segments == ("老版本也能接住。",)
    assert parse_chat_reply("直接给文字也能接住。").segments == ("直接给文字也能接住。",)


@pytest.mark.parametrize("reply", [[], [""], ["好", 1], None, ["x"] * 33, ["x" * 16001]])
def test_invalid_segment_arrays_cannot_be_displayed_or_partially_saved(reply):
    with pytest.raises(ChatFormatError):
        parse_chat_reply(json.dumps({"reply": reply, "memory_updates": [{"key": "k", "value": "v"}]}))


def test_fallback_splits_at_sentence_boundaries_without_cutting_deliverables():
    first = "这是一个语意完整的句子，它不会在逗号的地方被生硬地切开。"
    text = first * 5
    parts = speech_segments(text)
    assert len(parts) > 1 and "".join(parts) == text
    assert all(part.endswith("。") for part in parts)
    code = "这里是代码：\n\n```python\nprint('hello。world')\n\nprint(2)\n```"
    table = "| 名称 | 内容 |\n|---|---|\n| a | b |"
    assert speech_segments(code) == (code,)
    assert speech_segments(table) == (table,)


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_deepseek_first_request_avoids_empty_json_mode_and_keeps_thinking(protocol):
    settings = Settings(protocol=protocol, thinking=True)
    payload = build_chat_payload("你好", [], settings)
    assert "response_format" not in payload and "text" not in payload
    tuned = tune_payload(payload, settings)
    if protocol == "chat":
        assert tuned["thinking"] == {"type": "enabled"}
    else:
        assert tuned["reasoning"] == {"effort": "high"}
    settings.endpoint = "https://example.com/v1"
    other = build_chat_payload("你好", [], settings)
    assert (other["response_format"] if protocol == "chat" else other["text"]["format"]) == {"type": "json_object"}


def test_service_that_returns_empty_in_json_mode_succeeds_on_first_chat_call(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.thinking = True
    agent = ChatAgent(config)
    calls, progress = [], []

    class Client:
        def request(self, payload, cancelled, **kwargs):
            calls.append(payload)
            if "response_format" in payload:
                raise ModelResponseError("empty_final")
            assert kwargs["settings"].thinking
            return Completion('{"reply":["回来了呀。","今天怎么样？"],"emotion":"happy"}', {}, "test")

    agent.client = Client()
    reply = agent.answer("我回来了", [], threading.Event(), on_retry=progress.append)
    assert len(calls) == 1 and not progress and not reply.retried
    assert len(reply.segments) == 2 and config.settings.thinking


def test_assistant_history_uses_same_wire_format_without_replaying_memory_updates():
    history = [("user", "你好"), ("assistant", "回来啦。\n\n今天怎么样？")]
    payload = build_chat_payload("聊聊工作", history, Settings())
    old_reply = json.loads(payload["messages"][2]["content"])
    assert old_reply["reply"] == ["回来啦。", "今天怎么样？"]
    assert old_reply["memory_updates"] == []
    assert history[1][1] == "回来啦。\n\n今天怎么样？"


def start_speech(chat, parts=("第一段话呀。", "第二段是一个完整的意思。", "最后一句。")):
    chat.messages.append(("user", "聊聊吧"))
    reply = ChatReply("\n\n".join(parts), "happy", bubbles=tuple(parts))
    chat.on_reply(chat.generation, "聊聊吧", reply)
    return chat.reply_bubble


def test_text_fades_in_and_later_bubbles_wait_their_turn(chat):
    bubble = start_speech(chat)
    assert bubble.playing and len(bubble.speech_cards) == 1
    effect = bubble.speech_texts[0].graphicsEffect()
    assert effect.opacity() == 0
    QTest.qWait(180)
    assert 0 < effect.opacity() < 1
    assert len(bubble.speech_cards) == 1
    bubble.finish_playback()
    assert not bubble.playing and len(bubble.speech_cards) == 3
    assert effect.opacity() == 1
    assert len(chat.history) == 2  # One semantic turn, not three artificial turns.
    assert chat.transcript.toPlainText() == "第一段话呀。\n第二段是一个完整的意思。\n最后一句。"
    QTest.qWait(700)
    assert len(bubble.speech_cards) == 3


def test_playback_completes_automatically_and_prevents_mid_speech_auto_hide(chat, monkeypatch):
    bubble = chat.reply_bubble
    monkeypatch.setattr(bubble, "FADE_MS", 35)
    monkeypatch.setattr(bubble, "MIN_PAUSE_MS", 25)
    monkeypatch.setattr(bubble, "MAX_PAUSE_MS", 25)
    monkeypatch.setattr(chat, "_pointer_in_chat", lambda: False)
    start_speech(chat)
    chat._last_interaction = time.monotonic() - 100
    chat._check_auto_hide()
    assert chat.isVisible() and bubble.isVisible()
    QTest.qWait(450)
    assert not bubble.playing and len(bubble.speech_cards) == 3
    assert time.monotonic() - chat._last_interaction < 1


def test_dismissal_stops_pending_bubbles_and_reopen_shows_whole_reply(chat):
    bubble = start_speech(chat)
    bubble.dismiss()
    QTest.qWait(800)
    assert not bubble.playing and not bubble.isVisible()
    assert len(bubble.speech_cards) == 1
    chat.refresh_transcript()
    assert bubble.isVisible() and len(bubble.speech_cards) == 3
    assert not bubble.playing


def test_new_conversation_and_browsing_cannot_resurrect_old_playback(chat):
    bubble = start_speech(chat)
    chat.browse_message(-1)
    assert not bubble.playing and chat.transcript.toPlainText() == "聊聊吧"
    QTest.qWait(700)
    assert chat.transcript.toPlainText() == "聊聊吧"
    start_speech(chat)
    chat.new_conversation()
    QTest.qWait(700)
    assert not bubble.isVisible() and not bubble.playing and not chat.history


def test_new_user_message_interrupts_playback_but_preserves_full_model_history(chat):
    bubble = start_speech(chat)
    entered, gate = threading.Event(), threading.Event()
    received = []

    def answer(text, history, cancelled, **kwargs):
        received.extend(history)
        entered.set()
        gate.wait(2)
        return ChatReply("新回复。")

    chat.agent.answer = answer
    chat.input.setPlainText("换个话题吧")
    chat.submit()
    assert entered.wait(1)
    assert not bubble.playing and "最后一句。" in received[-1][1]
    chat.cancel_request()
    gate.set()


def test_many_bubbles_scroll_within_screen_height_and_keep_safe_text_rendering(chat):
    bubble = chat.reply_bubble
    parts = tuple(f"第 {i} 段，一个完整的小故事。" for i in range(20))
    bubble.set_sequence(parts, animate=False, max_height=300)
    bubble.show()
    QTest.qWait(30)
    assert bubble.height() <= 300 and len(bubble.speech_cards) == 20
    assert bubble.scroll.verticalScrollBar().maximum() > 0
    from PySide6.QtCore import QUrl
    assert bubble.speech_texts[0].loadResource(2, QUrl("file:///private.png")) is None
