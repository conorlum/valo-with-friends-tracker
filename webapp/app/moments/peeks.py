"""Peek geometry: design = this build's PLAN; labeller not wired into the worker."""

import math

import numpy as np

from app.control import engine, height_motion, stance
from app.moments import fights, peek_constants as pc


def windowed_velocity(rnd, slot, t) -> tuple[float, float] | None:
    """Mean velocity in px/s between samples at or before the window's ends."""
    track = rnd.tracks.get(slot)
    if track is None:
        return None
    ts, u, v, _ = track
    i = int(np.searchsorted(ts, t, side="right")) - 1
    j = int(np.searchsorted(ts, t - pc.SPEED_WINDOW_S, side="right")) - 1
    if i < 0 or j < 0 or i == j or rnd._segment[slot][i] != rnd._segment[slot][j]:
        return None
    dt = float(ts[i] - ts[j])
    if dt > pc.SPEED_MAX_PAIR_S:
        return None
    scale = engine.PX / 10000 / dt
    return float(u[i] - u[j]) * scale, float(v[i] - v[j]) * scale


def windowed_speed(rnd, slot, t) -> float | None:
    """Windowed speed in m/s, or None without a measurable sample pair."""
    velocity = windowed_velocity(rnd, slot, t)
    return None if velocity is None else math.hypot(*velocity) * rnd.geo.m_per_px


def _sample_times(t0, t1):
    """The fixed-step grid anchored at t0, including t1 when it lies on it."""
    for k in range(math.floor((t1 - t0) / pc.ONSET_STEP_S) + 1):
        yield t0 + k * pc.ONSET_STEP_S


def max_speed(rnd, slot, t0, t1) -> float | None:
    """Greatest measurable windowed speed on the onset grid over [t0, t1]."""
    return max((speed for t in _sample_times(t0, t1)
                if (speed := windowed_speed(rnd, slot, t)) is not None), default=None)


def still(rnd, slot, t) -> bool:
    speed = max_speed(rnd, slot, t - pc.HELD_LOOK_S, t)
    return speed is not None and speed <= pc.HELD_MAX_MPS


def line_clear(rnd, a, b, t, pa=None, ta=None, pb=None, tb=None) -> bool | None:
    """Smoke-aware sight, with each counterfactual position's own stance time."""
    pa = fights._live_pos(rnd, a, t) if pa is None else pa
    pb = fights._live_pos(rnd, b, t) if pb is None else pb
    if pa is None or pb is None:
        return None
    ends, over = [], []
    for slot, p, t_end in ((a, pa, t if ta is None else ta), (b, pb, t if tb is None else tb)):
        st = stance.boxed_stance(rnd, slot, t_end)
        ends.append((p[0], p[1], None) if st is None else (st.eye_px[0], st.eye_px[1], None))
        over.append(() if st is None else st.see_over)
    return engine.los(rnd.geo, ends[0], ends[1], rnd.smokes_at(t), see_over=(over[0], over[1]))


def exposures(rnd, a, b, t_lo, t_hi) -> list[dict]:
    """Unbroken clear-line runs; unknown positions close a run like blocked sight."""
    runs = []
    current = None
    for t in _sample_times(t_lo, t_hi):
        if line_clear(rnd, a, b, t):
            if current is None:
                current = {"on": t, "off": None, "at_start": t == t_lo}
                runs.append(current)
        elif current is not None:
            current["off"] = t
            current = None
    return runs


def opener(rnd, player, enemy, t_o) -> str:
    """Attribute the opening to moves that were necessary for the clear line (R3)."""
    earlier = t_o - pc.OPENER_DELTA_S
    pp = fights._live_pos(rnd, player, earlier)
    ep = fights._live_pos(rnd, enemy, earlier)
    p_blocked = pp is not None and line_clear(rnd, player, enemy, t_o, pa=pp, ta=earlier) is False
    e_blocked = ep is not None and line_clear(rnd, player, enemy, t_o, pb=ep, tb=earlier) is False
    if p_blocked and e_blocked:
        return "both"
    if p_blocked:
        return "peeker"
    if e_blocked:
        return "holder"
    return "holder" if still(rnd, player, t_o) else "unclear"


def closed_by_player(rnd, player, enemy, t_c) -> bool:
    """Would the line stay clear if the player had kept their earlier position?"""
    earlier = t_c - pc.OPENER_DELTA_S
    pp = fights._live_pos(rnd, player, earlier)
    return pp is not None and line_clear(rnd, player, enemy, t_c, pa=pp, ta=earlier) is True


