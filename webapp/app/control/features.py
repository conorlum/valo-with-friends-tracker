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
        col, row = min(int(u // CELL_UV), P - 1), min(int(v // CELL_UV), P - 1)
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
                  consumers: frozenset = RUNTIME_CONSUMERS) -> dict:
    """Each bundle's publishability. A bundle publishes its members' base edits only together and only when
    it is enabled, names a registered runtime consumer, every member's behaviour is resolved, every floor
    binding it relies on is verified against the map's current heights, every overlap with legacy hand paint
    is reclassified exactly, and no member's edits overlap a feature outside the bundle."""
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
