"""Map features compiled for control (docs/superpowers/plans/2026-10-04-map-interaction-tagger.md, M0 and M4/M5;
the contract is docs/superpowers/specs/2026-10-04-map-features-contract.md).

The annotations (`map_features` in tags.json, app/replays/map_feature_schema.py) are minimap u/v geometry plus
behaviour. This module turns them into what the engine would consume, on synthetic fixtures for now: no engine
caller uses it in this build (no feature is enabled, no CONTROL_REVISION bump), so every committed map computes
exactly what it did.

- **Rasterising** (`raster`): a point, polyline (with an optional width), polygon (even-odd) or paint becomes
  PAINT_GRID x PAINT_GRID cells, tested at cell centres in u/v. Deterministic, and twinned bit for bit by
  scripts/control_tagger_core.js `Features.raster`.
- **One base domain**: the walkable ground is fixed for the round (permanent ground plus every enabled bundle's
  potential ground, W8), so node indices never change with a feature's state. A state only adds a movement
  block over nodes (`movement_blocks`) and sight occluders.
- **Floors** (`floor_nodes`): a floor binding is {"z_band": [lo, hi] metres of position-z above the map's lowest
  floor, "height_sha"}; per cell it picks the node whose `node_z` lies in the band. On a map with a height asset an
  unbanded, stale or ambiguous binding is pending and blocks nothing; it never falls back to every floor. A flat
  map has one node per cell, so a binding is moot there.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from app.control.geometry import CELL, GRID, PAINT_GRID, PX, Geometry
from app.replays import map_feature_schema as ms

COMPILER_VERSION = 1
P = PAINT_GRID
CELL_UV = ms.UV_MAX / P          # 39.0625 u/v per paint cell (exact in binary)
PX_PER_PAINT = PX // P
PAINT_PER_GRID = P // GRID


# ---------------------------------------------------------------- rasterising

def _centres() -> tuple[np.ndarray, np.ndarray]:
    i = np.arange(P, dtype=np.float64)
    c = (i + 0.5) * CELL_UV
    return np.meshgrid(c, c)          # (u, v) of each cell centre, [row, col]


def _segment_cells(a, b, r2: float, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Cells whose centre is within sqrt(r2) of segment a-b. Same float operations, in the same order, as
    the JS twin (no sqrt): t = clamp(((p - a) . d) / (d . d)), then |a + t d - p|^2 <= r2."""
    ax, ay = float(a[0]), float(a[1])
    dx, dy = float(b[0]) - ax, float(b[1]) - ay
    dd = dx * dx + dy * dy
    if dd == 0.0:
        ex, ey = u - ax, v - ay
        return ex * ex + ey * ey <= r2
    t = ((u - ax) * dx + (v - ay) * dy) / dd
    t = np.minimum(np.maximum(t, 0.0), 1.0)
    ex, ey = ax + t * dx - u, ay + t * dy - v
    return ex * ex + ey * ey <= r2


def raster(geometry: dict | None) -> np.ndarray:
    """P x P bool cells, row-major (row = v)."""
    out = np.zeros((P, P), bool)
    if not geometry:
        return out
    kind = geometry.get("type")
    if kind == "paint":
        raw = np.frombuffer(base64.b64decode(geometry["cells"]), np.uint8)
        return np.unpackbits(raw, bitorder="little")[: P * P].reshape(P, P).astype(bool)
    if kind == "point":
        u, v = geometry["uv"]
        # floor of the rounded quotient, as JavaScript's Math.floor(u / CELL_UV) (not Python's exact //)
        col, row = min(int(np.floor(u / CELL_UV)), P - 1), min(int(np.floor(v / CELL_UV)), P - 1)
        out[row, col] = True
        return out
    u, v = _centres()
    if kind == "polyline":
        width = float(geometry.get("width") or 0.0)
        r = max(width / 2.0, CELL_UV / 2.0)
        pts = geometry["uv"]
        for a, b in zip(pts, pts[1:]):
            out |= _segment_cells(a, b, r * r, u, v)
        return out
    if kind == "polygon":
        pts = geometry["uv"]
        n = len(pts)
        j = n - 1
        for i in range(n):
            xi, yi = float(pts[i][0]), float(pts[i][1])
            xj, yj = float(pts[j][0]), float(pts[j][1])
            if yi != yj:
                crosses = (yi > v) != (yj > v)
                x_at = (xj - xi) * (v - yi) / (yj - yi) + xi
                out ^= crosses & (u < x_at)
            j = i
        return out
    raise ValueError(f"unknown geometry type {kind!r}")


def to_px(cells: np.ndarray) -> np.ndarray:
    """P x P cells as a PX x PX pixel mask (the tagger's paint scale)."""
    return cells.repeat(PX_PER_PAINT, 0).repeat(PX_PER_PAINT, 1)


def to_grid(cells: np.ndarray) -> np.ndarray:
    """P x P cells as the engine's GRID x GRID cells: a grid cell is covered when any of its paint cells is
    (a footprint never leaves half a cell open)."""
    return cells.reshape(GRID, PAINT_PER_GRID, GRID, PAINT_PER_GRID).any((1, 3))


# ---------------------------------------------------------------- floors

@dataclass
class Binding:
    nodes: np.ndarray                       # node indices
    pending: list = field(default_factory=list)   # why some or all cells aren't bound


def _floors_by_id(mf: dict) -> dict:
    return {f.get("id"): f for f in mf.get("floors") or [] if isinstance(f, dict)}


