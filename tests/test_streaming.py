import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtTest import QTest

from ameath.bubbles import ReplyBubble
from ameath.chat import ChatAgent, partial_bubbles
from ameath.chat_ui import ChatDialog
from ameath.config import Config
from ameath.llm import Completion, ModelClient, ModelError, ModelResponseError
from test_ui import app

FINAL = '{"reply":["第一段，带\\"引号\\"。","第二段。"],"emotion":"happy","memory_updates":[]}'


def test_partial_bubbles_only_returns_closed_strings():
    seen = [partial_bubbles(FINAL[:index]) for index in range(len(FINAL) + 1)]
    assert seen[0] == () and seen[-1] == ('第一段，带"引号"。', "第二段。")
    assert all(seen[index] == seen[index + 1][:len(seen[index])] for index in range(len(seen) - 1))
    assert partial_bubbles('{"reply":"整段字符串"}') == ()
    assert partial_bubbles("普通文字") == ()


def sse(chunks, finish="stop"):
    lines = [{"choices": [{"delta": {"reasoning_content": "内部思考"}}]}]
    lines += [{"choices": [{"delta": {"content": chunk}}]} for chunk in chunks]
    lines.append({"choices": [{"delta": {}, "finish_reason": finish}], "model": "local"})
    return "".join(f"data: {json.dumps(line, ensure_ascii=False)}\n\n" for line in lines) + "data: [DONE]\n\n"


@pytest.fixture
def server():
    state = {"body": "", "requests": [], "delay": 0.0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for line in state["body"].splitlines(keepends=True):
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
                time.sleep(state["delay"])

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["endpoint"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield state
    httpd.shutdown()


def local_client(tmp_path, server):
    config = Config(tmp_path)
    config.settings.endpoint = server["endpoint"]
    return ModelClient(config)


def test_stream_reads_server_sent_events_and_ignores_reasoning(tmp_path, server):
    server["body"] = sse([FINAL[:20], FINAL[20:45], FINAL[45:]])
    texts = []
    completion = local_client(tmp_path, server).stream({"model": "m", "messages": []}, threading.Event(), texts.append)
    assert completion.text == FINAL and completion.model == "local"
    assert texts[-1] == FINAL and len(texts) == 3 and "内部思考" not in "".join(texts)
    assert server["requests"][0]["stream"] is True


def test_stream_reports_truncation_and_stops_when_cancelled(tmp_path, server):
    server["body"] = sse([FINAL[:30]], finish="length")
    with pytest.raises(ModelResponseError) as error:
        local_client(tmp_path, server).stream({"model": "m", "messages": []}, threading.Event(), lambda text: None)
    assert error.value.reason == "length"
    server["body"] = sse([FINAL[:10], FINAL[10:30], FINAL[30:]])
    cancelled = threading.Event()
    texts = []

    def cancel_after_first(text):
        texts.append(text)
        cancelled.set()

    with pytest.raises(ModelError, match="取消"):
        local_client(tmp_path, server).stream({"model": "m", "messages": []}, cancelled, cancel_after_first)
    assert len(texts) == 1


class StreamingClient:
    def __init__(self, final=FINAL, gate=None):
        self.final, self.gate, self.partials = final, gate, []

    def stream(self, payload, cancelled, on_text, **kwargs):
        for index in range(1, len(self.final) + 1):
            on_text(self.final[:index])
            if self.gate is not None and partial_bubbles(self.final[:index]):
                self.gate.wait(3)
                self.gate = None
        return Completion(self.final, {}, "test")

    def request(self, payload, cancelled, **kwargs):
        return Completion(self.final, {}, "test")


def test_agent_reports_each_new_bubble_once(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    agent = ChatAgent(config)
    agent.client = StreamingClient()
    partials = []
    reply = agent.answer("你好", [], threading.Event(), on_partial=partials.append)
    assert partials == [('第一段，带"引号"。',), ('第一段，带"引号"。', "第二段。")]
    assert reply.segments == partials[-1]
    config.settings.stream_chat = False
    partials.clear()
    agent.answer("你好", [], threading.Event(), on_partial=partials.append)
    assert not partials


def test_bubble_waits_for_more_while_streaming_and_finishes_once_final(app):
    bubble = ReplyBubble()
    finished = []
    bubble.playback_finished.connect(lambda: finished.append(True))
    bubble.set_sequence(("一",), animate=True, streaming=True)
    QTest.qWait(ReplyBubble.FADE_MS + 100)
    assert bubble.playing and not finished and len(bubble.speech_cards) == 1
    assert not bubble.extend_sequence(("别的",))
    assert bubble.extend_sequence(("一", "二"))
    QTest.qWait(ReplyBubble.FADE_MS + 100)
    assert len(bubble.speech_cards) == 2 and bubble.playing
    assert bubble.extend_sequence(("一", "二"), final=True)
    assert finished == [True] and not bubble.playing
    bubble.close()


@pytest.fixture
def live_chat(app, tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.session_memory = False
    dialog = ChatDialog(config)
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.shutdown()
    dialog.deleteLater()
    app.processEvents()


def test_first_bubble_appears_before_the_reply_completes_and_is_not_replayed(live_chat):
    gate = threading.Event()
    live_chat.agent.client = StreamingClient(gate=gate)
    live_chat.input.setPlainText("你好")
    live_chat.submit()
    deadline = time.monotonic() + 2
    while not live_chat.reply_bubble.speech_cards and time.monotonic() < deadline:
        QTest.qWait(20)
    assert live_chat.busy and live_chat.streamed
    first = live_chat.reply_bubble.speech_cards[0]
    gate.set()
    while live_chat.busy and time.monotonic() < deadline + 2:
        QTest.qWait(20)
    assert live_chat.reply_bubble.speech_cards[0] is first
    assert live_chat.history[-1] == ("assistant", '第一段，带"引号"。\n\n第二段。')
    live_chat.reply_bubble.finish_playback()
    assert len(live_chat.reply_bubble.speech_cards) == 2


def test_cancelled_stream_leaves_no_late_bubbles_or_history(live_chat):
    gate = threading.Event()
    live_chat.agent.client = StreamingClient(gate=gate)
    live_chat.input.setPlainText("你好")
    live_chat.submit()
    deadline = time.monotonic() + 2
    while not live_chat.streamed and time.monotonic() < deadline:
        QTest.qWait(20)
    live_chat.cancel_request()
    gate.set()
    QTest.qWait(150)
    assert not live_chat.busy and not live_chat.history and not live_chat.streamed
    assert live_chat.messages[-1] == ("user", "你好")
