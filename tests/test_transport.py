import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ameath.agent import Agent, AgentError
from ameath.config import Config
from ameath.models import Question


@pytest.fixture
def service():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("Authorization"), body))
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "/unexpected")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            structured = body.get("response_format") == {"type": "json_object"} or body.get("text", {}).get("format") == {"type": "json_object"}
            content = json.dumps({"reply": "太好了！", "emotion": "excited", "memory_updates": [{"key": "饮品", "value": "喜欢红茶"}]}, ensure_ascii=False) if structured else "兼容接口回答"
            if self.path.endswith("/chat/completions"):
                data = {"choices": [{"message": {"content": content}}]}
            else:
                data = {"output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}]}
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize("protocol,route", [("responses", "/v1/responses"), ("chat", "/v1/chat/completions")])
def test_real_http_serialization_to_local_stub(tmp_path, service, protocol, route):
    base, received = service
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.protocol = protocol
    config.settings.endpoint = base + "/v1"
    config.set_key("test-token", False)
    answer = Agent(config).answer(Question("解释概念"), threading.Event())
    assert "回答" in answer.text
    assert received[0][0] == route
    assert received[0][1] == "Bearer test-token"
    assert "test-token" not in json.dumps(received[0][2])


def test_redirect_does_not_forward_credentials(tmp_path, service):
    base, received = service
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.endpoint = base + "/redirect"
    config.set_key("secret", False)
    with pytest.raises(AgentError, match="302"):
        Agent(config).answer(Question("hello"), threading.Event())
    assert len(received) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_chat_reply_expression_and_memory_cross_the_actual_http_boundary(tmp_path, service, protocol):
    from ameath.chat import ChatAgent
    base, received = service
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.protocol = protocol
    config.settings.endpoint = base
    reply = ChatAgent(config).answer("我喜欢红茶", [], threading.Event())
    assert reply.text == "太好了！" and reply.action == "jumping"
    assert reply.memories == (("饮品", "喜欢红茶"),)
    assert len(received) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows account-bound encryption")
def test_dpapi_key_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("AMEATH_API_KEY", raising=False)
    config = Config(tmp_path)
    config.set_key("test-encrypted-key", True)
    assert b"test-encrypted-key" not in config.key_path.read_bytes()
    assert Config(tmp_path).key() == "test-encrypted-key"
