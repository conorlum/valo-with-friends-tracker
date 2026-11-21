"""Per-fight gun, geometry and knowledge facts from a stored coach snapshot."""

from __future__ import annotations

import json
import math
from pathlib import Path

from app.coach import held, knowledge
from app.moments.constants import GUN_CLASS, TRADE_RANGE_M
from app.replays import guns


_GUN_CATEGORIES = {row["name"]: row["category"] for row in json.loads(
    (Path(__file__).parents[1] / "replays" / "guns.json").read_text(encoding="utf-8")
)["archetypes"].values()}


def _life_rows(view, slot, t, holds):
    """Holds in the life containing t, including its death instant for dropped guns."""
    life = next((iv for iv in reversed(view._intervals(slot)) if iv[0] <= t <= iv[1]), None)
    if life is None:
        return []
    return [row for row in holds if row["by"] == slot and life[0] <= row["t"] <= t]


def _gun_at(view, slot, t, holds, shots):
    if holds:
        history = _life_rows(view, slot, t, holds)
        row = history[-1] if history else None
        return (row, "held") if row and row.get("gun") else (None, None)
    own_shots = [row for row in shots if row.get("by") == slot]
    earlier = [row for row in own_shots if row["t"] <= t]
    row = earlier[-1] if earlier else next(iter(own_shots), None)
    return (row, "shot") if row and row.get("gun") else (None, None)


def _tier(row):
    if not row:
        return None
    if row.get("k") == "hold":
        return guns.tier(row.get("cat"))
    # Old shot rows lack categories. Prefer the gun table, then the moments firearm classes.
    category = _GUN_CATEGORIES.get(row.get("gun"), GUN_CLASS.get(row.get("gun")))
    category = {"pistol": "sidearm", "sheriff": "sidearm", "sniper": "sniper_rifle",
                "lmg": "machine_gun"}.get(category, category)
    return guns.tier(category)


def _start(rows, slot, enemy, t):
    starts = [max(float(row["t"]), t - 5) for row in rows
              if row.get("k") == "damage" and {row.get("by"), row.get("target")} == {slot, enemy}
              and row["t"] <= t and row.get("t1", row["t"]) >= t - 5]
    return min(starts, default=t)


def _known(view, slot, enemy, t0, gun, holds, shots, control, site):
    side = view.side(slot)
    signals = []
    if gun is not None:
        # Sightings are spans. A switch during a sighting can first reveal the current gun.
        knew = (control.get("knew") or {}).get(side) or {}
        for start, end, *_ in knew.get(str(enemy), []):
            for when in [start] + [row["t"] for row in holds if row["by"] == enemy and start <= row["t"] <= end]:
                if when >= t0:
                    continue
                held, _ = _gun_at(view, enemy, when, holds, shots) if holds else (None, None)
                if held and held.get("gun") == gun:
                    signals.append((float(when), 0, "seen"))
        for row in shots:
            if row.get("by") == enemy and row.get("gun") == gun and row["t"] < t0 \
                    and knowledge.shot_heard(view, side, row):
                signals.append((float(row["t"]), 1, "heard"))
    if signals:
        when, _, how = min(signals)
        return {"how": how, "t": when, "before_s": t0 - when}
    stats = site.get("stats") or {}
    loadouts = [row["loadout"] for player, row in stats.items()
                if int(player) in view.enemies(slot) and row.get("loadout") is not None]
    if loadouts and sum(loadouts) / len(loadouts) <= 1500:
        return {"how": "econ_guess", "gun_class": "sidearm"}
    return None


def _death(view, slot, enemy, t, holds, shots, *, agent=None):
    def given_away(row):
        # Another slot holding the same actor later in this life proves it left this player's hands.
        actor = (row.get("id"), row.get("gen"))
        return None not in actor and any(
            other["by"] != slot and (other.get("id"), other.get("gen")) == actor
            and row["t"] < other["t"] <= t for other in holds)

    history = _life_rows(view, slot, t, holds)
    if agent is None:
        agent = next((p.get("agent") for p in view.blob["players"] if int(p["slot"]) == slot), None)
    # Death tears down the equipped item with an unknown, actor-less close. Keep the
    # living state, but retain earlier unknown switches and real switches at death.
    last_held = next((row for row in reversed(history)
                      if not (row["t"] >= t and row.get("cat") == "other" and row.get("id") is None)), None)
    out_at_death = held.item_name(agent, last_held)
    possessed = [row for row in view.blob.get("util") or []
                 if row.get("k") == "ability" and row.get("by") == slot and row["t"] <= t
                 and (row.get("t1") is None or t <= row["t1"])
                 and any(lo <= t and (hi is None or t <= hi) for lo, hi in row.get("possessed") or [])]
    if possessed:
        out_at_death = held.ability_name(agent, max(possessed, key=lambda row: row["t"]))
    firearms = [row for row in history
                if _tier(row) is not None and not given_away(row)]
    best = max(reversed(firearms), key=lambda row: (_tier(row), row["t"]), default=None)
    killer, _ = _gun_at(view, enemy, t, holds, shots)
    dropped_tier, killer_tier = _tier(best), _tier(killer)
    better = dropped_tier is not None and killer_tier is not None and dropped_tier > killer_tier
    return {"dropped_gun": best.get("gun") if best else None,
            "dropped_id": best.get("id") if best else None,
            "dropped_gen": best.get("gen") if best else None,
            "dropped_tier": dropped_tier, "killer_gun_at_kill": killer.get("gun") if killer else None,
            "killer_tier": killer_tier, "better": better, "out_at_death": out_at_death}


