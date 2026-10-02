"""Toy maps and rounds for the map-control tests (app/control).

A toy map is a few floor rectangles (in minimap pixels, 0..1024) with walls inside them; the rest is
void. Rooms are small so each map's visibility bitsets build in seconds; `toy_geometry` caches them
per test session under one temporary directory. A toy round is a blob in format v1 (format.py) where
each player stands still (or walks a straight line) and faces a fixed direction.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from app.control.geometry import PX, geometry_from_masks, visibility

TOY_SCALE = 7e-5          # Ascent's: 0.14 m per pixel, 1.12 m cells
HZ = 16

_CACHE: dict = {}
_DIR = Path(tempfile.mkdtemp(prefix="control-toys-"))

# A hall of 40 x 25 cells (x 96-416, y 96-296 px; cells are 8 px).
HALL = (96, 96, 416, 296)


def open_hall():
    return toy_geometry("Open", [HALL])


def door_hall():
    """The hall split by a wall at x 200-216 (cells 25-26), with a one-cell door at the south
    end (y 288-296, row 36). West of it: 12 columns nobody can reach but through the door."""
    return toy_geometry("Door", [HALL], [(200, 96, 216, 288)])


def midwall_hall():
    """A wall from the north edge down to y 248 at x 248-264: its two sides share the south strip."""
    return toy_geometry("Midwall", [HALL], [(248, 96, 264, 248)])


def two_rooms(specials=None):
    """Two 10 x 10-cell rooms with void between them, optionally joined by `specials`."""
    return toy_geometry("Rooms", [(96, 96, 176, 176), (304, 96, 384, 176)], specials=specials)


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
    visibility(geo, cache_dir or _DIR)
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


# ---------------------------------------------------------------- the flat reference (heights, part 4)


def toy_ability(code, name, x, y, by, t=0.0, t1=10.0, kind="Zone", **extra) -> dict:
    u, v = uv(x, y)
    return {"k": "ability", "t": t, "t1": t1, "by": by, "kind": kind, "code": code, "name": name, "u": u, "v": v,
            **extra}


def barrier_hall():
    """The open hall with a barrier line down x 256: A starts west of it, B east. A copy: `open_hall()`
    is shared, so the barrier isn't left on it."""
    import copy

    from app.control.geometry import GRID

    geo = copy.copy(open_hall())
    geo.barrier = np.zeros((GRID, GRID), bool)
    geo.barrier[:, geo.cell_of_px(256, 200) % GRID] = True
    return geo


