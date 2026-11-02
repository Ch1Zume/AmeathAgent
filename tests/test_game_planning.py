import copy
import json

import pytest
from PySide6.QtTest import QTest

from ameath.config import Config
from ameath.game import (MAX_REPLANS, GameSession, Snapshot, clean_emotion, clean_speech, combat_summary,
                         match_step, choices_for)
from ameath.llm import Completion
from ameath.memory import Persona, PersonaCard
from test_ui import app  # noqa: F401


def card(name, cost=1, target=True):
    return {"name": name, "cost": cost, "line": f"{name} · {cost}能量", "playable": True,
            "requires_target": target, "valid_target_indices": [0] if target else []}


class CombatBridge:
    """Hand indices shift as cards leave, like the real bridge; 战斗专注 draws a card."""
    def __init__(self, hand, draws=("重击",)):
        self.turn, self.energy, self.executed = 1, 3, []
        self.hand = [dict(c) for c in hand]
        self.draws = list(draws)
        self.screen = "COMBAT"

    def snapshot(self):
        hand = [{**c, "i": i, "playable": c["cost"] <= self.energy} for i, c in enumerate(self.hand)]
        state = {"screen": self.screen, "run_id": "run-1", "native_profile_id": 0, "turn": self.turn,
                 "session": {"mode": "singleplayer", "phase": "run", "control_scope": "local_player"},
                 "available_actions": ["play_card", "end_turn"],
                 "run": {"floor": 6, "act": 1, "gold": 99, "hp": "50/80", "deck": [{"line": "打击", "card_ids": ["a"] * 10}]},
                 "combat": {"action_readiness": {"can_use_combat_actions": True},
                            "player": {"hp": "50/80", "block": 0, "energy": self.energy,
                                       "powers": [{"name": "力量", "amount": 2}]},
                            "hand": hand,
                            "enemies": [{"i": 0, "name": "养蜂人", "hp": "18/40", "block": 3, "alive": True,
                                         "intents": [{"intent_type": "Attack", "label": "6x2"}],
                                         "powers": [{"name": "蜂群", "amount": 3}]}]}}
        return Snapshot(state, [{"name": "play_card"}, {"name": "end_turn"}], {"status": "ready"})

    def execute(self, action, reason):
        self.executed.append(action)
        if action["action"] == "play_card":
            played = self.hand.pop(action["card_index"])
            self.energy -= played["cost"]
            if played["name"] == "战斗专注" and self.draws:
                self.hand.append(card(self.draws.pop(0)))
        elif action["action"] == "end_turn":
            self.turn += 1
            self.energy = 3
            self.hand = [card("打击"), card("防御", target=False)]
        return {"status": "completed"}


class ScriptedClient:
    def __init__(self, respond):
        self.respond, self.calls = respond, []

    def complete_json(self, system, material, cancelled, **kwargs):
        self.calls.append((material["task"], material, system, kwargs))
        return Completion(json.dumps(self.respond(material), ensure_ascii=False), {"total_tokens": 100}, "m")


@pytest.fixture
def config(tmp_path):
    config = Config(tmp_path)
    config.settings.mode = "live"
    PersonaCard(tmp_path).save(Persona(name="爱弥斯", speaking_style="元气、会自言自语"))
    return config


def play_turn(session, limit=10):
    for _ in range(limit):
        decision = session.propose(session.read())
        session.apply(decision)
        if decision.choice.action["action"] == "end_turn":
            return
    raise AssertionError("turn did not end")


