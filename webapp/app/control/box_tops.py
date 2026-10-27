"""Box-top stands: where players stood steady on a cover piece they got up to, for the height build to learn each
piece's top height from. A box stand is a run of one segment inside one piece, steady in z for BOX_SETTLE_S; it is a
top stand when the player rose onto it (a lower sample just before) or landed in it by a teleport (a later segment
that starts inside the piece). Pure: blob and label raster in, stands and `top_rows` out; nothing calls it yet."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from app.control import heights
from app.control.geometry import PX
from app.control.stance import BOX_RISE_M, BOX_SETTLE_S, BOX_STEADY_DZ_M

# PROVISIONAL(D4): W2, 36 replays: lowest top rise 0.9 m, highest hugger/follow-on rise 0.3 m
BOX_APPROACH_S = 2.0
FINE = 4                    # the fine cell's side, px
EPS = 1e-6


@dataclass(frozen=True)
class TopStand:
    round_index: int
    slot: int
    seg: int
    t0: float
    t1: float
    piece: int
    z_dm: int
    px: tuple               # (x, y) int px: the run's sample nearest its median, on the piece


def _steady_runs(z: np.ndarray, need: int) -> list[tuple[int, int]]:
    """Inclusive [i, j] runs of `z` whose range is within BOX_STEADY_DZ_M and that span at least `need` steps."""
    dz = BOX_STEADY_DZ_M * 10 + EPS
    out, i, n = [], 0, len(z)
    while i < n:
        lo = hi = z[i]
        j = i
        while j + 1 < n:
            v = z[j + 1]
            if max(hi, v) - min(lo, v) > dz:
                break
            lo, hi, j = min(lo, v), max(hi, v), j + 1
        if j - i >= need:
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def top_stands(blob: dict, cover_px: np.ndarray, round_index: int = 0) -> list[TopStand]:
    """The round's top stands, each player's in time order. Segments without z give none."""
    hz = blob["hz"]
    need = BOX_SETTLE_S * hz - EPS
    approach = int(round(BOX_APPROACH_S * hz))
    rise = BOX_RISE_M * 10
    out: list[TopStand] = []
    for slot, segments in (blob.get("tracks") or {}).items():
        for si, seg in enumerate(segments):
            m = len(seg["u"])
            if m == 0 or "z" not in seg:
                continue
            t = seg["t0"] + np.arange(m) / hz
            x = np.cumsum(np.asarray(seg["u"], np.int64)) * PX / 10000
            y = np.cumsum(np.asarray(seg["v"], np.int64)) * PX / 10000
            z = np.cumsum(np.asarray(seg["z"], np.int64))
            xi = np.clip(x.astype(int), 0, PX - 1)
            yi = np.clip(y.astype(int), 0, PX - 1)
            label = np.asarray(cover_px)[yi, xi]
            if not (label > 0).any():
                continue
            cut = np.flatnonzero(np.diff(np.r_[0, label, 0]) != 0)
            for a, e in zip(cut[:-1], cut[1:]):
                if label[a] == 0:
                    continue
                for i, j in ((a + i, a + j) for i, j in _steady_runs(z[a:e], int(np.ceil(need)))):
                    med = float(np.median(z[i:j + 1]))
                    lo = max(0, i - approach)
                    rose = i > lo and bool((z[lo:i] <= med - rise + EPS).any())
                    landed = si != 0 and label[0] == label[a]
                    if not (rose or landed):
                        continue
                    # the run's own sample pixel nearest its median: always on the piece (a rounded median can fall
                    # a pixel off it, or off a non-convex piece altogether)
                    k = i + int(np.argmin((x[i:j + 1] - np.median(x[i:j + 1])) ** 2
                                          + (y[i:j + 1] - np.median(y[i:j + 1])) ** 2))
                    out.append(TopStand(round_index, int(slot), si, float(t[i]), float(t[j]), int(label[a]),
                                        int(round(med)), (int(xi[k]), int(yi[k]))))
    return out


def top_rows(stands: list[TopStand]) -> np.ndarray:
    """K x 3 int32 rows (px x, px y, z dm): per 4 px fine cell, the stands' z levels merged within FLOOR_TOL_M,
    at most MAX_FLOORS per cell (the best-supported, ties the lower z)."""
    cells: dict[int, list[TopStand]] = defaultdict(list)
    for s in stands:
        cells[int(s.px[1]) // FINE * (PX // FINE) + int(s.px[0]) // FINE].append(s)
    tol = heights.FLOOR_TOL_M * 10
    rows = []
    for group in cells.values():
        group = sorted(group, key=lambda s: s.z_dm)
        levels, cur = [], [group[0]]
        for s in group[1:]:
            if s.z_dm - cur[0].z_dm <= tol + EPS:
                cur.append(s)
            else:
                levels.append(cur)
                cur = [s]
        levels.append(cur)
        levels.sort(key=lambda lv: (-len(lv), float(np.median([s.z_dm for s in lv]))))
        for lv in levels[:heights.MAX_FLOORS]:
            mx, my = float(np.median([s.px[0] for s in lv])), float(np.median([s.px[1] for s in lv]))
            near = min(lv, key=lambda s: ((s.px[0] - mx) ** 2 + (s.px[1] - my) ** 2, s.px[1], s.px[0]))
            rows.append((int(near.px[0]), int(near.px[1]),       # a member's pixel: on its piece
                         int(round(float(np.median([s.z_dm for s in lv]))))))
    rows.sort(key=lambda r: (r[1], r[0], r[2]))
    return np.asarray(rows, np.int32).reshape(-1, 3)
