"""The match brief: what the coach is handed before it asks anything. Facts and pointers for one player across
one match. Impact ranks where to look first; the detectors' flags add what Impact cannot see."""

from __future__ import annotations

import math
from bisect import bisect_right

from app.coach import areas, detectors, fights
from app.critutil import info

VERSION = 1
_PLAYER_KEYS = ("slot", "name", "agent", "team", "side")


def _player(p: dict) -> dict:
    return {k: p.get(k) for k in _PLAYER_KEYS}


def _economy(players: dict, stats: dict) -> dict | None:
    """Each team's loadout and remaining credits, summed over the players the site has a row for."""
    out: dict[str, dict] = {}
    for slot, row in (stats or {}).items():
        team = (players.get(str(slot)) or {}).get("team")
        if team is None or row.get("loadout") is None:
            continue
        sums = out.setdefault(team, {"loadout": 0, "remaining": 0})
        sums["loadout"] += row["loadout"]
        sums["remaining"] += row.get("remaining") or 0
    return dict(sorted(out.items())) or None


# Abilities whose hits the replay does not record, so `enemies_affected` undercounts them: Bassquake leaves no
# effect row at all, Nightfall only its damage (not its debuff) on casts without info. Measured on a Sunset
# match, 2026-11-14: six Bassquakes with zero rows while enemies stood in the cone, a Nightfall over four
# enemies with one row.
HITS_NOT_RECORDED = {"Bassquake": "none", "Nightfall": "damage only"}
SMOKE_NEAR_M = 10.0         # a smoke whose centre is this close to where a cast landed is listed with it


def _info_facts(cast: dict, table: dict) -> dict:
    measured = info.figure(cast)
    if measured is None:
        return {}
    out = {fact: measured[metric] for metric, fact in (
        ("cleared_m2", "cleared_m2"), ("located", "located"), ("hold_s", "held_back_s"))
        if measured.get(metric) is not None}
    ability = cast.get("ability_id") or f"{cast.get('agent')}/{cast.get('ability')}"
    ranks = table.get(ability) or {}
    for metric, abilities in (("cleared_m2", info.CLEAR_ABILITIES), ("hold_s", info.HOLD_ABILITIES)):
        if ability not in abilities or measured.get(metric) is None:
            continue
        rank = ranks.get(metric) or {}
        n, thresholds = rank.get("n", 0), rank.get("points")
        if n < 10 or not thresholds or len(thresholds) != 101:
            continue
        # Highest whole percentile threshold reached; ties take the upper rank, bounded to 0..100.
        pct = max(0, min(100, bisect_right(thresholds, measured[metric]) - 1))
        out.setdefault("ability_pct", {})[metric] = pct
        out.setdefault("ability_n", {})[metric] = n
    return out


def _metres(view, a: tuple, b: tuple) -> float:
    return round(math.hypot(a[0] - b[0], a[1] - b[1]) / view.uv_per_unit / 100.0, 1)


def _landing(c: dict, casts, view) -> dict:
    """Where a cast landed (its area) and the smokes up around that spot when it landed."""
    if view is None or c.get("u") is None or c.get("v") is None:
        return {}
    out = {}
    area = view.area(c["u"], c["v"])
    if area is not None:
        out["landed_area"] = area
    landed = (c.get("signals") or {}).get("landed")
    at = c["t"] if landed is None else landed
    smokes = []
    for s in casts:
        if s is c or "smoke" not in (s.get("kinds") or ()) or s.get("u") is None or s.get("v") is None:
            continue
        sig = s.get("signals") or {}
        start = sig.get("landed") if sig.get("landed") is not None else s["t"]
        end = sig.get("seen_end")
        if not (start <= at and (end is None or at <= end)):
            continue
        d = _metres(view, (c["u"], c["v"]), (s["u"], s["v"]))
        if d <= SMOKE_NEAR_M:
            smokes.append({"ability": s.get("ability"), "side": "own" if s.get("side") == c.get("side") else "enemy",
                           "metres": d, "radius_m": (s.get("presence") or {}).get("r_m")})
    if smokes:
        out["smokes_near_landing"] = sorted(smokes, key=lambda x: x["metres"])
    return out


