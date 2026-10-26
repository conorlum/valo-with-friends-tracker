"""A player's stance relative to a cover piece: standing on it (`on_box`, can see over the piece) or beside it
(hugging it, crossing it in a jump, just landed, no z). Both kinds have an anchor: a walkable node near the player
that stands in for them where the engine needs a node.

The z rule (the GATE probe, W9): a player whose pixel is inside a piece is on it when, over the last
BOX_SETTLE_S, their z stayed within BOX_STEADY_DZ_M and its median is at least BOX_RISE_M above the piece's
reference floor. The reference is per round and per piece: the median floor of the walkable ring 8-24 px around the
piece (known floors), else the median z of every player's samples on that ring. Nothing in the engine calls
`stance_at` yet (the identity witness reads `CALLS`)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

BOX_SETTLE_S = 0.5
ANCHOR_MAX_M = 2.0
# PROVISIONAL(D3): fitted by the GATE probe (W9, 36 replays); GATE 2 awaits the owner's call
BOX_RISE_M = 0.5
BOX_STEADY_DZ_M = 0.25

RING_NEAR_PX = 8           # the reference ring: walkable pixels this far from the piece ...
RING_FAR_PX = 24           # ... to this far, and in no piece (probes/gate_probe.py)
EPS = 1e-6

NO_ANCHOR = "stance: no anchor within 2 m of a box, nearest walkable node used"

CALLS = 0                  # how many times stance_at has been called (the identity witness reads it)


@dataclass(frozen=True)
class Stance:
    kind: str
    px: tuple[float, float]
    eye_px: tuple[float, float]
    see_over: int
    anchor: int | None


def _ring(geo, piece: int):
    """(boolean PX x PX mask of the piece's reference ring, the ring's pixels' distinct nodes), cached on geo."""
    cache = geo.__dict__.setdefault("_stance_rings", {})
    if piece in cache:
        return cache[piece]
    from scipy import ndimage

    lab = np.asarray(geo.cover_px, dtype=np.int32)
    d = ndimage.distance_transform_edt(lab != piece)
    mask = (d >= RING_NEAR_PX) & (d <= RING_FAR_PX) & np.asarray(geo.walk_px, bool) & (lab == 0)
    nodes = np.zeros(0, int)
    if geo.node_z is not None and mask.any():
        ys, xs = np.nonzero(mask)
        _, first = np.unique((ys // 4) * 4096 + xs // 4, return_index=True)
        nodes = np.unique([int(geo.node_at_px(float(xs[k]), float(ys[k]))) for k in first])
    cache[piece] = (mask, nodes)
    return cache[piece]


def _compute_reference(rnd, piece: int):
    """(use_heights, reference z) for one piece in this round, or None without any ring z."""
    geo = rnd.geo
    mask, nodes = _ring(geo, piece)
    if geo.node_z is not None and len(nodes):
        z = np.asarray(geo.node_z)[nodes]
        z = z[np.isfinite(z)]
        if len(z):
            return True, float(np.median(z))
    size = mask.shape[0]
    vals = []
    for s, tr in rnd.tracks.items():
        zs = rnd.track_z.get(s)
        if zs is None:
            continue
        xs = np.clip((tr[1] * size / 10000).astype(int), 0, size - 1)
        ys = np.clip((tr[2] * size / 10000).astype(int), 0, size - 1)
        keep = mask[ys, xs] & ~np.isnan(zs)
        if keep.any():
            vals.append(zs[keep])
    if not vals:
        return None
    return False, float(np.median(np.concatenate(vals)))


def _reference(rnd, piece: int):
    cache = rnd.__dict__.setdefault("_box_ref", {})
    if piece not in cache:
        cache[piece] = _compute_reference(rnd, piece)
    return cache[piece]


def _on_box(rnd, slot: int, i: int, ref) -> bool:
    if ref is None:
        return False
    use_heights, ref_z = ref
    zs = rnd.heights.get(slot) if use_heights else rnd.track_z.get(slot)
    if zs is None:
        return False
    w = int(math.floor(BOX_SETTLE_S * rnd.hz + 1e-9))
    if i - w < 0 or np.isnan(zs[i]):
        return False
    seg = rnd._segment[slot]
    if not (seg[i - w:i + 1] == seg[i]).all():
        return False
    win = zs[i - w:i + 1]
    if np.isnan(win).any():
        return False
    if float(win.max() - win.min()) > BOX_STEADY_DZ_M + EPS:
        return False
    return float(np.median(win)) - ref_z >= BOX_RISE_M - EPS


def _anchor(rnd, slot: int, i: int, px, label: int, ref) -> int | None:
    from app.control import geometry as cg

    geo = rnd.geo
    walk = geo.walk.ravel()
    n = min(len(geo.centres), walk.size)
    cen = np.asarray(geo.centres[:n], float)
    dist = np.hypot(cen[:, 0] - px[0], cen[:, 1] - px[1])
    near = np.nonzero(walk[:n] & (dist * geo.m_per_px <= ANCHOR_MAX_M + EPS))[0]

    def clear(node):
        return cg.los(geo, (float(cen[node, 0]), float(cen[node, 1]), None), (px[0], px[1], None),
                      see_over=(label, label))

    chosen = None
    cands = {int(k) for k in near}
    if cands:
        seg, tr = rnd._segment[slot], rnd.tracks[slot]
        size = geo.cover_px.shape[0]
        for j in range(i - 1, -1, -1):
            if seg[j] != seg[i]:
                break
            x, y = tr[1][j] * cg.PX / 10000, tr[2][j] * cg.PX / 10000
            if int(geo.cover_px[min(max(int(y), 0), size - 1), min(max(int(x), 0), size - 1)]) == 0:
                prev = int(geo.node_at_px(x, y))
                if prev in cands and clear(prev):
                    chosen = prev
                break
        if chosen is None:
            for node in sorted(cands, key=lambda k: (dist[k], k)):
                if clear(node):
                    chosen = node
                    break
    if chosen is None:
        ok = np.nonzero(walk[:n])[0]
        if not len(ok):
            return None
        chosen = int(ok[np.argmin(dist[ok])])           # first minimum: the lowest node id on a tie
        rnd.missing[NO_ANCHOR] += 1
    if ref is not None and ref[0]:
        node = int(geo.node_at_px(float(cen[chosen, 0]), float(cen[chosen, 1]), ref[1]))
        if 0 <= node < walk.size and walk[node]:
            chosen = node
    return chosen


def stance_at(rnd, slot, t) -> Stance | None:
    global CALLS
    CALLS += 1
    geo = rnd.geo
    if geo.cover_px is None:
        return None
    i = rnd._sample(slot, t)
    if i is None:
        return None
    from app.control import geometry as cg

    tr = rnd.tracks[slot]
    x, y = float(tr[1][i] * cg.PX / 10000), float(tr[2][i] * cg.PX / 10000)
    size = geo.cover_px.shape[0]
    if not (0 <= x < size and 0 <= y < size):
        return None
    label = int(geo.cover_px[int(y), int(x)])
    if label == 0:
        return None
    memo = rnd.__dict__.setdefault("_stance_memo", {})
    if (slot, i) in memo:
        return memo[(slot, i)]
    px = (x, y)
    ref = _reference(rnd, label)
    anchor = _anchor(rnd, slot, i, px, label, ref)
    if _on_box(rnd, slot, i, ref):
        out = Stance("on_box", px, px, label, anchor)
    else:
        eye = (float(geo.centres[anchor][0]), float(geo.centres[anchor][1])) if anchor is not None else px
        out = Stance("beside", px, eye, 0, anchor)
    memo[(slot, i)] = out
    return out
