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


# ---------------------------------------------------------------- heights (control heights, parts 3-4)

TOY_Z0 = 37          # world decimetres of the toys' "0 m": an offset, so nothing can assume the origin is 0


def z_dm(z_m: float) -> int:
    return TOY_Z0 + int(round(z_m * 10))


def z_track(points, t0: float = 0.0, t1: float = 30.0, hz: int = HZ) -> list[dict]:
    """`track` with heights: `points` = [(t, x px, y px, yaw, z m), ...], z linear between points and
    stored as the condenser stores it (revision 11: whole decimetres of world z, delta-encoded)."""
    [segment] = track([p[:4] for p in points], t0, t1, hz)
    pts = sorted(points)
    ts = t0 + np.arange(len(segment["u"])) / hz
    z = [z_dm(v) for v in np.interp(ts, [p[0] for p in pts], [p[4] for p in pts])]
    segment["z"] = [z[0], *(b - a for a, b in zip(z, z[1:]))]
    return [segment]


def height_blob(players: dict, *, t_end: float = 30.0, util=(), deaths=None, n: int = 1) -> dict:
    """`blob` whose players carry heights: {slot: (side, [(t, x, y, yaw, z m), ...])}. A player given
    four-value points has no z (a round from before revision 11)."""
    out = blob({s: (side, [p[:4] for p in pts]) for s, (side, pts) in players.items()}, t_end=t_end, util=util,
               deaths=deaths)
    out["round"] = n
    for s, (_, pts) in players.items():
        if len(pts[0]) == 5:
            out["tracks"][str(s)] = z_track(pts, 0.0, t_end)
    return out


def standing(x: float, y: float, z_m: float, side: str = "A", yaw: int = 0):
    """A player who stands still at a height for the whole round."""
    return side, [(0.0, x, y, yaw, z_m)]


def height_rounds(make, matches: int = 2, rounds: int = 3, t_end: float = 10.0) -> list:
    """[(match id, round number, blob)] for the height build: `make(match index, round number)` gives
    each round's players ({slot: (side, points)}) or a whole blob."""
    out = []
    for m in range(matches):
        for n in range(1, rounds + 1):
            made = make(m, n)
            out.append((f"match-{m}", n, made if "tracks" in made else height_blob(made, t_end=t_end, n=n)))
    return out


FAST_HZ = 125        # the rate real rounds are stored at: the walk rules are tuned on it


def fast_blob(players: dict, *, t_end: float = 10.0, util=(), n: int = 1) -> dict:
    """`height_blob` at FAST_HZ: {slot: (side, [(t, x, y, yaw, z m), ...])}."""
    out = blob({s: (side, [p[:4] for p in pts]) for s, (side, pts) in players.items()}, t_end=t_end, util=util)
    out["round"], out["hz"] = n, FAST_HZ
    for s, (_, pts) in players.items():
        out["tracks"][str(s)] = z_track(pts, 0.0, t_end, hz=FAST_HZ)
    return out


def stair_run(x0: int = 160, x1: int = 224, z0: float = 0.0, z1: float = 4.0, t0: float = 1.0, t1: float = 3.0,
              side: str = "A", y: int = 204):
    """Runs east from x0 to x1 px (9 m in 2 s by default) climbing z0 to z1, between two level runs."""
    return side, [(0.0, x0 - 32, y, 0, z0), (t0, x0, y, 0, z0), (t1, x1, y, 0, z1), (t1 + 1.0, x1 + 32, y, 0, z1),
                  (10.0, x1 + 32, y, 0, z1)]


def fast_rounds(make, matches: int = 2, rounds: int = 3, t_end: float = 10.0) -> list:
    """`height_rounds` at FAST_HZ: `make(match index, round number)` gives each round's players."""
    return [(f"match-{m}", n, fast_blob(make(m, n), t_end=t_end, n=n))
            for m in range(matches) for n in range(1, rounds + 1)]