def floor_nodes(geo: Geometry, binding: dict | None, cells: np.ndarray) -> Binding:
    """The nodes of `cells` (flat GRID*GRID bool) on the bound floor. A flat map: the cells themselves."""
    flat = np.flatnonzero(cells)
    if geo.heights is None:
        return Binding(flat.astype(np.int64))
    if not binding or not isinstance(binding.get("z_band"), list):
        return Binding(np.zeros(0, np.int64), [f"floor {(binding or {}).get('id')!r} has no height band"])
    if binding.get("height_sha") != geo.height_sha:
        return Binding(np.zeros(0, np.int64), [f"floor {binding.get('id')!r} was read from height asset "
                                               f"{binding.get('height_sha')!r}, the map has {geo.height_sha!r}"])
    lo, hi = binding["z_band"]
    nodes, ambiguous, missing = [], 0, 0
    for c in flat.tolist():
        if geo.unresolved is not None and geo.unresolved[c]:
            missing += 1
            continue
        hits = [int(n) for n in geo.node_of[c] if n >= 0 and lo <= geo.node_z[n] <= hi]
        if len(hits) == 1:
            nodes.append(hits[0])
        elif hits:
            ambiguous += 1
        else:
            missing += 1
    pending = []
    if ambiguous:
        pending.append(f"floor {binding.get('id')!r}: {ambiguous} cells have more than one floor in its band")
    if missing:
        pending.append(f"floor {binding.get('id')!r}: {missing} cells have no floor in its band")
    return Binding(np.array(nodes, np.int64), pending)


def feature_floor_nodes(geo: Geometry, mf: dict, feature: dict, cells: np.ndarray) -> Binding:
    """The nodes a feature's footprint covers on the floors it names (the union over them)."""
    if geo.heights is None:
        return Binding(np.flatnonzero(cells).astype(np.int64))
    floors = _floors_by_id(mf)
    ids = feature.get("floors")
    if not isinstance(ids, list) or not ids:
        return Binding(np.zeros(0, np.int64), [f"{feature.get('id')}: its floors are unresolved"])
    out, pending = [], []
    for fid in ids:
        b = floor_nodes(geo, floors.get(fid), cells)
        out.append(b.nodes)
        pending += [f"{feature.get('id')}: {p}" for p in b.pending]
    return Binding(np.unique(np.concatenate(out)) if out else np.zeros(0, np.int64), pending)


# ---------------------------------------------------------------- states

def state_of(feature: dict, states: dict | None) -> dict | None:
    """The feature's state record for a {feature id: state name} choice (default: its initial state)."""
    name = (states or {}).get(feature.get("id"), feature.get("initial_state"))
    for s in feature.get("states") or []:
        if s.get("name") == name:
            return s
    return None


@dataclass
class Effects:
    blocked: np.ndarray                      # geo.n bool: nodes no one may walk on
    pending: list = field(default_factory=list)


def movement_blocks(geo: Geometry, mf: dict, states: dict | None = None,
                    only: set | None = None) -> Effects:
    """The nodes the features' current states block for walking. `only` limits it to those feature ids (an
    enabled bundle's members); default: every feature."""
    blocked = np.zeros(geo.n, bool)
    pending = []
    for f in mf.get("features") or []:
        if only is not None and f.get("id") not in only:
            continue
        s = state_of(f, states)
        if s is None:
            pending.append(f"{f.get('id')}: no state {(states or {}).get(f.get('id'), f.get('initial_state'))!r}")
            continue
        if not s.get("blocks_movement") or not s.get("footprint"):
            continue
        cells = to_grid(raster(s["footprint"])).ravel()
        b = feature_floor_nodes(geo, mf, f, cells)
        blocked[b.nodes] = True
        pending += b.pending
    return Effects(blocked, pending)


def compose_masks(sight_px: np.ndarray, walk_px: np.ndarray, mf: dict, states: dict | None = None) -> tuple:
    """The tagger preview's 2D masks (PX x PX: sight True blocks, walk True walkable) with each feature's
    current state drawn in: a blocking footprint removes walking (and blocks sight when the state blocks
    sight), and each sight occluder's line is drawn as sight-blocking cells. Flat and all-floor: a picture
    for the page, which scripts/control_tagger_core.js `Features.composeFeatures` matches bit for bit; the
    engine never reads it (it uses `movement_blocks` and bounded occluders)."""
    sight, walk = sight_px.copy(), walk_px.copy()
    for f in mf.get("features") or []:
        s = state_of(f, states)
        if s is None:
            continue
        if s.get("footprint"):
            px = to_px(raster(s["footprint"]))
            if s.get("blocks_movement"):
                walk &= ~px
            if s.get("blocks_sight"):
                sight |= px
        if s.get("blocks_sight"):
            for occ in s.get("sight") or []:
                if (occ.get("bounds") or {}).get("ref") != "unresolved":
                    sight |= to_px(raster(occ.get("geometry")))
    return sight, walk


# ---------------------------------------------------------------- bounded sight

@dataclass(eq=False)
class BoundedOccluder:
    """A feature's sight blocker in one state: a pixel mask (its rasterised geometry) and the height band it
    fills, [bottom, top) in the engine's z frame (metres of position-z above the map's lowest floor), or every
    height (`all_height`). A sight line is blocked where it is inside the mask at a height inside the band."""
    owner: str
    mask: np.ndarray               # PX x PX bool
    bottom: float = -np.inf
    top: float = np.inf
    all_height: bool = False


def resolve_bounds(geo: Geometry, mf: dict, occ: dict, mask_px: np.ndarray, owner: str):
    """(BoundedOccluder, None) or (None, why it is pending). Ground-relative bounds stand on the bound floor's
    physical ground (its node z - STAND_M) under the occluder; world bounds convert through the height asset's
    origin. A flat map has no heights: resolved bounds there block in 2D, like every wall."""
    from app.control import heights as hc

    bounds = occ.get("bounds") or {}
    ref = bounds.get("ref")
    if ref == "all_height":
        return BoundedOccluder(owner, mask_px, all_height=True), None
    if ref not in ("ground", "world"):
        return None, f"{owner}: sight bounds unresolved"
    lo, hi = ms_known(bounds.get("bottom")), ms_known(bounds.get("top"))
    if lo is None or hi is None:
        return None, f"{owner}: a sight bound is unresolved"
    if geo.heights is None:
        return BoundedOccluder(owner, mask_px, all_height=True), None
    if ref == "world":
        origin = geo.heights.origin_z / 10.0
        return BoundedOccluder(owner, mask_px, lo - origin, hi - origin), None
    floor = _floors_by_id(mf).get(bounds.get("floor"))
    cells = mask_px.reshape(GRID, CELL, GRID, CELL).any((1, 3)).ravel()
    b = floor_nodes(geo, floor, cells)
    if b.pending or not len(b.nodes):
        return None, f"{owner}: " + ("; ".join(b.pending) or "no floor under the occluder")
    ground = float(np.median(geo.node_z[b.nodes])) - hc.STAND_M
    return BoundedOccluder(owner, mask_px, ground + lo, ground + hi), None


