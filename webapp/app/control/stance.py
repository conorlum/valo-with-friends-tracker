"""A player's stance relative to a cover piece: standing on it (`on_box`, can see over the piece) or beside it
(hugging it, crossing it in a jump, just landed, no z). An on-box anchor is the matched piece's nearest top node
(ties: lower id); without top nodes, and for beside stances, the existing ground anchor stands in for the player.

The z rule (R2, R3): over the last BOX_SETTLE_S, z must stay within BOX_STEADY_DZ_M in one track segment, with no
NaN. The window median matches a learnt top within TOP_TOL_M above it or TOP_TOL_M + CROUCH_DROP_M below it;
the nearest matching top wins (ties: lower). It must also be at least BOX_STEP_M above the piece's ring floor.
Try the piece under the pixel first, then topped pieces within TOP_REACH_M, nearest first. No earlier entry
into the outline is needed. Without heights, tops or a ring floor a piece never matches.
With OVER enabled, `over` sees over a learnt top immediately within 1.1 m of its outline, at or above its
crouch window, using the sample's real z. On-box stances win; vision alone keeps the beside ground anchor."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.control.heights import (BOX_SETTLE_S, BOX_STEADY_DZ_M, BOX_STEP_M, CROUCH_DROP_M,
                                 TOP_REACH_M, TOP_TOL_M)  # noqa: F401 (the rules hash covers them)

ANCHOR_MAX_M = 2.0

EPS = 1e-6

OVER = True

NO_ANCHOR = "stance: no anchor within 2 m of a box, nearest walkable node used"

CALLS = 0                  # how many times stance_at has been called (the identity witness reads it)


@dataclass(frozen=True)
class Stance:
    kind: str
    px: tuple[float, float]
    eye_px: tuple[float, float]
    see_over: tuple[int, ...]          # the cover pieces the eye sees over, sorted; () for beside (R6)
    anchor: int | None
    piece: int = 0                     # the piece stood on (0 for beside)
    own: int | None = None             # the node the eye stands over; None = the caller's cell


def vantage(geo, piece: int, z: float) -> tuple[int, ...]:
    """What the eye of a player at position-z `z` (m) over `piece` sees over (R6, D-E): `piece` itself and every
    other piece with learnt tops whose highest top, less STAND_M (its surface), is strictly below the eye
    (z + EYE_M). A piece with no known top is never added (it blocks). Cached and computed with z rounded to 0.1 m."""
    from app.control import heights as hc

    cache = geo.__dict__.setdefault("_vantage", {})
    key = (int(piece), round(float(z), 1))
    if key not in cache:
        eye = key[1] + hc.EYE_M
        over = {int(piece)}
        for q, tops in geo.box_tops.items():
            if tops and max(tops) - hc.STAND_M < eye:
                over.add(int(q))
        cache[key] = tuple(sorted(over))
    return cache[key]


def _top_match(rnd, slot: int, i: int, tops, ground):
    """The learnt top (m) the player is steady at in sample i, or None: same segment and no NaN over the last
    BOX_SETTLE_S, range within BOX_STEADY_DZ_M, median in a top's crouch window and at least BOX_STEP_M above
    its ring floor (`ground`, m). The nearest matching top wins, with the lower top on a tie."""
    zs = rnd.heights.get(slot)
    if zs is None or not tops or ground is None:
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
    if med < ground + BOX_STEP_M - EPS:
        return None
    from app.control.box_tops import match_top

    return match_top(med, tops)


def _on_piece(rnd, slot: int, i: int, piece: int) -> float | None:
    """The matched top (m) when the player is on `piece` there, else None."""
    return _top_match(rnd, slot, i, rnd.geo.box_tops.get(piece), rnd.geo.box_ground.get(piece))


def _over_match(rnd, slot: int, i: int, piece: int) -> float | None:
    """Nearest learnt top at or below the sample's crouch window; no steady-time requirement."""
    zs = rnd.heights.get(slot)
    tops, ground = rnd.geo.box_tops.get(piece), rnd.geo.box_ground.get(piece)
    if zs is None or not tops or ground is None:
        return None
    z = float(zs[i])
    if np.isnan(z) or z < ground + BOX_STEP_M - EPS:
        return None
    matches = [t for t in tops if z >= t - TOP_TOL_M - CROUCH_DROP_M - EPS]
    return min(matches, key=lambda t: (round(abs(t - z), 9), t)) if matches else None


