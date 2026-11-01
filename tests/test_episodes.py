import json
import threading
import time

import pytest
from PySide6.QtTest import QTest

from ameath.chat import ChatAgent, build_chat_payload, parse_episodes
from ameath.chat_ui import ChatDialog
from ameath.companion_ui import CompanionDialog
from ameath.config import Config, Settings
from ameath.llm import Completion, ModelError
from ameath.memory import MAX_LIFE_THREADS, MAX_PENDING_TURNS, CompanionMemory
from test_ui import app

SUMMARY = json.dumps({"shared": ["用户说这周在准备论文答辩，我们聊了紧张的感觉。"],
                      "life": [{"topic": "改曲子结尾", "summary": "最后一个音太神气，舍不得改回去。"}]},
                     ensure_ascii=False)


class Client:
    def __init__(self, text=SUMMARY, error=None):
        self.text, self.error, self.payloads = text, error, []

    def request(self, payload, cancelled, **kwargs):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        if "turns" in json.dumps(payload, ensure_ascii=False):
            return Completion(self.text, {}, "test")
        return Completion('{"reply":["收到。"],"emotion":"neutral","memory_updates":[]}', {}, "test")

    def stream(self, payload, cancelled, on_text, **kwargs):
        return self.request(payload, cancelled, **kwargs)


def live_agent(tmp_path, client=None):
    config = Config(tmp_path)
    config.settings.mode = "live"
    agent = ChatAgent(config)
    agent.client = client or Client()
    return agent


def test_pending_turns_are_bounded_and_skip_credentials(tmp_path):
    book = CompanionMemory(tmp_path)
    book.log_turn("我的密码是 synthetic", "好的")
    assert not book.pending_turns()
    for index in range(MAX_PENDING_TURNS + 5):
        book.log_turn(f"第 {index} 句", "嗯")
    turns = book.pending_turns()
    assert len(turns) == MAX_PENDING_TURNS and turns[-1]["user"] == f"第 {MAX_PENDING_TURNS + 4} 句"
    book.clear_pending(turns[9]["id"])
    assert len(book.pending_turns()) == MAX_PENDING_TURNS - 10


def test_life_threads_update_by_topic_and_survive_restart(tmp_path):
    book = CompanionMemory(tmp_path)
    book.add_episodes(["我们聊了夜晚。", "密钥是 synthetic"], [("改曲子结尾", "卡在最后一个音。")])
    book.add_episodes([], [("改曲子结尾", "最后一个音改好了，很神气。")])
    restarted = CompanionMemory(tmp_path)
    life = restarted.episodes("life")
    assert len(life) == 1 and "改好了" in life[0]["summary"]
    assert [item["summary"] for item in restarted.episodes("shared")] == ["我们聊了夜晚。"]
    restarted.add_episodes([], [(f"话题{index}", "内容") for index in range(MAX_LIFE_THREADS + 3)])
    assert len(restarted.episodes("life")) == MAX_LIFE_THREADS
    restarted.clear_episodes()
    assert not restarted.episodes()


def test_episode_context_keeps_recent_and_related_older_conversations(tmp_path):
    book = CompanionMemory(tmp_path)
    book.add_episodes(["用户在玩杀戮尖塔，卡在第三幕。"], [], when="2026-10-01T00:00:00+00:00")
    for index in range(8):
        book.add_episodes([f"闲聊第 {index} 次。"], [], when=f"2026-10-{10 + index}T00:00:00+00:00")
    context = book.episode_context("尖塔第三幕又输了")
    summaries = [item["summary"] for item in context["shared"]]
    assert summaries[0] == "用户在玩杀戮尖塔，卡在第三幕。" and len(summaries) == 5
    assert "闲聊第 7 次。" in summaries and context["shared"][0]["date"] == "2026-10-01"


def test_parse_episodes_rejects_broken_output_and_bounds_items():
    shared, life = parse_episodes(json.dumps({"shared": ["a", "", 3, "b", "c", "d"],
                                              "life": [{"topic": "t", "summary": "s"}, "bad",
                                                       {"topic": "u", "summary": "v", "status": "closed"}]}))
    assert shared == ["a", "b", "c"] and life == [("t", "s", "open"), ("u", "v", "closed")]
    with pytest.raises(ModelError):
        parse_episodes('{"shared": [')


def test_consolidation_summarizes_old_turns_and_deletes_only_their_text(tmp_path):
    client = Client()
    agent = live_agent(tmp_path, client)
    agent.memory.add_episodes([], [("改曲子结尾", "卡在最后一个音。")])
    for index in range(8):
        agent.memory.log_turn(f"消息 {index}", f"回复 {index}")
    assert agent.consolidate(threading.Event(), keep_recent=6) == 2
    material = json.dumps(client.payloads[0], ensure_ascii=False)
    assert "消息 1" in material and "消息 2" not in material and "卡在最后一个音" in material
    assert [turn["user"] for turn in agent.memory.pending_turns()] == [f"消息 {index}" for index in range(2, 8)]
    assert agent.memory.episodes("shared")[0]["summary"].startswith("用户说这周")
    assert "舍不得" in agent.memory.episodes("life")[0]["summary"]