def ms_known(value) -> float | None:
    from app.replays.map_feature_state import known
    return known(value)


def sight_occluders(geo: Geometry, mf: dict, states: dict | None = None) -> tuple[list, list]:
    """Every occluder of every feature's current state that blocks sight: its `sight` entries, plus its
    footprint under the state's `sight_bounds` (a breakable block's whole body). Unresolved ones are pending
    and block nothing."""
    out, pending = [], []
    for f in mf.get("features") or []:
        s = state_of(f, states)
        if s is None or not s.get("blocks_sight"):
            continue
        entries = list(s.get("sight") or [])
        if s.get("footprint") is not None:
            entries.append({"geometry": s["footprint"], "bounds": s.get("sight_bounds") or {"ref": "unresolved"}})
        for occ in entries:
            mask = to_px(raster(occ.get("geometry")))
            o, why = resolve_bounds(geo, mf, occ, mask, f.get("id"))
            if o is None:
                pending.append(why)
            else:
                out.append(o)
    return out, pending


def blocked_lines(occluders: list, a: tuple, b: np.ndarray, record: dict | None = None) -> np.ndarray:
    """Per target, is the line from `a` = (x px, y px, z) to that target (b: N x 3, z NaN or None for a 2D
    check) inside an occluder's mask at a height inside its band? One vertical-intersection rule for `cast_with`,
    `los_with` and `seen_from_with`: the line is sampled every pixel (its two ends excluded); a straight-up line
    (same x, y) is blocked when the mask covers its pixel and its z span meets the band."""
    b = np.asarray(b, float).reshape(-1, 3)
    n = len(b)
    out = np.zeros(n, bool)
    if not occluders or not n:
        return out
    ax, ay, az = float(a[0]), float(a[1]), (np.nan if a[2] is None else float(a[2]))
    flat = np.isnan(az) | np.isnan(b[:, 2])
    if record is not None and flat.any():
        record["flat_occluder_tests"] = record.get("flat_occluder_tests", 0) + int(flat.sum())
    length = np.hypot(b[:, 0] - ax, b[:, 1] - ay)
    steps = int(np.ceil(length.max())) + 1 if n else 1
    s = np.linspace(0.0, 1.0, max(steps, 2))[1:-1]
    for lo in range(0, n, 512):
        bb, fl, ln = b[lo:lo + 512], flat[lo:lo + 512], length[lo:lo + 512]
        xs = ax + s[None, :] * (bb[:, 0:1] - ax)
        ys = ay + s[None, :] * (bb[:, 1:2] - ay)
        zs = az + s[None, :] * (bb[:, 2:3] - az)
        xi = np.clip(xs, 0, PX - 1).astype(np.int32)
        yi = np.clip(ys, 0, PX - 1).astype(np.int32)
        hit = np.zeros(len(bb), bool)
        for occ in occluders:
            inside = occ.mask[yi, xi]
            if occ.all_height:
                hit |= inside.any(1)
                continue
            band = (zs >= occ.bottom) & (zs < occ.top)
            hit |= (inside & (band | fl[:, None])).any(1)
            vertical = ln < 1e-9
            if vertical.any():
                px = occ.mask[int(min(max(ay, 0), PX - 1)), int(min(max(ax, 0), PX - 1))]
                z0 = np.where(np.isnan(bb[vertical, 2]), -np.inf, np.minimum(az, bb[vertical, 2]))
                z1 = np.where(np.isnan(bb[vertical, 2]), np.inf, np.maximum(az, bb[vertical, 2]))
                hit[vertical] |= px & (z1 >= occ.bottom) & (z0 < occ.top)
        out[lo:lo + 512] = hit
    return out


def _eye_body(geo: Geometry, node: int, offset: float):
    if geo.heights is None or np.isnan(geo.node_z[node]):
        return np.nan
    return float(geo.node_z[node]) + offset


def cast_with(geo: Geometry, x: float, y: float, angles_deg, smokes: list, occluders: list, eye_z: float | None = None,
              own: int | None = None, record: dict | None = None) -> np.ndarray:
    """`geometry.cast`, minus the nodes a bounded occluder hides: each seen node is tested on the line from the
    eye to its body (centre, node z + BODY_M), so two floors behind one XY crossing can differ."""
    from app.control import geometry as cg
    from app.control import heights as hc

    seen = cg.cast(geo, x, y, angles_deg, smokes, eye_z=eye_z, own=own, record=record)
    if not occluders:
        return seen
    nodes = np.flatnonzero(seen)
    z = np.array([_eye_body(geo, int(n), hc.BODY_M) for n in nodes]) if eye_z is not None else np.full(len(nodes), np.nan)
    targets = np.column_stack([geo.centres[nodes].astype(float), z])
    hit = blocked_lines(occluders, (x, y, eye_z), targets, record)
    if own is not None:
        hit &= nodes != own
    seen[nodes[hit]] = False
    return seen


def los_with(geo: Geometry, a: tuple, b: tuple, smokes: list = (), occluders: list = (), record: dict | None = None) -> bool:
    """`geometry.los` (eye at a, target point b; z None for 2D), and no bounded occluder on the line."""
    from app.control import geometry as cg

    if not cg.los(geo, a, b, smokes, record):
        return False
    target = np.array([[b[0], b[1], np.nan if b[2] is None else b[2]]], float)
    return not bool(blocked_lines(list(occluders), a, target, record)[0])