def test_one_plan_call_drives_the_whole_turn_and_indices_follow_the_hand(config):
    bridge = CombatBridge([card("痛击", 2), card("防御", target=False), card("打击")])
    client = ScriptedClient(lambda m: {"choice_id": m["provided_choices"][0]["choice_id"], "reason": "单步", "plan": ""}
                            if m["task"] == "step" else {
        "math": "敌方 18+3=21；痛击 8+2 + 打击 6+2 = 18，差 3，不够斩杀", "lethal": False,
        "steps": [{"kind": "play_card", "name": "痛击", "target": 0}, {"kind": "play_card", "name": "打击", "target": 0},
                  {"kind": "end_turn"}],
        "reason": "先上易伤再打", "combat_strategy": "养蜂人越打越多蜂，靠力量少次数高伤害",
        "speech": ["嗯……打它越多越麻烦", "那就攒力量一拳打飞！", "第三句", "第四句被截掉"], "emotion": "playful"})
    session = GameSession(config, bridge, client=client)
    first = session.propose()
    assert first.source == "plan" and first.choice.name == "痛击"
    assert first.speech == ("嗯……打它越多越麻烦", "那就攒力量一拳打飞！", "第三句") and first.emotion == "neutral"
    session.apply(first)
    play_turn(session)
    assert [task for task, *_ in client.calls] == ["turn_plan"]
    assert [a.get("card_index") for a in bridge.executed] == [0, 1, None]  # 打击 moved from index 2 to 1
    assert bridge.executed[-1] == {"action": "end_turn"} and session.planned_steps == 2
    assert session.budget.by_task == {"turn_plan": {"requests": 1, "tokens": 100}}
    _task, material, system, kwargs = client.calls[0]
    assert material["opening"] is True and kwargs["detail"] == "sts2:turn_plan"
    assert material["combat_summary"]["incoming_estimate"] == 12 and material["combat_summary"]["enemy_effective_hp_total"] == 21
    assert "爱弥斯" in system and "元气" in system  # persona card shapes the voice
    assert session.combat["strategy"].startswith("养蜂人")
    # The next turn is a new plan, no longer the combat opening, and keeps the combat strategy.
    session.propose(session.read())
    assert [task for task, *_ in client.calls] == ["turn_plan", "turn_plan", "step"]  # 痛击 is gone: never guessed
    assert client.calls[1][1]["opening"] is False and client.calls[1][1]["combat_strategy"].startswith("养蜂人")


def test_drawing_a_new_card_triggers_a_replan_instead_of_finishing_a_stale_plan(config):
    bridge = CombatBridge([card("战斗专注", 0, target=False), card("打击")])

    def respond(material):
        names = [c["name"] for c in material["provided_choices"] if c["kind"] == "play_card"]
        steps = [{"kind": "play_card", "name": n, "target": None if n == "战斗专注" else 0} for n in names]
        return {"steps": steps + [{"kind": "end_turn"}], "math": "", "lethal": False, "speech": []}

    client = ScriptedClient(respond)
    session = GameSession(config, bridge, client=client)
    play_turn(session)
    assert [task for task, *_ in client.calls] == ["turn_plan", "replan"]
    assert "重击" in client.calls[1][1]["replan_reason"]
    assert [a.get("card_index") for a in bridge.executed if a["action"] == "play_card"] == [0, 0, 0]


def test_replans_are_capped_then_fall_back_to_single_steps(config):
    bridge = CombatBridge([card("打击"), card("打击"), card("打击")])

    def respond(material):
        if material["task"] == "step":
            return {"choice_id": next(c["choice_id"] for c in material["provided_choices"] if c["kind"] == "end_turn"),
                    "reason": "结束", "plan": ""}
        # Plays one real card, then names a card that is not in hand.
        return {"steps": [{"kind": "play_card", "name": "打击", "target": 0},
                          {"kind": "play_card", "name": "不存在", "target": 0}], "speech": []}

    client = ScriptedClient(respond)
    session = GameSession(config, bridge, client=client)
    play_turn(session)
    tasks = [task for task, *_ in client.calls]
    assert tasks == ["turn_plan"] + ["replan"] * MAX_REPLANS + ["step"]
    assert all(a["action"] in ("play_card", "end_turn") for a in bridge.executed)