def _cells_in(rect) -> np.ndarray:
    """Flat cells whose centre lies in the px rectangle (x0, y0, x1, y1)."""
    from app.control.geometry import CELL, GRID

    x0, y0, x1, y1 = rect
    ys, xs = np.divmod(np.arange(GRID * GRID), GRID)
    cx, cy = xs * CELL + CELL / 2, ys * CELL + CELL / 2
    return np.flatnonzero((cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1))


def toy_heights(name: str, floors, *, ground=(), upper=(), unresolved=(), links=(), walls=(), cache_dir=None,
                specials=None, slope=None):
    """A toy geometry with a hand-made height asset (heights.py), visibility built per node.

    Every walkable cell starts at 0 m. `ground` = [(px rect, z m), ...] sets the lowest floor (later
    entries win); `slope` = (x0 px, x1 px, z0 m, z1 m) makes the ground climb linearly from x0 to x1
    (z0 before, z1 after); `upper` = [(px rect, z m), ...] adds a floor above (twice for a third);
    `unresolved` = [px rect, ...] takes the height away (flat 2D there). Floors of neighbouring cells
    within STEP_UP_M connect, as the build infers; `links` = [((x, y, floor), (x, y, floor), one_way[, EDGE_*]),
    ...] adds a walked connection between two neighbouring cells: a climb (both ways), or a drop (one way: a fall
    unless the fourth element says EDGE_SLIDE)."""
    import copy

    from app.control import height_build as hb
    from app.control import heights as hc
    from app.control.geometry import CELL, GRID, attach_heights

    key = ("heights", name, tuple(floors), tuple(walls), repr((ground, upper, unresolved, links, specials, slope)))
    if key in _CACHE:
        return _CACHE[key]
    flat = toy_geometry(name + "-flat", floors, walls, cache_dir, specials)
    geo = copy.copy(flat)
    geo.rows = geo.row_of = None
    walk = geo.walk.ravel()
    z = {int(c): [0.0] for c in np.flatnonzero(walk)}
    if slope is not None:
        x0, x1, z0, z1 = slope
        for c in z:
            cx = (c % GRID) * CELL + CELL / 2
            z[c] = [float(np.interp(cx, [x0, x1], [z0, z1]))]
    for rect, height in ground:
        for c in _cells_in(rect):
            if int(c) in z:
                z[int(c)] = [float(height)]
    for rect, height in upper:
        for c in _cells_in(rect):
            if int(c) in z:
                z[int(c)] = sorted(z[int(c)] + [float(height)])
    gone = {int(c) for rect in unresolved for c in _cells_in(rect) if int(c) in z}
    for c in gone:
        del z[c]
    lowest = min(h for hs in z.values() for h in hs)
    heights = {c: [int(round((h - lowest) * 10)) for h in hs] for c, hs in z.items()}
    edges = {tuple(e[:4]): e[4] for e in hb.connect(heights, {}, geo).tolist()}
    for (ax, ay, fa), (bx, by, fb), one_way, *how in links:
        a, b = geo.cell_of_px(ax, ay), geo.cell_of_px(bx, by)
        edges[(a, fa, b, fb)] = (how[0] if how else hc.EDGE_FALL) if one_way else hc.EDGE_STEP
        if not one_way:
            edges[(b, fb, a, fa)] = hc.EDGE_STEP
    asset_floors = -np.ones((GRID * GRID, hc.MAX_FLOORS), np.int16)
    for c, hs in heights.items():
        asset_floors[c, : len(hs)] = hs
    unresolved_mask = np.zeros(GRID * GRID, bool)
    unresolved_mask[list(gone)] = True
    asset = hc.HeightAsset(asset_floors.reshape(GRID, GRID, hc.MAX_FLOORS),
                           np.zeros((GRID, GRID, hc.MAX_FLOORS), np.int16),
                           (asset_floors[:, 0] >= 0).reshape(GRID, GRID), unresolved_mask.reshape(GRID, GRID),
                           np.array(sorted((*key, kind) for key, kind in edges.items()), np.int32).reshape(-1, 5),
                           {"origin_z": z_dm(lowest)})
    attach_heights(geo, asset)
    geo.name = name
    visibility(geo, cache_dir or _DIR)
    _CACHE[key] = geo
    return geo