def seen_from_with(geo: Geometry, src: np.ndarray, smokes: list, occluders: list, skip: np.ndarray | None = None,
                   record: dict | None = None) -> np.ndarray:
    """engine.seen_from (the cached rows) with each source-target pair filtered by the bounded occluders: a
    pair counts when the line from the source's eye (node z + EYE_M) to the target's body (node z + BODY_M) is
    clear; a node with no height is tested in 2D. One clear source is enough."""
    from app.control import engine
    from app.control import heights as hc

    static = engine.seen_from(geo, src, smokes, skip)
    if not occluders:
        return static
    src = src[geo.row_of[src] >= 0]
    targets = np.flatnonzero(static & ~skip) if skip is not None else np.flatnonzero(static)
    if not len(targets):
        return static
    out = static.copy()
    out[targets] = False
    tz = np.array([_eye_body(geo, int(t), hc.BODY_M) for t in targets])
    tgt = np.column_stack([geo.centres[targets].astype(float), tz])
    rows = np.unpackbits(geo.rows[geo.row_of[src]], axis=1)[:, : geo.n][:, targets].astype(bool)
    for i, s in enumerate(src.tolist()):
        cand = rows[i] & ~out[targets]
        if not cand.any():
            continue
        x, y = geo.centres[s]
        for smoke in smokes:                  # the pair must be clear of smoke too, as in seen_from
            cand[cand] &= ~engine.smoke_blocks(geo.centres[[s]], geo.centres[targets[cand]], smoke)[0]
        if not cand.any():
            continue
        clear = ~blocked_lines(occluders, (float(x), float(y), _eye_body(geo, s, hc.EYE_M)), tgt[cand], record)
        idx = targets[cand]
        out[idx[clear]] = True
    return out


# ---------------------------------------------------------------- base reconciliation and bundles

# The runtime consumers this build can run features through. None: nothing is enabled until an engine
# integration registers one (R3), so every bundle is pending and every map's base geometry is unchanged.
RUNTIME_CONSUMERS: frozenset = frozenset()
LEGACY_SOURCES = ("cover_paint", "cant_walk_paint", "tag", "base")


def behaviour_problems(feature: dict) -> list[str]:
    """Why a feature's behaviour isn't runtime-ready: unresolved durations, policies, guards or delays."""
    out = []
    fid = feature.get("id")
    for row in feature.get("transitions") or []:
        rid = row.get("id")
        motion = row.get("motion")
        if motion is not None:
            if ms_known(motion.get("duration")) is None:
                out.append(f"{fid}.{rid}: motion duration unresolved")
            if row.get("mid_motion", "unresolved") == "unresolved":
                out.append(f"{fid}.{rid}: mid-motion policy unresolved")
        follow = row.get("follow_up")
        if follow is not None and ms_known(follow.get("after")) is None:
            out.append(f"{fid}.{rid}: follow-up delay unresolved")
        if "unresolved" in json.dumps(row.get("guard")):
            out.append(f"{fid}.{rid}: guard unresolved")
    if feature.get("initial_state") is None:
        out.append(f"{fid}: round-start state unresolved")
    return out


def _owned(feature: dict) -> dict:
    edits = feature.get("base_edits") or {}
    return {k: to_px(raster(edits.get(k))) for k in ("potential_ground", "remove_sight")}


def legacy_masks(entry: dict) -> dict:
    """The legacy hand paints that change the masks (tags.json entry), as pixel masks by source. Tag shapes
    need the minimap and its detector (geometry.tag_shapes); a caller that has them passes them as "tag"."""
    from app.control.geometry import unpack_paint

    return {k: unpack_paint(entry[k]) for k in ("cover_paint", "cant_walk_paint") if entry.get(k)}


@dataclass
class BundleStatus:
    id: str
    members: list
    publishable: bool
    reasons: list = field(default_factory=list)
    opens: dict = field(default_factory=dict)     # {"ground": px count, "sight": px count} it would open


def bundle_status(geo: Geometry | None, mf: dict, legacy: dict | None = None,
                  consumers: frozenset | None = None) -> dict:
    """Each bundle's publishability. A bundle publishes its members' base edits only together and only when
    it is enabled, names a registered runtime consumer, every member's behaviour is resolved, every floor
    binding it relies on is verified against the map's current heights, every overlap with legacy hand paint
    is reclassified exactly, and no member's edits overlap a feature outside the bundle. `consumers` defaults to
    the registered RUNTIME_CONSUMERS (read at call time)."""
    consumers = RUNTIME_CONSUMERS if consumers is None else consumers
    features = {f.get("id"): f for f in mf.get("features") or []}
    floors = _floors_by_id(mf)
    legacy = legacy or {}
    owned = {fid: _owned(f) for fid, f in features.items()}
    union = {fid: o["potential_ground"] | o["remove_sight"] for fid, o in owned.items()}
    bundle_of = {}
    for b in mf.get("bundles") or []:
        for m in b.get("members") or []:
            bundle_of[m] = b.get("id")
    out = {}
    for b in mf.get("bundles") or []:
        bid, members = b.get("id"), list(b.get("members") or [])
        reasons = []
        if not b.get("enabled"):
            reasons.append("not enabled")
        if b.get("runtime_consumer") not in consumers:
            reasons.append(f"no registered runtime consumer ({b.get('runtime_consumer')!r})")
        ground = np.zeros((PX, PX), bool)
        sight = np.zeros((PX, PX), bool)
        for m in members:
            f = features.get(m)
            if f is None:
                reasons.append(f"member {m!r} doesn't exist")
                continue
            reasons += behaviour_problems(f)
            ids = f.get("floors") if isinstance(f.get("floors"), list) else []
            edits = f.get("base_edits") or {}
            binding = edits.get("ground_binding")
            if owned[m]["potential_ground"].any():
                ids = ids + [binding] if isinstance(binding, str) else ids
                if not isinstance(binding, str):
                    reasons.append(f"{m}: restored ground has no floor binding")
            if geo is not None and geo.heights is not None:
                for fid in ids:
                    fl = floors.get(fid) or {}
                    if not isinstance(fl.get("z_band"), list) or fl.get("height_sha") != geo.height_sha:
                        reasons.append(f"{m}: floor {fid!r} is not verified against the map's heights")
                if isinstance(binding, str) and owned[m]["potential_ground"].any():
                    cells = owned[m]["potential_ground"].reshape(GRID, CELL, GRID, CELL).any((1, 3)).ravel()
                    cells &= ~geo.walk.ravel()
                    if cells.any():
                        reasons.append(f"{m}: restored ground has no floor in the height asset; rebuild the heights "
                                       "(it never takes the unresolved all-floor fallback)")
            for source, mask in legacy.items():
                overlap = union[m] & mask
                if not overlap.any():
                    continue
                covered = np.zeros((PX, PX), bool)
                for rc in edits.get("reclassify") or []:
                    if rc.get("source") == source:
                        covered |= to_px(raster(rc.get("geometry")))
                if (overlap & ~covered).any():
                    reasons.append(f"{m}: overlaps legacy {source} ({int((overlap & ~covered).sum())} px) without an "
                                   "exact reclassification")
            for other, mask in union.items():
                if other != m and bundle_of.get(other) != bid and (union[m] & mask).any():
                    reasons.append(f"{m}: its base edits overlap {other}, which is not in this bundle")
            ground |= owned[m]["potential_ground"]
            sight |= owned[m]["remove_sight"]
        out[bid] = BundleStatus(bid, members, not reasons, reasons,
                                {"ground": int(ground.sum()), "sight": int(sight.sum())})
    return out