def test_failed_cancelled_or_demo_consolidation_keeps_pending_turns(tmp_path):
    agent = live_agent(tmp_path, Client(error=ModelError("offline")))
    agent.memory.log_turn("你好", "你好呀")
    with pytest.raises(ModelError):
        agent.consolidate(threading.Event())
    agent.client = Client(text="不是 JSON")
    with pytest.raises(ModelError):
        agent.consolidate(threading.Event())
    cancelled = threading.Event()
    cancelled.set()
    agent.client = Client()
    assert agent.consolidate(cancelled) == 0
    agent.config.settings.mode = "demo"
    assert agent.consolidate(threading.Event()) == 0
    assert len(agent.memory.pending_turns()) == 1 and not agent.memory.episodes()


def test_chat_payload_separates_real_episodes_from_character_stories(tmp_path):
    agent = live_agent(tmp_path)
    agent.memory.add_episodes(["用户在准备答辩。"], [("改曲子结尾", "最后一个音很神气。")])
    agent.answer("你的曲子改完了吗", [], threading.Event())
    payload = json.dumps(agent.client.payloads[-1], ensure_ascii=False)
    assert "用户在准备答辩" in payload and "最后一个音很神气" in payload and "角色创作" in payload
    for setting in ("session_memory", "use_companion_context"):
        setattr(agent.config.settings, setting, False)
        agent.answer("最近怎么样", [], threading.Event())
        assert "用户在准备答辩" not in json.dumps(agent.client.payloads[-1], ensure_ascii=False)
        setattr(agent.config.settings, setting, True)
    assert "往日资料" not in json.dumps(build_chat_payload("你好", [], Settings()), ensure_ascii=False)


