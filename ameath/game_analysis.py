"""Deterministic arithmetic for the Slay the Spire 2 skill.

The model still decides; these helpers make sure buffs/debuffs, the deck's shape and the
map's paths are computed the same way every time instead of being re-derived by the model.
Values are estimates from the bridge state; the raw state stays authoritative.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from itertools import combinations

# Canonical power -> accepted ids (upper case, without a trailing _POWER) and Chinese names.
POWERS = {
    "strength": ({"STRENGTH"}, {"力量"}),
    "dexterity": ({"DEXTERITY"}, {"敏捷"}),
    "weak": ({"WEAK", "WEAKENED"}, {"虚弱"}),
    "frail": ({"FRAIL"}, {"脆弱"}),
    "vulnerable": ({"VULNERABLE"}, {"易伤"}),
    "vigor": ({"VIGOR"}, {"活力"}),
    "intangible": ({"INTANGIBLE"}, {"无实体"}),
    "thorns": ({"THORNS"}, {"荆棘"}),
}
# A debuff without a number is still active for one turn.
FLAGS = {"weak", "frail", "vulnerable", "intangible"}


def _power_parts(power) -> tuple[str, str, int | None]:
    if isinstance(power, dict):
        ident = str(power.get("power_id") or power.get("id") or "")
        name = str(power.get("name") or "")
        amount = power.get("amount")
        return ident, name, amount if type(amount) is int else None
    if isinstance(power, str):
        text = re.sub(r"\[debuff\]", "", power, flags=re.I).strip()
        match = re.match(r"^(.*?)[\s:：x×]*(-?\d+)?$", text)
        head = (match[1] if match else text).strip()
        amount = int(match[2]) if match and match[2] else None
        return head, head, amount
    return "", "", None


def power_key(ident: str, name: str) -> str | None:
    canonical = re.sub(r"_?POWER$", "", ident.strip().upper().replace(" ", "_"))
    for key, (ids, names) in POWERS.items():
        if canonical in ids or name.strip() in names or ident.strip() in names:
            return key
    return None


def modifiers(powers) -> dict:
    """{'strength': 2, 'weak': 1, ...} for the powers this calculator understands."""
    result: dict[str, int] = {}
    for power in powers or []:
        ident, name, amount = _power_parts(power)
        key = power_key(ident, name)
        if key is None:
            continue
        if amount is None:
            amount = 1 if key in FLAGS else 0
        result[key] = result.get(key, 0) + amount
    return {key: value for key, value in result.items() if value}


def _dynamic(card: dict, names: tuple[str, ...]):
    for row in card.get("dynamic_values") or []:
        if isinstance(row, dict) and str(row.get("name") or "") in names:
            value = row.get("base_value", row.get("current_value"))
            if isinstance(value, (int, float)):
                return int(value)
    return None


def _card_text(card: dict) -> str:
    for key in ("rules_text", "resolved_rules_text", "description", "line"):
        if isinstance(card.get(key), str) and card[key]:
            return card[key]
    return ""


DAMAGE_PATTERNS = (r"造成\s*(\d+)\s*点(?:伤害)?", r"[Dd]eal\s+(\d+)\s+damage")
BLOCK_PATTERNS = (r"获得\s*(\d+)\s*点格挡", r"[Gg]ain\s+(\d+)\s+[Bb]lock")
HIT_PATTERNS = (r"(\d+)\s*次", r"(\d+)\s+times")
AOE_PATTERNS = ("所有敌人", "全体敌人", "ALL enemies", "all enemies")


def card_values(card: dict) -> dict:
    """Base damage / hits / block of one hand card, from dynamic values first, then rules text."""
    text = _card_text(card)
    damage = _dynamic(card, ("Damage", "CalculatedDamage"))
    block = _dynamic(card, ("Block", "CalculatedBlock"))
    hits = _dynamic(card, ("Repeat", "Hits"))
    if damage is None:
        damage = next((int(m[1]) for p in DAMAGE_PATTERNS if (m := re.search(p, text))), None)
    if block is None:
        block = next((int(m[1]) for p in BLOCK_PATTERNS if (m := re.search(p, text))), None)
    if hits is None and damage is not None:
        hits = next((int(m[1]) for p in HIT_PATTERNS if (m := re.search(p, text))), None)
    return {"damage": damage, "hits": max(1, hits or 1) if damage is not None else 0, "block": block,
            "aoe": any(word in text for word in AOE_PATTERNS) or card.get("target_type") == "AllEnemies"}


def attack_damage(base: int, hits: int, attacker: dict, target: dict) -> int:
    """Slay the Spire rules: (base + strength) x0.75 if weak, x1.5 if target vulnerable, floored per hit."""
    per_hit = max(0, base + attacker.get("strength", 0))
    if attacker.get("weak"):
        per_hit = math.floor(per_hit * 0.75)
    if target.get("vulnerable"):
        per_hit = math.floor(per_hit * 1.5)
    if target.get("intangible"):
        per_hit = min(per_hit, 1)
    return per_hit * hits


def block_value(base: int, defender: dict) -> int:
    value = max(0, base + defender.get("dexterity", 0))
    return math.floor(value * 0.75) if defender.get("frail") else value


def card_cost(card: dict, energy: int) -> int | None:
    if card.get("costs_x") is True:
        return energy
    for key in ("energy_cost", "cost"):
        if type(card.get(key)) is int:
            return max(0, card[key])
    match = re.search(r"(\d+)\s*(?:能量|费|energy)", str(card.get("line") or ""))
    return int(match[1]) if match else None


def _enemy_index(enemy: dict, position: int) -> int:
    value = enemy.get("i", enemy.get("index"))
    return value if type(value) is int else position


def combat_math(combat: dict, enemies: list[tuple[int, dict, int]]) -> dict:
    """Per-card estimates and the best damage per target within this turn's energy.

    `enemies` holds (index, enemy, effective_hp) for living enemies.
    """
    player = combat.get("player") or {}
    mine = modifiers(player.get("powers"))
    theirs = {index: modifiers(enemy.get("powers")) for index, enemy, _ in enemies}
    energy = player.get("energy") if type(player.get("energy")) is int else 0
    cards, playable = [], []
    for card in combat.get("hand") or []:
        if not isinstance(card, dict):
            continue
        name = str(card.get("name") or card.get("card_id") or str(card.get("line") or "").split("·")[0]).strip()
        values = card_values(card)
        cost = card_cost(card, energy)
        row = {"name": name, "cost": cost}
        if values["damage"] is not None:
            row["hits"] = values["hits"]
            row["damage"] = {str(index): attack_damage(values["damage"], values["hits"], mine, theirs[index])
                             for index, _, _ in enemies}
            if values["aoe"]:
                row["aoe"] = True
        if values["block"] is not None:
            row["block"] = block_value(values["block"], mine)
        cards.append(row)
        if card.get("playable") is True and cost is not None and ("damage" in row or "block" in row):
            playable.append(row)
    best = {}
    attacks = [row for row in playable if "damage" in row][:10]
    for index, enemy, effective in enemies:
        top, picked = 0, []
        for size in range(1, len(attacks) + 1):
            for combo in combinations(attacks, size):
                if sum(row["cost"] for row in combo) > energy:
                    continue
                total = sum(row["damage"][str(index)] for row in combo)
                if total > top:
                    top, picked = total, [row["name"] for row in combo]
        best[str(index)] = {"damage": top, "cards": picked,
                            "kills": effective is not None and top >= effective}
    block_cards = sorted((row for row in playable if row.get("block")), key=lambda r: -r["block"] / max(1, r["cost"]))
    spent, block = 0, 0
    for row in block_cards:
        if spent + row["cost"] <= energy:
            spent += row["cost"]
            block += row["block"]
    return {"player_modifiers": mine, "enemy_modifiers": {str(i): m for i, m in theirs.items() if m},
            "cards": cards, "best_damage_by_target": best, "max_block_estimate": block,
            "thorns_warning": [str(i) for i, m in theirs.items() if m.get("thorns")],
            "notes": "伤害按基础值+力量、虚弱x0.75、目标易伤x1.5、无实体=1 逐段计算；格挡按+敏捷、脆弱x0.75。"
                     "未计入本回合打出后才生效的增益（例如先打力量牌）、抽牌、遗物和特殊卡牌效果；敌人意图数值为游戏显示值。"}


def deck_summary(run: dict) -> dict:
    """Card counts by type and name, so the build is visible at a glance."""
    deck = run.get("deck")
    if not isinstance(deck, list):
        return {}
    names, types = Counter(), Counter()
    for row in deck:
        if not isinstance(row, dict):
            continue
        count = (len(row["card_ids"]) if isinstance(row.get("card_ids"), list) and row["card_ids"]
                 else row["count"] if type(row.get("count")) is int else 1)
        name = str(row.get("name") or str(row.get("line") or row.get("card_id") or "?").split("·")[0]).strip()
        if row.get("upgraded") is True and not name.endswith("+"):
            name += "+"
        names[name] += count
        kind = str(row.get("card_type") or row.get("type") or "")
        if kind:
            types[kind] += count
    relics = [str(r.get("name") or r.get("relic_id")) if isinstance(r, dict) else str(r)
              for r in run.get("relics") or run.get("relic_ids") or []][:20]
    return {"size": sum(names.values()), "types": dict(types),
            "cards": [f"{name} x{count}" if count > 1 else name for name, count in names.most_common(40)],
            "relics": relics}


# -- map routes -------------------------------------------------------------
NODE_SHORT = {"Monster": "怪", "Elite": "精", "Boss": "首", "Rest": "休", "RestSite": "休", "Shop": "店",
              "Merchant": "店", "Event": "?", "Unknown": "?", "Treasure": "宝", "Ancient": "古"}


def _coord(row) -> str | None:
    if isinstance(row, str) and re.fullmatch(r"\d+,\d+", row.strip()):
        return row.strip()
    if isinstance(row, dict):
        if isinstance(row.get("coord"), str):
            return _coord(row["coord"])
        if type(row.get("row")) is int and type(row.get("col")) is int:
            return f"{row['row']},{row['col']}"
    return None


def map_graph(map_state: dict) -> dict[str, dict]:
    graph = {}
    for node in map_state.get("nodes") or []:
        coord = _coord(node)
        if coord is None:
            continue
        children = [c for c in (_coord(child) for child in node.get("children") or []) if c]
        graph[coord] = {"type": str(node.get("node_type") or node.get("type") or "Unknown"), "children": children}
    return graph


def _stats(types: list[str]) -> dict:
    counts = Counter(NODE_SHORT.get(t, "?") for t in types)
    return {"elite": counts["精"], "rest": counts["休"], "shop": counts["店"], "event": counts["?"],
            "monster": counts["怪"], "treasure": counts["宝"]}


def route_candidates(map_state: dict, options: list[tuple[int, str]], limit: int = 8) -> list[dict]:
    """Distinct paths from each next node to the top of the map, picked for variety.

    `options` holds (option_index, coord) for the travellable next nodes.
    """
    graph = map_graph(map_state)
    if not graph:
        return []
    per_option = []
    for option_index, start in options:
        if start not in graph:
            continue
        paths, stack = [], [(start, [start])]
        while stack and len(paths) < 1500:
            coord, path = stack.pop()
            children = [c for c in graph[coord]["children"] if c in graph and c not in path]
            if not children:
                paths.append(path)
                continue
            stack.extend((child, path + [child]) for child in children)
        unique = {}
        for path in paths:
            types = [graph[c]["type"] for c in path]
            unique.setdefault("".join(NODE_SHORT.get(t, "?") for t in types), (path, types))
        per_option.append((option_index, list(unique.items())))
    picks: list[tuple[int, str, list, list]] = []
    keys = (lambda s: (-s["elite"], -s["rest"]), lambda s: (s["elite"], -s["rest"]),
            lambda s: (-(s["rest"] + s["shop"]), s["elite"]), lambda s: (-s["event"] - s["treasure"], s["elite"]))
    share = max(1, limit // max(1, len(per_option)))
    for option_index, items in per_option:
        for key in keys[:share]:
            if not items:
                break
            sequence, (path, types) = min(items, key=lambda item: key(_stats(item[1][1])))
            if all(sequence != existing[1] or option_index != existing[0] for existing in picks):
                picks.append((option_index, sequence, path, types))
    picks = picks[:limit]
    return [{"route_id": f"r{n + 1}", "option_index": option_index, "sequence": sequence, "coords": path,
             **_stats(types)} for n, (option_index, sequence, path, types) in enumerate(picks)]