def reconcile(sight_px: np.ndarray, walk_px: np.ndarray, mf: dict, statuses: dict) -> tuple:
    """The round's base domain: the permanent masks with every publishable bundle's base edits applied
    (potential ground made walkable, owned baked-in sight removed). Pending bundles change nothing. A feature's
    closed or intact state then blocks through `movement_blocks` and its occluders, so opening one feature
    removes only its own contribution. Returns (sight, walk, {bundle id: opened px})."""
    sight, walk = sight_px.copy(), walk_px.copy()
    features = {f.get("id"): f for f in mf.get("features") or []}
    opened = {}
    for bid, st in statuses.items():
        if not st.publishable:
            continue
        g = np.zeros((PX, PX), bool)
        s = np.zeros((PX, PX), bool)
        for m in st.members:
            o = _owned(features[m])
            g |= o["potential_ground"]
            s |= o["remove_sight"]
        opened[bid] = {"ground": int((g & ~walk).sum()), "sight": int((s & sight).sum())}
        walk |= g
        sight &= ~s
    return sight, walk, opened


# ---------------------------------------------------------------- directed traversal

@dataclass(frozen=True)
class Arc:
    """One direction of a route between two bound nodes. Costs are seconds (entry, transit) and metres
    (length); None when unresolved. `states`: the owner's states in which it runs (None: always)."""
    route: str
    src: int
    dst: int
    entry_s: float | None
    transit_s: float | None
    length_m: float | None
    owner: str | None = None
    states: tuple | None = None
    in_transit: str = "unresolved"

    @property
    def cost_s(self) -> float | None:
        return None if self.entry_s is None or self.transit_s is None else self.entry_s + self.transit_s


def _point_node(geo: Geometry, mf: dict, uv, floor_id) -> tuple[int | None, str | None]:
    if uv is None:
        return None, "not placed"
    cell = geo.cell_of_px(uv[0] * PX / ms.UV_MAX, uv[1] * PX / ms.UV_MAX)
    if geo.heights is None:
        return cell, None
    if not isinstance(floor_id, str):
        return None, "floor unresolved"
    mask = np.zeros(GRID * GRID, bool)
    mask[cell] = True
    b = floor_nodes(geo, _floors_by_id(mf).get(floor_id), mask)
    if b.pending or len(b.nodes) != 1:
        return None, "; ".join(b.pending) or "no floor there"
    return int(b.nodes[0]), None


def compile_routes(geo: Geometry, mf: dict) -> tuple[list, list]:
    """Every authored direction of every route as an Arc between explicitly bound nodes (endpoints and access
    sites). A route's polyline is drawing only: it opens no ground and no sight, and gives no access along the
    way unless a site is authored. Never an all-floor cross product: an endpoint whose floor can't be bound
    makes its arcs pending. Two ends on one node (same place, same floor) are pending too."""
    arcs, pending = [], []
    for r in mf.get("routes") or []:
        rid = r.get("id")
        points = {e.get("id"): e for e in r.get("endpoints") or []}
        access = r.get("access")
        if isinstance(access, dict):
            points.update({s.get("id"): s for s in access.get("sites") or []})
        nodes = {}
        for pid, p in points.items():
            node, why = _point_node(geo, mf, p.get("uv"), p.get("floor"))
            if why:
                pending.append(f"{rid}.{pid}: {why}")
            nodes[pid] = node
        for d in r.get("directions") or []:
            a, b = nodes.get(d.get("from")), nodes.get(d.get("to"))
            if a is None or b is None:
                continue
            if a == b:
                pending.append(f"{rid}: {d.get('from')} -> {d.get('to')} joins a node to itself (same place, same floor)")
                continue
            length = d.get("length")
            arc = Arc(rid, a, b, ms_known(d.get("entry")), ms_known(d.get("transit")),
                      ms_known(length) if length is not None else None, r.get("owner"),
                      tuple(r["states"]) if isinstance(r.get("states"), list) else None, r.get("in_transit", "unresolved"))
            if arc.cost_s is None:
                pending.append(f"{rid}: {d.get('from')} -> {d.get('to')} travel time unresolved")
            arcs.append(arc)
    return arcs, pending


# Metric policies (the contract doc's consumer table names one per consumer).
WALK_ONLY = "walk_only"                          # 8-connected walking; transport never counts (ownership, metres)
TRANSPORT_REACHABILITY = "transport_reachability"  # can it be reached at all: any permitted arc counts
TRANSPORT_TIME = "transport_time"                # seconds: walking at a speed + resolved arc costs only