def test_plan_naming_no_legal_first_step_is_never_guessed(config):
    bridge = CombatBridge([card("打击")])

    def respond(material):
        if material["task"] == "step":
            return {"choice_id": material["provided_choices"][0]["choice_id"], "reason": "单步", "plan": ""}
        return {"steps": [{"kind": "play_card", "name": "打击", "target": 5}], "speech": ["我先想想"]}

    session = GameSession(config, bridge, client=ScriptedClient(respond))
    decision = session.propose()
    assert decision.source == "step" and decision.speech == ("我先想想",)


class RunBridge:
    """Map → elite combat → reward → map, to check when build reviews happen."""
    def __init__(self):
        self.screen, self.executed = "MAP", []
        self.deck = 10

    def snapshot(self):
        base = {"screen": self.screen, "run_id": "run-1", "native_profile_id": 0,
                "session": {"mode": "singleplayer", "phase": "run", "control_scope": "local_player"},
                "run": {"floor": 1, "act": 1, "deck": [{"line": "牌", "card_ids": ["x"] * self.deck}], "relics": ["燃烧之血"]}}
        if self.screen == "MAP":
            base.update(available_actions=["choose_map_node"], map={"options": [
                {"i": 0, "line": "精英", "node_type": "Elite"}, {"i": 1, "line": "怪物", "node_type": "Monster"}]})
        elif self.screen == "COMBAT":
            base.update(available_actions=["end_turn"], turn=1, combat={
                "action_readiness": {"can_use_combat_actions": True}, "hand": [],
                "player": {"hp": "40/80", "block": 0, "energy": 3}, "enemies": []})
        else:
            base.update(available_actions=["proceed"], reward={"rewards": [{"i": 0, "reward_type": "Relic", "claimable": True}]})
        return Snapshot(base, [{"name": n} for n in base["available_actions"]], {"status": "ready"})

    def execute(self, action, reason):
        self.executed.append(action)
        self.screen = {"choose_map_node": "COMBAT", "end_turn": "REWARD", "proceed": "MAP"}[action["action"]]
        return {"status": "completed"}


def test_build_reviews_happen_at_run_start_and_after_an_elite_and_feed_later_decisions(config):
    def respond(material):
        if material["task"] == "review":
            return {"archetype": "力量战士" if "精英" in material["reason"] else "初步：力量", "assessment": "燃烧之血续航",
                    "priorities": ["力量来源", "多段攻击"], "avoid": ["过多防御牌"], "route": "多打精英",
                    "speech": ["这局我想走力量流！"], "emotion": "excited"}
        choices = material["provided_choices"]
        pick = next((c for c in choices if c["kind"] in ("choose_map_node", "proceed")), choices[0])
        if material["task"] == "step":
            return {"choice_id": pick["choice_id"], "reason": "按构筑", "plan": "", "speech": []}
        return {"steps": [{"kind": "end_turn"}], "speech": []}

    client = ScriptedClient(respond)
    bridge = RunBridge()
    session = GameSession(config, bridge, client=client)
    first = session.propose()
    assert first.review is not None and first.review.reason == "开局定方向"
    assert first.review.speech == ("这局我想走力量流！",) and first.review.emotion == "excited"
    assert first.choice.detail == "Elite"
    session.apply(first)
    for _ in range(2):  # combat end_turn, then reward proceed
        session.apply(session.propose())
    assert bridge.screen == "MAP"
    after = session.propose()
    assert after.review is not None and after.review.reason == "刚打完精英"
    assert session.strategy["archetype"] == "力量战士"
    tasks = [task for task, *_ in client.calls]
    assert tasks.count("review") == 2
    step_materials = [m for task, m, *_ in client.calls if task == "step"]
    assert all(m["strategy"]["priorities"] == ["力量来源", "多段攻击"] for m in step_materials)
    # No review at the reward screen itself, only at the next map.
    assert tasks[tasks.index("turn_plan") + 1] == "step"


