"""A player's stance relative to a cover piece: standing on it (`on_box`, can see over the piece) or beside it
(hugging it, crossing it in a jump, just landed, no z). Both kinds have an anchor: a walkable node near the player
that stands in for them where the engine needs a node.

The z rule (R2, R3, R4): a player is on a piece when their z has been steady (range within BOX_STEADY_DZ_M over the
last BOX_SETTLE_S) at one of the piece's learnt tops (`geo.box_tops`, from the height map), within TOP_TOL_M. Inside the
piece's outline that is enough. Off it, within TOP_REACH_PX (`geo.box_reach`, nearest piece first), only when the
player's current stay at that top (the run of samples at the top height in one track segment) included a sample
inside the piece's outline. Without heights or tops nobody is ever on a box. Nothing in the engine calls
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
# PROVISIONAL(D4): W2, 36 replays: 159 of 162 top stands within 0.25 m of their top, none 0.25-0.55
TOP_TOL_M = 0.3
# PROVISIONAL(D4): W2, 36 replays: 98.8% of off-outline top-stay samples within 24 px, none beyond 36
TOP_REACH_PX = 36

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


def _top_match(rnd, slot: int, i: int, tops):
    """The learnt top (m) the player is steady at in sample i, or None: same segment and no NaN over the last
    BOX_SETTLE_S, range within BOX_STEADY_DZ_M, window median within TOP_TOL_M of a top (the nearest one)."""
    zs = rnd.heights.get(slot)
    if zs is None or not tops:
        return None
    w = int(math.floor(BOX_SETTLE_S * rnd.hz + 1e-9))
    if i - w < 0 or np.isnan(zs[i]):
        return None
    seg = rnd._segment[slot]
    if not (seg[i - w:i + 1] == seg[i]).all():
        return None
    win = zs[i - w:i + 1]
    if np.isnan(win).any():
        return None
    if float(win.max() - win.min()) > BOX_STEADY_DZ_M + EPS:
        return None
    med = float(np.median(win))
    best = min(tops, key=lambda t: (abs(t - med), t))
    return best if abs(best - med) <= TOP_TOL_M + EPS else None


def _stay_starts(rnd, slot: int, top: float) -> np.ndarray:
    """Per sample, the index where the stay at `top` containing it starts (-1 when the sample is not at the top):
    a stay is a maximal run in one track segment with z within TOP_TOL_M of the top. Cached on the round."""
    cache = rnd.__dict__.setdefault("_stance_stays", {})
    key = (slot, round(float(top), 4))
    if key not in cache:
        zs, seg = rnd.heights[slot], rnd._segment[slot]
        at = np.zeros(len(zs), bool)
        ok = ~np.isnan(zs)
        at[ok] = np.abs(zs[ok] - top) <= TOP_TOL_M + EPS
        starts = np.full(len(zs), -1, np.int64)
        cur = -1
        for k in range(len(zs)):
            if not at[k]:
                cur = -1
                continue
            if cur < 0 or seg[k] != seg[k - 1]:
                cur = k
            starts[k] = cur
        cache[key] = starts
    return cache[key]


def _inside_counts(rnd, slot: int, piece: int) -> np.ndarray:
    """Cumulative count of the slot's samples whose pixel is inside `piece`'s outline (index k = samples 0..k)."""
    from app.control import geometry as cg

    cache = rnd.__dict__.setdefault("_stance_inside", {})
    if (slot, piece) not in cache:
        geo, tr = rnd.geo, rnd.tracks[slot]
        size = geo.cover_px.shape[0]
        xs = np.clip((tr[1] * cg.PX / 10000).astype(int), 0, size - 1)
        ys = np.clip((tr[2] * cg.PX / 10000).astype(int), 0, size - 1)
        cache[(slot, piece)] = np.cumsum(np.asarray(geo.cover_px)[ys, xs] == piece)
    return cache[(slot, piece)]


def _entered(rnd, slot: int, i: int, piece: int, top: float) -> bool:
    """True when the stay at `top` that sample i belongs to included a sample inside the piece's outline."""
    start = int(_stay_starts(rnd, slot, top)[i])
    if start < 0:
        return False
    cnt = _inside_counts(rnd, slot, piece)
    return int(cnt[i]) - (int(cnt[start - 1]) if start > 0 else 0) > 0


def _on_piece(rnd, slot: int, i: int, piece: int, in_outline: bool) -> bool:
    tops = rnd.geo.box_tops.get(piece)
    top = _top_match(rnd, slot, i, tops)
    if top is None:
        return False
    return in_outline or _entered(rnd, slot, i, piece, top)


def _anchor(rnd, slot: int, i: int, px, label: int) -> int | None:
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
    return chosen


def _reach_ids(geo, x: float, y: float):
    """The pieces whose reach holds pixel (x, y), nearest first, or () without box_reach."""
    reach = geo.box_reach
    if reach is None:
        return ()
    size = reach.shape[0]
    ix, iy = int(x), int(y)
    if not (0 <= ix < size and 0 <= iy < size):
        return ()
    return tuple(int(p) for p in reach[iy, ix] if p)


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
    memo = rnd.__dict__.setdefault("_stance_memo", {})
    if (slot, i) in memo:
        return memo[(slot, i)]
    px = (x, y)
    out = None
    if label:
        if _on_piece(rnd, slot, i, label, True):
            out = Stance("on_box", px, px, label, _anchor(rnd, slot, i, px, label))
        else:
            anchor = _anchor(rnd, slot, i, px, label)
            eye = (float(geo.centres[anchor][0]), float(geo.centres[anchor][1])) if anchor is not None else px
            out = Stance("beside", px, eye, 0, anchor)
    else:
        for piece in _reach_ids(geo, x, y):
            if _on_piece(rnd, slot, i, piece, False):
                out = Stance("on_box", px, px, piece, int(geo.node_at_px(x, y)))
                break
    memo[(slot, i)] = out
    return out


def boxed_stance(rnd, slot, t) -> Stance | None:
    """The engine's guard, then `stance_at`: None without a position, without a cover array, outside it, or on a
    pixel with no piece and none within a topped piece's reach (so a player on open ground never costs a call)."""
    geo = rnd.geo
    if geo.cover_px is None:
        return None
    p = rnd.pos(slot, t)
    if p is None:
        return None
    size = geo.cover_px.shape[0]
    x, y = p[0], p[1]
    if not (0 <= x < size and 0 <= y < size):
        return None
    if int(geo.cover_px[int(y), int(x)]) == 0 and not _reach_ids(geo, x, y):
        return None
    return stance_at(rnd, slot, t)


def line_ends(rnd, a, b, t, pa, pb):
    """The two ends of a sight line between players `a` and `b` (positions `pa`, `pb` at t): each at its player's
    stance eye when it has one, else the player's own (x, y); and the see-over piece of each end (0 = none)."""
    ends, over = [], []
    for slot, p in ((a, pa), (b, pb)):
        st = boxed_stance(rnd, slot, t)
        if st is None:
            ends.append((p[0], p[1], None))
            over.append(0)
        else:
            ends.append((st.eye_px[0], st.eye_px[1], None))
            over.append(st.see_over)
    return ends[0], ends[1], (over[0], over[1])
