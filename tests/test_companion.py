import json
import threading
import time
from datetime import datetime

import pytest
from PySide6.QtTest import QTest

from ameath.chat import ChatAgent
from ameath.chat_ui import ChatDialog
from ameath.companion import (COOLDOWN, QUIET_SECONDS, RETURN_GAP, SHARE_AFTER_CHAT, CompanionPolicy,
                              ProactiveCompanion, asks_for_quiet)
from ameath.config import Config
from ameath.llm import Completion
from test_ui import app

AFTERNOON = datetime(2026, 10, 27, 15, 0)
NIGHT = datetime(2026, 10, 27, 23, 30)


@pytest.mark.parametrize("text", ["让我安静一会儿", "你先安静点", "别说话了", "现在别打扰我", "少说两句", "先别找我"])
def test_explicit_quiet_requests_are_recognized(text):
    assert asks_for_quiet(text)


@pytest.mark.parametrize("text", ["我喜欢安静的地方", "图书馆很安静", "我不想被别人打扰的时候会戴耳机"])
def test_descriptions_of_quiet_are_not_requests(text):
    assert not asks_for_quiet(text)


def test_policy_stays_quiet_unless_every_rule_allows_speaking():
    policy = CompanionPolicy(enabled=False, last_chat=0, last_interaction=0)
    now = SHARE_AFTER_CHAT + 1
    assert not policy.share_due(now, AFTERNOON)
    policy.enabled = True
    assert policy.share_due(now, AFTERNOON)
    assert not policy.share_due(now, NIGHT)
    policy.note_chat("让我安静一会儿", now)
    assert policy.quiet_until == now + QUIET_SECONDS
    assert not policy.allowed(now + QUIET_SECONDS - 1, AFTERNOON)
    later = now + QUIET_SECONDS + SHARE_AFTER_CHAT
    policy.last_interaction = later
    assert policy.share_due(later, AFTERNOON)
    policy.spoke(later, AFTERNOON.date())
    assert not policy.allowed(later + COOLDOWN - 1, AFTERNOON) and policy.allowed(later + COOLDOWN, AFTERNOON)


def test_policy_has_daily_cap_and_requires_recent_presence_for_sharing():
    policy = CompanionPolicy(enabled=True, daily_limit=2, last_chat=0, last_interaction=0)
    assert not policy.share_due(10 * 3600, AFTERNOON)  # Nobody has touched her for hours.
    now = 0.0
    for _ in range(2):
        now += COOLDOWN
        policy.spoke(now, AFTERNOON.date())
    assert not policy.allowed(now + COOLDOWN, AFTERNOON)
    assert policy.allowed(now + COOLDOWN, datetime(2026, 10, 28, 15, 0))
    assert policy.return_due(RETURN_GAP, now + COOLDOWN, datetime(2026, 10, 28, 9, 0))
    assert not policy.return_due(RETURN_GAP - 1, now + COOLDOWN, datetime(2026, 10, 28, 9, 0))


class Client:
    def __init__(self, text='{"reply":["回来啦。","上次说的答辩怎么样了？"],"emotion":"playful","memory_updates":[{"key":"k","value":"v"}]}'):
        self.text, self.payloads = text, []

    def request(self, payload, cancelled, **kwargs):
        self.payloads.append(payload)
        return Completion(self.text, {}, "test")


def test_proactive_reply_uses_episodes_and_never_flies_or_writes_memory(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    agent = ChatAgent(config)
    agent.client = Client()
    agent.memory.add_episodes(["用户在准备答辩。"], [])
    reply = agent.proactive("return", RETURN_GAP, [], threading.Event())
    assert reply.segments == ("回来啦。", "上次说的答辩怎么样了？")
    assert reply.emotion == "happy" and reply.memories == ()
    payload = json.dumps(agent.client.payloads[0], ensure_ascii=False)
    assert "用户在准备答辩" in payload and "不是用户说的话" in payload
    agent.client = Client('{"reply":[],"emotion":"neutral","memory_updates":[]}')
    assert agent.proactive("share", SHARE_AFTER_CHAT, [], threading.Event()) is None
    config.settings.mode = "demo"
    assert agent.proactive("share", SHARE_AFTER_CHAT, [], threading.Event()) is None


class Pet:
    transition = None
    state = "idle"
    is_moving = False

    def __init__(self):
        self.responses = []

    def respond(self, text, action, show_bubble=True):
        self.responses.append(action)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def companion(app, tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.proactive_enabled = True
    chat = ChatDialog(config)
    chat.agent.client = Client()
    clock = Clock()
    pet = Pet()
    controller = ProactiveCompanion(config, pet, chat, wall=lambda: AFTERNOON, clock=clock)
    yield controller, chat, pet, clock
    controller.shutdown()
    chat.shutdown()
    chat.deleteLater()
    app.processEvents()


def settle(controller, seconds=2):
    deadline = time.monotonic() + seconds
    while controller.pending and time.monotonic() < deadline:
        QTest.qWait(20)


def test_returning_user_gets_a_greeting_shown_as_a_proactive_message(companion):
    controller, chat, pet, clock = companion
    clock.now += RETURN_GAP
    controller.on_interaction()
    settle(controller)
    assert chat.messages[-1] == ("assistant", "回来啦。\n\n上次说的答辩怎么样了？")
    assert pet.responses == ["waving"] and controller.policy.spoken_count == 1
    clock.now += 60
    controller.on_interaction()  # A short gap is not a return.
    assert not controller.pending


def test_user_message_or_typing_cancels_a_prepared_line(companion):
    controller, chat, pet, clock = companion
    clock.now += RETURN_GAP
    controller.on_interaction()
    controller.on_user_message("在吗")
    settle(controller)
    assert not chat.messages
    clock.now += COOLDOWN + RETURN_GAP
    controller.on_interaction()
    chat.input.setPlainText("我正在打字")
    settle(controller)
    assert not chat.messages and controller.policy.spoken_count == 0


def test_idle_share_waits_for_quiet_tasks_and_respects_disable(companion):
    controller, chat, pet, clock = companion
    chat.hide()
    clock.now += SHARE_AFTER_CHAT
    controller.policy.last_interaction = clock.now
    controller.blocked = lambda: True  # e.g. a focus session is running
    controller.tick()
    assert not controller.pending
    controller.blocked = lambda: False
    controller.quiet()
    controller.tick()
    assert not controller.pending
    controller.policy.quiet_until = 0
    controller.tick()
    settle(controller)
    assert chat.messages and controller.policy.spoken_count == 1
    controller.set_enabled(False)
    assert not Config(chat.config.directory).settings.proactive_enabled
    clock.now += COOLDOWN + SHARE_AFTER_CHAT
    controller.policy.last_interaction = clock.now
    controller.tick()
    assert not controller.pending


def test_pet_menu_exposes_proactive_toggle_and_quiet(app):
    from ameath.pet import DesktopPet
    pet = DesktopPet(None)
    received = []
    pet.proactive_changed.connect(received.append)
    pet.quiet_requested.connect(lambda: received.append("quiet"))
    menu = pet.build_context_menu()
    actions = {action.text(): action for action in menu.actions()}
    actions["允许主动找我聊"].trigger()
    actions["安静两小时"].trigger()
    assert received == [True, "quiet"]
    menu.deleteLater()
    pet.deleteLater()
