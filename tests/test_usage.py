import json
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ameath.config import Config
from ameath.llm import ModelClient, ModelError
from ameath.usage import UsageLedger, usage_numbers
from test_ui import app  # noqa: F401  (QApplication fixture)


def test_usage_numbers_cover_deepseek_chat_and_responses_shapes():
    assert usage_numbers({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                          "prompt_cache_hit_tokens": 80, "completion_tokens_details": {"reasoning_tokens": 12}}) == {
        "prompt": 100, "completion": 20, "total": 120, "cached": 80, "reasoning": 12}
    assert usage_numbers({"input_tokens": 7, "output_tokens": 3, "input_tokens_details": {"cached_tokens": 2}})["total"] == 10
    assert usage_numbers({}) is None and usage_numbers({"total_tokens": "x"}) is None


def test_history_groups_days_weeks_and_months_and_keeps_unknown_separate(tmp_path):
    ledger = UsageLedger(tmp_path)
    at = lambda *args: datetime(*args, 12, tzinfo=timezone.utc)
    ledger.record("chat", "chat", "m", {"total_tokens": 100}, when=at(2026, 10, 30))
    ledger.record("game", "sts2:turn_plan", "m", {"total_tokens": 1000}, when=at(2026, 11, 2))
    ledger.record("game", "sts2:turn_plan", "m", {}, when=at(2026, 11, 2))
    ledger.record("agent", "explain", "m", {"total_tokens": 50}, when=at(2026, 11, 1))
    ledger.record("bogus", "", "m", {"total_tokens": 5}, when=at(2026, 11, 2))
    today = date(2026, 11, 2)  # a Monday

    day = ledger.history("day", 7, today)
    assert len(day) == 7 and day[-1].start == today
    assert day[-1].totals.total == 1005 and day[-1].totals.requests == 3 and day[-1].totals.unknown == 1
    assert day[-1].by_category["game"].unknown == 1 and day[-1].by_category["other"].total == 5
    assert day[-1].by_detail[("game", "sts2:turn_plan")].requests == 2

    week = ledger.history("week", 2, today)
    assert week[-1].start == today and week[-1].totals.total == 1005
    assert week[0].totals.total == 150  # Oct 30 and Nov 1 belong to the previous week

    month = ledger.history("month", 2, today)
    assert month[-1].totals.total == 1055 and month[0].totals.total == 100


@pytest.fixture
def usage_service():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            empty = self.path.startswith("/empty")
            data = {"model": "stub", "choices": [{"message": {"content": "" if empty else "好"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 4, "total_tokens": 34}}
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_every_completed_response_is_recorded_with_its_purpose(tmp_path, usage_service):
    config = Config(tmp_path)
    config.settings.endpoint = usage_service + "/v1"
    payload = {"model": "stub", "messages": [{"role": "user", "content": "hi"}]}
    ModelClient(config, "agent").request(payload, threading.Event(), detail="explain")
    config.settings.endpoint = usage_service + "/empty"
    with pytest.raises(ModelError):
        ModelClient(config, "chat").request(payload, threading.Event(), detail="chat")
    today = UsageLedger(tmp_path).history("day", 1)[-1]
    # The unusable empty answer was still served and billed, so it is still counted.
    assert today.by_category["agent"].total == 34 and today.by_category["chat"].total == 34
    assert today.by_detail[("agent", "explain")].requests == 1
    rows = (tmp_path / "usage.db").read_bytes()
    assert b"hi" not in rows  # only counts and labels, never the prompt


def test_usage_page_renders_each_period(app, tmp_path):
    from ameath.usage_ui import UsagePanel
    config = Config(tmp_path)
    UsageLedger(tmp_path).record("game", "sts2:review", "m", {"total_tokens": 4321})
    panel = UsagePanel(config)
    panel.resize(900, 700)
    panel.show()
    for period in ("day", "week", "month"):
        panel.set_period(period)
        assert "4,321" in panel.table.item(0, 3).text()
        panel.grab()  # paints the bars and chart
    assert "Token" in panel.headline.text()
    panel.close()
