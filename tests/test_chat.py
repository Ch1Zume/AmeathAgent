import json
import threading
import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtTest import QTest

from ameath.chat import ChatAgent, ChatReply, build_chat_payload, parse_chat_reply
from ameath.chat_ui import ChatDialog
from ameath.companion_ui import CompanionDialog
from ameath.config import Config, Settings
from ameath.llm import Completion, ModelError
from ameath.memory import CompanionMemory, Persona, PersonaCard, memory_updates
from test_ui import app, window


def test_chat_has_plain_conversation_history_without_paper_or_game_material():
    history = [("system", "discard this"), ("user", "我喜欢喝茶"), ("assistant", "喜欢哪一种？")]
    payload = build_chat_payload("红茶", history, Settings())
    assert "response_format" not in payload  # DeepSeek empty-final compatibility.
    assert [message["role"] for message in payload["messages"]] == ["system", "user", "assistant", "user"]
    assert payload["messages"][-1]["content"] == "红茶"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "discard this" not in serialized
    assert '"sources"' not in serialized and '"provided_choices"' not in serialized
    assert "没有通用联网搜索" in serialized
    assert "天气问候" in serialized and "重新查询天气的工具" in serialized


def test_responses_chat_has_json_format_and_does_not_store_server_history():
    payload = build_chat_payload("你好", [], Settings(protocol="responses"))
    assert payload["store"] is False
    assert "text" not in payload
    assert payload["input"] == [{"role": "user", "content": "你好"}]


@pytest.mark.parametrize("emotion,action", [("greeting", "waving"), ("excited", "jumping"),
                                         ("comforting", "waiting"), ("thoughtful", "review"), ("sad", "failed"),
                                         ("sleepy", "sleeping"), ("relaxed", "sitting"), ("playful", "flight")])
def test_reply_selects_only_the_matching_local_sprite_action(emotion, action):
    reply = parse_chat_reply(json.dumps({"reply": "一条回复", "emotion": emotion}))
    assert reply.text == "一条回复" and reply.action == action


def test_unknown_action_cannot_move_the_pet_or_run_a_command():
    reply = parse_chat_reply('{"reply":"Hello","emotion":"run_command","command":"anything"}')
    assert reply.emotion == "neutral" and reply.action == "idle"
    assert parse_chat_reply("普通兼容接口回复").text == "普通兼容接口回复"
    assert parse_chat_reply('```json\n{"reply":"你好","emotion":"happy"}\n```').action == "waving"
    with pytest.raises(ModelError):
        parse_chat_reply('{"reply":')


def test_persona_round_trips_and_invalid_cards_leave_existing_card_intact(tmp_path):
    card = PersonaCard(tmp_path)
    persona = replace(Persona(), speaking_style="用简洁的中文，偶尔幽默。")
    card.save(persona)
    assert PersonaCard(tmp_path).load() == persona
    imported = tmp_path / "import.json"
    imported.write_text('{"version": 99, "name":"other"}')
    with pytest.raises(ValueError):
        PersonaCard.read(imported)
    assert card.load() == persona


def test_memory_survives_restart_can_be_edited_and_deleted_and_prefers_relevant_facts(tmp_path):
    book = CompanionMemory(tmp_path)
    book.remember("喜欢的游戏", "杀戮尖塔 2")
    book.remember("饮食偏好", "喜欢红茶")
    restarted = CompanionMemory(tmp_path)
    assert len(restarted.all()) == 2
    assert restarted.relevant("想聊游戏杀戮尖塔", limit=1)[0]["key"] == "喜欢的游戏"
    tea = next(item for item in restarted.all() if item["key"] == "饮食偏好")
    restarted.remember("喝茶偏好", "喜欢绿茶", identifier=tea["id"])
    assert len(restarted.all()) == 2
    assert next(item for item in restarted.all() if item["id"] == tea["id"])["value"] == "喜欢绿茶"
    restarted.delete(tea["id"])
    assert len(book.all()) == 1


def test_memory_updates_are_bounded_and_credentials_are_filtered(tmp_path):
    updates = memory_updates([{"key": "饮品", "value": "喜欢红茶"},
                              {"key": "API key", "value": "synthetic test value"},
                              {"key": "称呼", "value": ["invalid"]},
                              {"key": "extra", "value": "over limit"}])
    assert updates == (("饮品", "喜欢红茶"),)
    with pytest.raises(ValueError):
        CompanionMemory(tmp_path).remember("密码", "synthetic value")


