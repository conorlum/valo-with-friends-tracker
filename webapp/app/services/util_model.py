"""Critical Util's round-win estimator: P(the attackers win | who is alive, the spike, the time) (design:
docs/superpowers/specs/2026-10-21-critical-util-design.md, "The currency").

Fitted on the tracker.gg kill feed of every match *without* a replay, through the same round replay fight-EV uses
(app/services/state_replay.py), so no cast is ever valued by a model that saw its own match. A cell's rate is
shrunk toward its parent cell (full state -> without time -> man advantage only -> 0.5), so a thin cell falls back
on its own instead of jumping to 0 or 1. Impact's graph weights are not probabilities and are not used.

The pure functions take plain observations; `load_observations` and `refresh` alone touch the DB.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict

VERSION = 1
PRIOR_M = 10.0                       # a cell's pull toward its parent, in observations
PRE_PLANT_EDGES = (20.0, 45.0, 70.0)  # round seconds: four buckets before the plant
POST_PLANT_EDGES = (10.0, 25.0)       # seconds since the plant: three after


def time_bucket(t: float | None, planted: bool, t_plant: float | None) -> str:
    if planted:
        since = 0.0 if t is None or t_plant is None else max(0.0, t - t_plant)
        return "p" + str(sum(since >= e for e in POST_PLANT_EDGES))
    return "t" + str(0 if t is None else sum(t >= e for e in PRE_PLANT_EDGES))


def keys(a: int, d: int, planted: bool, tb: str) -> list[str]:
    """Most specific first."""
    return [f"{a}v{d}:{int(planted)}:{tb}", f"{a}v{d}:{int(planted)}", f"{a}v{d}"]


def fit(observations) -> dict:
    """`observations`: (attackers alive, defenders alive, planted, round t, plant t, attackers won). Returns
    {"version", "n", "cells": {key: [wins, n]}}."""
    cells: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    n = 0
    for a, d, planted, t, t_plant, won in observations:
        n += 1
        for k in keys(a, d, planted, time_bucket(t, planted, t_plant)):
            cells[k][0] += int(bool(won))
            cells[k][1] += 1
    return {"version": VERSION, "n": n, "cells": dict(cells)}


def p_attack(model: dict, a: int, d: int, planted: bool, t: float | None, t_plant: float | None) -> float:
    if a <= 0 and d <= 0:
        return 0.5
    if d <= 0:
        return 1.0
    if a <= 0:
        return 0.0 if not planted else _shrunk(model, keys(0, d, True, time_bucket(t, True, t_plant)))
    return _shrunk(model, keys(a, d, planted, time_bucket(t, planted, t_plant)))


def _shrunk(model: dict, ks: list[str]) -> float:
    cells = (model or {}).get("cells") or {}
    p = 0.5
    for k in reversed(ks):               # coarsest first, each finer cell shrunk toward the one above it
        w, n = cells.get(k, (0, 0))
        p = (w + PRIOR_M * p) / (n + PRIOR_M)
    return p


def p_side(model, side: str, a: int, d: int, planted: bool, t, t_plant) -> float:
    p = p_attack(model, a, d, planted, t, t_plant)
    return p if side == "attack" else 1.0 - p


def score(model: dict, observations) -> dict:
    """Held-out check: log loss and Brier of the model against the man-advantage-only model and a coin."""
    out = {"n": 0, "log_loss": 0.0, "brier": 0.0, "log_loss_man": 0.0, "brier_man": 0.0, "log_loss_coin": math.log(2)}
    man_only = {"cells": {k: v for k, v in ((model or {}).get("cells") or {}).items() if k.count(":") == 0}}
    for a, d, planted, t, t_plant, won in observations:
        y = 1.0 if won else 0.0
        for p, ll, br in ((p_attack(model, a, d, planted, t, t_plant), "log_loss", "brier"),
                          (_shrunk(man_only, [f"{a}v{d}"]) if a > 0 and d > 0 else p_attack(model, a, d, planted, t, t_plant),
                           "log_loss_man", "brier_man")):
            q = min(max(p, 1e-6), 1 - 1e-6)
            out[ll] += -(y * math.log(q) + (1 - y) * math.log(1 - q))
            out[br] += (q - y) ** 2
        out["n"] += 1
    n = max(out["n"], 1)
    for k in ("log_loss", "brier", "log_loss_man", "brier_man"):
        out[k] = round(out[k] / n, 4)
    out["log_loss_coin"] = round(out["log_loss_coin"], 4)
    return out


def calibration(model: dict, observations, bins: int = 10) -> list[dict]:
    rows = [[0, 0.0, 0.0] for _ in range(bins)]
    for a, d, planted, t, t_plant, won in observations:
        p = p_attack(model, a, d, planted, t, t_plant)
        b = min(int(p * bins), bins - 1)
        rows[b][0] += 1
        rows[b][1] += p
        rows[b][2] += 1.0 if won else 0.0
    return [{"bin": i, "n": n, "predicted": round(sp / n, 3), "actual": round(sw / n, 3)}
            for i, (n, sp, sw) in enumerate(rows) if n]


# --- the DB


def load_observations(db, exclude_match_ids: set[int] | frozenset = frozenset(), *, by_match: bool = False):
    """Every state entry of every scraped round outside `exclude_match_ids`, as fit() observations (or, with
    `by_match`, {match_id: [observations]} for a held-out split)."""
    from sqlalchemy import select

    from app.models.kill_event import KillEvent
    from app.models.match import MatchPlayer, Team
    from app.models.round import Round
    from app.scoring.plant_window import attacking_team
    from app.services import state_replay as sr

    teams: dict[int, dict] = defaultdict(lambda: {Team.TEAM_1: set(), Team.TEAM_2: set()})
    for mp_id, match_id, team in db.execute(select(MatchPlayer.id, MatchPlayer.match_id, MatchPlayer.team)):
        if match_id not in exclude_match_ids:
            teams[match_id][team].add(mp_id)
    rounds: dict[int, list] = defaultdict(list)
    meta: dict[int, tuple] = {}
    for r in db.execute(select(Round.id, Round.match_id, Round.round_number, Round.outcome, Round.planted,
                               Round.plant_time, Round.exploded, Round.defused, Round.defuse_time)):
        if r.match_id in exclude_match_ids:
            continue
        rounds[r.match_id].append(r)
        meta[r.id] = (r.round_number, r.plant_time)
    kills: dict[int, list] = defaultdict(list)
    for k in db.execute(select(KillEvent.id, KillEvent.round_id, KillEvent.killer_match_player_id,
                               KillEvent.death_match_player_id, KillEvent.event_time_seconds)):
        if k.round_id in meta:
            kills[k.round_id].append(sr.KillEventInput(k.id, k.killer_match_player_id, k.death_match_player_id,
                                                       k.event_time_seconds))
    out: dict[int, list] = defaultdict(list)
    for match_id, rs in rounds.items():
        t = teams.get(match_id)
        if not t:
            continue
        inputs = tuple(sr.RoundInput(r.id, r.round_number, r.outcome, bool(r.planted), r.plant_time,
                                     bool(r.exploded), bool(r.defused), r.defuse_time,
                                     tuple(sorted(kills.get(r.id, ()), key=lambda e: (e.event_time_seconds, e.id))))
                       for r in sorted(rs, key=lambda r: r.round_number))
        entries, _, _ = sr.replay_match(sr.MatchInput(match_id, frozenset(t[Team.TEAM_1]), frozenset(t[Team.TEAM_2]),
                                                      inputs))
        for e in entries:
            round_number, t_plant = meta[e.round_id]
            attack = attacking_team(round_number)
            if attack is None:
                continue
            a_alive = len(e.team1_alive_ids if attack == Team.TEAM_1 else e.team2_alive_ids)
            d_alive = len(e.team2_alive_ids if attack == Team.TEAM_1 else e.team1_alive_ids)
            out[match_id].append((a_alive, d_alive, bool(e.post_plant), e.event_time_seconds, t_plant,
                                  e.winner == attack))
    if by_match:
        return dict(out)
    return [o for obs in out.values() for o in obs]


def replay_match_ids(db) -> set[int]:
    from sqlalchemy import select

    from app.models.replay import Replay

    return {m for (m,) in db.execute(select(Replay.match_id).where(Replay.match_id.is_not(None)))}


def refresh(db, *, holdout: float = 0.2) -> dict:
    """Fits the estimator on every non-replay match and stores it as util_model "win", with a held-out check
    (the last `holdout` share of matches by id, fitted without them). Returns the stored data."""
    from datetime import datetime, timezone

    from app.models.replay import UtilModel

    excluded = replay_match_ids(db)
    by_match = load_observations(db, excluded, by_match=True)
    ids = sorted(by_match)
    cut = int(len(ids) * (1 - holdout))
    train = [o for m in ids[:cut] for o in by_match[m]]
    test = [o for m in ids[cut:] for o in by_match[m]]
    check = score(fit(train), test) if test else None
    model = fit([o for m in ids for o in by_match[m]])
    model["check"] = check
    model["calibration"] = calibration(fit(train), test) if test else []
    model["matches"] = len(ids)
    key = hashlib.sha256(f"{VERSION}:{len(ids)}:{model['n']}:{ids[-1] if ids else 0}".encode()).hexdigest()[:16]
    db.merge(UtilModel(name="win", version=VERSION, key=key, data=model, computed_at=datetime.now(timezone.utc)))
    db.commit()
    return model


def load(db) -> dict | None:
    from app.models.replay import UtilModel

    row = db.get(UtilModel, "win")
    return None if row is None or row.version != VERSION else row.data
