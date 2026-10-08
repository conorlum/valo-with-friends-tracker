"""How players move, for the height build (docs/superpowers/specs/2026-10-05-height-slopes-design.md, parts 1
and 1b): the tracks of a stored round, the time after a movement ability that is left out of them, and the
**walks**, the second kind of ground evidence beside height_build.py's stands.

- **Tracks.** `tracks` gives each stored segment as arrays. With `skip` ({slot: [(t0, t1), ...]}) the samples
  inside those times are left out and the segment is cut there, so nothing downstream joins the two sides.
- **Blackouts.** `blackouts` is that `skip`: ABILITY_BLACKOUT_S from each cast listed in AIRBORNE_ABILITIES, and
  from each burst of speed no player makes on foot (BURST_MPS along the ground, BURST_UP_MPS upward, over
  BURST_S). Rounds condensed before revision 14 record no cast for Jett's updraft and dash or Waylay's dash
  (measured 2026-10-05), so there those are told by the burst; a teleport reads as one too, which is harmless.
  From revision 14 (`"movement_casts"`) the dashes are recorded casts and only the upward burst is read, for
  the updraft, which still has no cast.
- **Walks.** A run of at least WALK_S in which the player moves on the ground: each sample lies in a WALK_S
  window over which the player moved (WALK_MIN_MPS), z changed no faster than SLOPE_MAX against the distance
  covered, and z's acceleration (a least-squares parabola over the window) stayed within WALK_ACC_MAX. In the
  air z follows an arc (about 20 m/s2 in real rounds); on a slope it changes in step with the ground. A sample
  that a stand already holds is not a walk's. A walk keeps, for each cell it passes through, the lowest z it
  had there.

Local tooling only, like the build: the web app never imports this module.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from app.control import heights as hc
from app.control.geometry import CELL, GRID, PX, Geometry

DM = 10.0     # decimetres per metre


@dataclass
class Walk:
    round: int            # index into the build's rounds
    slot: int
    t0: float
    t1: float
    cells: tuple          # flat cells it passes through, in order (no repeats in a row)
    low: tuple            # the lowest z (world dm) of its samples in each of `cells`


def tracks(blob: dict, skip: dict | None = None):
    """(slot, t, x px, y px, z dm | None) per stored segment; with `skip`, per piece of one outside its times."""
    hz = blob["hz"]
    for slot, segments in (blob.get("tracks") or {}).items():
        for seg in segments:
            n = len(seg["u"])
            t = seg["t0"] + np.arange(n) / hz
            x = np.cumsum(np.asarray(seg["u"], np.int64)) * PX / 10000
            y = np.cumsum(np.asarray(seg["v"], np.int64)) * PX / 10000
            z = np.cumsum(np.asarray(seg["z"], np.int64)) if "z" in seg else None
            spans = (skip or {}).get(int(slot))
            if not spans:
                yield int(slot), t, x, y, z
                continue
            keep = np.ones(n, bool)
            for t0, t1 in spans:
                keep &= ~((t >= t0) & (t <= t1))
            for i, j in _runs_of(keep):
                yield int(slot), t[i:j], x[i:j], y[i:j], None if z is None else z[i:j]


def _runs_of(mask: np.ndarray) -> list[tuple[int, int]]:
    """The [i, j) runs of True in `mask`."""
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def cells_of(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (np.clip(y, 0, PX - 1).astype(int) // CELL) * GRID + np.clip(x, 0, PX - 1).astype(int) // CELL


def _merge(spans: list) -> list:
    out: list = []
    for t0, t1 in sorted(spans):
        if out and t0 <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], t1))
        else:
            out.append((t0, t1))
    return out


def blackouts(blob: dict, geo: Geometry) -> dict[int, list[tuple[float, float]]]:
    """{slot: [(t0, t1), ...]}: the times each player's samples are left out, merged and in order.

    Casts (`"cast"` rows) and listed abilities start one always. A round condensed with movement casts
    (`"movement_casts"`, condenser revision 14) is not read for dashes by speed: its dashes are its casts. A
    round condensed before has none, so there every burst counts."""
    out: dict[int, list] = defaultdict(list)
    for e in blob.get("util") or []:
        if e.get("k") in ("ability", "cast") and e.get("by") is not None \
                and f"{e.get('code')}_{e.get('name')}" in hc.AIRBORNE_ABILITIES:
            out[int(e["by"])].append((float(e["t"]), float(e["t"]) + hc.ABILITY_BLACKOUT_S))
    hz = blob["hz"]
    k = max(1, int(round(hc.BURST_S * hz)))
    casts = bool(blob.get("movement_casts"))
    for slot, t, x, y, z in tracks(blob):
        if len(t) <= k:
            continue
        fast = np.zeros(len(t) - k, bool) if casts \
            else np.hypot(x[k:] - x[:-k], y[k:] - y[:-k]) * geo.m_per_px > hc.BURST_MPS * k / hz
        # PROVISIONAL(D3): rising faster than BURST_UP_MPS still counts in a round with casts, because Jett's
        # updraft has no recorded cast (the condenser found no play that tells it apart).
        if z is not None:
            fast |= (z[k:] - z[:-k]) / DM > hc.BURST_UP_MPS * k / hz
        for i, j in _runs_of(fast):
            out[slot].append((float(t[i]), float(t[j - 1 + k]) + hc.ABILITY_BLACKOUT_S))
    return {slot: _merge(spans) for slot, spans in out.items()}


def _acc_filter(n: int, hz: float) -> np.ndarray:
    """Weights that give a least-squares parabola's second derivative (per s2) over n samples at `hz`."""
    k = np.arange(n) - (n - 1) / 2
    return 2 * np.linalg.pinv(np.stack([np.ones(n), k, k * k], 1))[2] * hz * hz