def _anchor(rnd, slot: int, i: int, px, label: int) -> int | None:
    from app.control import geometry as cg

    geo = rnd.geo
    # Every live ground node: on refined ground the coarse parent is disabled and its fine leaves hold the floor.
    walk = geo.walk_n & ~geo.is_top
    n = len(walk)
    cen = np.asarray(geo.centres[:n], float)
    dist = np.hypot(cen[:, 0] - px[0], cen[:, 1] - px[1])
    near = np.nonzero(walk & (dist * geo.m_per_px <= ANCHOR_MAX_M + EPS))[0]

    def clear(node):
        return cg.los(geo, (float(cen[node, 0]), float(cen[node, 1]), None), (px[0], px[1], None),
                      see_over=(label,))

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
        ok = np.nonzero(walk)[0]
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


def _view_ids(geo, x: float, y: float):
    """The pieces whose view reach holds pixel (x, y), nearest first, or () without box_view_reach."""
    reach = geo.box_view_reach
    if reach is None:
        return ()
    size = reach.shape[0]
    ix, iy = int(x), int(y)
    if not (0 <= ix < size and 0 <= iy < size):
        return ()
    return tuple(int(p) for p in reach[iy, ix] if p)


def _top_anchor(geo, px, piece: int) -> int | None:
    """Nearest top node of the matched piece; None preserves the caller's ground fallback."""
    nodes = geo.top_nodes[geo.top_piece == piece]
    if not len(nodes):
        return None
    distance = np.sum((geo.centres[nodes].astype(float) - px) ** 2, axis=1)
    return int(nodes[np.lexsort((nodes, distance))[0]])


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
        top = _on_piece(rnd, slot, i, label)
        if top is not None:
            anchor = _top_anchor(geo, px, label)
            if anchor is None:
                anchor = _anchor(rnd, slot, i, px, label)
            z = rnd.heights[slot][i] if OVER else top
            out = Stance("on_box", px, px, vantage(geo, label, z), anchor, label)
    if out is None:
        for piece in _reach_ids(geo, x, y):
            if piece == label:
                continue
            top = _on_piece(rnd, slot, i, piece)
            if top is not None:
                anchor = _top_anchor(geo, px, piece)
                if anchor is None:
                    anchor = int(geo.node_at_px(x, y))
                z = rnd.heights[slot][i] if OVER else top
                out = Stance("on_box", px, px, vantage(geo, piece, z), anchor, piece)
                break
    if out is None and OVER:
        candidates = dict.fromkeys(((label,) if label else ()) + _view_ids(geo, x, y))
        matches = []
        for piece in candidates:
            top = _over_match(rnd, slot, i, piece)
            if top is not None:
                z = float(rnd.heights[slot][i])
                matches.append((abs(top - z), top, piece))
        if matches:
            _, _, piece = min(matches)
            see_over = vantage(geo, piece, z)
            if not label or label in see_over:
                anchor = _anchor(rnd, slot, i, px, label) if label else None
                out = Stance("over", px, px, see_over, anchor, piece, own=int(geo.node_at_px(x, y, z)))
    if out is None and label:
        anchor = _anchor(rnd, slot, i, px, label)
        eye = (float(geo.centres[anchor][0]), float(geo.centres[anchor][1])) if anchor is not None else px
        out = Stance("beside", px, eye, (), anchor)
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
    reach_ids = _view_ids if OVER else _reach_ids
    if int(geo.cover_px[int(y), int(x)]) == 0 and not reach_ids(geo, x, y):
        return None
    return stance_at(rnd, slot, t)


def line_ends(rnd, a, b, t, pa, pb):
    """The two ends of a sight line between players `a` and `b` (positions `pa`, `pb` at t): each at its player's
    stance eye when it has one, else the player's own (x, y); and the see-over pieces of each end (a tuple, () = none)."""
    ends, over = [], []
    for slot, p in ((a, pa), (b, pb)):
        st = boxed_stance(rnd, slot, t)
        if st is None:
            ends.append((p[0], p[1], None))
            over.append(())
        else:
            ends.append((st.eye_px[0], st.eye_px[1], None))
            over.append(st.see_over)
    return ends[0], ends[1], (over[0], over[1])