def walk_edges(geo: Geometry) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(src, dst, metres) of every walk between neighbouring nodes, 8-connected, both ways where two-way."""
    from app.control import topology

    topo = topology.of(geo)
    if isinstance(topo, topology.NodeTopology):
        return topo.src, topo.dst, np.where(topo.diag, np.sqrt(2.0), 1.0) * geo.cell_m
    walk = geo.walk.ravel()
    src, dst, cost = [], [], []
    ys, xs = np.divmod(np.arange(GRID * GRID), GRID)
    for dy, dx in topology.SPREAD_ORDER:
        ny, nx = ys + dy, xs + dx
        ok = walk & (ny >= 0) & (ny < GRID) & (nx >= 0) & (nx < GRID)
        nb = np.where(ok, ny * GRID + nx, 0)
        ok &= walk[nb]
        src.append(np.flatnonzero(ok))
        dst.append(nb[ok])
        cost.append(np.full(int(ok.sum()), geo.cell_m * (np.sqrt(2.0) if dy and dx else 1.0)))
    return np.concatenate(src), np.concatenate(dst), np.concatenate(cost)


class TraversalGraph:
    """One compiled graph for every traversal consumer: walking edges plus route arcs, read through a named
    metric policy. `blocked` (geo.n bool) removes nodes a feature's state closes; `states` picks which
    state-conditional arcs run; `reversed` answers "from where can one reach X" (a way back, a return path)."""

    def __init__(self, geo: Geometry, arcs: list, *, blocked: np.ndarray | None = None, states: dict | None = None,
                 reversed: bool = False):
        self.geo, self.n = geo, geo.n
        self.arcs = [a for a in arcs if a.states is None or (states or {}).get(a.owner) in a.states]
        self.blocked = np.zeros(geo.n, bool) if blocked is None else blocked
        self.is_reversed = reversed
        src, dst, m = walk_edges(geo)
        keep = ~self.blocked[src] & ~self.blocked[dst]
        self._walk = (src[keep], dst[keep], m[keep])
        self.unresolved = [a for a in self.arcs if a.cost_s is None]

    def reverse(self) -> "TraversalGraph":
        g = object.__new__(TraversalGraph)
        g.__dict__.update(self.__dict__)
        g.is_reversed = not self.is_reversed
        return g

    def _edges(self, policy: str, speed: float = 1.0):
        src, dst, m = self._walk
        w = m if policy != TRANSPORT_TIME else m / speed
        if policy != WALK_ONLY:
            arcs = [a for a in self.arcs if not self.blocked[a.src] and not self.blocked[a.dst]]
            if policy == TRANSPORT_TIME:
                arcs = [a for a in arcs if a.cost_s is not None]
            src = np.concatenate([src, [a.src for a in arcs]]).astype(np.int64)
            dst = np.concatenate([dst, [a.dst for a in arcs]]).astype(np.int64)
            w = np.concatenate([w, [a.cost_s if a.cost_s is not None else 0.0 for a in arcs]])
        if self.is_reversed:
            src, dst = dst, src
        return src, dst, w

    def _matrix(self, policy: str, speed: float = 1.0):
        from scipy.sparse import csr_matrix

        src, dst, w = self._edges(policy, speed)
        # parallel edges (an arc beside a walk) keep the cheapest, as scipy would sum duplicates
        order = np.lexsort((w, dst, src))
        src, dst, w = src[order], dst[order], w[order]
        first = np.ones(len(src), bool)
        first[1:] = (src[1:] != src[:-1]) | (dst[1:] != dst[:-1])
        # csgraph treats an explicit 0 as "no edge": a free hop gets the smallest positive weight
        return csr_matrix((np.maximum(w[first], 1e-12), (src[first], dst[first])), shape=(self.n, self.n))

    def reachable(self, start: int, policy: str = TRANSPORT_REACHABILITY) -> np.ndarray:
        from scipy.sparse.csgraph import breadth_first_order

        order = breadth_first_order(self._matrix(policy), start, directed=True, return_predecessors=False)
        out = np.zeros(self.n, bool)
        out[order] = True
        return out

    def walk_metres(self, start: int) -> np.ndarray:
        """Walking distance in metres (no transport); inf where unreachable."""
        from scipy.sparse.csgraph import dijkstra

        return dijkstra(self._matrix(WALK_ONLY), directed=True, indices=start)

    def time_seconds(self, start: int, speed_mps: float) -> np.ndarray:
        """Seconds to each node: walking at `speed_mps`, plus resolved arcs' entry + transit. An arc with an
        unknown cost is left out (never a free step), and is listed in `self.unresolved`."""
        from scipy.sparse.csgraph import dijkstra

        return dijkstra(self._matrix(TRANSPORT_TIME, speed_mps), directed=True, indices=start)

    def arrival_times(self, start: int, depart: float, speed_mps: float, availability: dict) -> np.ndarray:
        """Earliest arrival times from `start` leaving at `depart`, respecting each route's availability
        ({route id: [(t0, t1), ...]}; a route not listed is always available). A traveller may wait at an arc's
        start for it to open. A route closing while someone is on it follows its in-transit policy: complete
        (they arrive), abort (the whole ride must fit in one open interval); unresolved routes are left out of
        temporal queries. Forward graphs only."""
        import heapq

        if self.is_reversed:
            raise ValueError("arrival times run forward")
        src, dst, m = self._walk
        walk_out = [[] for _ in range(self.n)]
        for a, b, w in zip(src.tolist(), dst.tolist(), (m / speed_mps).tolist()):
            walk_out[a].append((b, w))
        arc_out = [[] for _ in range(self.n)]
        for a in self.arcs:
            if a.cost_s is not None and a.in_transit in ("complete", "abort") \
                    and not self.blocked[a.src] and not self.blocked[a.dst]:
                arc_out[a.src].append(a)
        best = np.full(self.n, np.inf)
        best[start] = depart
        heap = [(depart, start)]
        while heap:
            t, u = heapq.heappop(heap)
            if t > best[u]:
                continue
            nxt = [(v, t + w) for v, w in walk_out[u]]
            for a in arc_out[u]:
                leave = _next_departure(t, a, availability.get(a.route))
                if leave is not None:
                    nxt.append((a.dst, leave + a.cost_s))
            for v, at in nxt:
                if at < best[v]:
                    best[v] = at
                    heapq.heappush(heap, (at, v))
        return best


def _next_departure(t: float, arc: Arc, intervals) -> float | None:
    if intervals is None:
        return t
    for t0, t1 in sorted(intervals):
        leave = max(t, t0)
        if leave > t1:
            continue
        if arc.in_transit == "abort" and leave + arc.cost_s > t1:
            continue
        return leave
    return None


# ---------------------------------------------------------------- the consumed-input manifest

MANIFEST_VERSION = 1


def _enabled_projection(mf: dict, members: set) -> dict:
    """The runtime projection of what enabled bundles use: their member features, the triggers aimed at them,
    the routes they own, the floors any of those bind and the enabled bundles themselves."""
    proj = ms.runtime_projection(mf)
    feats = [f for f in proj.get("features") or [] if f.get("id") in members]
    trigs = [t for t in proj.get("triggers") or [] if any(x.get("feature") in members for x in t.get("targets") or [])]
    routes = [r for r in proj.get("routes") or [] if r.get("owner") in members]
    bundles = [b for b in proj.get("bundles") or [] if set(b.get("members") or []) & members]
    used = {dst for src, _, dst in ms.references({"features": feats, "triggers": trigs, "routes": routes})}
    floors = [f for f in proj.get("floors") or [] if f.get("id") in used]
    return {"features": feats, "triggers": trigs, "routes": routes, "bundles": bundles, "floors": floors}


def compile_assets(geo: Geometry, mf: dict, statuses: dict) -> dict | None:
    """What an engine would load for a map's enabled features (None when it has none): per enabled feature and
    state, its blocked nodes and occluders, plus the enabled routes' arcs and the reconciled base edits."""
    members = {m for st in statuses.values() if st.publishable for m in st.members}
    if not members:
        return None
    sub = {**mf, "features": [f for f in mf.get("features") or [] if f.get("id") in members],
           "routes": [r for r in mf.get("routes") or [] if r.get("owner") in members]}
    states = {}
    for f in sub["features"]:
        for s in f.get("states") or []:
            pick = {f.get("id"): s.get("name")}
            one = {**sub, "features": [f]}
            blocked = movement_blocks(geo, one, pick).blocked
            occ, _ = sight_occluders(geo, one, pick)
            states[f"{f.get('id')}:{s.get('name')}"] = {
                "blocked": np.flatnonzero(blocked).tolist(),
                "occluders": [{"mask": _hash_array(o.mask), "bottom": None if o.all_height else o.bottom,
                               "top": None if o.all_height else o.top, "all_height": o.all_height} for o in occ]}
    arcs, _ = compile_routes(geo, sub)
    return {"states": states, "nodes": int(geo.n),
            "arcs": [[a.route, a.src, a.dst, a.entry_s, a.transit_s, a.length_m, a.owner,
                      list(a.states) if a.states is not None else None, a.in_transit] for a in arcs]}


