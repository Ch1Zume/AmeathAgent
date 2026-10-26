import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ameath.chat import ChatAgent, ChatFormatError, parse_chat_reply
from ameath.config import Config, Settings
from ameath.llm import Completion, ModelError, ModelResponseError, extract_text, tune_payload


@pytest.mark.parametrize("data,protocol,reason", [
    ({"choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning_content": "private reasoning"}}]}, "chat", "empty_final"),
    ({"choices": [{"finish_reason": "length", "message": {"content": '{"reply":"partial'}}]}, "chat", "length"),
    ({"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]}, "chat", "content_filter"),
    ({"choices": [{"finish_reason": "aborted", "message": {"content": "unfinished"}}]}, "chat", "interrupted"),
    ({"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{}]}}]}, "chat", "tool_calls"),
    ({"output": []}, "chat", "invalid_shape"),
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []}, "responses", "length"),
    ({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": []}, "responses", "content_filter"),
    ({"status": "completed", "output": [{"type": "reasoning", "content": [{"type": "reasoning_text", "text": "private reasoning"}]}]}, "responses", "empty_final"),
])
def test_response_failures_are_classified_and_reasoning_is_not_shown(data, protocol, reason):
    with pytest.raises(ModelResponseError) as caught:
        extract_text(data, protocol)
    assert caught.value.reason == reason
    assert "private reasoning" not in str(caught.value)


def test_compatible_text_blocks_can_be_read_without_exposing_other_parts():
    data = {"choices": [{"message": {"content": [{"type": "reasoning", "text": "private"},
              {"type": "text", "text": "hello"}, {"type": "output_text", "text": "world"}]}}]}
    assert extract_text(data, "chat") == "hello\n\nworld"


def test_responses_thinking_is_explicit_instead_of_falling_back_to_provider_default():
    settings = Settings(protocol="responses", thinking=False)
    assert tune_payload({}, settings)["reasoning"] == {"effort": "none"}
    assert tune_payload({}, settings, thinking=True)["reasoning"] == {"effort": "high"}
    settings.protocol = "chat"
    result = tune_payload({"reasoning_effort": "high"}, settings, thinking=False)
    assert result["thinking"] == {"type": "disabled"} and "reasoning_effort" not in result


@pytest.fixture
def response_service():
    received, responses = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            status, body = responses[min(len(received) - 1, len(responses) - 1)]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received, responses
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_empty_json_response_recovers_once_over_actual_http_without_logging_private_text(tmp_path, response_service):
    endpoint, received, responses = response_service
    responses.extend([(200, {"choices": [{"finish_reason": "stop", "message": {"content": " ", "reasoning_content": "hidden-thought-sentinel"}}]}),
                      (200, {"choices": [{"finish_reason": "stop", "message": {"content": '{"reply":"reply-sentinel","emotion":"greeting"}'}}]})])
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.endpoint = endpoint
    config.set_key("key-sentinel", False)
    progress = []
    reply = ChatAgent(config).answer("prompt-sentinel", [], threading.Event(), on_retry=progress.append)
    assert reply.text == "reply-sentinel" and reply.retried and reply.action == "waving"
    assert len(received) == 2 and len(progress) == 1
    assert "response_format" in received[0] and "response_format" not in received[1]
    assert received[0]["messages"] == received[1]["messages"]
    log = (tmp_path / "model-diagnostics.jsonl").read_text()
    assert '"reason": "empty_final"' in log and '"reason": "completed"' in log
    assert all(value not in log for value in ("prompt-sentinel", "reply-sentinel", "hidden-thought-sentinel", "key-sentinel"))


def test_retries_are_bounded_to_one_and_http_errors_are_not_retried(tmp_path, response_service):
    endpoint, received, responses = response_service
    responses.append((200, {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}))
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.endpoint = endpoint
    agent = ChatAgent(config)
    with pytest.raises(ModelError, match="两次"):
        agent.answer("hello", [], threading.Event())
    assert len(received) == 2
    received.clear()
    responses[:] = [(402, {"error": {"message": "not logged"}})]
    with pytest.raises(ModelError, match="余额不足"):
        agent.answer("hello", [], threading.Event())
    assert len(received) == 1


def test_thinking_limit_retries_in_fast_mode_and_keeps_the_saved_setting(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.thinking = True
    agent = ChatAgent(config)
    calls = []

    class Client:
        def request(self, payload, cancelled, **kwargs):
            calls.append((payload, kwargs))
            if len(calls) == 1:
                raise ModelResponseError("length")
            return Completion('{"reply":"done","emotion":"neutral"}', {}, "test")

    agent.client = Client()
    assert agent.answer("hello", [], threading.Event()).retried
    assert calls[1][1]["thinking"] is False
    assert calls[1][0]["max_completion_tokens"] == 4096
    assert config.settings.thinking is True


def test_cancellation_during_retry_notification_prevents_another_request(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    agent = ChatAgent(config)
    cancelled = threading.Event()
    calls = []

    class Client:
        def request(self, *args, **kwargs):
            calls.append(True)
            raise ModelResponseError("empty_final")

    agent.client = Client()
    with pytest.raises(ModelError, match="取消"):
        agent.answer("hello", [], cancelled, on_retry=lambda message: cancelled.set())
    assert len(calls) == 1


def test_responses_retry_removes_json_mode_and_respects_cancellation_settings(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    config.settings.protocol = "responses"
    config.settings.thinking = True
    agent = ChatAgent(config)
    calls = []

    class Client:
        def request(self, payload, cancelled, **kwargs):
            calls.append((payload, kwargs))
            if len(calls) == 1:
                return Completion('{"reply":', {}, "test")
            return Completion('{"reply":"done","emotion":"neutral"}', {}, "test")

    agent.client = Client()
    assert agent.answer("hello", [], threading.Event()).retried
    assert "text" not in calls[0][0] and "text" not in calls[1][0]
    assert calls[1][0]["instructions"] == calls[0][0]["instructions"]
    assert calls[1][0]["max_output_tokens"] == 4096 and calls[1][1]["thinking"] is False
    assert config.settings.thinking


def test_reading_responses_output_budget_includes_selected_thinking_mode():
    from ameath.agent import build_payload
    from ameath.models import Question
    settings = Settings(protocol="responses", thinking=True)
    assert build_payload(Question("explain"), settings)["max_output_tokens"] == 8192