def util_facts(round_util: dict | None, slot: int, view=None, *, info_table: dict | None = None) -> list[dict]:
    """The player's casts this round from Critical Util (app/services/replay_util.py): what each did, measured, and
    its value (the change in the side's round-win chance after it, percentage points). Slots, never names.
    `view` (the round's RoundView) names where each cast landed. `info_table` is the payload-wide compact
    percentile table stored once in the snapshot's context."""
    out = []
    casts = (round_util or {}).get("casts") or ()
    for c in casts:
        if c.get("slot") != slot:
            continue
        ours = [k for k in c.get("kills") or () if k.get("ours")]
        mates = {h.get("mate") for h in c.get("harm") or ()}       # one teammate hit twice is one teammate
        fact = {"t": c["t"], "ability": c.get("ability"), "kind": c.get("kind"), "value": c.get("value"),
                "enemies_affected": c.get("enemies") or 0, "kills": len(ours),
                "teammates_hit": len(mates), "teammate_died_affected": c.get("harm_died") or 0,
                "followed": c.get("followed"), "wasted": bool(c.get("wasted"))}
        fact.update(_info_facts(c, info_table or {}))
        if c.get("ability") in HITS_NOT_RECORDED and (c["ability"] != "Nightfall" or info.figure(c) is None):
            fact["hits_recorded"] = HITS_NOT_RECORDED[c["ability"]]
        if c.get("trigger") is not None and c["trigger"] > c["t"]:
            fact["trigger"] = c["trigger"]      # a device's first trigger, break or touch on an enemy
        if c.get("scored") is False:
            fact["after_decided"] = True        # shown, never scored: `value` is None
        cover = c.get("cover") or {}
        if cover.get("blocked_s"):
            # seconds of enemy sight lines cut, added up over every line: not how long the smoke lasted
            fact["sight_line_s"] = cover["blocked_s"]
            fact["sight_lines"] = cover.get("pairs")
        presence = c.get("presence") or {}
        if presence.get("waiting_s"):
            fact["waiting_s"] = presence["waiting_s"]
        if presence.get("life_s"):
            fact["life_s"] = presence["life_s"]
        fact.update(_landing(c, casts, view))
        out.append(fact)
    for u in (round_util or {}).get("unused") or ():
        if u.get("slot") == slot:
            out.append({"t": u["t"], "kind": u["kind"], "held": u.get("abilities") or [], "points": u.get("points")})
    return out


def build(snap, slot: int) -> dict:
    players = snap.context["players"]
    subject = _player(players[str(slot)])
    rounds, ranked, gaps = [], [], {}
    score = {"own": 0, "enemy": 0}          # going into each round; unknown from the first round with no winner
    for n in snap.round_numbers:
        view, site, control = snap.view(n), snap.site(n), snap.control(n)
        db = site.get("db") or {}
        stats = (site.get("stats") or {}).get(str(slot))
        winner = db.get("winner")
        live = [k for k in view.kills if k["t"] <= view.t_decided]
        role = snap.role(n, slot)
        round_fights = fights.for_round(snap, n, slot)
        rounds.append({
            "round": n, "role": role, "winner": winner,
            "won": None if winner is None else winner == subject["team"], "decided_at": view.t_decided,
            "plant": db.get("plant"), "defuse": db.get("defuse"), "stats": stats,
            "economy": _economy(players, site.get("stats")), "score_before": dict(score) if score else None,
            "deaths": [k["t"] for k in live if k["victim"] == slot],
            "kills": [{"t": k["t"], "victim": snap.names.get(k["victim"])} for k in live if k.get("killer") == slot],
            "utility": [{k: v for k, v in row.items() if k != "by"} for row in view.utility(by=slot)],
            "flags": detectors.flags(view, slot, (control or {}).get("knew"), role, fights=round_fights),
            "fights": [{key: value for key, value in fight.items() if key != "flags"} for fight in round_fights],
            "util_facts": util_facts(snap.util(n), slot, view, info_table=snap.context.get("util_info")),
            "gaps": snap.gaps(n)})
        if score is not None and winner is not None:
            score["own" if winner == subject["team"] else "enemy"] += 1
        else:
            score = None
        if stats and stats.get("impact") is not None:
            ranked.append({"round": n, "impact": stats["impact"]})
        if snap.gaps(n):
            gaps[str(n)] = snap.gaps(n)
    ranked.sort(key=lambda r: (-abs(r["impact"]), r["round"]))
    match = snap.context["match"]
    names = areas.tree(snap.asset_key)
    if names is None:
        names = [{"name": c["name"], "parent": None, "super": c["super"], "approximate": True}
                 for c in sorted(areas.callouts(snap.asset_key), key=lambda c: c["name"])]
        if not names:
            gaps = {"match": ["areas"], **gaps}
    return {"v": VERSION,
            "match": {"uuid": match["uuid"], "map": match["map"], "score": match.get("score"),
                      "rounds": snap.round_numbers},
            "subject": subject,
            "players": [_player(players[s]) for s in sorted(players, key=int)],
            "areas": names,
            "rounds": rounds, "ranked": ranked, "data_gaps": gaps,
            "no_gun_deaths": detectors.no_gun_deaths(rounds)}
