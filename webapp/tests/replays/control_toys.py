"""Toy maps and rounds for the map-control tests (app/control).

A toy map is a few floor rectangles (in minimap pixels, 0..1024) with walls inside them; the rest is
void. Rooms are small so each map's visibility bitsets build in seconds; `toy_geometry` caches them
per test session under one temporary directory. A toy round is a blob in format v1 (format.py) where
each player stands still (or walks a straight line) and faces a fixed direction.
"""

from __future__ import annotations

import numpy as np

from app.control.geometry import PX, geometry_from_masks, visibility

TOY_SCALE = 7e-5          # Ascent's: 0.14 m per pixel, 1.12 m cells
HZ = 16

_CACHE: dict = {}


def rect_mask(rects) -> np.ndarray:
    mask = np.zeros((PX, PX), bool)
    for x0, y0, x1, y1 in rects:
        mask[y0:y1, x0:x1] = True
    return mask


def toy_geometry(name: str, floors, walls=(), cache_dir=None, specials=None, scale: float = TOY_SCALE):
    """A geometry whose sight blocks everywhere but the floors, minus the walls; visibility built
    (or loaded) under `cache_dir`."""
    key = (name, tuple(floors), tuple(walls), repr(specials))
    if key in _CACHE:
        return _CACHE[key]
    floor = rect_mask(floors)
    wall = rect_mask(walls)
    geo = geometry_from_masks(name, ~floor | wall, floor & ~wall, scale, specials)
    visibility(geo, cache_dir)
    _CACHE[key] = geo
    return geo


def uv(x_px: float, y_px: float) -> tuple[int, int]:
    return int(round(x_px / PX * 10000)), int(round(y_px / PX * 10000))


def track(points, t0: float = 0.0, t1: float = 30.0, hz: int = HZ) -> list[dict]:
    """One segment from `points` = [(t, x px, y px, yaw), ...] (piecewise linear, yaw held)."""
    n = int(round((t1 - t0) * hz)) + 1
    ts = t0 + np.arange(n) / hz
    pts = sorted(points)
    xs = np.interp(ts, [p[0] for p in pts], [p[1] for p in pts])
    ys = np.interp(ts, [p[0] for p in pts], [p[2] for p in pts])
    yaw_idx = np.searchsorted([p[0] for p in pts], ts, side="right") - 1
    yaws = [int(pts[max(0, i)][3]) % 360 for i in yaw_idx]
    u = [uv(x, y)[0] for x, y in zip(xs, ys)]
    v = [uv(x, y)[1] for x, y in zip(xs, ys)]
    enc = lambda a: [a[0], *(b - c for c, b in zip(a, a[1:]))]  # noqa: E731
    yaw_d = [yaws[0], *(((b - c + 180) % 360) - 180 for c, b in zip(yaws, yaws[1:]))]
    return [{"t0": t0, "u": enc(u), "v": enc(v), "yaw": yaw_d}]


def blob(players: dict, *, t_end: float = 30.0, t_decided: float | None = None, util=(), deaths=None) -> dict:
    """`players` = {slot: (side, [(t, x, y, yaw), ...])}; `deaths` = {slot: t}."""
    deaths = deaths or {}
    return {
        "v": 1, "round": 1, "map": "Toy", "hz": HZ, "t_start": 0.0,
        "t_decided": t_end if t_decided is None else t_decided, "t_end": t_end,
        "players": [{"slot": s, "agent": "Jett", "side": side} for s, (side, _) in players.items()],
        "tracks": {str(s): track(pts, 0.0, t_end) for s, (_, pts) in players.items()},
        "alive": {str(s): [[0.0, deaths.get(s), "kill" if s in deaths else None]] for s in players},
        "kills": [], "plant": None, "defuse": None, "util": list(util),
    }
