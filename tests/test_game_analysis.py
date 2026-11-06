import contextlib
import json

from ameath.game import GameError, GameSession, Snapshot, combat_summary, load_postmortems
from ameath.game_analysis import card_values, combat_math, deck_summary, modifiers, route_candidates
from test_game_planning import ScriptedClient, config  # noqa: F401
from test_ui import app  # noqa: F401

BASE = {"run_id": "run-1", "native_profile_id": 0,
        "session": {"mode": "singleplayer", "phase": "run", "control_scope": "local_player"}}


def test_modifiers_read_full_and_compact_powers():
    assert modifiers([{"power_id": "STRENGTH_POWER", "name": "力量", "amount": 3},
                      {"power_id": "WEAK_POWER", "amount": 2, "is_debuff": True}]) == {"strength": 3, "weak": 2}
    assert modifiers(["VULNERABLE 2 [debuff]", "力量 -1", "蜂群 3"]) == {"vulnerable": 2, "strength": -1}
    assert modifiers([{"name": "无实体"}]) == {"intangible": 1}


def test_card_values_prefer_dynamic_base_values_then_text():
    dynamic = {"dynamic_values": [{"name": "Damage", "base_value": 5, "current_value": 8}, {"name": "Repeat", "base_value": 2}]}
    assert card_values(dynamic)["damage"] == 5 and card_values(dynamic)["hits"] == 2
    text = card_values({"line": "旋风斩 · X能量 · 对所有敌人造成5点伤害"})
    assert text["damage"] == 5 and text["aoe"] is True
    assert card_values({"rules_text": "Gain 5 Block."})["block"] == 5


def test_damage_counts_strength_weak_vulnerable_and_block_counts_dexterity_frail():
    combat = {"player": {"energy": 3, "powers": [{"name": "力量", "amount": 2}, {"name": "敏捷", "amount": 1},
                                                 {"name": "脆弱", "amount": 1}]},
              "hand": [{"name": "打击", "energy_cost": 1, "playable": True, "rules_text": "造成6点伤害。"},
                       {"name": "双重打击", "energy_cost": 1, "playable": True, "rules_text": "造成5点伤害2次。"},
                       {"name": "重击", "energy_cost": 2, "playable": True, "rules_text": "造成14点伤害。"},
                       {"name": "防御", "energy_cost": 1, "playable": True, "rules_text": "获得5点格挡。"}]}
    enemies = [(0, {"powers": [{"name": "易伤", "amount": 2}]}, 50), (1, {"powers": []}, 12)]
    math = combat_math(combat, enemies)
    cards = {row["name"]: row for row in math["cards"]}
    assert cards["打击"]["damage"] == {"0": 12, "1": 8}       # (6+2)*1.5, 6+2
    assert cards["双重打击"]["damage"] == {"0": 20, "1": 14}  # floor(7*1.5)*2, 7*2
    assert cards["防御"]["block"] == 4                        # floor((5+1)*0.75)
    best = math["best_damage_by_target"]
    assert best["0"]["damage"] == 20 + 24 and set(best["0"]["cards"]) == {"双重打击", "重击"}
    assert best["1"]["kills"] is True and best["0"]["kills"] is False

    weak = combat_math({"player": {"energy": 1, "powers": ["WEAK 1"]},
                        "hand": [{"name": "打击", "energy_cost": 1, "playable": True, "rules_text": "造成6点伤害。"}]},
                       [(0, {"powers": [{"name": "无实体", "amount": 1}]}, 5), (1, {}, 5)])
    assert weak["cards"][0]["damage"] == {"0": 1, "1": 4}


def test_combat_summary_carries_the_math_for_living_enemies():
    state = {"combat": {"player": {"hp": "30/80", "energy": 1, "powers": [{"name": "力量", "amount": 1}]},
                        "hand": [{"name": "打击", "cost": 1, "playable": True, "line": "打击 · 1能量 · 造成6点伤害"}],
                        "enemies": [{"i": 0, "name": "A", "hp": "7/9", "block": 0, "alive": True},
                                    {"i": 1, "name": "B", "hp": "0/9", "alive": False}]}}
    summary = combat_summary(state)
    assert summary["math"]["best_damage_by_target"] == {"0": {"damage": 7, "cards": ["打击"], "kills": True}}


