"""Peek geometry: design = this build's PLAN; labeller not wired into the worker."""

import math

import numpy as np

from app.control import engine, stance
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
