"""Map control's per-replay views, read from the stored summaries (docs/replay-map-control-plan.md,
"Per-player control", "Where it shows"; docs/map-control-stages-4-7-impl.md, S4.1).

- `load_round_summaries`: one query for a replay's ok rows; each `summary` unpacked
  (app/replays/control_format.py). Never the ticks.
- `player_tables`: per round and per match, each player's control, coverage, active ratio and lost
  control at death, and each team's redundant control, for the viewer's Control tab.

Standard library and the DB only: the engine (app/control) is never imported here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy.orm import load_only

from app.models.replay import Replay, ReplayRoundControl
from app.replays import control_format as cf
from app.services import replay_control

# Bumped when these views' output changes for the same rows, so ETags change with it.
VIEWS_VERSION = 1


@dataclass
class RoundSummaries:
    summaries: dict[int, dict] = field(default_factory=dict)   # round -> unpacked summary
    fingerprints: dict[int, str] = field(default_factory=dict)
    stale: list[int] = field(default_factory=list)
    unreadable: list[int] = field(default_factory=list)
    etag: str = '""'


def load_round_summaries(db, replay: Replay) -> RoundSummaries:
    rows = (db.query(ReplayRoundControl)
            .options(load_only(ReplayRoundControl.round_number, ReplayRoundControl.status,
                               ReplayRoundControl.fingerprint, ReplayRoundControl.summary,
                               ReplayRoundControl.computed_at))
            .filter(ReplayRoundControl.replay_id == replay.id, ReplayRoundControl.status == "ok")
            .order_by(ReplayRoundControl.round_number).all())
    groups = replay_control.side_groups(db, replay)
    out = RoundSummaries()
    tag = hashlib.sha256(f"views{VIEWS_VERSION};".encode("utf-8"))
    for row in rows:
        n = row.round_number
        stale = row.fingerprint != replay_control.round_fingerprint(replay, groups, n)
        # The stale flag is in the tag too: a geometry or link change makes rows stale without touching them.
        tag.update(f"{n}:{row.fingerprint}:{row.computed_at}:{int(stale)};".encode("utf-8"))
        try:
            out.summaries[n] = cf.unpack_summary(row.summary)
        except (cf.ControlFormatError, ValueError, OSError):
            out.unreadable.append(n)
            continue
        out.fingerprints[n] = row.fingerprint
        if stale:
            out.stale.append(n)
    out.etag = '"' + tag.hexdigest()[:16] + '"'
    return out


# ---------------------------------------------------------------- the player table


def _per_second(m2s: float, seconds: float) -> float | None:
    return round(m2s / seconds, 1) if seconds > 0 else None


def _ratio(active: float, passive: float) -> float | None:
    both = active + passive
    return round(active / both, 3) if both > 0 else None


def _live_seconds(summary: dict) -> float:
    """The live round's length: its heatmap sections' seconds, which the engine accumulates over the
    same window as the stats (`t_decided` itself can be null in a summary)."""
    return float(sum(float(s.get("seconds") or 0.0) for s in summary.get("sections") or []))


def _deaths(player: dict) -> list[dict]:
    """The deaths inside the live round: lost control counts only those."""
    return [d for d in player.get("deaths") or [] if d.get("live")]


def round_table(summary: dict) -> dict:
    """One round: each slot's numbers, and each side group's redundant control as an average m²
    over the live round (signed: the team's own area minus its players' control)."""
    live = _live_seconds(summary)
    players = {}
    for slot, p in (summary.get("players") or {}).items():
        deaths = _deaths(p)
        players[slot] = {
            "team": p.get("team"), "side": p.get("side"), "alive_s": p.get("alive_s"),
            "control_m2": p.get("control_m2"), "active_m2": p.get("active_m2"), "passive_m2": p.get("passive_m2"),
            "active_ratio": p.get("active_ratio"),
            "lost": [{"t": d.get("t"), "control_m2": d.get("control_m2"), "share_of_team": d.get("share_of_team"),
                      "by_level_m2": d.get("by_level_m2"), "went_to_m2": d.get("went_to_m2")} for d in deaths],
        }
    redundant = {group: _per_second(float(v), live) for group, v in (summary.get("redundant_m2s") or {}).items()}
    return {"live_s": round(live, 3), "group_side": summary.get("group_side") or {}, "players": players,
            "redundant_m2": redundant}


def match_table(summaries: dict[int, dict]) -> dict:
    """The whole match: m²·s summed over rounds, divided by the summed seconds alive (or live, for
    redundant control); lost control as the live deaths, their summed m² and their mean share of
    the team. Side groups keep their players all match, so redundant control is per group."""
    acc: dict[str, dict] = {}
    redundant: dict[str, float] = {}
    live_total = 0.0
    for summary in summaries.values():
        live_total += _live_seconds(summary)
        for group, v in (summary.get("redundant_m2s") or {}).items():
            redundant[group] = redundant.get(group, 0.0) + float(v)
        for slot, p in (summary.get("players") or {}).items():
            a = acc.setdefault(slot, {"team": p.get("team"), "alive_s": 0.0, "active_m2s": 0.0, "passive_m2s": 0.0,
                                      "control_m2s": 0.0, "rounds": 0, "deaths": 0, "lost_m2": 0.0, "shares": []})
            a["rounds"] += 1
            for key in ("alive_s", "active_m2s", "passive_m2s", "control_m2s"):
                a[key] += float(p.get(key) or 0.0)
            for d in _deaths(p):
                a["deaths"] += 1
                a["lost_m2"] += float(d.get("control_m2") or 0.0)
                if d.get("share_of_team") is not None:
                    a["shares"].append(float(d["share_of_team"]))
    players = {}
    for slot, a in sorted(acc.items(), key=lambda kv: int(kv[0])):
        players[slot] = {
            "team": a["team"], "rounds": a["rounds"], "alive_s": round(a["alive_s"], 3),
            "control_m2": _per_second(a["control_m2s"], a["alive_s"]),
            "active_m2": _per_second(a["active_m2s"], a["alive_s"]),
            "passive_m2": _per_second(a["passive_m2s"], a["alive_s"]),
            "active_ratio": _ratio(a["active_m2s"], a["passive_m2s"]),
            "deaths": a["deaths"], "lost_m2": round(a["lost_m2"], 1),
            "lost_mean_m2": round(a["lost_m2"] / a["deaths"], 1) if a["deaths"] else None,
            "lost_mean_share": round(sum(a["shares"]) / len(a["shares"]), 4) if a["shares"] else None,
        }
    return {"rounds": len(summaries), "live_s": round(live_total, 3), "players": players,
            "redundant_m2": {group: _per_second(v, live_total) for group, v in sorted(redundant.items())}}


def player_tables(replay: Replay, loaded: RoundSummaries) -> dict:
    rounds: dict[str, dict] = {}
    for n in range(1, replay.round_count + 1):
        if n in loaded.summaries:
            rounds[str(n)] = {"status": "ok", "stale": n in loaded.stale, **round_table(loaded.summaries[n])}
        else:
            rounds[str(n)] = {"status": "unreadable" if n in loaded.unreadable else "missing"}
    return {"rounds": rounds, "match": match_table(loaded.summaries), "stale_rounds": sorted(loaded.stale)}