def test_deck_summary_counts_cards_types_and_relics():
    run = {"deck": [{"name": "打击", "card_type": "Attack"}, {"name": "打击", "card_type": "Attack"},
                    {"name": "燃烧", "card_type": "Power", "upgraded": True}],
           "relics": [{"name": "燃烧之血"}]}
    summary = deck_summary(run)
    assert summary["size"] == 3 and summary["types"] == {"Attack": 2, "Power": 1}
    assert summary["cards"] == ["打击 x2", "燃烧+"] and summary["relics"] == ["燃烧之血"]


def full_map():
    """Two starting nodes; the left branch has an elite, the right one a rest site and a shop."""
    def node(row, col, kind, children):
        return {"row": row, "col": col, "node_type": kind, "children": [{"row": r, "col": c} for r, c in children]}
    return {"nodes": [node(1, 0, "Monster", [(2, 0)]), node(1, 2, "Monster", [(2, 2)]),
                      node(2, 0, "Elite", [(3, 1)]), node(2, 2, "Rest", [(3, 2)]),
                      node(3, 1, "Rest", [(4, 1)]), node(3, 2, "Shop", [(4, 1)]),
                      node(4, 1, "Boss", [])],
            "available_nodes": [{"index": 0, "row": 1, "col": 0, "node_type": "Monster"},
                                {"index": 1, "row": 1, "col": 2, "node_type": "Monster"}]}


def test_route_candidates_enumerate_distinct_paths_with_counts():
    candidates = route_candidates(full_map(), [(0, "1,0"), (1, "1,2")])
    sequences = {c["sequence"]: c for c in candidates}
    assert set(sequences) == {"怪精休首", "怪休店首"}
    assert sequences["怪精休首"]["elite"] == 1 and sequences["怪休店首"]["shop"] == 1
    compact = {"nodes": [{"coord": "0,0", "node_type": "Monster", "children": ["1,0"]},
                         {"coord": "1,0", "node_type": "Boss", "children": []}]}
    assert route_candidates(compact, [(0, "0,0")])[0]["sequence"] == "怪首"


class MapBridge:
    def __init__(self):
        self.position, self.executed, self.hp = "start", [], 70

    def snapshot(self):
        state = {**BASE, "screen": "MAP", "available_actions": ["choose_map_node"],
                 "run": {"floor": 1, "act": 1, "current_hp": self.hp, "max_hp": 80, "gold": 99,
                         "deck": [{"name": "打击", "card_type": "Attack"}]}}
        graph = full_map()
        if self.position == "start":
            options = graph["available_nodes"]
        else:
            row, col = map(int, self.position.split(","))
            node = next(n for n in graph["nodes"] if (n["row"], n["col"]) == (row, col))
            options = [{"index": i, **child, "node_type": next(n["node_type"] for n in graph["nodes"]
                                                               if (n["row"], n["col"]) == (child["row"], child["col"]))}
                       for i, child in enumerate(node["children"])]
        state["map"] = {"nodes": graph["nodes"], "available_nodes": options, "is_traveling": False}
        return Snapshot(state, [{"name": "choose_map_node"}], {"status": "ready"})

    def execute(self, action, reason):
        self.executed.append(action)
        options = self.snapshot().state["map"]["available_nodes"]
        chosen = next(o for o in options if o["index"] == action["option_index"])
        self.position = f"{chosen['row']},{chosen['col']}"
        return {"status": "completed"}


def test_route_is_planned_once_and_followed_without_more_model_calls(config):
    def respond(material):
        if material["task"] == "review":
            return {"archetype": "力量", "speech": ["走力量！", "第二句不该说"]}
        if material["task"] == "route":
            pick = next(r for r in material["route_options"] if r["sequence"] == "怪休店首")
            return {"route_id": pick["route_id"], "reason": "先稳住血量", "speech": ["不该说"]}
        raise AssertionError(material["task"])

    client = ScriptedClient(respond)
    bridge = MapBridge()
    session = GameSession(config, bridge, client=client)
    first = session.propose()
    assert first.source == "route" and first.choice.action["option_index"] == 1
    assert first.review.speech == ("走力量！",) and first.speech == ()
    session.apply(first)
    for _ in range(2):
        decision = session.propose()
        assert decision.source == "routed"
        session.apply(decision)
    assert bridge.position == "3,2"
    assert [task for task, *_ in client.calls] == ["review", "route"]
    route_material = next(m for task, m, *_ in client.calls if task == "route")
    assert route_material["deck"]["size"] == 1 and "coords" not in route_material["route_options"][0]
    assert [e["kind"] for e in session.run_log] == ["review", "route", "choice", "choice", "choice"]


