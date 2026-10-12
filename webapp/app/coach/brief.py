"""The match brief: what the coach is handed before it asks anything. Facts and pointers for one player across
one match. Impact ranks where to look first; the detectors' flags add what Impact cannot see."""

from __future__ import annotations

from app.coach import areas, detectors

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


def build(snap, slot: int) -> dict:
    players = snap.context["players"]
    subject = _player(players[str(slot)])
    rounds, ranked, gaps = [], [], {}
    for n in snap.round_numbers:
        view, site, control = snap.view(n), snap.site(n), snap.control(n)
        db = site.get("db") or {}
        stats = (site.get("stats") or {}).get(str(slot))
        winner = db.get("winner")
        live = [k for k in view.kills if k["t"] <= view.t_decided]
        role = snap.role(n, slot)
        rounds.append({
            "round": n, "role": role, "winner": winner,
            "won": None if winner is None else winner == subject["team"], "decided_at": view.t_decided,
            "plant": db.get("plant"), "defuse": db.get("defuse"), "stats": stats,
            "economy": _economy(players, site.get("stats")),
            "deaths": [k["t"] for k in live if k["victim"] == slot],
            "kills": [{"t": k["t"], "victim": snap.names.get(k["victim"])} for k in live if k.get("killer") == slot],
            "utility": [{k: v for k, v in row.items() if k != "by"} for row in view.utility(by=slot)],
            "flags": detectors.flags(view, slot, (control or {}).get("knew"), role),
            "gaps": snap.gaps(n)})
        if stats and stats.get("impact") is not None:
            ranked.append({"round": n, "impact": stats["impact"]})
        if snap.gaps(n):
            gaps[str(n)] = snap.gaps(n)
    ranked.sort(key=lambda r: (-abs(r["impact"]), r["round"]))
    match = snap.context["match"]
    names = sorted({c["name"] for c in areas.callouts(match["map"])})
    if not names:
        gaps = {"match": ["areas"], **gaps}
    return {"v": VERSION,
            "match": {"uuid": match["uuid"], "map": match["map"], "score": match.get("score"),
                      "rounds": snap.round_numbers},
            "subject": subject,
            "players": [_player(players[s]) for s in sorted(players, key=int)],
            "areas": names,
            "rounds": rounds, "ranked": ranked, "data_gaps": gaps}
