"""Builds a map's heights from stored rounds (docs/superpowers/specs/2026-10-01-control-heights-design.md,
part 3). The constants and the asset are app/control/heights.py's; scripts/build_control_heights.py is the
command.

Rounds in (`[(match id, round number, blob), ...]`, blobs of condenser revision 11, whose tracks carry z),
a `HeightBuild` out:

- **Stands and walks.** Each player's track is cut into stands: runs of at least STAND_S in which z stays within
  STAND_TOL_M of the run's median. A short stand with lower ground just before and just after it is the top of
  a jump and is dropped (STAND_APEX_S). What is left of the track gives walks (height_motion.py): runs in which
  the player moves along the ground, uphill or down. For ABILITY_BLACKOUT_S after a movement ability a player
  gives neither. Stands and walks near a live temporary platform (heights.PLATFORMS) lose those cells. A stand
  belongs to every cell its samples pass through, with its median z; a walk gives each cell the lowest z it had
  there.
- **Floors.** In each cell those heights are grouped into levels: groups within FLOOR_TOL_M that have
  FLOOR_MIN_STANDS of them from FLOOR_MIN_ROUNDS rounds in FLOOR_MIN_MATCHES matches. Levels less than
  FLOOR_SEP_M apart are one band (a slope's levels chain); a height no level holds joins a band only within
  FLOOR_TOL_M of it, so a stray sample between two floors is nobody's. A band's height is its low end
  (LOW_PCT): a player can't be below the floor. The lowest band needs no stand; any band above it needs a group
  of stands that passes the same rule. Two groups of stands in one band, a band
  of stands alone spread over more than FLOOR_SPREAD_MAX_M, or more than MAX_FLOORS floors leave the cell
  unresolved, with the reason.
- **Unsampled cells.** A walkable cell with no floor takes a ground floor when at least FILL_MIN_NEIGHBOURS cells
  within FILL_R cells of it (by walking) have floors and all of those floors agree within FILL_TOL_M: their
  median, and no upper floors. Where they disagree, it is filled along the gradient when it has single-floor
  neighbours directly beside it on opposite sides, no steeper than SLOPE_MAX between them, and a ground run
  crossed from one to the other through it at those floors' heights: the mean of the pair. Otherwise it is unresolved. Filling never
  averages across a drop nobody walked, and a filled cell never fills another.
- **Connections.** Floors of neighbouring cells (all 8) within STEP_UP_M connect both ways. A bigger step connects
  only where it was walked (CONNECT_MIN_ROUNDS rounds, or the run that crossed a gradient-filled cell). Seen
  going up it connects both ways (what is climbed can be dropped from); seen only going down it is one-way: a
  slide when the player stayed on the ground, a fall otherwise. Unresolved cells carry no connections here: the
  engine gives them today's 2D walking.
- **Unresolved areas, loudly.** Unresolved cells are grouped into areas (8-connected), each with its size,
  its bounding box in minimap px and why.
- **Readiness.** `visited` is the share of walkable cells any sample fell in; `supported` the share with a
  floor from stands or walks (not filled). A map is ready at HEIGHT_SUPPORTED_MIN supported with no unresolved
  area larger than UNRESOLVED_MAX cells touching a cell with two floors.

Local tooling only, like the engine: the web app never imports this module.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

import hashlib
from collections import Counter

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from app.control import height_motion as hm
from app.control import heights as hc
from app.control.geometry import CELL, GRID, PX, Geometry
from app.control.height_motion import Walk

DM = 10.0     # decimetres per metre: every height in here is whole decimetres unless a name says _m

# Why a walkable cell is unresolved (the report's `why`).
NO_SAMPLES = "no samples"
NEIGHBOURS_DISAGREE = "neighbours disagree"
SPREAD = "spread too wide"
TOO_CLOSE = "floors too close"
TOO_MANY = "too many floors"


@dataclass
class Stand:
    round: int            # index into the build's rounds
    slot: int
    t0: float
    t1: float
    z: int                # median, world dm
    cells: tuple          # flat cells its samples pass through, in order (no repeats in a row)
    x: float              # mean position, px
    y: float


_tracks = hm.tracks


def _runs(z: np.ndarray, tol: float) -> list[tuple[int, int]]:
    """Greedy [i, j) runs in which every z is within `tol` of the run's median."""
    out, i, n = [], 0, len(z)
    while i < n:
        lo = hi = z[i]
        j = i + 1
        while j < n:
            lo2, hi2 = min(lo, z[j]), max(hi, z[j])
            if hi2 - lo2 > 2 * tol:
                break
            if hi2 - lo2 > tol:   # inside the tolerance of some member, maybe not of the median: look
                med = float(np.median(z[i:j + 1]))
                if max(hi2 - med, med - lo2) > tol:
                    break
            lo, hi, j = lo2, hi2, j + 1
        out.append((i, j))
        i = j
    return out