def reference_rounds() -> dict:
    """name -> (geometry, blob, link or None): the rounds whose control output is pinned in
    tests/fixtures/control/reference_flat.json. Recorded with the engine as it was before the per-floor
    work (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4): a map without a height
    asset must keep giving these bytes. Between them they walk every part of the engine a flat map uses:
    moving and still players, a door, a wall, a one-way special, smokes (hollow and solid), Viper's wall,
    a trip, an alarmbot, a Chamber trap, a turret, a camera, a drone, a damage zone, flashes, a nearsight,
    statuses, a reveal, a wallbang, deaths (the blob's and the link's), the spike and the barriers."""
    from app.control.engine import ControlLink

    still = lambda side, x, y, yaw: (side, [(0.0, x, y, yaw)])                       # noqa: E731
    walk = lambda side, x0, y0, x1, y1, yaw, t1=10.0: (side, [(0.0, x0, y0, yaw), (t1, x1, y1, yaw)])  # noqa: E731
    out = {}
    out["open"] = (open_hall(), blob({0: walk("A", 120, 150, 260, 150, 0), 1: still("A", 130, 250, 20),
                                      5: still("B", 400, 150, 180), 6: walk("B", 400, 270, 300, 200, 200)},
                                     t_end=10.0), None)
    out["door"] = (door_hall(), blob({0: walk("A", 150, 150, 190, 292, 90), 1: still("A", 120, 200, 0),
                                      5: still("B", 400, 200, 180), 6: walk("B", 380, 120, 240, 280, 135)},
                                     t_end=10.0, t_decided=9.0), None)
    smokes = [toy_ability("Wraith", "4_Smoke", 250, 270, 5, t=1.03, t1=6.3),
              toy_ability("Sarge", "4_Smoke_Production", 330, 150, 0, t=2.0, t1=8.0),
              toy_ability("Phoenix", "MolotovFire", 150, 200, 5, t=3.0, t1=7.0),
              {"k": "flash", "t": 0.5, "by": 5, "ability": "phoenix_curveball_left", "targets": [0],
               "hits": [[0, 1.0, 0.8]]},
              {"k": "nearsight", "t": 4.0, "by": 0, "ability": "omen_paranoia", "targets": [5], "hits": [[5, 4.2, None]]},
              {"k": "status", "t": 5.0, "t1": 6.5, "by": 5, "target": 1, "status": "slowed"},
              {"k": "status", "t": 2.5, "t1": 3.5, "by": 0, "target": 6, "status": "concussed"},
              {"k": "reveal", "t": 6.0, "t1": 7.5, "by": 0, "target": 6},
              {"k": "damage", "t": 7.0, "t1": 7.2, "by": 5, "target": 0, "src": "gun", "wall": True, "n": 2},
              {"k": "damage", "t": 3.2, "t1": 3.6, "by": 5, "target": 1, "src": "ability", "wall": False, "n": 3},
              {"k": "shot", "t": 7.0, "by": 5, "u": 4000, "v": 2000},
              toy_ability("", "Spike", 300, 250, 1, t=4.0, t1=12.0, kind="Bomb")]
    out["midwall"] = (midwall_hall(), blob({0: walk("A", 120, 150, 230, 270, 45), 1: still("A", 150, 200, 0),
                                            5: still("B", 400, 200, 180), 6: walk("B", 390, 120, 290, 270, 160)},
                                           t_end=12.0, t_decided=11.0, util=smokes, deaths={6: 8.3}), None)
    rooms = two_rooms([{"kind": "drop", "a": list(uv(170, 170)), "b": list(uv(310, 170)), "one_way": True}])
    out["rooms"] = (rooms, blob({0: walk("A", 110, 110, 165, 165, 45), 1: still("A", 150, 120, 0),
                                 5: walk("B", 370, 110, 320, 165, 135)}, t_end=10.0), None)
    setups = [toy_ability("Pandemic", "E_SmokeScreenManager", 250, 120, 4, t=0.0, t1=10.0, kind="GameObject",
                          points=[list(uv(250, 100)), list(uv(250, 230))], on=[[2.0, 5.0], [7.0, None]]),
              toy_ability("Gumshoe", "4_TripWire", 300, 240, 0, kind="GameObject", end=list(uv(300, 292))),
              toy_ability("Killjoy", "Q_StealthAlarmbot", 180, 270, 1, kind="GameObject"),
              toy_ability("Deadeye", "E_Trap", 380, 120, 5, kind="GameObject", t1=6.0),
              toy_ability("Killjoy", "E_Turret", 200, 120, 1, kind="Pawn", yaw=0, yaws=[[0.0, 0], [4.0, 60]])]
    out["setups"] = (open_hall(), blob({0: still("A", 150, 200, 0), 1: still("A", 130, 120, 30),
                                        4: walk("A", 120, 270, 220, 250, 0),
                                        5: walk("B", 400, 110, 330, 260, 150), 6: still("B", 390, 250, 190)},
                                       t_end=10.0, util=setups), None)
    pawns = [toy_ability("Gumshoe", "E_PossessableCamera", 350, 150, 0, kind="Pawn", yaw=90,
                         possessed=[[1.0, 4.0]], yaws=[[0.0, 90], [2.0, 150]]),
             toy_ability("Hunter", "E_Drone", 150, 250, 1, t=3.0, t1=8.0, kind="Pawn",
                         path=[[3.0, *uv(150, 250)], [5.0, *uv(250, 250)], [8.0, *uv(380, 200)]],
                         possessed=[[3.0, 8.0]], yaws=[[3.0, 0], [5.0, 340]]),
             toy_ability("Guide", "Q_PossessableScout", 400, 120, 5, t=2.0, t1=6.0, kind="Pawn",
                         path=[[2.0, *uv(400, 120)], [6.0, *uv(250, 130)]])]
    out["pawns"] = (open_hall(), blob({0: still("A", 150, 200, 0), 1: still("A", 130, 270, 0),
                                       5: still("B", 400, 280, 180), 6: walk("B", 400, 150, 320, 150, 180)},
                                      t_end=10.0, util=pawns, deaths={5: 6.4}), None)
    link = ControlLink(sides={0: "attack", 1: "attack", 5: "defense", 6: "defense"}, db_deaths=((1, 5.2),))
    out["barrier"] = (barrier_hall(), blob({0: walk("A", 150, 150, 300, 160, 0), 1: still("A", 150, 250, 0),
                                            5: still("B", 400, 200, 180), 6: walk("B", 380, 270, 270, 270, 180)},
                                           t_end=10.0), link)
    return out