def is_jiggle_exposure(rnd, player, enemy, exp) -> bool:
    """A brief exposure opened and closed by the living player's movement (R3a)."""
    on, off = exp["on"], exp["off"]
    return (off is not None and off - on <= pc.JIGGLE_MAX_S
            and rnd.alive(player, off) and rnd.alive(enemy, off)
            and opener(rnd, player, enemy, on) in ("peeker", "both")
            and closed_by_player(rnd, player, enemy, off))


def first_gun_event(rnd, blob, slot, group) -> tuple[float | None, int | None]:
    """Recover damage sources lost by _events; kills always qualify (D5)."""
    gun = {(float(r["t"]), r.get("by"), r.get("target")) for r in blob.get("util") or ()
           if r.get("k") == "damage" and r.get("src") == "gun"}
    for t, kind, by, other, _ in sorted(group, key=lambda e: e[0]):
        target = other if by == slot else slot
        if kind == "kill" or (kind == "damage" and (t, by, target) in gun):
            return t, other
    return None, None


def distance_band(distance) -> str | None:
    """D10 boundaries apply to the unrounded distance."""
    if distance is None:
        return None
    return "close" if distance < pc.DIST_BANDS_M[0] else "mid" if distance < pc.DIST_BANDS_M[1] else "long"


def _flights(blob, geo):
    """D14: sample-mask runs per slot, using the stored segments' raw decimetres."""
    flights = {}
    for slot, t, x, y, z in height_motion.tracks(blob):
        if z is None:
            continue
        mask = height_motion.airborne(x, y, z, blob["hz"], geo.m_per_px)
        edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
        flights.setdefault(slot, []).extend((float(t[i]), float(t[j - 1]))
                                            for i, j in edges.reshape(-1, 2))
    return flights