def asset_hashes(assets: dict) -> dict:
    """The hash of each loaded asset part, from its canonical bytes (what verification recomputes)."""
    return {key: _canon(assets[key]) for key in sorted(assets)}


def manifest(geo: Geometry, mf: dict | None, legacy: dict | None = None,
             consumers: frozenset | None = None) -> dict | None:
    """The consumed-input manifest of a map's features, or None when no bundle is publishable: then the map
    has no `features` input at all, and its control inputs and fingerprints are exactly what they were.
    Editorial fields never reach it; enabling a bundle, a runtime edit, the height asset, the schema, the
    compiler, the reducer's semantics or the consumer set do."""
    from app.replays import map_feature_state as fs

    if not mf:
        return None
    statuses = bundle_status(geo, mf, legacy, consumers)
    assets = compile_assets(geo, mf, statuses)
    if assets is None:
        return None
    members = {m for st in statuses.values() if st.publishable for m in st.members}
    enabled = sorted(b for b, st in statuses.items() if st.publishable)
    return {"v": MANIFEST_VERSION, "schema": ms.SCHEMA_VERSION, "compiler": COMPILER_VERSION,
            "semantics": {"guards": fs.GUARD_VOCABULARY, "priority": fs.PRIORITY, "mid_motion": list(fs.MID_MOTION)},
            "consumers": sorted({(mf_bundle(mf, b) or {}).get("runtime_consumer") for b in enabled}),
            "bundles": enabled,
            "runtime": _canon(_enabled_projection(mf, members)), "height": geo.height_sha,
            "compiled": asset_hashes(assets)}


def mf_bundle(mf: dict, bid: str) -> dict | None:
    for b in mf.get("bundles") or []:
        if b.get("id") == bid:
            return b
    return None


def manifest_digest(m: dict | None) -> str | None:
    return None if m is None else _canon(m)


def verify(expected: dict | None, assets: dict | None, geo: Geometry | None = None, mf: dict | None = None,
           legacy: dict | None = None, consumers: frozenset | None = None) -> list[str]:
    """Mismatches between an expected manifest and what was actually loaded (empty: they agree). The loaded
    assets' bytes are hashed again, never trusted from a claim; with the geometry and definitions, the
    definitions are compiled again and must give the same manifest (definition-to-compiled correspondence)."""
    problems = []
    if (expected is None) != (assets is None):
        return [f"expected {'no' if expected is None else 'a'} features input, loaded {'none' if assets is None else 'some'}"]
    if expected is None:
        return []
    got = asset_hashes(assets)
    for key in sorted(set(got) | set(expected.get("compiled") or {})):
        if got.get(key) != (expected.get("compiled") or {}).get(key):
            problems.append(f"compiled {key}: loaded {got.get(key)}, expected {(expected.get('compiled') or {}).get(key)}")
    if geo is not None and mf is not None:
        fresh = manifest(geo, mf, legacy, consumers)
        if fresh != expected:
            diff = sorted(k for k in set(fresh or {}) | set(expected) if (fresh or {}).get(k) != expected.get(k))
            problems.append(f"definitions no longer compile to the expected manifest ({', '.join(diff)})")
    return problems


