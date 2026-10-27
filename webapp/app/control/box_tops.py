"""Box-top stands: where players stood steady on a cover piece they got up to, for the height build to learn each
piece's top height from. A box stand is a run of one segment inside one piece, steady in z for BOX_SETTLE_S; it is a
top stand when the player rose onto it (a lower sample just before) or landed in it by a teleport (a later segment
that starts inside the piece). Pure: blob and label raster in, stands and `top_rows` out; nothing calls it yet."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from app.control import heights
from app.control.geometry import PX
from app.control.stance import BOX_RISE_M, BOX_SETTLE_S, BOX_STEADY_DZ_M, TOP_REACH_PX

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


# ---------------------------------------------------------------- read from a geometry (W7)

REACH_SLOTS = 3
_CACHE_MAX = 8
_tops_cache: dict = {}
_reach_cache: dict = {}


def _remember(cache: dict, key, value) -> None:
    if len(cache) >= _CACHE_MAX:
        cache.pop(next(iter(cache)))
    cache[key] = value


def _key(geo):
    """(height_sha, cover_sha), or None when either is missing (then nothing is cached)."""
    if geo.height_sha is None or geo.cover_sha is None:
        return None
    return geo.height_sha, geo.cover_sha


def _tops_of(geo) -> dict:
    tops = getattr(geo.heights, "box_tops", None) if geo.heights is not None else None
    if tops is None or not len(tops) or geo.cover_px is None:
        return {}
    rows = np.asarray(tops).reshape(-1, 3)
    xs, ys = np.clip(rows[:, 0], 0, PX - 1), np.clip(rows[:, 1], 0, PX - 1)
    labels = np.asarray(geo.cover_px)[ys, xs]
    found: dict[int, set] = defaultdict(set)
    for label, z in zip(labels.tolist(), rows[:, 2].tolist()):
        if label:
            found[label].add(round(z / 10.0, 1))
    return {p: tuple(sorted(found[p])) for p in sorted(found)}


def piece_tops(geo):
    """{cover piece id: its distinct top heights in m (node_z units), sorted}, read-only; {} without heights, learnt
    tops or cover. Computed on first use from the geometry's current heights and cover, cached by their shas."""
    key = _key(geo)
    if key is not None and key in _tops_cache:
        return MappingProxyType(_tops_cache[key])
    tops = _tops_of(geo)
    if key is not None:
        _remember(_tops_cache, key, tops)
    return MappingProxyType(tops)


def _reach_of(geo, tops) -> np.ndarray:
    from scipy import ndimage

    reach = TOP_REACH_PX
    cover = np.asarray(geo.cover_px)
    boxes = ndimage.find_objects(cover.astype(np.int32))
    dist = np.full((PX, PX, REACH_SLOTS), np.inf, np.float32)
    ids = np.zeros((PX, PX, REACH_SLOTS), np.int16)
    for piece in sorted(tops):
        if piece > len(boxes) or boxes[piece - 1] is None:
            continue
        ys, xs = boxes[piece - 1]
        y0, y1 = max(ys.start - reach - 1, 0), min(ys.stop + reach + 1, PX)
        x0, x1 = max(xs.start - reach - 1, 0), min(xs.stop + reach + 1, PX)
        d = ndimage.distance_transform_edt(cover[y0:y1, x0:x1] != piece).astype(np.float32)
        d[d > reach] = np.inf
        carry_d, carry_i = d, np.full(d.shape, piece, np.int16)
        for k in range(REACH_SLOTS):
            slot_d, slot_i = dist[y0:y1, x0:x1, k], ids[y0:y1, x0:x1, k]
            swap = carry_d < slot_d                  # strict: a tie keeps the lower id, which came first
            new_d, new_i = np.where(swap, carry_d, slot_d), np.where(swap, carry_i, slot_i)
            carry_d, carry_i = np.where(swap, slot_d, carry_d), np.where(swap, slot_i, carry_i)
            dist[y0:y1, x0:x1, k], ids[y0:y1, x0:x1, k] = new_d, new_i
    ids[~np.isfinite(dist)] = 0
    ids.flags.writeable = False
    return ids


def piece_reach(geo):
    """PX x PX x 3 int16 (read-only): per pixel, up to 3 topped pieces within TOP_REACH_PX, nearest first (ties: lower
    id), 0-padded; None when no piece has a top. Computed per piece on its padded bounding box, cached by shas."""
    tops = piece_tops(geo)
    if not tops:
        return None
    key = _key(geo)
    if key is not None and key in _reach_cache:
        return _reach_cache[key]
    reach = _reach_of(geo, tops)
    if key is not None:
        _remember(_reach_cache, key, reach)
    return reach
