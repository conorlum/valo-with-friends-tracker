"""The pure replay-search index builder (R3, R4, R5, R9).

Standard library plus `app.search.match`; no database. `build_rows` turns one replay's DB-free inputs into rows
shaped by `ROW_KEYS`. The loader (which gathers `inputs`) lives elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import re

from app.search.match import ROW_KEYS

INDEX_REVISION = 1

# D12: Impact's tier thresholds, copied because replay services may not import `app.scoring`.
PISTOL_ROUNDS = (1, 13)
SAVE_BELOW = 1000
ECO_BELOW = 3250
FORCE_BELOW = 4200

_TEAMS = ("team-1", "team-2")


def source_key(parts: dict) -> str:
    payload = json.dumps({"revision": INDEX_REVISION, **parts}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def buy_of(round_number, loadouts) -> str | None:
    if round_number in PISTOL_ROUNDS:
        return "pistol"
    if not loadouts:
        return None
    mean = sum(loadouts) / len(loadouts)
    if mean < SAVE_BELOW:
        return "save"
    if mean < ECO_BELOW:
        return "eco"
    if mean < FORCE_BELOW:
        return "force"
    return "full"


KEY_COLUMN_LENGTH = 16  # `ReplayMomentIndex.ability_key`; anything longer is stored as None

# The stored names put the archetype letter (or the digit slot) between underscores: "Q_Molotov",
# "4_Smoke", "Grenadier_Q_SemtexBasic", "BountyHunter_4_WolfHound". Many start with a codename instead.
_KEY_TOKEN = re.compile(r"(?:^|_)([CQEX4])_")


def _key_of(name):
    """The archetype letter of an ability name ("Q_Molotov" -> "Q", "Cable_X_FishingHook_Cage" -> "X"); None when
    the name has no single-character token."""
    if not name:
        return None
    found = _KEY_TOKEN.search(name)
    return found.group(1) if found else None


def _slot_info(players: dict, slot) -> dict:
    if slot is None:
        return {}
    return players.get(slot) or players.get(str(slot)) or {}


def _slots(players: dict) -> list[int]:
    return sorted(int(s) for s in players)


def _state_index(states, t, strict: bool):
    """The state entry in force at t: last with `s.t <= t` (or `< t` when strict), else the first."""
    best = states[0]
    for s in states:
        if (s["t"] < t) if strict else (s["t"] <= t):
            best = s
        else:
            break
    return best


def build_rows(inputs: dict) -> list[dict]:
    replay = inputs["replay"]
    players = inputs["players"]
    all_slots = _slots(players)
    rounds = {int(n): r for n, r in inputs["rounds"].items()}
    rows: list[dict] = []
    score = {"team-1": 0, "team-2": 0}

    for n in sorted(rounds):
        rnd = rounds[n]
        loadouts = rnd.get("loadouts") or {}
        plant_t = rnd.get("plant_t")
        states = sorted(rnd["states"], key=lambda s: s["t"]) if rnd.get("states") else None
        kills = list(rnd.get("kills") or [])
        base = {
            "replay_id": replay["id"], "uuid": replay["uuid"], "map": replay["map"],
            "played_at": replay.get("played_at"), "match_id": replay["match_id"], "round": n,
            "attack_team": rnd.get("attack_team"), "won_team": rnd.get("won_team"),
            "buy1": buy_of(n, loadouts.get("team-1")), "buy2": buy_of(n, loadouts.get("team-2")),
            "score1": score["team-1"], "score2": score["team-2"],
            "planted": plant_t is not None, "site": rnd.get("site"), "plant_t": plant_t,
        }
        round_rows: list[dict] = []

        def alive_slots_at(t):
            dead = {k["victim_slot"] for k in kills if k["t"] <= t}
            return [s for s in all_slots if s not in dead]

        def add(kind, t, *, t_end=None, slot=None, other_slot=None, ability=None, ability_key=None, ult=False,
                first=False, tags=None, weapon=None, strict=False, alive=None, alive_slots=None,
                actor=True, agent=None):
            who = _slot_info(players, slot) if actor else {}
            other = _slot_info(players, other_slot)
            if alive is None and states:
                alive = _state_index(states, t, strict)["alive"]
            row = dict(base)
            row.update({
                "kind": kind, "t": t, "t_end": t_end,
                "team": who.get("team"), "slot": slot if actor else None,
                "player_id": who.get("player_id"), "agent": agent or who.get("agent"),
                "other_slot": other_slot, "other_player_id": other.get("player_id"),
                "other_agent": other.get("agent"), "other_team": other.get("team"),
                "ability": ability, "ability_key": ability_key, "ult": ult, "first": first,
                "alive1": alive[0] if alive else None, "alive2": alive[1] if alive else None,
                "alive_slots": alive_slots if alive_slots is not None else [],
                "post_plant": plant_t is not None and t >= plant_t,
                "tags": list(tags or []), "weapon": weapon,
            })
            row["seq"] = len(round_rows)
            round_rows.append({k: row[k] for k in ROW_KEYS})

        if states:
            for i, st in enumerate(states):
                end = states[i + 1]["t"] if i + 1 < len(states) else None
                pieces = [(st["t"], end)]
                if plant_t is not None and st["t"] < plant_t and (end is None or plant_t < end):
                    pieces = [(st["t"], plant_t), (plant_t, end)]
                for a, b in pieces:
                    add("state", a, t_end=b, alive=st["alive"], alive_slots=alive_slots_at(a), actor=False)

        first_i = min(range(len(kills)), key=lambda i: (kills[i]["t"], i)) if kills else None
        for i, k in enumerate(kills):
            first = i == first_i
            add("kill", k["t"], slot=k["killer_slot"], other_slot=k["victim_slot"], first=first,
                weapon=k.get("weapon"), strict=True)
            add("death", k["t"], slot=k["victim_slot"], other_slot=k["killer_slot"], first=first,
                weapon=k.get("weapon"), strict=True)

        if plant_t is not None:
            add("plant", plant_t, actor=False)
        if rnd.get("defuse_t") is not None:
            add("defuse", rnd["defuse_t"], actor=False)

        for c in rnd.get("casts") or []:
            key = next((x for x in (_key_of(nm) for nm in c.get("names") or ()) if x), None)
            if key is not None and len(key) > KEY_COLUMN_LENGTH:
                key = None
            add("cast", c["t"], t_end=c.get("t_end"), slot=c.get("by"), agent=c.get("agent"),
                ability=c.get("ability_id"), ability_key=key, ult=c.get("type") == "ult",
                tags=c.get("kinds"))

        for f in rnd.get("fights") or []:
            add("fight", f["t0"], t_end=f.get("t1"), slot=f["slot"], first=f.get("first") == "player",
                tags=f.get("tags"))

        for b in rnd.get("backshots") or []:
            add("backshot", b["t"], slot=b["victim_slot"], tags=[b["cause"]] if b.get("cause") else [])

        rows.extend(round_rows)
        if rnd.get("won_team") in score:
            score[rnd["won_team"]] += 1
    return rows