# ---------------------------------------------------------------- generations (M5)

def active_sha(name: str, asset_dir=None) -> str | None:
    """The map's active feature generation (index.json `features_sha`), or None: no enabled features."""
    from app.control.geometry import ASSET_DIR

    path = (asset_dir or ASSET_DIR) / "index.json"
    try:
        entry = json.loads(path.read_text(encoding="utf-8")).get("maps", {}).get(name) or {}
    except (OSError, ValueError):
        return None
    return entry.get("features_sha") or None


GENERATIONS = "features"           # <asset dir>/features/<manifest digest>.json


def load_generation(asset_dir, sha: str) -> dict:
    """A published generation {map, manifest, assets}. Content-addressed: a file whose manifest doesn't hash to
    its name is refused (GeometryError: the machine's problem, retried, never a bad round)."""
    from app.control.geometry import GeometryError

    path = asset_dir / GENERATIONS / f"{sha}.json"
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GeometryError(f"feature generation {sha} unreadable: {error}") from error
    if manifest_digest(body.get("manifest")) != sha:
        raise GeometryError(f"feature generation {sha}: its manifest hashes to {manifest_digest(body.get('manifest'))}")
    return body


def publish_generation(asset_dir, name: str, manifest_: dict, assets: dict) -> str:
    """Writes a complete, verified generation, then moves the map's pointer (index.json `features_sha`) to it in
    one atomic replace. A failure at any step leaves the previous pointer, and so the previous generation, in
    use; in-flight tasks keep the immutable file they loaded. Returns the generation's digest. Never run on the
    committed asset folder in this build (R3): no map has an enabled feature."""
    import os

    from app.control.geometry import GeometryError

    sha = manifest_digest(manifest_)
    folder = asset_dir / GENERATIONS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"map": name, "manifest": manifest_, "assets": assets}, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    body = load_generation(asset_dir, sha)
    problems = verify(manifest_, body["assets"])
    if problems:
        raise GeometryError(f"feature generation {sha} did not read back intact: {problems}")
    index_path = asset_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index.setdefault("maps", {}).setdefault(name, {})["features_sha"] = sha
    tmp_index = index_path.with_suffix(".tmp")
    tmp_index.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp_index, index_path)
    return sha


# ---------------------------------------------------------------- rotation

def rotation_pose(feature: dict, fraction: float) -> dict | None:
    """The panel's geometry `fraction` (0..1) of the way through a rotation: an authored phase at or before it
    when there are any ({"at": fraction, "panel": geometry}); else the rest panel (drawn at the start
    orientation) turned about the pivot by (end - start) * fraction degrees, clockwise on the minimap for "cw".
    None while the pivot, panel, angles or direction are unresolved: the motion isn't described, so nothing is
    guessed (not 90 degrees, not any duration). scripts/control_tagger_core.js `rotationPose` is its twin."""
    import math

    rot = feature.get("rotation") or {}
    phases = sorted((p for p in rot.get("phases") or [] if isinstance(p, dict) and p.get("panel")), key=lambda p: p["at"])
    if phases:
        chosen = [p for p in phases if p["at"] <= fraction]
        return (chosen[-1] if chosen else phases[0])["panel"]
    pivot, panel, direction = rot.get("pivot"), rot.get("panel"), rot.get("direction") or {}
    start, end = ms_known(rot.get("start_deg")), ms_known(rot.get("end_deg"))
    if not pivot or not panel or start is None or end is None or direction.get("status") != "known" \
            or direction.get("value") not in ("cw", "ccw"):
        return None
    sign = 1.0 if direction["value"] == "cw" else -1.0
    theta = math.radians(sign * (end - start) * float(fraction))
    c, s = math.cos(theta), math.sin(theta)
    cx, cy = pivot["uv"]
    pts = [[cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c] for x, y in panel["uv"]]
    return {**panel, "uv": pts}


# ---------------------------------------------------------------- diagnostics needing geometry

def diagnose(geo: Geometry, mf: dict) -> list[dict]:
    """The warnings validation can't give without masks and heights: route endpoints off walkable ground,
    stale floor bindings, and a blocking footprint that leaks a passage diagonally (it splits the ground
    4-connected but not 8-connected, and the engine walks 8-connected)."""
    out = []
    walk = geo.walk.ravel()
    for fl in mf.get("floors") or []:
        if fl.get("z_band") is not None and geo.heights is not None and fl.get("height_sha") != geo.height_sha:
            out.append({"where": fl.get("id"), "code": "stale_floor",
                        "message": f"read from height asset {fl.get('height_sha')!r}; the map has {geo.height_sha!r}"})
    for r in mf.get("routes") or []:
        for e in r.get("endpoints") or []:
            if e.get("uv") is None:
                continue
            x, y = e["uv"][0] * PX / ms.UV_MAX, e["uv"][1] * PX / ms.UV_MAX
            if not walk[geo.cell_of_px(x, y)]:
                out.append({"where": f"{r.get('id')}.{e.get('id')}", "code": "off_ground",
                            "message": "endpoint is not on walkable ground (not snapped: restore ground or move it)"})
    grid_walk = geo.walk
    for f in mf.get("features") or []:
        for s in f.get("states") or []:
            if not s.get("blocks_movement") or not s.get("footprint"):
                continue
            foot = to_grid(raster(s["footprint"]))
            ring = ndimage.binary_dilation(foot, np.ones((3, 3), bool)) & ~foot & grid_walk
            open_ = grid_walk & ~foot
            four, _ = ndimage.label(open_)
            eight, _ = ndimage.label(open_, np.ones((3, 3), bool))
            if len(np.unique(four[ring])) > len(np.unique(eight[ring])):
                out.append({"where": f"{f.get('id')}.states.{s.get('name')}", "code": "diagonal_leak",
                            "message": "the footprint closes the passage only 4-connected; walking (8-connected) "
                                       "slips past a corner: widen it by a cell"})
    return out


def _hash_array(a: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(np.asarray(a, bool)).tobytes()).hexdigest()[:12]


def _canon(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