@pytest.fixture
def live_chat(app, tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    dialog = ChatDialog(config)
    dialog.agent.client = Client()
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.shutdown()
    dialog.deleteLater()
    app.processEvents()


def wait_for(condition, seconds=3):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(20)
    return condition()


def send(chat, text):
    chat.input.setPlainText(text)
    chat.submit()
    assert wait_for(lambda: not chat.busy)


def test_replies_are_queued_folded_after_the_live_window_and_on_new_conversation(live_chat):
    memory = live_chat.agent.memory
    for index in range(ChatDialog.EPISODE_BATCH_ROUNDS - 1):
        memory.log_turn(f"旧消息 {index}", "嗯")
    send(live_chat, "第十二句")
    assert wait_for(lambda: len(memory.pending_turns()) == ChatDialog.LIVE_ROUNDS)
    assert memory.pending_turns()[-1]["user"] == "第十二句" and memory.episodes("shared")
    live_chat.new_conversation()
    assert wait_for(lambda: not memory.pending_turns())


def test_late_or_proactive_replies_are_never_queued(live_chat):
    from ameath.chat import ChatReply
    live_chat.show_proactive("今天有点冷。")
    live_chat.on_reply(live_chat.generation + 1, "旧问题", ChatReply("迟到的回复"))
    assert not live_chat.agent.memory.pending_turns()


def test_leftover_turns_are_folded_when_the_next_session_starts(app, tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    CompanionMemory(tmp_path).log_turn("昨天的话", "嗯")
    original = ChatAgent.__init__

    def with_fake_client(self, config):
        original(self, config)
        self.client = Client()

    ChatAgent.__init__ = with_fake_client
    try:
        dialog = ChatDialog(config)
    finally:
        ChatAgent.__init__ = original
    assert wait_for(lambda: not dialog.agent.memory.pending_turns())
    assert dialog.agent.memory.episodes("shared")
    dialog.shutdown()
    dialog.deleteLater()


def test_editor_lists_deletes_and_opting_out_discards_unsummarized_text(live_chat):
    memory = live_chat.agent.memory
    memory.add_episodes(["用户在准备答辩。"], [("改曲子结尾", "最后一个音很神气。")])
    memory.log_turn("还没整理的话", "嗯")
    editor = CompanionDialog(live_chat.config, live_chat)
    assert editor.episode_list.count() == 2
    editor.episode_list.setCurrentRow(0)
    editor.delete_episode()
    assert editor.episode_list.count() == 1 and len(memory.episodes()) == 1
    editor.session_memory.setChecked(False)
    assert not memory.pending_turns() and not Config(live_chat.config.directory).settings.session_memory
    send(live_chat, "关掉以后")
    assert not memory.pending_turns()
    editor.clear_episodes()
    assert not memory.episodes()
    editor.close()


@pytest.fixture
def shanghai(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_old_databases_gain_thread_status_without_losing_rows(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / "companion.sqlite3") as db:
        db.execute("CREATE TABLE episodes (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, topic TEXT NOT NULL, summary TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)")
        db.execute("INSERT INTO episodes (kind,topic,summary,created,updated) VALUES ('life','改曲子结尾','卡在最后一个音。','2026-10-27T00:00:00+00:00','2026-10-27T00:00:00+00:00')")
    book = CompanionMemory(tmp_path)
    thread = book.episodes("life")[0]
    assert thread["status"] == "open" and thread["last_mentioned"] == ""
    assert book.share_thread()["topic"] == "改曲子结尾"


def test_unrelated_character_stories_stay_out_and_todays_topics_are_named(tmp_path):
    book = CompanionMemory(tmp_path)
    book.add_episodes([], [("改曲子结尾", "最后一个音太神气，舍不得改回去。")], mentioned="2026-10-01T00:00:00+00:00")
    book.add_episodes([], [("挑战旧纪录", "差一步，最后那个岔路选错了。", "closed")], mentioned="2026-10-01T00:00:00+00:00")
    context = book.episode_context("帮我看看这封邮件的最后一个段落")
    assert context["life"] == [] and context["mentioned_today"] == []
    context = book.episode_context("你那首曲子后来怎么样了")
    assert [item["topic"] for item in context["life"]] == ["改曲子结尾"]
    assert context["life"][0]["status"] == "进行中"
    assert book.episode_context("旧纪录破了吗")["life"][0]["status"] == "告一段落"
    assert book.note_mentions("今天先不改曲子了，那个结尾留到明天。") == 1
    assert book.episode_context("")["mentioned_today"] == ["改曲子结尾"]


def test_share_picks_the_longest_unmentioned_open_thread_and_skips_today(tmp_path):
    book = CompanionMemory(tmp_path)
    book.add_episodes([], [("改曲子结尾", "卡在最后一个音。")], mentioned="2026-10-20T00:00:00+00:00")
    book.add_episodes([], [("折纸飞机", "翼角改了三次。")], mentioned="2026-10-10T00:00:00+00:00")
    book.add_episodes([], [("整理卡带", "排好了。", "closed")], mentioned="2026-10-01T00:00:00+00:00")
    assert book.share_thread()["topic"] == "折纸飞机"
    book.mark_mentioned(book.share_thread()["id"])
    assert book.share_thread()["topic"] == "改曲子结尾"
    book.mark_mentioned(book.share_thread()["id"])
    assert book.share_thread() is None


def test_consolidation_records_mention_time_and_closed_threads(tmp_path, shanghai):
    summary = json.dumps({"shared": [], "life": [{"topic": "改曲子结尾", "summary": "终于改好了。", "status": "closed"}]},
                         ensure_ascii=False)
    agent = live_agent(tmp_path, Client(summary))
    agent.memory.log_turn("曲子呢", "改好啦")
    agent.consolidate(threading.Event())
    thread = agent.memory.episodes("life")[0]
    assert thread["status"] == "closed" and thread["last_mentioned"]
    assert agent.memory.share_thread() is None
    assert agent.memory.episode_context("")["mentioned_today"] == ["改曲子结尾"]


def test_dates_shown_to_the_model_use_the_local_clock(tmp_path, shanghai):
    from ameath.chat import build_episode_payload
    book = CompanionMemory(tmp_path)
    book.add_episodes(["用户说起早上的组会。"], [], when="2026-10-01T23:30:00+00:00")
    assert book.episode_context("")["shared"][0]["date"] == "2026-10-02"
    payload = build_episode_payload([{"created": "2026-10-01T23:30:00+00:00", "user": "早", "assistant": "早呀"}],
                                    [], Settings())
    assert "2026-10-02 07:30" in json.dumps(payload, ensure_ascii=False)


def test_shared_story_is_marked_only_when_the_line_is_delivered(tmp_path):
    from ameath.chat import ChatReply
    reply_text = '{"reply":["纸飞机的翼角我又改了一次。"],"emotion":"happy","memory_updates":[]}'

    class Speaker(Client):
        def request(self, payload, cancelled, **kwargs):
            self.payloads.append(payload)
            return Completion(reply_text, {}, "test")

    agent = live_agent(tmp_path, Speaker())
    agent.memory.add_episodes([], [("折纸飞机", "翼角改了三次。")], mentioned="2026-10-10T00:00:00+00:00")
    agent.memory.add_episodes([], [("改曲子结尾", "卡在最后一个音。")], mentioned="2026-10-20T00:00:00+00:00")
    reply = agent.proactive("share", 1800, [], threading.Event())
    payload = json.dumps(agent.client.payloads[-1], ensure_ascii=False)
    assert "翼角改了三次" in payload and "卡在最后一个音" not in payload
    assert isinstance(reply, ChatReply) and reply.thread == agent.memory.share_thread()["id"]
    assert agent.memory.episode_context("")["mentioned_today"] == []  # Not delivered yet.


def test_delivered_replies_mark_the_stories_they_talk_about(live_chat):
    from ameath.chat import ChatReply
    memory = live_chat.agent.memory
    memory.add_episodes([], [("改曲子结尾", "卡在最后一个音。")], mentioned="2026-10-01T00:00:00+00:00")
    live_chat.on_reply(live_chat.generation + 1, "旧问题", ChatReply("曲子的结尾我改好啦。"))
    assert memory.episode_context("")["mentioned_today"] == []
    live_chat.show_proactive("早呀，曲子的结尾还差一点。")
    assert memory.episode_context("")["mentioned_today"] == ["改曲子结尾"]