def test_route_is_replanned_when_the_next_planned_node_is_an_elite_at_low_hp(config):
    calls = []

    def respond(material):
        calls.append(material["task"])
        if material["task"] == "review":
            return {"archetype": "力量"}
        names = [r["sequence"] for r in material["route_options"]]
        target = "怪精休首" if "怪精休首" in names else names[0]
        return {"route_id": next(r["route_id"] for r in material["route_options"] if r["sequence"] == target)}

    bridge = MapBridge()
    session = GameSession(config, bridge, client=ScriptedClient(respond))
    session.apply(session.propose())
    bridge.hp = 20
    session.propose()
    assert calls == ["review", "route", "route"]


def test_non_combat_steps_stay_silent_but_keep_the_build_note(config):
    class RewardBridge:
        def snapshot(self):
            state = {**BASE, "screen": "REWARD", "available_actions": ["choose_reward_card"], "run": {"floor": 3},
                     "reward": {"cards": [{"i": 0, "name": "燃烧"}]}}
            return Snapshot(state, [{"name": "choose_reward_card"}], {"status": "ready"})

    def respond(material):
        if material["task"] == "review":
            return {"archetype": "力量"}
        assert "deck" in material
        return {"choice_id": material["provided_choices"][0]["choice_id"], "reason": "补力量", "plan": "",
                "build_fit": "力量流的核心能力牌", "comment": "拿了！", "speech": ["我拿这张！"]}

    session = GameSession(config, RewardBridge(), client=ScriptedClient(respond))
    decision = session.propose()
    assert decision.speech == () and decision.comment == "" and decision.build_fit == "力量流的核心能力牌"


def test_postmortem_is_written_once_and_its_lessons_reach_the_next_run(config):
    def respond(material):
        if material["task"] == "postmortem":
            assert material["run_log"] and material["game_over"]["is_victory"] is False
            return {"result": "第 3 层倒下", "summary": "血线管理失误", "key_moments": ["第 2 层打精英"],
                    "lessons": ["血量低于一半不打精英"], "speech": ["下次我会更小心。"], "emotion": "sad"}
        return {"archetype": "力量"}

    over = Snapshot({**BASE, "screen": "GAME_OVER", "run": {"floor": 3}, "game_over": {"is_victory": False, "floor": 3}},
                    [], {"status": "ready"})
    session = GameSession(config, MapBridge(), client=ScriptedClient(respond))
    session.run_log.append({"kind": "choice", "choice": "精英"})
    result = session.postmortem(over)
    assert result.lessons == ("血量低于一半不打精英",) and result.speech == ("下次我会更小心。",)
    assert session.postmortem(over) is None
    rows = load_postmortems(config.directory)
    assert len(rows) == 1 and rows[0]["result"] == "第 3 层倒下"
    assert "usage" not in rows[0]

    client = ScriptedClient(respond)
    nxt = GameSession(config, MapBridge(), client=client)
    with contextlib.suppress(GameError):  # the scripted reply has no route or step; only the review matters
        nxt.propose()
    review_material = next(m for task, m, *_ in client.calls if task == "review")
    assert review_material["past_lessons"] == ["血量低于一半不打精英"]
    assert json.loads(json.dumps(review_material["past_lessons"], ensure_ascii=False))


def test_demo_sessions_never_write_a_postmortem(config):
    session = GameSession(config, MapBridge(), demo=True, client=ScriptedClient(lambda m: {}))
    session.run_log.append({"kind": "choice"})
    over = Snapshot({**BASE, "screen": "GAME_OVER", "game_over": {}}, [], {})
    assert session.postmortem(over) is None and load_postmortems(config.directory) == []


def test_game_panel_shows_build_route_and_review_history(app, config):
    from ameath.game_ui import GamePanel

    panel = GamePanel(config)
    panel.show_postmortems()
    assert "还没有复盘记录" in panel.decisions_view.toPlainText()
    (config.directory / "game_reviews.jsonl").write_text(json.dumps(
        {"result": "第 3 层倒下", "summary": "血线管理失误", "lessons": ["血量低于一半不打精英"]}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    panel.show_postmortems()
    text = panel.decisions_view.toPlainText()
    assert "第 3 层倒下" in text and "血量低于一半不打精英" in text
    panel.session = GameSession(config, MapBridge(), client=ScriptedClient(lambda m: {}))
    panel.session.strategy = {"archetype": "力量", "core_cards": ["燃烧"], "priorities": ["多段攻击"]}
    panel.session.route = {"coords": [], "sequence": "怪休店首"}
    panel.update_build()
    assert panel.build_label.text() == "本局流派：力量 · 核心：燃烧 · 优先：多段攻击 · 路线：怪休店首"
