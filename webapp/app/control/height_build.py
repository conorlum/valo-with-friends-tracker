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

Local tooling only, like the engine: the web app never imports this module.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from app.control import heights as hc
from app.control.geometry import GRID, PX, Geometry

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


def _tracks(blob: dict):
    """(slot, t, x px, y px, z dm | None) per stored segment."""
    hz = blob["hz"]
    for slot, segments in (blob.get("tracks") or {}).items():
        for seg in segments:
            n = len(seg["u"])
            t = seg["t0"] + np.arange(n) / hz
            x = np.cumsum(np.asarray(seg["u"], np.int64)) * PX / 10000
            y = np.cumsum(np.asarray(seg["v"], np.int64)) * PX / 10000
            z = np.cumsum(np.asarray(seg["z"], np.int64)) if "z" in seg else None
            yield int(slot), t, x, y, z


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


def stands(blob: dict, geo: Geometry, round_index: int = 0) -> list[Stand]:
    """The round's stands, each player's in time order. Segments without z give none."""
    hz = blob["hz"]
    tol = hc.STAND_TOL_M * DM
    need = int(np.ceil(hc.STAND_S * hz - 1e-9)) + 1     # samples spanning STAND_S
    apex_n = int(round(0.25 * hz))                      # how far either side an apex looks for lower ground
    out = []
    for slot, t, x, y, z in _tracks(blob):
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
    """`found` without the stands within PLATFORM_R_M of a platform that was up during them."""
    if not plats:
        return found
    r2 = (hc.PLATFORM_R_M / geo.m_per_px) ** 2
    return [s for s in found
            if not any(t0 <= s.t1 and s.t0 <= t1 and (s.x - x) ** 2 + (s.y - y) ** 2 <= r2 for t0, t1, x, y in plats)]


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
                         "rounds_without_z": 0}
    for i, (_, _, blob) in enumerate(rounds):
        mine = stands(blob, geo, i)
        if not any("z" in seg for segs in (blob.get("tracks") or {}).values() for seg in segs):
            counts["rounds_without_z"] += 1
        kept = off_platforms(mine, platforms(blob), geo)
        counts["on_platforms"] += len(mine) - len(kept)
        found += kept
    counts["stands"] = len(found)
    return found, round_match, counts