def _pickup(view, names, slot, t, death, holds, shots, site_kills):
    actor = (death["dropped_id"], death["dropped_gen"])
    if None in actor:
        return None, 0
    picked = next((row for row in holds if row["t"] > t and row.get("from") == slot
                   and row.get("by") in view.enemies(slot) and (row.get("id"), row.get("gen")) == actor), None)
    if picked is None:
        return None, 0
    picker = picked["by"]
    count = 0
    for kill in view.kills:
        if kill["t"] <= picked["t"]:
            continue
        if kill.get("victim") == picker:
            break                      # the count stops at the picker's death
        if kill.get("killer") != picker or kill.get("victim") not in view.slots \
                or view.side(kill["victim"]) == view.side(picker):
            continue
        last = next((row for row in reversed(shots) if row.get("by") == picker
                     and picked["t"] <= row["t"] <= kill["t"]), None)
        if not last or (last.get("gid"), last.get("gen")) != actor:
            continue
        current = next((row for row in reversed(holds) if row["by"] == picker and row["t"] <= kill["t"]), None)
        if current is None or (current.get("id"), current.get("gen")) != actor:
            continue
        weapon = ((site_kills.get(str(kill.get("i"))) or {}).get("weapon"))
        if weapon is not None and str(weapon).lower() != str(death["dropped_gun"]).lower():
            continue
        count += 1
    return {"by": names.get(picker), "t": picked["t"]}, count


def for_round(snap, n: int, slot: int) -> list[dict]:
    """One fight per subject kill or death while the round was open."""
    view = snap.view(n)
    control = snap.control(n) or {}
    context = getattr(snap, "context", None) or {}
    site = (context.get("rounds") or {}).get(str(n)) or {}
    rows = view.blob.get("util") or []
    holds = sorted((row for row in rows if row.get("k") == "hold"), key=lambda row: row["t"])
    shots = sorted((row for row in rows if row.get("k") == "shot"), key=lambda row: row["t"])
    out = []
    for kill in view.kills:
        killer, victim, t = kill.get("killer"), kill.get("victim"), float(kill["t"])
        if t > view.t_decided or killer == victim or slot not in (killer, victim):
            continue
        won = killer == slot
        enemy = victim if won else killer
        if enemy not in view.slots or view.side(enemy) == view.side(slot):
            continue
        t0 = _start(rows, slot, enemy, t)
        g = t0 if t0 < t else t - 0.05
        before = math.nextafter(t0, -math.inf) if t0 < t else g
        side = view.side(slot)
        alive = {"own": len(view.living(side, before)),
                 "enemy": sum(view.alive(player, before) for player in view.enemies(slot))}
        gaps = [distance for mate in view.teammates(slot) if view.alive(mate, g)
                and (distance := view.distance_m(slot, mate, g)) is not None]
        lone = min(gaps) > TRADE_RANGE_M if gaps else None
        metres = view.distance_m(slot, enemy, g)
        mine, my_source = _gun_at(view, slot, t0, holds, shots)
        theirs, enemy_source = _gun_at(view, enemy, t0, holds, shots)
        my_gun = mine.get("gun") if mine else None
        enemy_gun = theirs.get("gun") if theirs else None
        my_range = guns.range_verdict(my_gun, metres) if _tier(mine) is not None else None
        enemy_range = guns.range_verdict(enemy_gun, metres) if _tier(theirs) is not None else None
        known = _known(view, slot, enemy, t0, enemy_gun, holds, shots, control, site)
        agent = ((context.get("players") or {}).get(str(slot)) or {}).get("agent")
        death = None if won else _death(view, slot, enemy, t, holds, shots, agent=agent)
        handover = None if won else {"my_best_tier": death["dropped_tier"],
                                    "killer_tier": death["killer_tier"], "better": death["better"]}
        picked_up, kills_with = (None, 0) if won else _pickup(view, snap.names, slot, t, death, holds, shots,
                                                                site.get("kills") or {})
        flags = []
        one_v_one = alive == {"own": 1, "enemy": 1}
        if death and death["better"] and lone and not one_v_one and alive["own"] >= alive["enemy"]:
            flags.append("gun_handover")
        off_range = None
        if my_range is not None and enemy_range is not None:
            if my_range == "beyond" and enemy_range == "in":
                off_range = "outranged"
            elif known and known["how"] in ("seen", "heard") and known["before_s"] >= 2 \
                    and enemy_range == "in" and guns.is_close_range(enemy_gun):
                my_limit = guns.effective_m(my_gun)
                if my_limit is None or my_limit > guns.effective_m(enemy_gun):
                    off_range = "into_their_range"
        if off_range:
            flags.append("off_range_fight")
        site_kills = site.get("kills") or {}
        kill_weapon = (site_kills.get(str(kill.get("i"))) or {}).get("weapon")
        out.append({"t": t, "t0": t0, "g": g, "subject": slot, "side": side, "won": won,
                    "enemy": snap.names.get(enemy), "my_gun": my_gun, "enemy_gun": enemy_gun,
                    "my_gun_source": my_source, "enemy_gun_source": enemy_source, "kill_weapon": kill_weapon,
                    "metres": round(metres, 1) if metres is not None else None,
                    "short_range": metres < guns.SHORT_RANGE_M if metres is not None else None,
                    "my_range": my_range, "enemy_range": enemy_range, "alive": alive,
                    "one_v_one": one_v_one, "lone": lone, "known": known, "death": death,
                    "handover": handover, "picked_up": picked_up, "kills_with": kills_with,
                    "flags": flags, "off_range": off_range})
    return out
