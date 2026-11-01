"""Steady stands within reach of cover, and the box tops they teach. Only unambiguous stands inside an outline
teach; the others match learnt tops or become conflicts. Build evidence uses world dm, diagnostics world metres."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import math
from types import MappingProxyType

import numpy as np

from app.control import heights
from app.control.geometry import CELL, GRID, PX
from app.control.heights import (BOX_RING_PX, BOX_SETTLE_S, BOX_STEADY_DZ_M, BOX_STEP_M,
                                 CROUCH_DROP_M, CROUCH_LAG_S, CROUCH_NEAR_S, TOP_REACH_M, TOP_TOL_M)

FINE = 4                    # the fine cell's side, px
EPS = 1e-6


@dataclass(frozen=True)
class TopStand:
    round_index: int
    slot: int
    seg: int
    t0: float
    t1: float
    inside: int
    z_dm: int
    px: tuple               # (x, y) int px: the run's sample nearest its median
    candidates: tuple = ()
    cand_dist: tuple = ()    # metres to each candidate's outline, in candidates order
    match: str = ""
    round: int = 0
    crouch: str = "none"

    @property
    def piece(self):
        return self.inside

    @property
    def cells(self):
        return (self.px[1] // CELL * GRID + self.px[0] // CELL,)

    @property
    def fine_cells(self):
        return (self.px[1] // FINE * (PX // FINE) + self.px[0] // FINE,)


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


def reach_distances(cover_px, m_per_px):
    """Per-piece cropped distances in metres, including all pieces (no runtime reach-slot limit)."""
    from scipy import ndimage

    pad = int(math.ceil(TOP_REACH_M / m_per_px)) + 1
    out = {}
    for piece, box in enumerate(ndimage.find_objects(np.asarray(cover_px, np.int32)), 1):
        if box is None:
            continue
        ys, xs = box
        crop = (slice(max(0, ys.start - pad), min(PX, ys.stop + pad)),
                slice(max(0, xs.start - pad), min(PX, xs.stop + pad)))
        out[piece] = (crop, ndimage.distance_transform_edt(cover_px[crop] != piece) * m_per_px)
    return out


def top_stands(blob: dict, cover_px: np.ndarray, round_index: int = 0, *, m_per_px: float,
               skip=None, reach=None) -> list[TopStand]:
    """Steady runs within TOP_REACH_M of any outline, cut at gaps in reach or blackouts. Each keeps every
    candidate within reach of its representative pixel, and the outline under that pixel (0 outside)."""
    from app.control import height_motion as hm

    reach = reach_distances(cover_px, m_per_px) if reach is None else reach
    hz = blob["hz"]
    need = BOX_SETTLE_S * hz - EPS
    out: list[TopStand] = []
    for slot, segments in (blob.get("tracks") or {}).items():
        crouch_spans = []
        if "crouch" in blob:
            crouch_spans = [(t0 + CROUCH_LAG_S, t1 + CROUCH_LAG_S)
                            for t0, t1 in blob["crouch"].get(str(slot), ())]
            crouch_spans.extend((row["t"], row["t1"]) for row in blob.get("util", ())
                                if row.get("k") == "status" and row.get("status") == "netted"
                                and row.get("target") == int(slot))
        for si, seg in enumerate(segments):
            m = len(seg["u"])
            if m == 0 or "z" not in seg:
                continue
            t = seg["t0"] + np.arange(m) / hz
            x = np.cumsum(np.asarray(seg["u"], np.int64)) * PX / 10000
            y = np.cumsum(np.asarray(seg["v"], np.int64)) * PX / 10000
            z = np.cumsum(np.asarray(seg["z"], np.int64))
            crouched = np.zeros(m, bool)
            for t0, t1 in crouch_spans:
                crouched |= (t >= t0) & (t <= t1)
            xi = np.clip(x.astype(int), 0, PX - 1)
            yi = np.clip(y.astype(int), 0, PX - 1)
            near = np.zeros(m, bool)
            for (ys, xs), d in reach.values():
                at = (xi >= xs.start) & (xi < xs.stop) & (yi >= ys.start) & (yi < ys.stop)
                near[at] |= d[yi[at] - ys.start, xi[at] - xs.start] <= TOP_REACH_M + EPS
            for t0, t1 in (skip or {}).get(int(slot), ()):
                near &= ~((t >= t0) & (t <= t1))
            for a, e in hm._runs_of(near):
                for i, j in ((a + i, a + j) for i, j in _steady_runs(z[a:e], int(np.ceil(need)))):
                    crouch = "none"
                    if crouched[i:j + 1].any():
                        med = float(np.median(z[i:j + 1] + crouched[i:j + 1] * CROUCH_DROP_M * 10))
                        crouch = "key"
                    else:
                        med = float(np.median(z[i:j + 1]))
                        if any(t0 - CROUCH_NEAR_S <= t[j] and t1 + CROUCH_NEAR_S >= t[i]
                               for t0, t1 in crouch_spans):
                            crouch = "up"
                    # A member's pixel, rather than a rounded median that may lie off a non-convex outline.
                    k = i + int(np.argmin((x[i:j + 1] - np.median(x[i:j + 1])) ** 2
                                          + (y[i:j + 1] - np.median(y[i:j + 1])) ** 2))
                    px = (int(xi[k]), int(yi[k]))
                    nearby = tuple((p, float(d[px[1] - ys.start, px[0] - xs.start]))
                                   for p, ((ys, xs), d) in reach.items()
                                  if xs.start <= px[0] < xs.stop and ys.start <= px[1] < ys.stop
                                  and d[px[1] - ys.start, px[0] - xs.start] <= TOP_REACH_M + EPS)
                    cands = tuple(p for p, _ in nearby)
                    cand_dist = tuple(dist for _, dist in nearby)
                    out.append(TopStand(round_index, int(slot), si, float(t[i]), float(t[j]),
                                        int(cover_px[px[1], px[0]]), int(round(med)), px, cands, cand_dist,
                                        crouch=crouch))
    return out


def ground_of(cover_px: np.ndarray, floors: np.ndarray, *, fine_cells=None, fine_floors=None) -> dict[int, int]:
    """{cover piece id: the floor it stands on} in `floors`' units: the most common of the lowest floors of the
    cells round it (a tie takes the lower). Not the lowest: a piece at a ledge's edge has a drop on one side
    (Ascent's piece 17: floor at 2.0 m round most of it, 0.0 m on one side, and players at 2.0 m on its outline).
    `floors` is the coarse grid's floors, (GRID*GRID, levels) or (GRID, GRID, levels), -1 for none; round a piece =
    coarse cells holding a pixel within BOX_RING_PX of its outline that is on no cover piece. A piece with no floor
    round it is left out."""
    from scipy import ndimage

    cover = np.asarray(cover_px)
    flat = np.asarray(floors).reshape(GRID * GRID, -1)
    # Replace selected parent footprints with their actual child floors when building after refinement.
    leaf = None
    if fine_cells is not None and len(fine_cells):
        leaf = np.repeat(np.repeat(flat.reshape(GRID, GRID, -1), 2, axis=0), 2, axis=1)
        leaf.reshape(-1, leaf.shape[-1])[fine_cells] = fine_floors
    out: dict[int, int] = {}
    for piece, box in enumerate(ndimage.find_objects(cover.astype(np.int32)), start=1):
        if box is None:
            continue
        ys, xs = box
        y0, x0 = max(ys.start - BOX_RING_PX, 0), max(xs.start - BOX_RING_PX, 0)
        crop = cover[y0:min(ys.stop + BOX_RING_PX, PX), x0:min(xs.stop + BOX_RING_PX, PX)]
        ring = (ndimage.distance_transform_edt(crop != piece) <= BOX_RING_PX + EPS) & (crop == 0)
        ry, rx = np.nonzero(ring)
        got = (flat[np.unique((ry + y0) // CELL * GRID + (rx + x0) // CELL)] if leaf is None else
               leaf.reshape(-1, leaf.shape[-1])[np.unique((ry + y0) // FINE * (PX // FINE)
                                                          + (rx + x0) // FINE)]).astype(np.int32)
        lowest = np.where(got >= 0, got, np.iinfo(np.int32).max).min(axis=1)
        lowest = lowest[lowest < np.iinfo(np.int32).max]
        if lowest.size:
            values, counts = np.unique(lowest, return_counts=True)       # sorted: argmax takes the lower on a tie
            out[piece] = int(values[np.argmax(counts)])
    return out


def on_a_step(stands: list[TopStand], ground: dict[int, int]) -> list[TopStand]:
    """The stands at least BOX_STEP_M above the floor round their piece (`ground`, dm, as `z_dm`); a piece with no
    floor round it has no tops."""
    step = BOX_STEP_M * 10 - EPS
    return [s for s in stands if s.piece in ground and s.z_dm - ground[s.piece] >= step]


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


def match_top(z_m, tops):
    """Nearest learnt top in the standing/crouch window, metres; ties take the lower top."""
    matches = [t for t in tops if t - TOP_TOL_M - CROUCH_DROP_M - EPS <= z_m <= t + TOP_TOL_M + EPS]
    # Recorded z is quantized to dm; ignore sub-nanometre floating error when comparing a decimal tie.
    return min(matches, key=lambda t: (round(abs(t - z_m), 9), t)) if matches else None


def _fold_crouches(stands):
    """Fold lower levels into better-supported higher levels of one piece, before emitting any rows.
    Support is the original number of stands, fixed before folds; equal support keeps both levels.
    Known key/up stances keep their heights, but still support their original levels."""
    # Keep the recorded dm levels separate here: FLOOR_TOL_M would already merge the entire crouch window.
    levels = defaultdict(list)
    for s in stands:
        levels[s.z_dm].append(s)
    folded = []
    for z, lv in sorted(levels.items()):
        higher = [h for h, other in levels.items() if len(other) > len(lv)
                  and 0 < h - z <= (TOP_TOL_M + CROUCH_DROP_M) * 10 + EPS]
        target = min(higher) if higher else None
        folded.extend(replace(s, z_dm=int(round(target)))
                      if target is not None and s.crouch == "none" else s for s in lv)
    return folded


def judge(stands, ground, leaf_floor):
    """(world-dm top rows, [(stand, piece, top world dm)], conflict records, fate counts).
    `leaf_floor(px)` yields only connected floor heights in world dm at that pixel's leaf."""
    counts = dict(teach=0, assign=0, conflict=0, ground=0, platform=0, blackout=0)
    teaching, pending = defaultdict(list), []
    for s in stands:
        cands = tuple(sorted(p for p in s.candidates
                             if p in ground and s.z_dm - ground[p] >= BOX_STEP_M * 10 - EPS))
        if not cands or (not s.inside and any(abs(z - s.z_dm) <= TOP_TOL_M * 10 + EPS
                                             for z in leaf_floor(s.px))):
            counts['ground'] += 1
        elif len(cands) == 1 and s.inside == cands[0]:
            teaching[cands[0]].append(s)
            counts['teach'] += 1
        else:
            pending.append((s, cands))
    learnt, rows = {}, []
    for piece, group in sorted(teaching.items()):
        got = top_rows(_fold_crouches(group))
        learnt[piece] = sorted(set(int(z) for z in got[:, 2]))
        rows.extend(got.tolist())
    assigned, conflicts = [], []
    for s, cands in pending:
        matches = []
        for piece in cands:
            top = match_top(s.z_dm / 10, [z / 10 for z in learnt.get(piece, ())])
            if top is not None:
                top_dm = int(round(top * 10))
                matches.append((abs(top_dm - s.z_dm), piece, top_dm))
        if matches:
            _, piece, z = min(matches)
            assigned.append((s, piece, z))
            counts['assign'] += 1
        else:
            distances = dict(zip(s.candidates, s.cand_dist))
            candidates = []
            for p in cands:
                candidate = [p, [round(z / 10, 1) for z in learnt.get(p, ())]]
                dist = distances.get(p)
                if dist is not None:
                    candidate.append(round(dist, 2))
                candidates.append(candidate)
            conflicts.append({'match': s.match, 'round': s.round, 'slot': s.slot, 't0': s.t0, 't1': s.t1,
                              'z': round(s.z_dm / 10, 1), 'px': list(s.px),
                              'candidates': candidates})
            counts['conflict'] += 1
    rows.sort(key=lambda r: (r[1], r[0], r[2]))
    return np.asarray(rows, np.int32).reshape(-1, 3), assigned, conflicts, counts


# ---------------------------------------------------------------- read from a geometry (W7)

REACH_SLOTS = 3
_CACHE_MAX = 16
_tops_cache: dict = {}
_ground_cache: dict = {}
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


def piece_ground(geo):
    """{cover piece id: its ring floor in m}, read-only; {} without heights or cover. Cached by their shas,
    like piece_tops, using the attached asset's coarse floors (dm). A piece with no ring floor is left out."""
    if geo.heights is None or geo.cover_px is None:
        return MappingProxyType({})
    key = _key(geo)
    if key is not None and key in _ground_cache:
        return MappingProxyType(_ground_cache[key])
    ground = {piece: z / 10.0 for piece, z in ground_of(geo.cover_px, geo.heights.floors).items()}
    if key is not None:
        _remember(_ground_cache, key, ground)
    return MappingProxyType(ground)


def _reach_of(geo, tops, metres=TOP_REACH_M, metric=False) -> np.ndarray:
    from scipy import ndimage

    reach = int(math.ceil(metres / geo.m_per_px - 1e-9))
    pad = math.ceil(metres / geo.m_per_px) + 1 if metric else reach + 1
    cover = np.asarray(geo.cover_px)
    boxes = ndimage.find_objects(cover.astype(np.int32))
    dist = np.full((PX, PX, REACH_SLOTS), np.inf, np.float32)
    ids = np.zeros((PX, PX, REACH_SLOTS), np.int16)
    for piece in sorted(tops):
        if piece > len(boxes) or boxes[piece - 1] is None:
            continue
        ys, xs = boxes[piece - 1]
        y0, y1 = max(ys.start - pad, 0), min(ys.stop + pad, PX)
        x0, x1 = max(xs.start - pad, 0), min(xs.stop + pad, PX)
        d = ndimage.distance_transform_edt(cover[y0:y1, x0:x1] != piece).astype(np.float32)
        if metric:
            d[d * geo.m_per_px > metres + 1e-6] = np.inf
        else:
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


def piece_reach(geo, metres=TOP_REACH_M, metric=False):
    """PX x PX x 3 int16 (read-only): up to 3 topped pieces within metres, nearest first (ties: lower id), 0-padded;
    None without tops. Metric mode uses metres directly; the default rounds reach up to pixels. Computed per piece
    on its padded bounding box, cached by shas, distance and metric mode."""
    tops = piece_tops(geo)
    if not tops:
        return None
    key = _key(geo)
    if key is not None:
        key = (key, metres, metric)
    if key is not None and key in _reach_cache:
        return _reach_cache[key]
    reach = _reach_of(geo, tops, metres, metric)
    if key is not None:
        _remember(_reach_cache, key, reach)
    return reach