def _rate(v: np.ndarray, n: int, hz: float) -> np.ndarray:
    """v's rate of change per second, a least-squares line over the n samples centred on each one; NaN where
    that window doesn't fit in the track."""
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        k = np.arange(n) - (n - 1) / 2
        out[n // 2:len(v) - n // 2] = np.convolve(v, (k / (k @ k) * hz)[::-1], "valid")
    return out


def airborne(x: np.ndarray, y: np.ndarray, z: np.ndarray, hz: float, m_per_px: float) -> np.ndarray:
    """Per sample: the player is in the air (a jump, a fall, a boost's way down).

    One least-squares parabola over a window can't tell: a takeoff or a landing bends z the other way and the
    two cancel. So a flight is found by what only a flight does, and then followed along its arc:

    - a **seed** is a run of samples where z changes faster than SLOPE_MAX allows for the ground covered
      (AIR_RATE_S windows, AIR_RATE_SLACK_MPS of slack), or where z's rate keeps dropping by at least AIR_CORE
      of gravity across two windows in a row (one bend in a slope moves it once, not twice);
    - from the seed, the arc `z = a + b t - GRAVITY_MPS2 / 2 t^2` is fitted and followed both ways for as long
      as the track stays within AIR_FIT_M of it: that is the flight;
    - a flight found only by the second kind of seed must end in a landing (z's rate jumps up by
      AIR_LANDING_MPS where the arc stops fitting). The top of a staircase bends like the start of a fall, and
      has no landing.

    A flight shorter than about a quarter of a second with no fast part isn't found: a hop of a few decimetres.
    """
    n_all = len(z)
    air = np.zeros(n_all, bool)
    n = 2 * int(round(hc.AIR_RATE_S * hz / 2)) + 1
    if n < 3 or n_all < 3 * n:
        return air
    zm = z / DM
    vz = _rate(zm, n, hz)
    vh = np.hypot(_rate(x * m_per_px, n, hz), _rate(y * m_per_px, n, hz))
    s = n - 1
    drop = np.full(n_all, np.nan)
    drop[s:] = vz[s:] - vz[:-s]
    again = np.full(n_all, np.nan)
    again[:-s] = drop[s:]
    least = -hc.AIR_CORE * hc.GRAVITY_MPS2 * s / hz
    with np.errstate(invalid="ignore"):
        steep = np.abs(vz) > hc.SLOPE_MAX * vh + hc.AIR_RATE_SLACK_MPS
        seed = steep | ((drop <= least) & (again <= least))
    t = np.arange(n_all) / hz
    for a, b in _runs_of(seed):
        lo, hi = max(0, a - n // 2), min(n_all, b + n // 2)
        tau = t[lo:hi] - t[a]
        slope, start = np.polyfit(tau, zm[lo:hi] + hc.GRAVITY_MPS2 / 2 * tau ** 2, 1)
        tau = t - t[a]
        fits = np.abs(zm - (start + slope * tau - hc.GRAVITY_MPS2 / 2 * tau ** 2)) <= hc.AIR_FIT_M
        i, j = a, b
        while i > 0 and fits[i - 1]:
            i -= 1
        while j < n_all and fits[j]:
            j += 1
        if not steep[a:b].any():
            before, after = j - 3 - n // 2, j + 2 + n // 2
            if before < 0 or after >= n_all or not (vz[after] - vz[before] >= hc.AIR_LANDING_MPS):
                continue
        air[i:j] = True
    return air


def on_ground(x: np.ndarray, y: np.ndarray, z: np.ndarray, hz: float, m_per_px: float) -> np.ndarray:
    """Per sample: it isn't in a flight (`airborne`), and some WALK_S window holding it is a walking one: it
    holds no airborne sample, the player moved, and the module docstring's two limits hold. Any such window
    rather than every one: where a slope begins or ends the windows across the bend fail the acceleration
    limit, and the ones on either side of it still hold its samples."""
    n = len(z)
    w = int(round(hc.WALK_S * hz)) + 1
    if w < 3 or n < w:
        return np.zeros(n, bool)
    air = airborne(x, y, z, hz, m_per_px)
    span = (w - 1) / hz
    dxy = np.hypot(x[w - 1:] - x[:n - w + 1], y[w - 1:] - y[:n - w + 1]) * m_per_px
    dz = (z[w - 1:] - z[:n - w + 1]) / DM
    acc = np.convolve(z / DM, _acc_filter(w, hz)[::-1], "valid")
    ok = (np.convolve(air.astype(int), np.ones(w, int), "valid") == 0) & (dxy >= hc.WALK_MIN_MPS * span) \
        & (np.abs(dz) <= hc.SLOPE_MAX * dxy + 1 / DM) & (np.abs(acc) <= hc.WALK_ACC_MAX)   # z is whole decimetres
    return (np.convolve(ok, np.ones(w, int)) > 0) & ~air


def walks(blob: dict, geo: Geometry, round_index: int = 0, found=(), skip: dict | None = None) -> list[Walk]:
    """The round's walks, each player's in time order. `found` are the round's stands: their samples are
    theirs. Segments without z give none."""
    hz = blob["hz"]
    need = int(np.ceil(hc.WALK_S * hz - 1e-9)) + 1
    busy: dict[int, list] = defaultdict(list)
    for s in found:
        busy[s.slot].append((s.t0, s.t1))
    out = []
    for slot, t, x, y, z in tracks(blob, skip):
        if z is None:
            continue
        good = on_ground(x, y, z.astype(float), hz, geo.m_per_px)
        for t0, t1 in busy.get(slot, ()):
            good &= ~((t >= t0 - 1e-9) & (t <= t1 + 1e-9))
        for i, j in _runs_of(good):
            if j - i < need:
                continue
            cells = cells_of(x[i:j], y[i:j])
            starts = np.r_[0, np.flatnonzero(cells[1:] != cells[:-1]) + 1]
            low = np.minimum.reduceat(z[i:j], starts)
            out.append(Walk(round_index, slot, float(t[i]), float(t[j - 1]), tuple(int(c) for c in cells[starts]),
                            tuple(int(v) for v in low)))
    out.sort(key=lambda w: (w.slot, w.t0))
    return out
