"""Aggregates over the stored aim events (replay_aim): where the crosshair was when an enemy came into view and how
fast it reached the enemy. Only `ok` events enter placement and time figures; the other classes are counted. Every
figure carries its n, medians their IQR, and a cell under MIN_N ok events is `{"too_few": True, "n": n}`.

The worker stores the first time within each AIM_LADDER_DEG value, so the on-target rule (`ON_RULE`) is a constant
here, not a recompute."""

from __future__ import annotations

import math
import threading
import time
from collections import Counter

import numpy as np

from app.moments import constants as mc
from app.moments.peeks import distance_band
from app.services import player_data, replay_aim, replay_gaps

ON_RULE = "body"
MIN_N = 10
EVERYONE_TTL_S = 600
BANDS = ("close", "mid", "long")
_FLOOR_DEG = 0.5


def on_threshold(d_m: float, rule: str = ON_RULE) -> float:
    """The error (degrees) under which the crosshair counts as on the enemy at `d_m` metres. body: atan(half a body
    / distance), applied as the largest stored ladder value at or under it, never under 0.5; five: at least 5;
    fixed5: always 5."""
    if rule == "fixed5":
        return 5
    body = math.degrees(math.atan(mc.AIM_BODY_HALF_M / d_m))
    fit = [v for v in mc.AIM_LADDER_DEG if v <= body]
    fitted = max(fit) if fit else _FLOOR_DEG
    return max(5, fitted) if rule == "five" else fitted


def _ladder_index(threshold) -> int:
    ladder = mc.AIM_LADDER_DEG
    return max((i for i, v in enumerate(ladder) if v <= threshold), default=0)


def _usable(events):
    return [e for e in events if e.cls == "ok" and e.err is not None and e.d_m is not None]


def _median_cell(values) -> dict:
    """Median with IQR of seconds-or-None values; None is never reached (infinite). `not_reached` when the median
    is infinite (over half censored)."""
    n = len(values)
    xs = sorted(math.inf if v is None else float(v) for v in values)
    reached = sum(1 for x in xs if not math.isinf(x))
    med = float(np.median(xs))
    if math.isinf(med):
        return {"n": n, "median": None, "q1": None, "q3": None, "not_reached": True, "reached": reached / n}
    def finite_q(q):
        with np.errstate(invalid="ignore"):
            v = float(np.percentile(xs, q))
        return None if math.isinf(v) or math.isnan(v) else v
    return {"n": n, "median": med, "q1": finite_q(25), "q3": finite_q(75), "not_reached": False,
            "reached": reached / n}


def _on_time(e, rule):
    times = getattr(e, "on_times", None)
    if not times:
        return None
    i = _ladder_index(on_threshold(e.d_m, rule))
    return times[i] if i < len(times) else None


def summary(events, rule: str = ON_RULE) -> dict:
    events = list(events)
    ok = _usable(events)
    out = {"classes": dict(Counter(e.cls for e in events)), "n_ok": len(ok)}
    if len(ok) < MIN_N:
        out["placement"] = {"too_few": True, "n": len(ok)}
    else:
        errs = np.array([float(e.err) for e in ok])
        on = sum(1 for e in ok if e.err <= on_threshold(e.d_m, rule))
        near = sum(1 for e in ok if on_threshold(e.d_m, rule) < e.err <= mc.CROSSHAIR_NEAR_DEG)
        n = len(ok)
        out["placement"] = {"n": n, "median": float(np.median(errs)), "q1": float(np.percentile(errs, 25)),
                            "q3": float(np.percentile(errs, 75)), "on": on / n, "near": near / n,
                            "off": (n - on - near) / n}
    bands = {}
    for band in BANDS:
        sub = [e for e in ok if distance_band(e.d_m) == band]
        if len(sub) < MIN_N:
            bands[band] = {"too_few": True, "n": len(sub)}
            continue
        bands[band] = {"n": len(sub),
                       "to_on": _median_cell([_on_time(e, rule) for e in sub]),
                       "to_shot": _median_cell([e.shot for e in sub]),
                       "to_dmg": _median_cell([e.dmg for e in sub])}
    out["bands"] = bands
    return out


def per_match(events) -> dict:
    """match id -> {date, n, median err} over ok events: the trend."""
    groups: dict = {}
    for e in _usable(events):
        groups.setdefault(e.match_id, []).append(e)
    return {m: {"date": es[0].played_at, "n": len(es), "median": float(np.median([float(e.err) for e in es]))}
            for m, es in groups.items()}


class _Event:
    """A stored aim event with its player, agent and match."""

    def __init__(self, row, mp, played_at):
        self.__dict__.update({c: getattr(row, c) for c in (
            "replay_id", "round_number", "idx", "slot", "fight_i", "opponent", "t", "cls", "err", "err_125",
            "err_250", "d_m", "fine", "hold", "opener", "gun_class", "hp", "enemy_hp", "outcome", "t_end",
            "on_times", "shot", "dmg")})
        self.player_id, self.agent, self.match_id = mp.player_id, mp.agent, mp.match_id
        self.played_at = played_at


def events_for(db, player_ids, agents=None, *, one_per_match: bool = False) -> list:
    """The stored events of these players' own slots (None = everyone), optionally of only `agents`, and with
    `one_per_match` the lowest player id's rows of a match two of the accounts played."""
    from app.models.match import Match, MatchPlayer
    from app.models.replay import ReplayPlayer

    q = (db.query(ReplayPlayer.replay_id, ReplayPlayer.slot, MatchPlayer, Match.played_at)
         .join(MatchPlayer, MatchPlayer.id == ReplayPlayer.match_player_id)
         .join(Match, Match.id == MatchPlayer.match_id))
    ids = None if player_ids is None else set(player_ids)
    if ids is not None:
        if not ids:
            return []
        q = q.filter(MatchPlayer.player_id.in_(ids))
        if one_per_match:
            q = q.filter(player_data.one_row_per_match(ids))
    owners = {(rid, slot): (mp, played) for rid, slot, mp, played in q.all()}
    if agents is not None:
        wanted = set(agents)
        owners = {k: v for k, v in owners.items() if v[0].agent in wanted}
    if ids is not None:
        replay_ids = sorted({rid for rid, _ in owners})
        if not replay_ids:
            return []
    else:
        replay_ids = None
    rows = replay_aim.current_events(db, replay_ids)
    return [_Event(r, *owners[(r.replay_id, r.slot)]) for r in rows if (r.replay_id, r.slot) in owners]


_EVERYONE: dict = {}
_LOCK = threading.Lock()


def everyone_summary(db) -> dict:
    """The all-players reference, cached in-process for ten minutes per GAPS_REVISION."""
    key = replay_gaps.GAPS_REVISION
    now = time.monotonic()
    with _LOCK:
        hit = _EVERYONE.get(key)
        if hit and now - hit[0] < EVERYONE_TTL_S:
            return hit[1]
    value = summary(events_for(db, None))
    with _LOCK:
        _EVERYONE.clear()
        _EVERYONE[key] = (time.monotonic(), value)
    return value
