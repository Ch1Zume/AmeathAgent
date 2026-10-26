import copy
import json
import threading

import pytest

from ameath.config import Config
from ameath.game import (DemoBridge, GameError, GameSession, GameWaiting, SessionBudget,
                          Snapshot, choices_for, decision_from_json, normalized, validate_game_endpoint)


def test_compact_and_full_wire_keys_normalize():
    assert normalized({"RunId": "ABC", "Combat": {"CurrentHp": 20}}) == {"run_id": "ABC", "combat": {"current_hp": 20}}


@pytest.mark.parametrize("address", ["http://example.com:8080", "https://127.0.0.1:8080", "http://127.0.0.1:8080/state",
                                     "http://user:secret@127.0.0.1:8080", "http://127.0.0.1:8080?key=secret"])
def test_game_bridge_is_loopback_only(address):
    with pytest.raises(GameError):
        validate_game_endpoint(address)


def test_targeted_cards_use_only_advertised_target_domains():
    snapshot = DemoBridge().snapshot()
    snapshot.state["combat"]["hand"][0]["valid_target_indices"] = [2, 5]
    plays = [c.action for c in choices_for(snapshot) if c.action["action"] == "play_card"]
    assert {c["target_index"] for c in plays} == {2, 5}
    snapshot.state["combat"]["hand"][0]["valid_target_indices"] = []
    assert not [c for c in choices_for(snapshot) if c.action["action"] == "play_card"]


def test_unknown_indices_unplayable_cards_and_unadvertised_actions_are_not_offered():
    snapshot = DemoBridge().snapshot()
    snapshot.state["combat"]["hand"][0]["playable"] = False
    snapshot.descriptors.append({"name": "run_console_command"})
    snapshot.state["available_actions"].append("run_console_command")
    assert [c.action for c in choices_for(snapshot)] == [{"action": "end_turn"}]
    snapshot.state["combat"]["action_readiness"]["can_use_combat_actions"] = False
    assert not choices_for(snapshot)


def test_map_indices_are_wire_indices_not_array_positions_and_travel_is_not_repeated():
    bridge = DemoBridge()
    bridge.position = 2
    snapshot = bridge.snapshot()
    snapshot.state["map"]["options"][0]["i"] = 7
    assert any(c.action.get("option_index") == 7 for c in choices_for(snapshot))
    snapshot.state["map"]["is_traveling"] = True
    assert not choices_for(snapshot)


def test_model_cannot_invent_actions_or_indices():
    snapshot = DemoBridge().snapshot()
    choices = choices_for(snapshot)
    with pytest.raises(GameError):
        decision_from_json('{"choice_id":"delete_profile","action":"end_turn"}', snapshot, choices, {})
    with pytest.raises(GameError):
        decision_from_json('```json\n{}\n```', snapshot, choices, {})


def test_pause_during_model_work_prevents_execution(tmp_path):
    session = GameSession(Config(tmp_path), DemoBridge(), demo=True)
    decision = session.propose()
    session.stop()
    with pytest.raises(GameError, match="暂停"):
        session.apply(decision)
    assert not session.bridge.executed


def test_stale_state_prevents_execution(tmp_path):
    bridge = DemoBridge()
    session = GameSession(Config(tmp_path), bridge, demo=True)
    decision = session.propose()
    bridge.position = 1
    with pytest.raises(GameError, match="变化"):
        session.apply(decision)
    assert not bridge.executed


def test_run_boundary_stops_takeover(tmp_path):
    session = GameSession(Config(tmp_path), DemoBridge(), demo=True)
    session.propose()
    snapshot = session.bridge.snapshot()
    snapshot.state["run_id"] = "other-run"
    with pytest.raises(GameError, match="对局"):
        session.propose(snapshot)


def test_live_game_never_uses_fixed_demo_strategy(tmp_path):
    session = GameSession(Config(tmp_path), DemoBridge(), demo=False)
    with pytest.raises(GameError, match="真实游戏"):
        session.propose()
    assert not session.bridge.executed


def test_unconfirmed_action_is_not_repeated(tmp_path):
    class PendingBridge(DemoBridge):
        def execute(self, action, reason):
            self.executed.append(action)
            return {"status": "pending"}

    bridge = PendingBridge()
    session = GameSession(Config(tmp_path), bridge, demo=True)
    decision = session.propose()
    session.apply(decision)
    with pytest.raises(GameWaiting, match="重复"):
        session.apply(decision)
    assert len(bridge.executed) == 1


def test_demo_scene_sequence_is_a_complete_observe_decide_act_loop(tmp_path):
    session = GameSession(Config(tmp_path), DemoBridge(), demo=True)
    for _ in range(4):
        decision = session.propose()
        session.apply(decision)
    assert len(session.bridge.executed) == 4
    assert session.bridge.snapshot().screen == "GAME_OVER"
    assert session.budget.requests == 0
    entries = [json.loads(line) for line in session.journal.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 8
    assert all(row["demo"] for row in entries)
    assert "reasoning_content" not in session.journal.read_text(encoding="utf-8")


def test_budget_stops_new_requests_and_marks_missing_usage_unknown():
    budget = SessionBudget(2, 100)
    budget.reserve()
    budget.account({})
    assert budget.unknown_usage == 1
    budget.reserve()
    budget.account({"total_tokens": 120})
    with pytest.raises(GameError, match="额度"):
        budget.reserve()


def test_transient_state_read_waits_without_calling_model(tmp_path):
    class TransitionBridge(DemoBridge):
        reads = 0

        def snapshot(self):
            self.reads += 1
            if self.reads == 1:
                raise GameWaiting("transition")
            return super().snapshot()

    config = Config(tmp_path)
    config.settings.game_interval = 0.001
    session = GameSession(config, TransitionBridge(), demo=True)
    snapshot = session.wait_next(timeout=1)
    assert snapshot.screen == "COMBAT"
    assert session.budget.requests == 0