def _sideways(rnd, slot, opp, t):
    start, enemy = rnd.pos(slot, t), rnd.pos(opp, t)
    end = rnd.pos(slot, min(t + pc.WIDE_LOOK_S, rnd.life_end(slot, t)))
    if start is None or enemy is None or end is None:
        return None
    dx, dy = enemy[0] - start[0], enemy[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    return abs((end[1] - start[1]) * dx - (end[0] - start[0]) * dy) / length * rnd.geo.m_per_px


def _history(rnd, blob, slot, opp, main, previous):
    """Return jiggle count and (spot time, distance) candidates, including long damage runs."""
    onset, lower = main["on"], main["on"] - pc.REPEEK_WINDOW_S
    jiggles, spots = 0, []
    for exp in previous:
        if exp["off"] is None or exp["off"] < lower:
            continue
        if is_jiggle_exposure(rnd, slot, opp, exp):
            jiggles += 1
        else:
            spots.append(exp["off"])
    for r in blob.get("util") or ():
        if r.get("k") != "damage" or {r.get("by"), r.get("target")} != {slot, opp}:
            continue
        end = r["t"] if r.get("t1") is None else r["t1"]
        if r["t"] < onset and end >= lower:
            spots.append(min(end, onset - pc.ONSET_STEP_S))
    here = rnd.pos(slot, onset)
    candidates = []
    for at in spots:
        there = rnd.pos(slot, at)
        distance = None if here is None or there is None else math.hypot(
            here[0] - there[0], here[1] - there[1]) * rnd.geo.m_per_px
        candidates.append((at, distance))
    return jiggles, candidates


def _label(rnd, blob, slot, opp, main, group, speed, sideways, candidates, flights, abilities):
    """R4 precedence; unknown measurements never stand in for a negative check."""
    t = main["on"]
    positions = all(fights._live_pos(rnd, s, at) is not None
                    for s in (slot, opp) for at in (t, t - pc.OPENER_DELTA_S))
    who = opener(rnd, slot, opp, t) if positions else None
    nearby_flights = [(a, b) for a, b in flights.get(slot, ())
                      if a <= t + pc.JUMP_LOOK_S and b >= t - pc.JUMP_LOOK_S]
    movement_cast = blob.get("movement_casts") and any(r.get("k") == "cast" and r.get("by") == slot
        and abs(r["t"] - t) <= fights.DASH_EXCLUDE_S for r in blob.get("util") or ())
    if movement_cast or any(a <= d and b >= c for a, b in nearby_flights
                            for c, d in abilities.get(slot, ())):
        return who, "ability_peek", None
    if nearby_flights and who in ("peeker", "both"):
        return who, "jump_peek", None
    if speed is None or not positions:
        return who, "other", "unmeasured"
    if who in ("holder", "unclear"):
        held_speed = max_speed(rnd, slot, t - pc.HELD_LOOK_S, t)
        if held_speed is None:
            return who, "other", "unmeasured"
        if who == "holder":
            return (who, "held", None) if held_speed <= pc.HELD_MAX_MPS else (who, "other", "caught_moving")
        return who, "other", "unclear"
    off = math.inf if main["off"] is None else main["off"]
    for at, kind, _, other, _ in group:
        if kind != "kill" or other != opp or not t <= at <= min(off + pc.EVENT_SLACK_S, t + pc.JIGGLE_MAX_S):
            continue
        velocity = windowed_velocity(rnd, slot, at)
        before, now = rnd.pos(slot, t - pc.OPENER_DELTA_S), rnd.pos(slot, at)
        if velocity is None or before is None or now is None:
            return who, "other", "unmeasured"
        if velocity[0] * (before[0] - now[0]) + velocity[1] * (before[1] - now[1]) > 0:
            return who, "jiggle", None
    if any(d is not None and d <= pc.REPEEK_SPOT_M for _, d in candidates):
        return who, "re_peek", None
    if any(d is None for _, d in candidates):
        return who, "other", "unmeasured"
    if speed >= pc.SWING_MPS:
        if sideways is None:
            return who, "other", "unmeasured"
        return (who, "wide_swing", None) if sideways >= pc.WIDE_M else (who, "other", "fast_short")
    return (who, "slow_peek", None) if speed > pc.HELD_MAX_MPS else (who, "other", "still_peek")


def _opening(rnd, blob, slot, i, group, flights, abilities):
    t0, t1 = group[0][0], max(e[4] for e in group)
    event, opp = first_gun_event(rnd, blob, slot, group)
    row = dict(slot=slot, i=i, opponent=opp, t0=fights._r2(t0), t1=fights._r2(t1),
               t_event=fights._r2(event), t_first_sight=None, t_onset=None, opener=None,
               type="other", reason="no_gun", jiggles=0, speed_mps=None, sideways_m=None,
               exposure_s=None, repeek_gap_s=None, repeek_spot_m=None, distance_m=None, band=None,
               result="unresolved", gun_class=fights._moving(rnd, blob, slot, t0, t1)[0]["gun_class"])
    if event is None:
        return row
    sights = [fights._first_sight(rnd, a, b, t0 - fights.SIGHT_LOOK_S, t1)[0]
              for a, b in ((slot, opp), (opp, slot))]
    row["t_first_sight"] = fights._r2(min((t for t in sights if t is not None), default=None))
    kills = [e for e in group if e[1] == "kill" and e[3] == opp]
    if kills:
        row["result"] = "won" if min(kills, key=lambda e: e[0])[2] == slot else "lost"
    distance = fights._dist_m(rnd, slot, opp, event)
    row.update(distance_m=None if distance is None else round(distance, 1), band=distance_band(distance))

    # One scan holds both the history and the main exposure's closing time (D5).
    runs = exposures(rnd, slot, opp, event - pc.ONSET_LOOKBACK_S, t1 + pc.EVENT_SLACK_S)
    main = next((exp for exp in runs if exp["on"] <= event
                 and (exp["off"] is None or event < exp["off"])), None)
    if main is None:
        main = next((exp for exp in reversed(runs) if exp["off"] is not None
                     and exp["off"] <= event <= exp["off"] + pc.EVENT_SLACK_S), None)
    if main is None:
        row["reason"] = "no_line"
        return row
    row["exposure_s"] = fights._r2(None if main["off"] is None else main["off"] - main["on"])
    if main["at_start"]:
        row["reason"] = "no_onset"
        return row
    t = main["on"]
    speed, sideways = windowed_speed(rnd, slot, t), _sideways(rnd, slot, opp, t)
    jiggles, candidates = _history(rnd, blob, slot, opp, main, runs[:runs.index(main)])
    nearby = [c for c in candidates if c[1] is not None and c[1] <= pc.REPEEK_SPOT_M]
    latest = max(nearby or candidates, key=lambda c: c[0], default=None)
    distance = fights._dist_m(rnd, slot, opp, t)
    row.update(t_onset=fights._r2(t), speed_mps=fights._r2(speed), sideways_m=fights._r2(sideways),
               jiggles=jiggles, repeek_gap_s=None if latest is None else fights._r2(t - latest[0]),
               repeek_spot_m=None if latest is None else fights._r2(latest[1]),
               distance_m=None if distance is None else round(distance, 1), band=distance_band(distance))
    row["opener"], row["type"], row["reason"] = _label(
        rnd, blob, slot, opp, main, group, speed, sideways, candidates, flights, abilities)
    return row


def round_openings(rnd, blob) -> list[dict]:
    """D6: recompute fights locally, with one slot-level opening per fight."""
    flights = _flights(blob, rnd.geo)
    abilities = height_motion.blackouts(blob, rnd.geo)
    return [_opening(rnd, blob, slot, i, group, flights, abilities) for slot in sorted(rnd.team)
            for i, group in enumerate(fights._split(fights._events(rnd, blob, slot)))]
