"""Builds a map's heights from stored rounds (docs/superpowers/specs/2026-10-01-control-heights-design.md,
part 3). The constants and the asset are app/control/heights.py's; scripts/build_control_heights.py is the
command.

Rounds in (`[(match id, round number, blob), ...]`, blobs of condenser revision 11, whose tracks carry z),
a `HeightBuild` out:

- **Stands, not samples.** Each player's track is cut into stands: runs of at least STAND_S in which z
  stays within STAND_TOL_M of the run's median. Running up a slope, a jump, a rope, a boost's way up and a
  fall make none. A short stand with lower ground just before and just after it is the top of a jump and
  is dropped (STAND_APEX_S). Stands near a live temporary platform (heights.PLATFORMS) are dropped too. A
  stand belongs to every cell its samples pass through, with its median z.
- **Floors.** In each cell the stands are grouped by height (the densest FLOOR_TOL_M window first). A group
  is a floor when it has FLOOR_MIN_STANDS stands from FLOOR_MIN_ROUNDS rounds in FLOOR_MIN_MATCHES
  matches. Floors closer than FLOOR_SEP_M, a floor spread over more than FLOOR_SPREAD_MAX_M, or more than
  MAX_FLOORS floors leave the cell unresolved, with the reason.
- **Unsampled cells.** A walkable cell with no floor takes a ground floor when at least
  FILL_MIN_NEIGHBOURS cells within FILL_R cells of it (by walking) have floors and all of those floors
  agree within FILL_TOL_M: their median, and no upper floors. Otherwise it is unresolved. Filling never
  averages across a drop, and a filled cell never fills another.
- **Connections.** Floors of neighbouring cells (all 8) within STEP_UP_M connect both ways. A bigger step
  connects only where it was walked: a stand on one followed within CONNECT_S by a stand on the other, in
  CONNECT_MIN_ROUNDS rounds. Seen going up it connects both ways (what is climbed can be dropped from);
  seen only going down it is one-way, a drop. Unresolved cells carry no connections here: the engine
  gives them today's 2D walking.
- **Unresolved areas, loudly.** Unresolved cells are grouped into areas (8-connected), each with its size,
  its bounding box in minimap px and why.
- **Readiness.** `visited` is the share of walkable cells any sample fell in; `supported` the share with a
  floor from stands (not filled). A map is ready at HEIGHT_SUPPORTED_MIN supported with no unresolved
  area larger than UNRESOLVED_MAX cells touching a cell with two floors.

Local tooling only, like the engine: the web app never imports this module.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import hashlib
from collections import Counter

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from app.control import height_motion as hm
from app.control import heights as hc
from app.control.geometry import CELL, GRID, PX, Geometry

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
    """`found` without the cells within PLATFORM_R_M of a platform that was up during the stand: a stand
    left with no cell is dropped. By cell, not by the stand's mean position: a long level walk that starts
    on a wall has its mean far from it."""
    if not plats:
        return found
    r2 = (hc.PLATFORM_R_M / geo.m_per_px + CELL) ** 2      # a cell's centre is within a cell of its samples
    out = []
    for s in found:
        near = [(x, y) for t0, t1, x, y in plats if t0 <= s.t1 and s.t0 <= t1]
        cells = tuple(c for c in s.cells
                      if not any(((c % GRID + 0.5) * CELL - x) ** 2 + ((c // GRID + 0.5) * CELL - y) ** 2 <= r2
                                 for x, y in near)) if near else s.cells
        if cells:
            out.append(s if cells == s.cells else Stand(s.round, s.slot, s.t0, s.t1, s.z, cells, s.x, s.y))
    return out


def group_cell(z: np.ndarray, rounds: np.ndarray, matches: np.ndarray) -> tuple[list[tuple[int, int]], str | None]:
    """One cell's stands (median z in world dm, round index, match index each) as floors:
    ([(height, spread), ...] lowest first, None), or ([], reason) when the cell can't be resolved, or
    ([], None) when no group is a floor."""
    tol = hc.FLOOR_TOL_M * DM
    left = np.ones(len(z), bool)
    floors = []
    while left.any():
        zs = np.sort(z[left])
        counts = np.searchsorted(zs, zs + 2 * tol, side="right") - np.arange(len(zs))
        start = zs[int(np.argmax(counts))]       # the densest window (the lowest of equals)
        members = left & (z >= start) & (z <= start + 2 * tol)
        for _ in range(2):                       # settle on the stands around the group's own median
            med = float(np.median(z[members]))
            members = left & (np.abs(z - med) <= tol)
        left &= ~members
        if members.sum() >= hc.FLOOR_MIN_STANDS and len(set(rounds[members].tolist())) >= hc.FLOOR_MIN_ROUNDS \
                and len(set(matches[members].tolist())) >= hc.FLOOR_MIN_MATCHES:
            p10, p90 = np.percentile(z[members], [10, 90])
            floors.append((int(round(float(np.median(z[members])))), int(round(float(p90 - p10)))))
    floors.sort()
    if len(floors) > hc.MAX_FLOORS:
        return [], TOO_MANY
    if any(b[0] - a[0] < hc.FLOOR_SEP_M * DM for a, b in zip(floors, floors[1:])):
        return [], TOO_CLOSE
    if any(spread > hc.FLOOR_SPREAD_MAX_M * DM for _, spread in floors):
        return [], SPREAD
    return floors, None


@dataclass
class HeightBuild:
    asset: hc.HeightAsset
    report: dict
    stands: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)      # flat cell -> why it is unresolved
    ready: bool = False


def cell_floors(found: list[Stand], round_match: list[int], geo: Geometry) -> tuple[dict, dict]:
    """({cell: [(height, spread), ...]}, {cell: reason}) over the walkable cells with stands."""
    by_cell: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for s in found:
        for cell in set(s.cells):
            by_cell[cell].append((s.z, s.round, round_match[s.round]))
    walk = geo.walk.ravel()
    floors, reasons = {}, {}
    for cell, rows in by_cell.items():
        if not walk[cell]:
            continue
        z, rounds, matches = (np.array(col) for col in zip(*rows))
        got, why = group_cell(z.astype(float), rounds, matches)
        if why is not None:
            reasons[cell] = why
        elif got:
            floors[cell] = got
    return floors, reasons


def all_stands(rounds: list, geo: Geometry) -> tuple[list[Stand], list[int], dict]:
    """Every round's stands off platforms, each round's match index, and the counts."""
    match_ids = sorted({str(match) for match, _, _ in rounds})
    round_match = [match_ids.index(str(match)) for match, _, _ in rounds]
    found, counts = [], {"stands": 0, "on_platforms": 0, "rounds": len(rounds), "matches": len(match_ids),
                         "rounds_without_z": 0, "visited": np.zeros(GRID * GRID, bool)}
    for i, (_, _, blob) in enumerate(rounds):
        mine = stands(blob, geo, i)
        for _, _, x, y, _ in _tracks(blob):
            counts["visited"][(np.clip(y, 0, PX - 1).astype(int) // CELL) * GRID
                              + np.clip(x, 0, PX - 1).astype(int) // CELL] = True
        if not any("z" in seg for segs in (blob.get("tracks") or {}).values() for seg in segs):
            counts["rounds_without_z"] += 1
        kept = off_platforms(mine, platforms(blob), geo)
        counts["on_platforms"] += len(mine) - len(kept)
        found += kept
    counts["stands"] = len(found)
    return found, round_match, counts


# ---------------------------------------------------------------- fill, connections, areas


EIGHT = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


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


def _floor_of(heights: list[int], z: int) -> int | None:
    """The index of the floor a stand at z is on: the nearest within FLOOR_TOL_M."""
    best = min(range(len(heights)), key=lambda i: abs(heights[i] - z), default=None)
    return best if best is not None and abs(heights[best] - z) <= hc.FLOOR_TOL_M * DM else None


def walked(found: list[Stand], heights: dict) -> dict:
    """{(cell a, floor a, cell b, floor b): the rounds it was walked in}: a to b, neighbouring cells, from
    the cells one stand passes through and from one stand to the same player's next within CONNECT_S."""
    seen: dict[tuple, set] = defaultdict(set)

    def note(a: int, za: int, b: int, zb: int, rnd: int) -> None:
        ay, ax = divmod(a, GRID)
        by, bx = divmod(b, GRID)
        if a == b or abs(ay - by) > 1 or abs(ax - bx) > 1 or a not in heights or b not in heights:
            return
        fa, fb = _floor_of(heights[a], za), _floor_of(heights[b], zb)
        if fa is not None and fb is not None:
            seen[(a, fa, b, fb)].add(rnd)

    previous: Stand | None = None
    for s in sorted(found, key=lambda s: (s.round, s.slot, s.t0)):
        for a, b in zip(s.cells, s.cells[1:]):
            note(a, s.z, b, s.z, s.round)
        if previous is not None and (previous.round, previous.slot) == (s.round, s.slot) \
                and 0 <= s.t0 - previous.t1 <= hc.CONNECT_S:
            note(previous.cells[-1], previous.z, s.cells[0], s.z, s.round)
        previous = s
    return seen