def test_planned_steps_match_names_and_targets_exactly():
    bridge = CombatBridge([card("打击"), card("打击+"), card("防御", target=False)])
    choices = choices_for(bridge.snapshot())
    assert match_step({"kind": "play_card", "name": "打击+", "target": 0}, choices).action == {
        "action": "play_card", "card_index": 1, "target_index": 0}
    assert match_step({"kind": "play_card", "name": "防御", "target": None}, choices).action["card_index"] == 2
    assert match_step({"kind": "play_card", "name": "防御", "target": 0}, choices) is None
    assert match_step({"kind": "run_console_command", "name": "x"}, choices) is None
    assert match_step({"kind": "end_turn"}, choices).action == {"action": "end_turn"}


def test_speech_is_bounded_and_emotions_exclude_flight_or_sleep():
    assert clean_speech("单句") == ("单句",)
    assert clean_speech(["  ", "很" * 100]) == ("很" * 60,)
    assert clean_speech({"x": 1}) == ()
    assert clean_emotion("playful") == clean_emotion("sleepy") == "neutral"
    assert clean_emotion("thoughtful") == "thoughtful"


def test_combat_summary_reads_full_and_compact_intents():
    state = {"combat": {"player": {"current_hp": 30, "block": 5, "energy": 2},
                        "enemies": [{"i": 0, "name": "A", "current_hp": 10, "block": 0, "is_alive": True,
                                     "intents": [{"intent_type": "Attack", "damage": 4, "hits": 3}]},
                                    {"i": 1, "name": "B", "hp": "7/9", "alive": True, "intents": [{"intent_type": "Attack", "total_damage": 9}]},
                                    {"i": 2, "name": "dead", "hp": "0/9", "alive": False}]}}
    summary = combat_summary(state)
    assert summary["incoming_estimate"] == 21 and summary["unblocked_estimate"] == 16
    assert [e["name"] for e in summary["enemies"]] == ["A", "B"] and summary["enemy_effective_hp_total"] == 17


def test_panel_waits_for_her_voice_before_acting(app, config):
    from ameath.game_ui import GamePanel
    bridge = CombatBridge([card("打击")])
    client = ScriptedClient(lambda m: {"steps": [{"kind": "play_card", "name": "打击", "target": 0}],
                                       "speech": ["先打一下试试"], "emotion": "thoughtful"})
    panel = GamePanel(config)
    spoken = []
    panel.voice_router = lambda lines, emotion: spoken.append((lines, emotion)) or "voice"
    panel.session = GameSession(config, bridge, client=client)
    panel.start("step")
    deadline = 200
    while not spoken and deadline:
        QTest.qWait(10)
        deadline -= 1
    assert spoken == [(("先打一下试试",), "thoughtful")]
    QTest.qWait(300)
    assert not bridge.executed  # still speaking
    panel.voice_finished()
    for _ in range(200):
        if bridge.executed and not panel.busy:
            break
        QTest.qWait(10)
    assert bridge.executed and bridge.executed[0]["action"] == "play_card"
    panel.shutdown()


def test_panel_falls_back_to_a_text_bubble_when_chat_is_busy(app, config):
    from ameath.game_ui import GamePanel
    bridge = CombatBridge([card("打击")])
    client = ScriptedClient(lambda m: {"steps": [{"kind": "play_card", "name": "打击", "target": 0}],
                                       "speech": ["好"], "emotion": "happy"})
    panel = GamePanel(config)
    comments = []
    panel.comment.connect(lambda text, state: comments.append((text, state)))
    panel.voice_router = lambda lines, emotion: ""
    panel.session = GameSession(config, bridge, client=client)
    panel.start("step")
    for _ in range(400):
        if bridge.executed and not panel.busy:
            break
        QTest.qWait(10)
    assert ("好", "happy") in comments and bridge.executed
    panel.shutdown()
