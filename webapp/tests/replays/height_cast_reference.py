"""Frozen pre-optimization height caster (cd574b2), used as an independent oracle."""
from app.control.geometry import *
from app.control.geometry import hc

def _cast_heights(geo: Geometry, x: float, y: float, angles_deg: np.ndarray, smokes: list, eye: float,
                  own: int | None, record: dict | None) -> np.ndarray:
    """`cast` on nodes, with the height test. The own-cell and target-cell plate rule below is not the
    spec's wording (it exempts both cells, which fails its own tunnel test). Per ray: `hor`, the steepest slope from the eye to the
    ground passed so far (a cell's ground is its lowest floor's position-z - STAND_M - LEDGE_M; the
    viewer's own cell and the cell being tested don't count), and blocked slope intervals, one per upper
    floor passed: the slopes at which a line is at the plate's height (position-z - STAND_M) somewhere
    inside the plate's cell. A plate of the viewer's own cell blocks every line that reaches its height
    before leaving the cell (so nobody sees through their own floor or ceiling), and a plate of the cell
    being tested blocks from the cell's near edge up to the point tested (so a body on a slab is seen
    from below only over its edge). In the viewer's own cell only the viewer's own node is seen."""
    a = np.deg2rad(angles_deg)
    dx, dy = np.cos(a), np.sin(a)
    rays = len(a)
    seen = np.zeros(geo.n, bool)
    live = np.ones(rays, bool)
    hits = np.zeros(rays, np.int16)
    reach = np.full(rays, np.inf)
    for s in smokes:
        if isinstance(s, Wall):
            reach = np.minimum(reach, wall_hit_px(s, x, y, dx, dy))
    smokes = [s for s in smokes if not isinstance(s, Wall)]
    started_in = [(x - s[0]) ** 2 + (y - s[1]) ** 2 < s[2] ** 2 for s in smokes]
    src = geo.space_at_px(x, y)
    own = geo.node_at_px(x, y, eye - hc.EYE_M) if own is None else own
    seen[own] = True
    node_of, node_z, unresolved = (geo.space_nodes if geo.adaptive else geo.node_of), geo.node_z, (geo.space_unresolved if geo.adaptive else geo.unresolved)
    ground = (node_z[np.maximum(node_of[:, 0], 0)] if geo.adaptive else node_z[: GRID * GRID]) - hc.STAND_M - hc.LEDGE_M
    if geo.adaptive:
        ground[node_of[:, 0] < 0] = np.nan
    upper = hc.MAX_FLOORS - 1 if geo.n > GRID * GRID else 0      # no plate work on a map with none
    hor = np.full(rays, -np.inf)
    flat = np.zeros(rays, bool)                    # the ray met an unresolved cell: 2D from there on
    lo = np.full((rays, SLAB_SLOTS), np.inf)       # blocked slope intervals [lo, hi]; empty ones never match
    hi = np.full((rays, SLAB_SLOTS), -np.inf)
    used = np.zeros(rays, np.int16)
    pend_lo = np.full((rays, max(upper, 1)), np.inf)   # the current cell's plates, joined once the ray leaves it
    pend_hi = np.full((rays, max(upper, 1)), -np.inf)
    cur = np.full(rays, -1, np.int64)
    index = np.arange(rays)
    step_m = RAY_STEP_PX * geo.m_per_px
    with np.errstate(invalid="ignore", divide="ignore"):
        for step in range(0, RAY_MAX_PX, RAY_STEP_PX):
            px = x + dx * step
            py = y + dy * step
            live &= (px >= 0) & (px < PX) & (py >= 0) & (py < PX) & (step < reach)
            pxc = np.clip(px, 0, PX - 1).astype(np.int32)
            pyc = np.clip(py, 0, PX - 1).astype(np.int32)
            if step > 3:
                hits += geo.sight[pyc, pxc]
                live &= hits <= geo.tol_hits
            for (sx, sy, r, solid), inside0 in zip(smokes, started_in):
                inside = (px - sx) ** 2 + (py - sy) ** 2 < r * r
                live &= ~inside if solid and step > 0 else inside == inside0
            if not live.any():
                break
            cell = geo.space_grid[pyc // 4, pxc // 4] if geo.adaptive else (pyc // CELL) * GRID + pxc // CELL
            entered = cell != cur
            if entered.any():
                # the plates of the cell each ray just left now block what lies beyond it
                for k in range(upper):
                    add = entered & (pend_hi[:, k] >= pend_lo[:, k])
                    if add.any():
                        rows = index[add]
                        prev = np.clip(used[add] - 1, 0, SLAB_SLOTS - 1)
                        # a plate that carries on from the last one (the next cell of a bridge) widens its
                        # interval; anything else takes a new slot, or the last one when they are used up
                        joins = (used[add] > 0) & (pend_lo[add, k] <= hi[rows, prev] + 1e-6) \
                            & (pend_hi[add, k] >= lo[rows, prev] - 1e-6)
                        slot = np.where(joins, prev, np.minimum(used[add], SLAB_SLOTS - 1))
                        lo[rows, slot] = np.minimum(lo[rows, slot], pend_lo[add, k])
                        hi[rows, slot] = np.maximum(hi[rows, slot], pend_hi[add, k])
                        used[add] = np.minimum(used[add] + ~joins, SLAB_SLOTS)
                if upper:
                    pend_lo[entered] = np.inf
                    pend_hi[entered] = -np.inf
                went_flat = entered & live & unresolved[cell] & ~flat
                if went_flat.any():
                    flat |= went_flat
                    if record is not None:
                        record["unresolved_rays"] = record.get("unresolved_rays", 0) + int(went_flat.sum())
                cur = np.where(entered, cell, cur)
            d = step * geo.m_per_px
            in_src = cell == src
            away = live & ~in_src
            if away.any():
                slabs = bool(upper) and (bool(used.any()) or bool((pend_hi >= pend_lo).any()))
                for k in range(upper + 1):
                    node = node_of[cell, k]
                    there = away & (node >= 0)
                    if not there.any():
                        continue
                    z = node_z[np.maximum(node, 0)]
                    slope = (z + hc.BODY_M - eye)[:, None] / d
                    ok = slope[:, 0] >= hor
                    if slabs:     # SLOPE_EPS: a line that grazes a plate's edge is blocked, whatever the rounding
                        ok &= ~((slope >= lo - SLOPE_EPS) & (slope <= hi + SLOPE_EPS)).any(1) \
                            & ~((slope >= pend_lo - SLOPE_EPS) & (slope <= pend_hi + SLOPE_EPS)).any(1)
                    # no known height (off the walk mask, or unresolved): the 2D answer
                    ok |= flat | np.isnan(z)
                    seen[node[there & ok]] = True
                # this cell's ground raises the horizon for what is beyond it
                np.fmax(hor, np.where(away, (ground[cell] - eye) / d, np.nan), out=hor)
            for k in range(upper):
                node = node_of[cell, k + 1]
                has = live & (node >= 0)
                if not has.any():
                    continue
                plate = node_z[np.maximum(node, 0)] - hc.STAND_M - eye
                mine = has & in_src
                else_ = has & ~in_src
                if mine.any():
                    # the viewer's own cell: every line that reaches the plate's height before leaving the
                    # cell, which it does by the next step at the latest
                    out = plate / (d + step_m)
                    above = plate > 0
                    pend_lo[:, k] = np.where(mine, np.where(above, np.minimum(pend_lo[:, k], out), -np.inf),
                                             pend_lo[:, k])
                    pend_hi[:, k] = np.where(mine, np.where(above, np.inf, np.maximum(pend_hi[:, k], out)),
                                             pend_hi[:, k])
                if else_.any():
                    far = plate / d
                    near = plate / max(d - step_m, step_m / 2)
                    pend_lo[:, k] = np.where(else_, np.minimum(pend_lo[:, k], np.minimum(near, far)), pend_lo[:, k])
                    pend_hi[:, k] = np.where(else_, np.maximum(pend_hi[:, k], np.maximum(near, far)), pend_hi[:, k])
    return seen