def stands(blob: dict, geo: Geometry, round_index: int = 0, skip: dict | None = None) -> list[Stand]:
    """The round's stands, each player's in time order. Segments without z give none; `skip` leaves out the
    time after a movement ability (height_motion.blackouts)."""
    hz = blob["hz"]
    tol = hc.STAND_TOL_M * DM
    need = int(np.ceil(hc.STAND_S * hz - 1e-9)) + 1     # samples spanning STAND_S
    apex_n = int(round(0.25 * hz))                      # how far either side an apex looks for lower ground
    out = []
    for slot, t, x, y, z in _tracks(blob, skip):
        if z is None:
            continue
        for i, j in _runs(z, tol):
            if j - i < need:
                continue
            med = float(np.median(z[i:j]))
            if (j - i - 1) / hz < hc.STAND_APEX_S:
                before, after = z[max(0, i - apex_n):i], z[j:j + apex_n]
                if len(before) and len(after) and before.min() < med - tol and after.min() < med - tol:
                    continue    # lower just before and just after: the top of a jump, not a place to stand
            cells = (np.clip(y[i:j], 0, PX - 1).astype(int) // (PX // GRID)) * GRID \
                + np.clip(x[i:j], 0, PX - 1).astype(int) // (PX // GRID)
            keep = np.r_[True, cells[1:] != cells[:-1]]
            out.append(Stand(round_index, slot, float(t[i]), float(t[j - 1]), int(round(med)),
                             tuple(int(c) for c in cells[keep]), float(x[i:j].mean()), float(y[i:j].mean())))
    out.sort(key=lambda s: (s.slot, s.t0))
    return out


def platforms(blob: dict) -> list[tuple[float, float, float, float]]:
    """Live temporary platforms (heights.PLATFORMS) as (t0, t1, x px, y px)."""
    out = []
    for e in blob.get("util") or []:
        if e.get("k") == "ability" and f"{e.get('code')}_{e.get('name')}" in hc.PLATFORMS:
            ends = [v for v in (e.get("t1"), e.get("gone"), blob.get("t_end")) if v is not None]
            out.append((float(e["t"]), float(min(ends)), e["u"] * PX / 10000, e["v"] * PX / 10000))
    return out


def off_platforms(found: list[Stand], plats: list, geo: Geometry) -> list[Stand]:
    """`found` (stands or walks) without the cells within PLATFORM_R_M of a platform that was up during the
    run. A run left with no cell is dropped; one that loses cells in its middle becomes a run for each
    stretch that is left (they keep the whole run's times, so nothing joins them again). By cell, not by the
    run's mean position: a long level walk that starts on a wall has its mean far from it."""
    if not plats:
        return found
    r2 = (hc.PLATFORM_R_M / geo.m_per_px + CELL) ** 2      # a cell's centre is within a cell of its samples
    out = []
    for s in found:
        near = [(x, y) for t0, t1, x, y in plats if t0 <= s.t1 and s.t0 <= t1]
        keep = [not any(((c % GRID + 0.5) * CELL - x) ** 2 + ((c // GRID + 0.5) * CELL - y) ** 2 <= r2
                        for x, y in near) for c in s.cells] if near else [True] * len(s.cells)
        if all(keep):
            out.append(s)
            continue
        for i, j in hm._runs_of(np.array(keep)):      # each stretch that is left is a run of its own
            cut = {"cells": s.cells[i:j]}
            if isinstance(s, Walk):
                cut["low"] = s.low[i:j]
            out.append(replace(s, **cut))
    return out


def _enough(count: int, rounds: np.ndarray, matches: np.ndarray) -> bool:
    """The floor rule: FLOOR_MIN_STANDS of them, from FLOOR_MIN_ROUNDS rounds in FLOOR_MIN_MATCHES matches."""
    return count >= hc.FLOOR_MIN_STANDS and len(set(rounds.tolist())) >= hc.FLOOR_MIN_ROUNDS \
        and len(set(matches.tolist())) >= hc.FLOOR_MIN_MATCHES


def _levels(z: np.ndarray, rounds: np.ndarray, matches: np.ndarray) -> list[np.ndarray]:
    """The supported levels among some heights: groups (the densest FLOOR_TOL_M window first, then the next)
    that pass the floor rule, each as a mask over `z`. A height no group holds is in none."""
    tol = hc.FLOOR_TOL_M * DM
    left = np.ones(len(z), bool)
    levels = []
    while left.any():
        zs = np.sort(z[left])
        counts = np.searchsorted(zs, zs + 2 * tol, side="right") - np.arange(len(zs))
        start = zs[int(np.argmax(counts))]       # the densest window (the lowest of equals)
        members = left & (z >= start) & (z <= start + 2 * tol)
        for _ in range(2):                       # settle on the heights around the group's own median
            med = float(np.median(z[members]))
            members = left & (np.abs(z - med) <= tol)
        left &= ~members
        if _enough(int(members.sum()), rounds[members], matches[members]):
            levels.append(members)
    return levels


def stand_groups(z: np.ndarray, rounds: np.ndarray, matches: np.ndarray) -> list[float]:
    """The groups of stands that pass the floor rule: each one's 10th-to-90th percentile spread, in dm."""
    out = []
    for members in _levels(z, rounds, matches):
        p10, p90 = np.percentile(z[members], [10, 90])
        out.append(float(p90 - p10))
    return out


def group_cell(z: np.ndarray, rounds: np.ndarray, matches: np.ndarray,
               stand: np.ndarray | None = None) -> tuple[list[tuple[int, int]], str | None, int]:
    """One cell's ground samples (z in world dm, round index, match index, whether it is a stand: all of them
    without `stand`) as floors: ([(height, spread), ...] lowest first, None, the ground floor's KIND_*), or
    ([], reason, KIND_NONE) when the cell can't be resolved, or ([], None, KIND_NONE) when nothing is a floor.

    Bands are built from supported levels only (`_levels`, over stands and walks alike): levels less than
    FLOOR_SEP_M apart are one band, and a sample no level holds joins a band only when it lies within
    FLOOR_TOL_M of it. So a stray sample between two floors bridges nothing and is nobody's; a slope's levels
    chain into one band. The lowest band is a floor; any band above it also needs a group of stands that
    passes the floor rule (a boost passes through the air above a cell). A floor's height is the low end of its
    band (LOW_PCT, never the single lowest sample of several). Two groups of stands in one band are two
    levels too close to tell apart; the stands of a band with no walk in it may not spread over
    FLOOR_SPREAD_MAX_M."""
    if stand is None:
        stand = np.ones(len(z), bool)
    tol = hc.FLOOR_TOL_M * DM
    levels = sorted(_levels(z, rounds, matches), key=lambda m: float(z[m].min()))
    bands: list[np.ndarray] = []
    for members in levels:
        if bands and z[members].min() - z[bands[-1]].max() < hc.FLOOR_SEP_M * DM:
            bands[-1] = bands[-1] | members
        else:
            bands.append(members.copy())
    loose = ~np.logical_or.reduce(levels) if levels else np.zeros(len(z), bool)
    bands = [band | (loose & (z >= z[band].min() - tol) & (z <= z[band].max() + tol)) for band in bands]
    floors, kinds = [], []
    for members in bands:
        zs, st = z[members], stand[members]
        groups = stand_groups(zs[st], rounds[members][st], matches[members][st])
        if not groups and (floors or st.all()):
            continue      # above the ground, or with no walk in it, a band needs a group of stands
        if len(groups) >= 2:
            return [], TOO_CLOSE, hc.KIND_NONE
        if st.all() and groups[0] > hc.FLOOR_SPREAD_MAX_M * DM:
            return [], SPREAD, hc.KIND_NONE      # as before the slopes: the stands of one level, on a steep ramp
        low, p10 = np.percentile(zs, [hc.LOW_PCT, 10], method="higher")
        p90 = np.percentile(zs, 90, method="lower")
        floors.append((int(round(float(low))), int(round(float(max(p90 - p10, 0))))))
        kinds.append(hc.KIND_STANDS if groups else hc.KIND_WALKS)
    if len(floors) > hc.MAX_FLOORS:
        return [], TOO_MANY, hc.KIND_NONE
    return floors, None, kinds[0] if kinds else hc.KIND_NONE


@dataclass
class HeightBuild:
    asset: hc.HeightAsset
    report: dict
    stands: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)      # flat cell -> why it is unresolved
    ready: bool = False
    walks: list = field(default_factory=list)


def cell_floors(found: list[Stand], round_match: list[int], geo: Geometry,
                found_walks: list[Walk] = ()) -> tuple[dict, dict, dict]:
    """({cell: [(height, spread), ...]}, {cell: reason}, {cell: the ground floor's KIND_*}) over the walkable
    cells with ground samples. A stand gives each of its cells its median; a walk gives each of its cells the
    lowest z it had there."""
    by_cell: dict[int, list[tuple[int, int, int, bool]]] = defaultdict(list)
    for s in found:
        for cell in set(s.cells):
            by_cell[cell].append((s.z, s.round, round_match[s.round], True))
    for w in found_walks:
        lows: dict[int, int] = {}
        for cell, z in zip(w.cells, w.low):
            lows[cell] = min(z, lows.get(cell, z))
        for cell, z in lows.items():
            by_cell[cell].append((z, w.round, round_match[w.round], False))
    walk = geo.walk.ravel()
    floors, reasons, kinds = {}, {}, {}
    for cell, rows in by_cell.items():
        if not walk[cell]:
            continue
        z, rounds, matches, stand = (np.array(col) for col in zip(*rows))
        got, why, kind = group_cell(z.astype(float), rounds, matches, stand.astype(bool))
        if why is not None:
            reasons[cell] = why
        elif got:
            floors[cell], kinds[cell] = got, kind
    return floors, reasons, kinds


def all_ground(rounds, geo: Geometry) -> tuple[list[Stand], list[Walk], list[int], dict]:
    """Every round's stands and walks, outside blackouts and off platforms; each round's match index; and the
    counts. `rounds` is read once, a round at a time (it may be a generator)."""
    found, found_walks, match_of, dt = [], [], [], []
    counts = {"stands": 0, "walks": 0, "on_platforms": 0, "blackouts": 0, "rounds": 0, "matches": 0,
              "rounds_without_z": 0, "visited": np.zeros(GRID * GRID, bool)}
    for i, (match, _, blob) in enumerate(rounds):
        match_of.append(str(match))
        dt.append(1.0 / blob["hz"])
        skip = hm.blackouts(blob, geo)
        counts["blackouts"] += sum(len(spans) for spans in skip.values())
        mine = stands(blob, geo, i, skip)
        strides = hm.walks(blob, geo, i, mine, skip)
        for _, _, x, y, _ in _tracks(blob):
            counts["visited"][hm.cells_of(x, y)] = True
        if not any("z" in seg for segs in (blob.get("tracks") or {}).values() for seg in segs):
            counts["rounds_without_z"] += 1
        plats = platforms(blob)
        kept = off_platforms(mine, plats, geo)
        counts["on_platforms"] += len(mine) - len(kept)
        found += kept
        found_walks += off_platforms(strides, plats, geo)
    match_ids = sorted(set(match_of))
    counts.update({"stands": len(found), "walks": len(found_walks), "rounds": len(match_of),
                   "matches": len(match_ids), "dt": dt})
    return found, found_walks, [match_ids.index(m) for m in match_of], counts


def all_stands(rounds, geo: Geometry) -> tuple[list[Stand], list[int], dict]:
    """`all_ground` without the walks."""
    found, _, round_match, counts = all_ground(rounds, geo)
    return found, round_match, counts


# ---------------------------------------------------------------- fill, connections, areas


EIGHT = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
# a cell's neighbours directly beside it on opposite sides
OPPOSITE = [((-1, 0), (1, 0)), ((0, -1), (0, 1)), ((-1, -1), (1, 1)), ((-1, 1), (1, -1))]


def _neighbours(cell: int, walk: np.ndarray):
    """The walkable cells among `cell`'s eight neighbours (walk: GRID x GRID)."""
    y, x = divmod(cell, GRID)
    for dy, dx in EIGHT:
        ny, nx = y + dy, x + dx
        if 0 <= ny < GRID and 0 <= nx < GRID and walk[ny, nx]:
            yield ny * GRID + nx


def fill(floors: dict, reasons: dict, geo: Geometry) -> tuple[dict, dict]:
    """({cell: ground height} for the cells filled from their neighbours, {cell: reason} for every other
    walkable cell without a floor, `reasons` included)."""
    walk = geo.walk
    filled, why = {}, dict(reasons)
    tol = hc.FILL_TOL_M * DM
    for cell in np.flatnonzero(walk.ravel()).tolist():
        if cell in floors or cell in why:
            continue
        near, front = {cell}, {cell}
        for _ in range(hc.FILL_R):
            front = {n for c in front for n in _neighbours(c, walk)} - near
            near |= front
        known = [c for c in near if c in floors]
        heights = [h for c in known for h, _ in floors[c]]
        if len(known) < hc.FILL_MIN_NEIGHBOURS:
            why[cell] = NO_SAMPLES
        elif max(heights) - min(heights) > tol:
            why[cell] = NEIGHBOURS_DISAGREE     # a drop or a second floor nearby: never averaged
        else:
            filled[cell] = int(round(float(np.median(heights))))
    return filled, why


def crossings(runs) -> dict:
    """{(a, c, b): [(round, z in a, z in c, z in b), ...]}: each time a ground run (a walk, or a stand that
    moves) went from cell a through c to b, a and b each within CROSS_REACH cells of c in the run's own order,
    with the run's height in the three cells (world dm). `off_platforms` has already cut every run where it
    lost cells, so a run never reaches across ground it wasn't seen on."""
    seen: dict[tuple, list] = defaultdict(list)
    for run in runs:
        cells = run.cells
        zs = [run.z] * len(cells) if isinstance(run, Stand) else run.low
        for j, c in enumerate(cells):
            for i in range(max(0, j - hc.CROSS_REACH), j):
                for k in range(j + 1, min(len(cells), j + 1 + hc.CROSS_REACH)):
                    a, b = cells[i], cells[k]
                    if a != b and a != c and b != c:
                        seen[(a, c, b)].append((run.round, zs[i], zs[j], zs[k]))
    return seen


def _on_floors(crossed: dict, a: int, c: int, b: int, ha: int, hb: int, reach: float) -> bool:
    """Whether some run that went a -> c -> b did so on these two floors: in each anchor it was within `reach`
    of that anchor's floor, it went the way the floors go, and in between it stayed between its two ends. A
    run on a bridge over the anchors, or in a tunnel under them, crosses nothing of theirs."""
    tol = hc.FLOOR_TOL_M * DM
    for _, za, zc, zb in crossed.get((a, c, b), ()):
        if abs(za - ha) <= reach and abs(zb - hb) <= reach and (zb - za) * (hb - ha) >= 0 \
                and min(za, zb) - tol <= zc <= max(za, zb) + tol:
            return True
    return False


def fill_gradient(floors: dict, why: dict, crossed: dict, geo: Geometry) -> tuple[dict, set]:
    """The slopes `fill` refused (the slopes spec, part 3): ({cell: ground height}, {(from cell, to cell)}).
    A NEIGHBOURS_DISAGREE cell is filled when it has sampled single-floor neighbours directly beside it on
    opposite sides, no steeper than SLOPE_MAX between them, and a ground run crossed from one to the other
    through it on those two floors (`crossings`, `_on_floors`: within one cell's steepest rise plus
    FLOOR_TOL_M of each): it takes the mean of the pair. Several pairs must agree within FILL_TOL_M. The second
    result is the steps those runs took (into the cell and out of it, in the direction seen), which `connect`
    takes as walked on the ground. Only `floors` anchor a fill: a filled cell never fills another."""
    filled, granted = {}, set()
    tol = hc.FILL_TOL_M * DM
    reach = (hc.SLOPE_MAX * geo.cell_m + hc.FLOOR_TOL_M) * DM
    for cell, reason in why.items():
        if reason != NEIGHBOURS_DISAGREE:
            continue
        y, x = divmod(cell, GRID)
        means, steps = [], set()
        for (ay, ax), (by, bx) in OPPOSITE:
            if not (0 <= y + ay < GRID and 0 <= x + ax < GRID and 0 <= y + by < GRID and 0 <= x + bx < GRID):
                continue
            a, b = (y + ay) * GRID + x + ax, (y + by) * GRID + x + bx
            if len(floors.get(a, ())) != 1 or len(floors.get(b, ())) != 1:
                continue
            ha, hb_ = floors[a][0][0], floors[b][0][0]
            run_m = 2 * geo.cell_m * (2 ** 0.5 if ay and ax else 1.0)
            if abs(ha - hb_) / DM > hc.SLOPE_MAX * run_m:
                continue
            ab = _on_floors(crossed, a, cell, b, ha, hb_, reach)
            ba = _on_floors(crossed, b, cell, a, hb_, ha, reach)
            if not (ab or ba):
                continue
            means.append((ha + hb_) / 2)
            if ab:
                steps |= {(a, cell), (cell, b)}
            if ba:
                steps |= {(b, cell), (cell, a)}
        if means and max(means) - min(means) <= tol:
            filled[cell] = int(round(float(np.mean(means))))
            granted |= steps
    return filled, granted


def _floor_of(heights: list[int], z: int) -> int | None:
    """The index of the floor a sample at z is on: the highest at or below it (FLOOR_TOL_M of slack), unless
    that is FLOOR_SEP_M or more below (the sample is in the air, or on a level that isn't a floor)."""
    best = None
    for i, h in enumerate(heights):
        if h <= z + hc.FLOOR_TOL_M * DM:
            best = i
    return best if best is not None and z - heights[best] < hc.FLOOR_SEP_M * DM else None


def walked(found: list[Stand], heights: dict, found_walks: list[Walk] = (), dt: list[float] | None = None) -> tuple[dict, dict]:
    """({(cell a, floor a, cell b, floor b): the rounds it was walked in}, the same for the walks made on the
    ground): a to b, neighbouring cells. On the ground: the cells one stand or one walk passes through, and
    from one of a player's runs to their next when no sample lies between them (`dt`: each round's sample
    spacing). Not known to be on the ground: from one stand to the same player's next within CONNECT_S (a jump
    down, a fall)."""
    seen: dict[tuple, set] = defaultdict(set)
    ground: dict[tuple, set] = defaultdict(set)

    def note(a: int, za: int, b: int, zb: int, rnd: int, on_ground: bool) -> None:
        ay, ax = divmod(a, GRID)
        by, bx = divmod(b, GRID)
        if a == b or abs(ay - by) > 1 or abs(ax - bx) > 1 or a not in heights or b not in heights:
            return
        fa, fb = _floor_of(heights[a], za), _floor_of(heights[b], zb)
        if fa is not None and fb is not None:
            seen[(a, fa, b, fb)].add(rnd)
            if on_ground:
                ground[(a, fa, b, fb)].add(rnd)

    def ends(run) -> tuple[int, int]:
        return (run.z, run.z) if isinstance(run, Stand) else (run.low[0], run.low[-1])

    def same_player(a, b) -> bool:
        return a is not None and (a.round, a.slot) == (b.round, b.slot)

    previous = last_stand = None
    for run in sorted([*found, *found_walks], key=lambda r: (r.round, r.slot, r.t0)):
        zs = [run.z] * len(run.cells) if isinstance(run, Stand) else run.low
        for (a, za), (b, zb) in zip(zip(run.cells, zs), zip(run.cells[1:], zs[1:])):
            note(a, za, b, zb, run.round, True)
        # a walk that picks up where the last run stopped (or a stand where a walk did): one ground run. Two
        # stands end to end are left to the rule below: a teleport reads as that.
        if same_player(previous, run) and dt is not None and (isinstance(run, Walk) or isinstance(previous, Walk)) \
                and 0 <= run.t0 - previous.t1 <= 1.5 * dt[run.round]:
            note(previous.cells[-1], ends(previous)[1], run.cells[0], ends(run)[0], run.round, True)
        if isinstance(run, Stand):
            if same_player(last_stand, run) and 0 <= run.t0 - last_stand.t1 <= hc.CONNECT_S:
                note(last_stand.cells[-1], last_stand.z, run.cells[0], run.z, run.round, False)
            last_stand = run
        previous = run
    return seen, ground


def connect(heights: dict, seen: dict, geo: Geometry, ground: dict | None = None,
            granted: set = frozenset()) -> np.ndarray:
    """The directed walks between resolved floors, K x 5 (cell a, floor a, cell b, floor b, EDGE_*), sorted.
    Floors within STEP_UP_M are a step, both ways. A bigger one connects only where it was walked
    (CONNECT_MIN_ROUNDS rounds of `seen`, or a step `fill_gradient` granted): seen going up it is a step both
    ways (what is climbed can be dropped from); seen only going down it is one-way, a slide when it was walked
    on the ground (`ground`, or granted) and a fall otherwise."""
    walk = geo.walk
    step = hc.STEP_UP_M * DM
    ground = ground or {}
    edges: dict[tuple, int] = {}
    for a, floors_a in heights.items():
        for b in _neighbours(a, walk):
            if b not in heights:
                continue
            for i, ha in enumerate(floors_a):
                for j, hb in enumerate(heights[b]):
                    key = (a, i, b, j)
                    # The spec infers this between ground floors; here between any two
                    # floors, so a bridge is walkable along itself where few rounds walked it.
                    if abs(ha - hb) <= step:
                        edges[key] = hc.EDGE_STEP
                        continue
                    given = i == 0 and j == 0 and (a, b) in granted
                    on_ground = given or len(ground.get(key, ())) >= hc.CONNECT_MIN_ROUNDS
                    if not (on_ground or len(seen.get(key, ())) >= hc.CONNECT_MIN_ROUNDS):
                        continue
                    if hb > ha:
                        edges[key] = edges[(b, j, a, i)] = hc.EDGE_STEP     # climbed: it can be dropped from
                    else:
                        edges.setdefault(key, hc.EDGE_SLIDE if on_ground else hc.EDGE_FALL)
    return np.array(sorted((*key, kind) for key, kind in edges.items()), np.int32).reshape(-1, 5)


def _bbox(cells: np.ndarray) -> list[int]:
    ys, xs = np.divmod(cells, GRID)
    return [int(xs.min()) * CELL, int(ys.min()) * CELL, int(xs.max() + 1) * CELL - 1, int(ys.max() + 1) * CELL - 1]


def unresolved_areas(why: dict) -> list[dict]:
    """The unresolved cells as areas (8-connected), largest first: size, bounding box in minimap px, and
    why (each reason's cell count)."""
    mask = np.zeros(GRID * GRID, bool)
    mask[list(why)] = True
    lab, n = ndimage.label(mask.reshape(GRID, GRID), np.ones((3, 3), bool))
    lab = lab.ravel()
    out = []
    for k in range(1, n + 1):
        cells = np.flatnonzero(lab == k)
        out.append({"cells": int(len(cells)), "bbox": _bbox(cells),
                    "why": dict(Counter(why[c] for c in cells.tolist()).most_common())})
    out.sort(key=lambda a: (-a["cells"], a["bbox"]))
    return out


def air_only(heights: dict, why: dict, edges: np.ndarray, geo: Geometry) -> list[dict]:
    """Floors no walk reaches from the map's main ground: groups of floors cut off from the largest
    connected piece, counting an unresolved cell as joined to every floor around it (as the engine walks
    it). Ropes, boosts and teleports aren't connections, so what only they reach is listed here."""
    ids: dict[tuple, int] = {}
    for cell, floors in heights.items():
        for i in range(len(floors)):
            ids[(cell, i)] = len(ids)
    for cell in why:
        ids[(cell, -1)] = len(ids)
    if not ids:
        return []
    pairs = [(ids[(a, i)], ids[(b, j)]) for a, i, b, j, _ in edges.tolist()]
    for cell in why:
        for other in _neighbours(cell, geo.walk):
            if other in heights:
                pairs += [(ids[(cell, -1)], ids[(other, i)]) for i in range(len(heights[other]))]
            elif other in why:
                pairs.append((ids[(cell, -1)], ids[(other, -1)]))
    rows, cols = zip(*pairs) if pairs else ((), ())
    graph = coo_matrix((np.ones(len(pairs), bool), (rows, cols)), shape=(len(ids), len(ids)))
    _, label = connected_components(graph, directed=False)
    main = int(np.argmax(np.bincount(label)))
    groups: dict[int, list[tuple]] = defaultdict(list)
    for key, node in ids.items():
        if label[node] != main and key[1] >= 0:
            groups[int(label[node])].append(key)
    out = []
    for keys in groups.values():
        cells = np.array(sorted({c for c, _ in keys}))
        zs = [heights[c][i] for c, i in keys]
        out.append({"cells": int(len(cells)), "bbox": _bbox(cells), "z": [int(min(zs)), int(max(zs))]})
    out.sort(key=lambda a: (-a["cells"], a["bbox"]))
    return out


def readiness(supported: np.ndarray, unresolved: np.ndarray, count: np.ndarray, n_walk: int) -> tuple[float, list]:
    """(the supported share of walkable cells, why the map is below the bar: empty when it is ready).
    Flat GRID*GRID arrays: cells with a supported floor, unresolved cells, floors per cell."""
    by_two = ndimage.binary_dilation((count >= 2).reshape(GRID, GRID), np.ones((3, 3), bool)).ravel()
    lab, _ = ndimage.label(unresolved.reshape(GRID, GRID), np.ones((3, 3), bool))
    lab = lab.ravel()
    sizes = np.bincount(lab)
    blocking = [int(sizes[k]) for k in set(lab[unresolved & by_two].tolist()) if sizes[k] > hc.UNRESOLVED_MAX]
    share = float(supported.sum()) / n_walk if n_walk else 0.0
    not_ready = []
    if share < hc.HEIGHT_SUPPORTED_MIN:
        not_ready.append(f"supported {share:.1%} is under {hc.HEIGHT_SUPPORTED_MIN:.0%}")
    if blocking:
        not_ready.append(f"{len(blocking)} unresolved area(s) larger than {hc.UNRESOLVED_MAX} cells touch a cell "
                         f"with two floors (largest {max(blocking)} cells)")
    return share, not_ready


def build(rounds, geo: Geometry) -> HeightBuild:
    """A map's heights from `rounds` = [(match id, round number, blob), ...]. Never refuses: `ready` says
    whether the map reaches the bar, and `report["not_ready"]` why not."""
    found, found_walks, round_match, counts = all_ground(rounds, geo)
    dt = counts.pop("dt")
    floors, reasons, kinds = cell_floors(found, round_match, geo, found_walks)
    filled, why = fill(floors, reasons, geo)
    sloped, granted = fill_gradient(floors, why, crossings([*found, *found_walks]), geo)
    for cell in sloped:
        del why[cell]
    heights = {cell: [h for h, _ in got] for cell, got in floors.items()}
    heights.update({cell: [h] for cell, h in {**filled, **sloped}.items()})
    seen, ground = walked(found, heights, found_walks, dt)
    edges = connect(heights, seen, geo, ground, granted)
    walk = geo.walk.ravel()
    n_walk = int(walk.sum())
    origin = min((h for hs in heights.values() for h in hs), default=0)
    asset_floors = -np.ones((GRID * GRID, hc.MAX_FLOORS), np.int16)
    spread = np.zeros((GRID * GRID, hc.MAX_FLOORS), np.int16)
    supported = np.zeros(GRID * GRID, bool)
    unresolved = np.zeros(GRID * GRID, bool)
    kind = np.zeros(GRID * GRID, np.int8)
    for cell, got in floors.items():
        supported[cell], kind[cell] = True, kinds[cell]
        for i, (h, s) in enumerate(got):
            asset_floors[cell, i], spread[cell, i] = h - origin, s
    for cells, code in ((filled, hc.KIND_FILLED), (sloped, hc.KIND_GRADIENT)):
        for cell, h in cells.items():
            asset_floors[cell, 0], kind[cell] = h - origin, code
    unresolved[list(why)] = True
    count = (asset_floors >= 0).sum(1)
    areas = unresolved_areas(why)
    share, not_ready = readiness(supported, unresolved, count, n_walk)
    visited = counts.pop("visited") & walk
    pairs = {tuple(e[:4]) for e in edges.tolist()}
    drops = [(code, heights[a][i] - heights[b][j]) for a, i, b, j, code in edges.tolist() if code != hc.EDGE_STEP]
    report = {
        "walkable_cells": n_walk, "visited_cells": int(visited.sum()), "supported_cells": int(supported.sum()),
        "filled_cells": len(filled) + len(sloped), "unresolved_cells": len(why),
        "visited": round(float(visited.sum()) / n_walk, 4) if n_walk else 0.0, "supported": round(share, 4),
        "cells_by_kind": {"stands": int((kind == hc.KIND_STANDS).sum()), "walks": int((kind == hc.KIND_WALKS).sum()),
                          "filled": len(filled), "gradient": len(sloped)},
        "cells_2_floors": int((count == 2).sum()), "cells_3_floors": int((count == 3).sum()),
        "refused_cells": sum(1 for r in why.values() if r == TOO_MANY),
        "unresolved_why": dict(Counter(why.values()).most_common()), "unresolved_areas": areas,
        "air_only": air_only(heights, why, edges, geo), "edges": int(len(edges)),
        "one_way_edges": sum(1 for a, i, b, j in pairs if (b, j, a, i) not in pairs),
        "slides": sum(1 for code, _ in drops if code == hc.EDGE_SLIDE),
        "falls": sum(1 for code, _ in drops if code == hc.EDGE_FALL),
        "falls_closed": sum(1 for code, drop in drops if code == hc.EDGE_FALL and drop > hc.SILENT_DROP_M * DM),
        "origin_z": int(origin), **counts, "ready": not not_ready, "not_ready": not_ready,
    }
    meta = {"origin_z": int(origin), "stands": counts["stands"], "walks": counts["walks"], "rounds": counts["rounds"],
            "matches": counts["matches"],
            "walk_sha": hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]}
    report["walk_sha"] = meta["walk_sha"]
    asset = hc.HeightAsset(asset_floors.reshape(GRID, GRID, hc.MAX_FLOORS), spread.reshape(GRID, GRID, hc.MAX_FLOORS),
                           supported.reshape(GRID, GRID), unresolved.reshape(GRID, GRID), edges, meta,
                           kind.reshape(GRID, GRID))
    return HeightBuild(asset, report, found, why, not not_ready, found_walks)


# ---------------------------------------------------------------- the report, for the user


def report_lines(name: str, report: dict) -> list[str]:
    """The build's result as printed lines; every unresolved area is a WARNING line (the spec: loudly)."""
    r = report
    k = r["cells_by_kind"]
    out = [f"{name}: {r['stands']} stands and {r['walks']} walks from {r['rounds']} rounds of {r['matches']} matches"
           + (f" ({r['rounds_without_z']} rounds without heights)" if r["rounds_without_z"] else "")
           + (f", {r['on_platforms']} stands by a platform dropped" if r["on_platforms"] else "")
           + (f", {r['blackouts']} blackouts after a movement ability" if r["blackouts"] else ""),
           f"  visited {r['visited']:.1%} of {r['walkable_cells']} walkable cells, supported {r['supported']:.1%} "
           f"(bar {hc.HEIGHT_SUPPORTED_MIN:.0%}; {k['walks']} cells from walks alone), filled {k['filled']} flat and "
           f"{k['gradient']} along a gradient, unresolved {r['unresolved_cells']}",
           f"  cells with 2 floors: {r['cells_2_floors']}, with 3: {r['cells_3_floors']}, refused (more than "
           f"{hc.MAX_FLOORS}): {r['refused_cells']}; walks {r['edges']} ({r['one_way_edges']} one-way: {r['slides']} "
           f"slides, {r['falls']} falls, {r['falls_closed']} of them too high for the unknown)"]
    for area in r["air_only"]:
        out.append(f"  reached only through the air: {area['cells']} cells at px {area['bbox']}, "
                   f"z {area['z'][0]}..{area['z'][1]} dm")
    for area in r["unresolved_areas"]:
        why = ", ".join(f"{reason} {n}" for reason, n in area["why"].items())
        out.append(f"WARNING {name}: unresolved area of {area['cells']} cells at px {area['bbox']} ({why}): "
                   f"flat 2D sight and walking there")
    out.append(f"  {'READY' if r['ready'] else 'NOT READY: ' + '; '.join(r['not_ready'])}")
    return out


def picture(build_: HeightBuild, geo: Geometry, path) -> None:
    """The review picture: the map coloured by ground height (blue low to yellow high), cells with an
    upper floor outlined in white (three floors in magenta), drops of more than STEP_UP_M between
    neighbouring ground cells marked in black, unresolved cells in red. For spotting a cave whose roof
    nobody stands on; never committed."""
    from PIL import Image

    asset = build_.asset
    ground = asset.floors[..., 0].astype(float)
    has = ground >= 0
    top = max(float(ground[has].max()), 1.0) if has.any() else 1.0
    f = np.clip(ground / top, 0, 1)
    rgb = np.zeros((GRID, GRID, 3), np.uint8)
    rgb[..., 0] = (40 + 215 * f).astype(np.uint8)
    rgb[..., 1] = (90 + 150 * f).astype(np.uint8)
    rgb[..., 2] = (200 - 170 * f).astype(np.uint8)
    rgb[~has] = (24, 24, 28)
    rgb[geo.walk & ~has & ~asset.unresolved] = (70, 70, 78)
    rgb[asset.unresolved] = (220, 40, 40)
    img = np.repeat(np.repeat(rgb, CELL, 0), CELL, 1)
    count = asset.floor_count()
    for cy, cx in zip(*np.nonzero(count >= 2)):
        colour = (255, 255, 255) if count[cy, cx] == 2 else (255, 0, 255)
        y0, x0 = cy * CELL, cx * CELL
        img[y0, x0:x0 + CELL] = img[y0 + CELL - 1, x0:x0 + CELL] = colour
        img[y0:y0 + CELL, x0] = img[y0:y0 + CELL, x0 + CELL - 1] = colour
    step = hc.STEP_UP_M * DM
    for cy, cx in zip(*np.nonzero(has)):
        if cx + 1 < GRID and has[cy, cx + 1] and abs(ground[cy, cx] - ground[cy, cx + 1]) > step:
            img[cy * CELL:(cy + 1) * CELL, (cx + 1) * CELL - 1:(cx + 1) * CELL + 1] = 0
        if cy + 1 < GRID and has[cy + 1, cx] and abs(ground[cy, cx] - ground[cy + 1, cx]) > step:
            img[(cy + 1) * CELL - 1:(cy + 1) * CELL + 1, cx * CELL:(cx + 1) * CELL] = 0
    Image.fromarray(img).save(path)


# ---------------------------------------------------------------- the checks before an asset is committed


def kill_line_check(rounds: list, geo: Geometry) -> dict:
    """Risk 1 with heights (the spec's "The kill-line check"): of the kills where both killer and victim
    have a position sample with z within KILL_SAMPLE_S, both stand on resolved cells, and the line is
    clear in 2D (walls, smokes and wall abilities) today, how many the heights block. A kill line is
    blocked only if the killer's eye sees neither the victim's body nor their head. `geo` has the
    heights; excluded kills are counted by reason."""
    from app.control import engine

    excluded: Counter = Counter()
    qualifying = blocked = 0
    examples = []
    for match, n, blob in rounds:
        rnd = engine.RoundInputs(blob, geo)
        for kill in blob.get("kills") or []:
            t, killer, victim = kill.get("t"), kill.get("killer"), kill.get("victim")
            if t is None or killer is None or victim is None or killer == victim:
                excluded["no killer"] += 1
                continue
            ends = []
            for slot in (killer, victim):
                # the nearest sample within KILL_SAMPLE_S (wider than the engine's own sample-and-a-half)
                ts = rnd.tracks[slot][0] if slot in rnd.tracks else np.zeros(0)
                at = int(np.searchsorted(ts, t))
                i = min((j for j in (at - 1, at) if 0 <= j < len(ts)), key=lambda j: abs(ts[j] - t), default=None)
                if i is None or abs(ts[i] - t) > hc.KILL_SAMPLE_S:
                    ends = None
                    break
                z = rnd.heights.get(slot)
                if z is None or np.isnan(z[i]):
                    ends = None
                    break
                ends.append((rnd.tracks[slot][1][i] * PX / 10000, rnd.tracks[slot][2][i] * PX / 10000, float(z[i])))
            if ends is None:
                excluded["no position with z near the kill"] += 1
                continue
            (kx, ky, kz), (vx, vy, vz) = ends
            if geo.unresolved[geo.cell_of_px(kx, ky)] or geo.unresolved[geo.cell_of_px(vx, vy)]:
                excluded["on an unresolved cell"] += 1
                continue
            smokes = rnd.smokes_at(engine.snap(t))
            if not geo_los(geo, (kx, ky, None), (vx, vy, None), smokes):
                excluded["blocked in 2D (wall or smoke)"] += 1
                continue
            qualifying += 1
            eye = kz + hc.EYE_M
            if not geo_los(geo, (kx, ky, eye), (vx, vy, vz + hc.BODY_M), smokes) and \
                    not geo_los(geo, (kx, ky, eye), (vx, vy, vz + hc.EYE_M), smokes):
                blocked += 1
                if len(examples) < 10:
                    examples.append({"match": str(match), "round": n, "t": round(float(t), 2),
                                     "killer_px": [round(kx), round(ky)], "victim_px": [round(vx), round(vy)],
                                     "z": [round(kz, 1), round(vz, 1)]})
    share = blocked / qualifying if qualifying else 0.0
    return {"qualifying": qualifying, "blocked": blocked, "share": round(share, 4),
            "passes": qualifying > 0 and share <= hc.KILL_LINE_BAR, "excluded": dict(excluded.most_common()),
            "examples": examples}


def geo_los(geo: Geometry, a: tuple, b: tuple, smokes) -> bool:
    from app.control.geometry import los

    return los(geo, a, b, smokes)


def must_block_check(lines: list, geo: Geometry, map_name: str) -> dict:
    """The hand-listed sightlines that are impossible in game (app/static/data/control/must_block.json):
    each must be blocked. A line whose viewer or target height is still unknown (null) is listed as not
    checked, never as passing: a map with one fails the check until its heights are filled in."""
    results = []
    for line in lines:
        if line.get("map") != map_name:
            continue
        vx, vy, vz = line["viewer"]
        tx, ty, tz = line["target"]
        if vz is None or tz is None:
            results.append({"source": line.get("source"), "checked": False})
            continue
        origin = geo.heights.origin_z if geo.heights is not None else 0
        eye = (vz - origin) / 10.0 + hc.EYE_M
        body = (tz - origin) / 10.0 + hc.BODY_M
        results.append({"source": line.get("source"), "checked": True,
                        "blocked": not geo_los(geo, (vx, vy, eye), (tx, ty, body), [])})
    checked = [r for r in results if r["checked"]]
    return {"lines": len(results), "checked": len(checked), "blocked": sum(r["blocked"] for r in checked),
            "passes": len(checked) == len(results) and all(r["blocked"] for r in checked),
            "unchecked": len(results) - len(checked),
            "results": results}