def connect(heights: dict, seen: dict, geo: Geometry) -> np.ndarray:
    """The directed walks between resolved floors, K x 4 (cell a, floor a, cell b, floor b), sorted."""
    walk = geo.walk
    step = hc.STEP_UP_M * DM
    edges = set()
    for a, floors_a in heights.items():
        for b in _neighbours(a, walk):
            if b not in heights:
                continue
            for i, ha in enumerate(floors_a):
                for j, hb in enumerate(heights[b]):
                    # The spec infers this between ground floors; here between any two
                    # floors, so a bridge is walkable along itself where few rounds walked it.
                    if abs(ha - hb) <= step:
                        edges.add((a, i, b, j))
                    elif len(seen.get((a, i, b, j), ())) >= hc.CONNECT_MIN_ROUNDS:
                        edges.add((a, i, b, j))
                        if hb > ha:
                            edges.add((b, j, a, i))     # climbed: what is climbed can be dropped from
    return np.array(sorted(edges), np.int32).reshape(-1, 4)


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
    pairs = [(ids[(a, i)], ids[(b, j)]) for a, i, b, j in edges.tolist()]
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


def build(rounds: list, geo: Geometry) -> HeightBuild:
    """A map's heights from `rounds` = [(match id, round number, blob), ...]. Never refuses: `ready` says
    whether the map reaches the bar, and `report["not_ready"]` why not."""
    found, round_match, counts = all_stands(rounds, geo)
    floors, reasons = cell_floors(found, round_match, geo)
    filled, why = fill(floors, reasons, geo)
    heights = {cell: [h for h, _ in got] for cell, got in floors.items()}
    heights.update({cell: [h] for cell, h in filled.items()})
    edges = connect(heights, walked(found, heights), geo)
    walk = geo.walk.ravel()
    n_walk = int(walk.sum())
    origin = min((h for hs in heights.values() for h in hs), default=0)
    asset_floors = -np.ones((GRID * GRID, hc.MAX_FLOORS), np.int16)
    spread = np.zeros((GRID * GRID, hc.MAX_FLOORS), np.int16)
    supported = np.zeros(GRID * GRID, bool)
    unresolved = np.zeros(GRID * GRID, bool)
    for cell, got in floors.items():
        supported[cell] = True
        for i, (h, s) in enumerate(got):
            asset_floors[cell, i], spread[cell, i] = h - origin, s
    for cell, h in filled.items():
        asset_floors[cell, 0] = h - origin
    unresolved[list(why)] = True
    count = (asset_floors >= 0).sum(1)
    areas = unresolved_areas(why)
    share, not_ready = readiness(supported, unresolved, count, n_walk)
    visited = counts.pop("visited") & walk
    pairs = set(map(tuple, edges.tolist()))
    report = {
        "walkable_cells": n_walk, "visited_cells": int(visited.sum()), "supported_cells": int(supported.sum()),
        "filled_cells": len(filled), "unresolved_cells": len(why),
        "visited": round(float(visited.sum()) / n_walk, 4) if n_walk else 0.0, "supported": round(share, 4),
        "cells_2_floors": int((count == 2).sum()), "cells_3_floors": int((count == 3).sum()),
        "refused_cells": sum(1 for r in why.values() if r == TOO_MANY),
        "unresolved_why": dict(Counter(why.values()).most_common()), "unresolved_areas": areas,
        "air_only": air_only(heights, why, edges, geo), "edges": int(len(edges)),
        "one_way_edges": sum(1 for a, i, b, j in pairs if (b, j, a, i) not in pairs),
        "origin_z": int(origin), **counts, "ready": not not_ready, "not_ready": not_ready,
    }
    meta = {"origin_z": int(origin), "stands": counts["stands"], "rounds": counts["rounds"],
            "matches": counts["matches"],
            "walk_sha": hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]}
    report["walk_sha"] = meta["walk_sha"]
    asset = hc.HeightAsset(asset_floors.reshape(GRID, GRID, hc.MAX_FLOORS), spread.reshape(GRID, GRID, hc.MAX_FLOORS),
                           supported.reshape(GRID, GRID), unresolved.reshape(GRID, GRID), edges, meta)
    return HeightBuild(asset, report, found, why, not not_ready)


# ---------------------------------------------------------------- the report, for the user


def report_lines(name: str, report: dict) -> list[str]:
    """The build's result as printed lines; every unresolved area is a WARNING line (the spec: loudly)."""
    r = report
    out = [f"{name}: {r['stands']} stands from {r['rounds']} rounds of {r['matches']} matches"
           + (f" ({r['rounds_without_z']} rounds without heights)" if r["rounds_without_z"] else "")
           + (f", {r['on_platforms']} stands by a platform dropped" if r["on_platforms"] else ""),
           f"  visited {r['visited']:.1%} of {r['walkable_cells']} walkable cells, supported {r['supported']:.1%} "
           f"(bar {hc.HEIGHT_SUPPORTED_MIN:.0%}), filled {r['filled_cells']}, unresolved {r['unresolved_cells']}",
           f"  cells with 2 floors: {r['cells_2_floors']}, with 3: {r['cells_3_floors']}, refused (more than "
           f"{hc.MAX_FLOORS}): {r['refused_cells']}; walks {r['edges']} ({r['one_way_edges']} one-way)"]
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
    """The hand-listed sightlines that are impossible in game (tests/replays/control_must_block.json):
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
