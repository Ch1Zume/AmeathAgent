import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ameath.config import Config
from ameath.game import DemoBridge, GameError, GameSession, SpireBridge


@pytest.fixture
def bridge_server():
    simulator = DemoBridge()
    received = []
    health = {"service": "sts2-ai-agent", "status": "ready", "mod_version": "0.16.2",
              "game_version": "v0.111.0", "play_running": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                data = health
            elif self.path == "/decision-snapshot":
                snapshot = simulator.snapshot()
                data = {"state": snapshot.state, "available_actions": snapshot.descriptors}
            else:
                self.send_error(404)
                return
            self.reply(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, dict(self.headers), body))
            self.reply(simulator.execute(body, body["client_context"]["decision_reason"]))

        def reply(self, data):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "request_id": "test", "data": data}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SpireBridge(f"http://127.0.0.1:{server.server_port}"), health, received
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_bridge_reads_atomic_frame_and_dispatches_without_model_credentials(tmp_path, bridge_server):
    bridge, health, received = bridge_server
    session = GameSession(Config(tmp_path), bridge, demo=True)
    decision = session.propose()
    session.apply(decision)
    route, headers, body = received[0]
    assert route == "/action"
    assert body["action"] == "play_card"
    assert body["target_index"] == 0
    assert body["client_context"]["source"] == "ameath"
    assert "Authorization" not in headers


def test_bridge_refuses_wrong_service_or_competing_autoplay(bridge_server):
    bridge, health, received = bridge_server
    health["service"] = "another-service"
    with pytest.raises(GameError, match="桥接"):
        bridge.snapshot()
    health["service"] = "sts2-ai-agent"
    health["play_running"] = True
    with pytest.raises(GameError, match="正在运行"):
        bridge.snapshot()
    assert not received