def test_chat_uses_the_saved_persona_and_relevant_memory_through_the_shared_model_core(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    agent = ChatAgent(config)
    assert agent.client.__class__.__name__ == "ModelClient"
    agent.persona.save(replace(Persona(), speaking_style="回答时先给结论。"))
    agent.memory.remember("饮品偏好", "喜欢红茶")
    captured = []

    class Client:
        def request(self, payload, cancelled, **kwargs):
            captured.append(payload)
            return Completion('{"reply":"你之前说过喜欢红茶。","emotion":"thoughtful"}', {}, "test")

    agent.client = Client()
    assert agent.answer("我喜欢喝什么？", [], threading.Event()).action == "review"
    serialized = json.dumps(captured[0], ensure_ascii=False)
    assert "先给结论" in serialized and "喜欢红茶" in serialized
    config.settings.use_companion_context = False
    agent.answer("你好", [], threading.Event())
    serialized = json.dumps(captured[1], ensure_ascii=False)
    assert "先给结论" not in serialized and "喜欢红茶" not in serialized


@pytest.fixture
def chat(app, tmp_path):
    dialog = ChatDialog(Config(tmp_path))
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.shutdown()
    dialog.deleteLater()
    app.processEvents()


def send_and_wait(chat, text):
    chat.input.setPlainText(text)
    chat.submit()
    deadline = time.monotonic() + 3
    while chat.busy and time.monotonic() < deadline:
        QTest.qWait(20)
    assert not chat.busy and chat.last_reply is not None


def test_pet_single_click_opens_chat_without_showing_the_app_and_right_click_menu_opens_app(window, app):
    window.hide()
    window.pet.show()
    app.processEvents()
    QTest.mouseClick(window.pet, Qt.MouseButton.LeftButton, pos=QPoint(110, 160))
    assert window.chat.isVisible() and not window.isVisible()
    menu = window.pet.build_context_menu()
    next(action for action in menu.actions() if action.text() == "打开 Ameath").trigger()
    assert window.isVisible()
    menu.deleteLater()


def test_reply_animates_pet_and_keeps_a_separate_conversation(window):
    window.open_chat()
    send_and_wait(window.chat, "我成功完成了项目！")
    assert window.chat.last_reply.emotion == "excited"
    assert window.pet.animation == "jumping"
    assert "庆祝" in window.chat.transcript.toPlainText()
    assert len(window.chat.history) == 2 and not window.history
    window.chat.hide()
    window.open_chat()
    assert len(window.chat.history) == 2


def test_demo_never_opens_network_and_explicit_notes_become_persistent_memory(chat, monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("demo attempted network"))
    send_and_wait(chat, "记住：我喜欢红茶")
    assert CompanionMemory(chat.config.directory).all()[0]["value"] == "我喜欢红茶"
    chat.new_conversation()
    assert not chat.history and not chat.messages
    assert CompanionMemory(chat.config.directory).all()
    chat.config.settings.memory_enabled = False
    send_and_wait(chat, "记住：这条不应保存")
    assert CompanionMemory(chat.config.directory).all()[0]["value"] == "我喜欢红茶"


def test_cancelled_or_closed_chat_cannot_show_late_reply_or_save_late_memory(chat, app):
    gate = threading.Event()
    entered = threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        gate.wait(2)
        return ChatReply("late", "excited", memories=(("late", "should not save"),))

    chat.agent.answer = delayed
    chat.input.setPlainText("你好")
    chat.submit()
    assert entered.wait(1)
    generation = chat.generation
    chat.hide()
    chat.on_reply(generation, "你好", ChatReply("late", "excited", memories=(("late", "no"),)))
    gate.set()
    QTest.qWait(40)
    assert not chat.busy and chat.last_reply is None
    assert not chat.agent.memory.all()


def test_memory_and_persona_editor_save_edit_delete_and_toggle_context(chat):
    editor = CompanionDialog(chat.config, chat)
    editor.persona_fields["personality"].setPlainText("好奇、坦诚。")
    editor.save_persona()
    assert chat.agent.persona.load().personality == "好奇、坦诚。"
    editor.memory_key.setText("称呼")
    editor.memory_value.setPlainText("希望被称为小明")
    editor.save_memory()
    assert editor.memory_list.count() == 1
    editor.memory_list.setCurrentRow(0)
    editor.memory_value.setPlainText("希望被称为小白")
    editor.save_memory()
    assert chat.agent.memory.all()[0]["value"] == "希望被称为小白"
    editor.memory_list.setCurrentRow(0)
    editor.delete_memory()
    assert not chat.agent.memory.all()
    editor.use_context.setChecked(False)
    assert not Config(chat.config.directory).settings.use_companion_context
    editor.close()


def test_chat_does_not_load_images_from_model_markdown(chat):
    assert chat.transcript.loadResource(2, QUrl("file:///C:/private.png")) is None
    assert chat.transcript.loadResource(2, QUrl("https://example.com/image.png")) is None


def test_disabling_persona_memory_also_drops_earlier_facts_from_future_history(chat):
    send_and_wait(chat, "记住：我喜欢红茶")
    chat.config.settings.use_companion_context = False
    chat.refresh_mode()
    assert not chat.history
    assert chat.messages and chat.agent.memory.all()


def test_chat_can_request_sleep_and_flight_and_typing_wakes_the_pet(window):
    window.open_chat()
    send_and_wait(window.chat, "晚安，你睡觉吧")
    assert window.pet.state == "sleeping"
    window.chat.input.setPlainText("飞一圈看看")
    assert window.pet.state == "idle"
    window.chat.submit()
    deadline = time.monotonic() + 3
    while window.chat.busy and time.monotonic() < deadline:
        QTest.qWait(20)
    assert window.pet.animation == "takeoff" and window.pet.is_moving


def test_chat_ui_recovers_empty_reply_once_and_shows_one_final_turn(window):
    from ameath.llm import ModelResponseError
    window.config.settings.mode = "live"
    calls = []

    class Client:
        def request(self, *args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise ModelResponseError("empty_final")
            return Completion('{"reply":"我在呢。","emotion":"greeting","memory_updates":[]}', {}, "test")

        def stream(self, payload, cancelled, on_text, **kwargs):
            return self.request(payload, cancelled, **kwargs)

    window.chat.agent.client = Client()
    window.open_chat()
    send_and_wait(window.chat, "你好")
    assert len(calls) == 2 and window.chat.last_reply.retried
    assert "自动重试 1 次" in window.chat.status.text()
    assert len(window.chat.history) == 2 and window.pet.animation == "waving"


def test_chat_auto_hides_after_pointer_leaves_and_keeps_draft_and_history(chat, monkeypatch):
    monkeypatch.setattr(chat, "_pointer_in_chat", lambda: False)
    chat.history = [("assistant", "我在这里")]
    chat.messages = list(chat.history)
    chat.refresh_transcript()
    chat.input.setPlainText("还没写完的草稿")
    chat._last_interaction = time.monotonic() - chat.AUTO_HIDE_SECONDS + 1
    chat._check_auto_hide()
    assert chat.isVisible()
    chat._last_interaction -= 2
    chat._check_auto_hide()
    assert not chat.isVisible() and not chat.reply_bubble.isVisible()
    assert chat.input.toPlainText() == "还没写完的草稿"
    assert chat.history == [("assistant", "我在这里")]
    chat.show()
    chat.refresh_transcript()
    assert chat.reply_bubble.isVisible() and chat.dismiss_timer.isActive()


@pytest.mark.parametrize("guard", ["hover", "busy", "composing", "typing"])
def test_chat_interaction_renews_auto_hide_grace_period(chat, monkeypatch, guard):
    monkeypatch.setattr(chat, "_pointer_in_chat", lambda: guard == "hover")
    chat._last_interaction = time.monotonic() - 100
    chat.busy = guard == "busy"
    chat.input.composing = guard == "composing"
    if guard == "typing":
        chat.input.setPlainText("继续输入")
    chat._check_auto_hide()
    assert chat.isVisible()
    assert time.monotonic() - chat._last_interaction < 1


def test_proactive_reply_also_hides_without_an_open_input(chat, monkeypatch):
    monkeypatch.setattr(chat, "_pointer_in_chat", lambda: False)
    chat.hide()
    chat.show_proactive("今天也陪着你。")
    assert chat.reply_bubble.isVisible() and chat.dismiss_timer.isActive()
    chat._last_interaction = time.monotonic() - 9
    chat._check_auto_hide()
    assert not chat.reply_bubble.isVisible()
    assert chat.messages[-1] == ("assistant", "今天也陪着你。")


def test_auto_hide_timer_runs_without_manual_polling(chat, monkeypatch):
    monkeypatch.setattr(chat, "_pointer_in_chat", lambda: False)
    monkeypatch.setattr(chat, "AUTO_HIDE_SECONDS", 0.05)
    QTest.qWait(350)
    assert not chat.isVisible() and not chat.dismiss_timer.isActive()
