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
- **Floors** (`floor_nodes`): per cell a floor binding picks the node whose `node_z` lies in its band. A binding is
  {`z_band`: [lo, hi] metres above `origin_z`, the lowest floor (world dm) of the asset it was read from;
  `height_sha`: that asset}. The band is rebased to the map's current lowest floor before it is used, so a feature
  follows its floor when the heights are rebuilt (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md,
  section 5). On a map with a height asset an unbanded or ambiguous binding, or one whose frame is unknown (no
  `origin_z`, and another asset than the map's), is pending and blocks nothing; it never falls back to every
  floor. A flat map has one node per cell, so a binding is moot there.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

import numpy as np
from scipy import ndimage

from app.control.geometry import CELL, GRID, PAINT_GRID, PX, Geometry
from app.replays import map_feature_schema as ms
from app.replays import map_feature_inputs as fi
from app.replays import map_feature_artifacts as fa

COMPILER_VERSION = fi.FEATURE_COMPILER_VERSION
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

@dataclass(frozen=True)
class Placement:
    nodes: tuple[int, ...] = ()
    ground_m: tuple[float, ...] = ()
    reasons: tuple[dict, ...] = ()

    @property
    def ok(self):
        return bool(self.nodes) and not self.reasons


def _reason(code, path, cells=(), counts=()):
    return {'code': code, 'path': path, 'cells': list(cells), 'cell_count': len(cells),
            'floor_counts': list(counts)}


def resolve_cells(geo: Geometry, cells: list[int], path: str) -> Placement:
    """All cells must have permanent ground and exactly one real floor; never synthetic node zero."""
    from app.control.heights import MAX_FLOORS, STAND_M
    cells = sorted(set(cells))
    if not cells:
        return Placement(reasons=(_reason('empty_geometry', path),))
    grouped, nodes, ground = {}, [], []
    asset = geo.heights
    if asset is not None and (asset.floors.shape != (GRID, GRID, MAX_FLOORS)
                              or asset.unresolved.shape != (GRID, GRID)):
        return Placement(reasons=(_reason('invalid_height_asset', path, cells),))
    for cell in cells:
        count, code = 0, None
        if type(cell) is not int or not 0 <= cell < GRID * GRID:
            code = 'off_map'
        else:
            if asset is not None:
                floors = asset.floors.reshape(GRID * GRID, MAX_FLOORS)[cell]
                real = np.isfinite(floors) & (floors >= 0)
                count = int(real.sum())
            else:
                count = int(geo.walk.ravel()[cell])
            if not geo.walk.ravel()[cell]:
                code = 'off_ground'
            elif asset is None:
                nodes.append(cell)
            elif asset.unresolved.ravel()[cell] or geo.unresolved[cell]:
                code = 'unresolved_height'
            elif count == 0:
                code = 'missing_floor'
            elif count != 1:
                code = 'multi_floor'
            else:
                node = int(geo.node_of[cell, int(np.flatnonzero(real)[0])])
                if node < 0 or not np.isfinite(geo.node_z[node]):
                    code = 'missing_floor'
                else:
                    nodes.append(node)
                    ground.append(float(geo.node_z[node]) - STAND_M)
        if code:
            bucket = grouped.setdefault(code, ([], []))
            bucket[0].append(cell)
            bucket[1].append(count)
    reasons = tuple(_reason(code, path, *grouped[code]) for code in sorted(grouped))
    return Placement((), (), reasons) if reasons else Placement(tuple(nodes), tuple(ground))


def _place_shape(geo, shape, path):
    rep = ms.Report()
    ms._geometry(rep, path, shape)
    if rep.errors:
        code = 'off_map' if any(e['code'] == 'bad_coordinates' for e in rep.errors) else 'invalid_geometry'
        return Placement(reasons=(_reason(code, path),))
    return resolve_cells(geo, np.flatnonzero(to_grid(raster(shape))).tolist(), path)


def placement(geo: Geometry, source: dict) -> dict[str, Placement]:
    """Resolve all states/phases and their required trigger, route and base-edit dependencies."""
    from app.control.heights import STAND_M
    mf = source.get('map_features', source)
    results = {}
    for f in mf.get('features') or []:
        fid, pieces = f.get('id'), []
        def shape(value, path):
            pieces.append(_place_shape(geo, value, path))
        def state_parts(s, path):
            if s.get('footprint') is not None:
                shape(s['footprint'], path + '.footprint')
            elif s.get('blocks_movement'):
                pieces.append(Placement(reasons=(_reason('empty_geometry', path + '.footprint'),)))
            for i, occ in enumerate(s.get('sight') or []):
                shape(occ.get('geometry'), f'{path}.sight[{i}].geometry')
        for s in f.get('states') or []:
            state_parts(s, f'features.{fid}.states.{s.get("name")}')
        rotation = f.get('rotation') or {}
        for i, phase in enumerate(rotation.get('phases') or []):
            state_parts(phase, f'features.{fid}.rotation.phases[{i}]')
            if phase.get('panel') is not None:
                shape(phase['panel'], f'features.{fid}.rotation.phases[{i}].panel')
            if phase.get('geometry') is not None:
                shape(phase['geometry'], f'features.{fid}.rotation.phases[{i}].geometry')
        for key in ('potential_ground', 'remove_sight'):
            if (f.get('base_edits') or {}).get(key) is not None:
                shape(f['base_edits'][key], f'features.{fid}.base_edits.{key}')
        for t in mf.get('triggers') or []:
            if any(target.get('feature') == fid for target in t.get('targets') or []):
                shape(t.get('geometry'), f'triggers.{t.get("id")}.geometry')
        for r in mf.get('routes') or []:
            if r.get('owner') != fid:
                continue
            ends = r.get('endpoints') or []
            if len(ends) != 2:
                pieces.append(Placement(reasons=(_reason('missing_endpoint', f'routes.{r.get("id")}'),)))
            sites = (r.get('access') or {}).get('sites', []) if isinstance(r.get('access'), dict) else []
            for p in ends + sites:
                shape({'type': 'point', 'uv': p.get('uv')}, f'routes.{r.get("id")}.{p.get("id")}')
        reasons = tuple(r for p in pieces for r in p.reasons)
        nodes = sorted({n for p in pieces for n in p.nodes})
        # A behaviour-only feature is valid without a geometry claim.
        results[fid] = Placement((), (), reasons) if reasons else Placement(
            tuple(nodes), tuple(float(geo.node_z[n]) - STAND_M for n in nodes) if geo.heights is not None else ())
    return results


def _candidate_geometry(geo, mf, members):
    if geo.heights is not None:
        return geo
    walk = geo.walk_px.copy()
    sight = geo.sight.copy()
    for f in mf.get('features') or []:
        if f.get('id') in members:
            own = _owned(f)
            walk |= own['potential_ground']
            sight &= ~own['remove_sight']
    return replace(geo, walk_px=walk, sight=sight,
                   walk=walk.reshape(GRID, CELL, GRID, CELL).mean((1, 3)) > 0.5,
                   walk_n=(walk.reshape(GRID, CELL, GRID, CELL).mean((1, 3)) > 0.5).ravel())

@dataclass
class Binding:
    nodes: np.ndarray                       # node indices
    pending: list = field(default_factory=list)   # why some or all cells aren't bound


def _floors_by_id(mf: dict) -> dict:
    return {f.get("id"): f for f in mf.get("floors") or [] if isinstance(f, dict)}


def _band_shift(geo: Geometry, binding: dict) -> float | None:
    """Metres to add to a binding's `z_band` to read it in `geo`'s frame. A band is in metres above the lowest
    floor of the asset it was read from (`origin_z`, world dm); `geo.node_z` is above the lowest floor of the
    asset the map has now, and a rebuild that finds lower ground moves that. None when the binding doesn't say
    where its frame was and the map's asset is no longer the one it was read from: its numbers can't be read."""
    origin = binding.get("origin_z")
    if isinstance(origin, (int, float)) and not isinstance(origin, bool):
        return (origin - geo.heights.origin_z) / 10.0
    return 0.0 if binding.get("height_sha") == geo.height_sha else None


def floor_nodes(geo: Geometry, binding: dict | None, cells: np.ndarray) -> Binding:
    """The nodes of `cells` (flat GRID*GRID bool) on the bound floor. A flat map: the cells themselves."""
    placed = resolve_cells(geo, np.flatnonzero(cells).tolist(), 'floor')
    return Binding(np.array(placed.nodes, np.int64), [r['code'] for r in placed.reasons])


def feature_floor_nodes(geo: Geometry, mf: dict, feature: dict, cells: np.ndarray) -> Binding:
    """The nodes a feature's footprint covers on the floors it names (the union over them)."""
    p = resolve_cells(geo, np.flatnonzero(cells).tolist(), f'features.{feature.get("id")}.footprint')
    return Binding(np.array(p.nodes, np.int64), [f"{r['path']}: {r['code']}" for r in p.reasons])


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
    placements = placement(geo, mf)
    for f in mf.get("features") or []:
        if only is not None and f.get("id") not in only:
            continue
        if placements[f.get('id')].reasons:
            pending += [f"{r['path']}: {r['code']}" for r in placements[f.get('id')].reasons]
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
    cells = mask_px.reshape(GRID, CELL, GRID, CELL).any((1, 3)).ravel()
    b = floor_nodes(geo, None, cells)
    if b.pending or not len(b.nodes):
        return None, f"{owner}: " + ('; '.join(b.pending) or 'no floor under the occluder')
    ref = bounds.get("ref")
    if ref == "all_height":
        return BoundedOccluder(owner, mask_px, all_height=True), None
    if ref not in ("ground", "world"):
        return None, f"{owner}: sight bounds unresolved"
    lo, hi = ms_known(bounds.get("bottom")), ms_known(bounds.get("top"))
    if lo is None or hi is None:
        return None, f"{owner}: a sight bound is unresolved"
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None, f"{owner}: sight bounds require a finite positive height interval"
    if geo.heights is None:
        return BoundedOccluder(owner, mask_px, all_height=True), None
    if ref == "world":
        origin = geo.heights.origin_z / 10.0
        return BoundedOccluder(owner, mask_px, lo - origin, hi - origin), None
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
    placements = placement(geo, mf)
    for f in mf.get("features") or []:
        if placements[f.get('id')].reasons:
            pending += [f"{r['path']}: {r['code']}" for r in placements[f.get('id')].reasons]
            continue
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
        vertical = ln < 1e-9
        if vertical.any():
            # a straight-up line's z span; with no height at either end, the 2D answer (every height)
            vfl = fl[vertical]
            with np.errstate(invalid="ignore"):
                z0 = np.where(vfl, -np.inf, np.minimum(az, bb[vertical, 2]))
                z1 = np.where(vfl, np.inf, np.maximum(az, bb[vertical, 2]))
            apx = (int(min(max(ay, 0), PX - 1)), int(min(max(ax, 0), PX - 1)))
        for occ in occluders:
            inside = occ.mask[yi, xi]
            if occ.all_height:
                hit |= inside.any(1)
            else:
                band = (zs >= occ.bottom) & (zs < occ.top)
                hit |= (inside & (band | fl[:, None])).any(1)
            if vertical.any() and occ.mask[apx]:
                # the span [z0, z1] meets the band [bottom, top); an all-height band is every height
                hit[vertical] |= True if occ.all_height else (z1 >= occ.bottom) & (z0 < occ.top)
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
RUNTIME_CONSUMERS: frozenset = fi.RUNTIME_CONSUMERS
LEGACY_SOURCES = ("cover_paint", "cant_walk_paint", "tag", "base")


def behaviour_problems(feature: dict) -> list[str]:
    """Why a feature's behaviour isn't runtime-ready: unresolved durations, policies, guards or delays."""
    out = []
    fid = feature.get("id")
    if "sliding" in feature:
        # The authoring preview is available; no engine consumer samples moving masks yet. Never silently
        # compile a sliding door as one static "closing" footprint, even for an explicitly enabled bundle.
        from app.replays.map_feature_motion import geometry_problems
        out += [f"{fid}: {p}" for p in geometry_problems(feature)]
        out.append(f"{fid}: sliding geometry requires a runtime motion consumer (preview only)")
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


def bounds_problems(geo: Geometry, mf: dict, feature: dict) -> list[dict]:
    """Check every authored blocking volume, including all rotation phases."""
    out = []
    fid = feature['id']
    def check(shape, bounds, path):
        _, why = resolve_bounds(geo, mf, {'bounds': bounds}, to_px(raster(shape)), fid)
        if why:
            out.append({'code': 'invalid_bounds', 'path': path, 'cells': [], 'cell_count': 0,
                        'floor_counts': [], 'reason': why})
    for state in feature.get('states') or []:
        path = f'features.{fid}.states.{state.get("name")}'
        if state.get('blocks_sight') and state.get('footprint'):
            check(state['footprint'], state.get('sight_bounds'), path + '.sight_bounds')
        for i, occ in enumerate(state.get('sight') or []):
            check(occ.get('geometry'), occ.get('bounds'), f'{path}.sight[{i}].bounds')
    for i, phase in enumerate((feature.get('rotation') or {}).get('phases') or []):
        path = f'features.{fid}.rotation.phases[{i}]'
        shape = phase.get('panel') or phase.get('footprint') or phase.get('geometry')
        if shape is not None:
            check(shape, phase.get('sight_bounds'), path + '.sight_bounds')
        for j, occ in enumerate(phase.get('sight') or []):
            check(occ.get('geometry'), occ.get('bounds'), f'{path}.sight[{j}].bounds')
    return out


def state_problems(geo: Geometry, mf: dict, feature: dict) -> list[str]:
    """Why some state of a feature doesn't compile to what it says: a blocking footprint whose floor bindings
    are unresolved, empty, stale, missing or ambiguous (it would block nothing, or not all of itself), or a
    sight occluder that is pending. Every state, not only the initial one: a bundle publishes for the round."""
    out = []
    for s in feature.get("states") or []:
        one = {**feature, "initial_state": s.get("name")}
        if s.get("blocks_movement") and s.get("footprint"):
            cells = to_grid(raster(s["footprint"])).ravel()
            b = feature_floor_nodes(geo, mf, one, cells)
            out += [f"state {s.get('name')!r}: {p}" for p in b.pending]
            if cells.any() and not len(b.nodes) and not b.pending:
                out.append(f"state {s.get('name')!r}: {feature.get('id')}: its footprint binds no node")
        _, pending = sight_occluders(geo, {**mf, "features": [one]})
        out += [f"state {s.get('name')!r}: {p}" for p in pending]
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
    intended: bool = False


def required_features(features, members):
    """Required transitive parents, with missing dependencies and cycles kept pending."""
    required, visiting, reasons = set(), set(), []
    def visit(fid):
        if fid in visiting:
            reasons.append(f'{fid}: parent dependency cycle')
            return
        if fid in required:
            return
        required.add(fid)
        feature = features.get(fid)
        if feature is None:
            reasons.append(f'{fid}: required feature does not exist')
            return
        visiting.add(fid)
        if feature.get('parent'):
            visit(feature['parent'])
        visiting.remove(fid)
    for fid in members:
        visit(fid)
    return required, reasons


def bundle_status(geo: Geometry | None, mf: dict, legacy: dict | None = None,
                  consumers: frozenset | None = None) -> dict:
    """Each bundle's publishability. A bundle publishes its members' base edits only together and only when
    it is enabled, names a registered runtime consumer, every member's behaviour is resolved, every floor
    binding it relies on is verified against the map's current heights, every member's states compile with
    nothing pending (`state_problems`; needs `geo`), every overlap with legacy hand paint is reclassified
    exactly, and no member's edits overlap a feature outside the bundle. `consumers` defaults to the registered
    RUNTIME_CONSUMERS (read at call time)."""
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
        required, reasons = required_features(features, members)
        if not b.get("enabled"):
            reasons.append("not enabled")
        if b.get("runtime_consumer") not in consumers:
            reasons.append(f"no registered runtime consumer ({b.get('runtime_consumer')!r})")
        for fid in sorted(required - set(members)):
            if fid in features:
                reasons += behaviour_problems(features[fid])
        ground = np.zeros((PX, PX), bool)
        sight = np.zeros((PX, PX), bool)
        for m in members:
            f = features.get(m)
            if f is None:
                reasons.append(f"member {m!r} doesn't exist")
                continue
            reasons += behaviour_problems(f)
            edits = f.get("base_edits") or {}
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
        if geo is not None and not reasons:
            # Ownership/legacy checks precede placement; candidate belongs to this bundle alone.
            candidate = _candidate_geometry(geo, mf, set(members))
            placed = placement(candidate, mf)
            for m in sorted(required):
                reasons += [f"{r['path']}: {r['code']}" for r in placed[m].reasons]
                reasons += [f"{m}: {p}" for p in state_problems(candidate, mf, features[m])]
                reasons += [f"{r['path']}: {r['reason']}" for r in bounds_problems(candidate, mf, features[m])]
                for phase in (features[m].get('rotation') or {}).get('phases') or []:
                    for occ in phase.get('sight') or []:
                        _, why = resolve_bounds(candidate, mf, occ, to_px(raster(occ.get('geometry'))), m)
                        if why:
                            reasons.append(why)
            _, route_pending = compile_routes(candidate, {**mf, 'routes': [r for r in mf.get('routes') or []
                                                      if r.get('owner') in required]})
            reasons += route_pending
        out[bid] = BundleStatus(bid, members, not reasons, reasons,
                                {"ground": int(ground.sum()), "sight": int(sight.sum())},
                                bool(b.get('enabled') and b.get('runtime_consumer') in consumers))
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
    if not ms._uv(uv):
        return None, 'not placed or off map'
    cell = geo.cell_of_px(uv[0] * PX / ms.UV_MAX, uv[1] * PX / ms.UV_MAX)
    mask = np.zeros(GRID * GRID, bool)
    mask[cell] = True
    b = floor_nodes(geo, None, mask)
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
        if any(n is None for n in nodes.values()):
            continue
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

MANIFEST_VERSION = fi.FEATURE_MANIFEST_VERSION


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
    if not any(st.intended for st in statuses.values()):
        return None
    sub = {**mf, "features": [f for f in mf.get("features") or [] if f.get("id") in members],
           "routes": [r for r in mf.get("routes") or [] if r.get("owner") in members]}
    states = {}
    for f in sub["features"]:
        status = next(st for st in statuses.values() if st.publishable and f.get('id') in st.members)
        candidate = _candidate_geometry(geo, mf, set(status.members))
        for s in f.get("states") or []:
            pick = {f.get("id"): s.get("name")}
            one = {**sub, "features": [f]}
            blocked = movement_blocks(candidate, one, pick).blocked
            occ, _ = sight_occluders(candidate, one, pick)
            states[f"{f.get('id')}:{s.get('name')}"] = {
                "blocked": np.flatnonzero(blocked).tolist(),
                "occluders": [{"mask": _packed_mask(o.mask), "bottom": None if o.all_height else o.bottom,
                               "top": None if o.all_height else o.top, "all_height": o.all_height} for o in occ]}
    arcs = []
    for st in statuses.values():
        if st.publishable:
            candidate = _candidate_geometry(geo, mf, set(st.members))
            routes = {**sub, 'routes': [r for r in sub['routes'] if r.get('owner') in st.members]}
            one_arcs, _ = compile_routes(candidate, routes)
            arcs += one_arcs
    return {"states": states, "nodes": int(geo.n),
            "arcs": [[a.route, a.src, a.dst, a.entry_s, a.transit_s, a.length_m, a.owner,
                      list(a.states) if a.states is not None else None, a.in_transit] for a in arcs]}


def asset_hashes(assets: dict) -> dict:
    """The hash of each loaded asset part, from its canonical bytes (what verification recomputes)."""
    return {key: _canon(assets[key]) for key in sorted(assets)}


def _packed_mask(mask):
    """NumPy fast path of the parent-safe packed mask format."""
    mask = np.asarray(mask, bool)
    raw = np.packbits(mask.ravel(), bitorder='little').tobytes()
    return {'shape': list(mask.shape), 'bitorder': 'little', 'count': int(mask.size), 'bytes': len(raw),
            'data': base64.b64encode(raw).decode('ascii')}


def _archived_mask(descriptor, shape):
    if descriptor.get('shape') != list(shape):
        raise fa.FeatureArtifactCorrupt('permanent mask dimensions mismatch')
    return np.frombuffer(fa.unpack_mask(descriptor), np.uint8).reshape(shape).astype(bool)


def geometry_from_feature_inputs(inputs: fi.FeatureInput, asset=None) -> Geometry:
    """Reconstruct permanent geometry from the archive, with no current-source/pointer lookup."""
    from app.control.geometry import geometry_from_masks, attach_heights
    envelope = fi.read_json(inputs.canonical_inputs)
    base = envelope['base']
    geo = geometry_from_masks(inputs.key.map_name, _archived_mask(base['sight'], (PX, PX)),
                              _archived_mask(base['walk'], (PX, PX)), base['scale'], base['specials'])
    geo.barrier = None if base['barrier'] is None else _archived_mask(base['barrier'], (GRID, GRID))
    geo.barrier_sha = base.get('barrier_sha')
    if inputs.key.height_digest == 'flat':
        if asset is not None:
            raise fa.FeatureArtifactCorrupt('flat key cannot attach heights')
    else:
        if asset is None or asset.digest != inputs.key.height_digest:
            raise fa.FeatureArtifactMissing('exact archived height required')
        attach_heights(geo, asset)
    return geo


def verify_permanent_context(geo: Geometry, inputs: fi.FeatureInput) -> None:
    """A cached artifact still belongs to the actual map context used for this invocation."""
    if (geo.height_sha or 'flat') != inputs.key.height_digest or geo.name != inputs.key.map_name:
        raise fa.FeatureArtifactCorrupt('compiler geometry/key mismatch')
    if hashlib.sha256(inputs.canonical_inputs).hexdigest() != inputs.key.tags_digest:
        raise fa.FeatureArtifactCorrupt('compiler input identity mismatch')
    reconstructed = geometry_from_feature_inputs(inputs, geo.heights)
    for actual, expected in ((geo.sight, reconstructed.sight), (geo.walk_px, reconstructed.walk_px)):
        if not np.array_equal(actual, expected):
            raise fa.FeatureArtifactCorrupt('compiler permanent masks do not match archive')
    if geo.uv_per_unit != reconstructed.uv_per_unit or geo.specials != reconstructed.specials \
            or ((geo.barrier is None) != (reconstructed.barrier is None)) \
            or (geo.barrier is not None and not np.array_equal(geo.barrier, reconstructed.barrier)):
        raise fa.FeatureArtifactCorrupt('compiler permanent context does not match archive')


def compile_artifact(geo: Geometry, inputs: fi.FeatureInput, code_commit: str) -> fa.FeatureArtifact:
    """The existing compiler, applied to permanent archived definitions/context, with complete assets."""
    envelope = fi.read_json(inputs.canonical_inputs)
    if inputs.key.compiler_version != COMPILER_VERSION or envelope['normalization'] != fi.FEATURE_NORMALIZATION_VERSION:
        raise fa.UnsupportedFeatureCompiler('recorded feature compiler/normalization unavailable')
    verify_permanent_context(geo, inputs)
    mf = envelope['runtime']
    mf = {**mf, 'features': mf['features'] + envelope['outside_base_edits']}
    legacy = {k: _archived_mask(v, (PX, PX)) for k, v in envelope['legacy'].items()}
    statuses = bundle_status(geo, mf, legacy, consumers=frozenset(envelope['consumers']))
    active = sorted(b for b, status in statuses.items() if status.publishable)
    assets = compile_assets(geo, mf, statuses) or {'states': {}, 'nodes': int(geo.n), 'arcs': []}
    bindings, triggers, deltas, phases = {}, {}, {}, {}
    for bid in active:
        status = statuses[bid]
        candidate = _candidate_geometry(geo, mf, set(status.members))
        placed = placement(candidate, mf)
        for member in status.members:
            p = placed[member]
            bindings[member] = {'nodes': list(p.nodes), 'ground_m': list(p.ground_m)}
            f = next(f for f in mf['features'] if f['id'] == member)
            for i, phase in enumerate((f.get('rotation') or {}).get('phases') or []):
                geometry = phase.get('panel') or phase.get('footprint') or phase.get('geometry')
                mask = to_px(raster(geometry))
                occ, why = resolve_bounds(candidate, mf, {'bounds': phase.get('sight_bounds')}, mask, member)
                if why:
                    raise fa.FeatureArtifactCorrupt('eligible phase has unresolved bounds')
                phases[f'{member}:{i}'] = {'mask': _packed_mask(mask), 'bottom': None if occ.all_height else occ.bottom,
                                         'top': None if occ.all_height else occ.top, 'all_height': occ.all_height}
        ground = np.zeros((PX, PX), bool)
        sight = np.zeros((PX, PX), bool)
        for f in mf['features']:
            if f['id'] in status.members:
                own = _owned(f)
                ground |= own['potential_ground']
                sight |= own['remove_sight']
        deltas[bid] = {'potential_ground': _packed_mask(ground), 'remove_sight': _packed_mask(sight)}
        for trigger in mf.get('triggers') or []:
            if any(t.get('feature') in status.members for t in trigger.get('targets') or []):
                p = _place_shape(candidate, trigger.get('geometry'), f'triggers.{trigger["id"]}')
                triggers[trigger['id']] = {'nodes': list(p.nodes), 'mask': _packed_mask(to_px(raster(trigger['geometry']))),
                                          'targets': trigger.get('targets', [])}
    sight, walk, _ = reconcile(geo.sight, geo.walk_px, mf, statuses)
    assets.update(bindings=bindings, triggers=triggers, base_deltas=deltas, phases=phases,
                  base_domain={'sight': _packed_mask(sight), 'walk': _packed_mask(walk),
                               'barrier': None if geo.barrier is None else _packed_mask(geo.barrier)})
    pending = {bid: {'reasons': status.reasons, 'placement': {m: [dict(r, members=status.members) for r in p.reasons]
               for m, p in placement(geo, mf).items() if m in status.members}}
               for bid, status in sorted(statuses.items()) if not status.publishable}
    manifest = {'v': MANIFEST_VERSION, 'key': asdict(inputs.key),
                'inputs_sha256': hashlib.sha256(inputs.canonical_inputs).hexdigest(),
                'schema': 1, 'normalization': fi.FEATURE_NORMALIZATION_VERSION,
                'intended_bundles': sorted(statuses), 'active_bundles': active, 'pending': pending,
                'compiled': {k: hashlib.sha256(fi.canonical_json(v)).hexdigest() for k, v in assets.items()}}
    artifact = fa.FeatureArtifact(hashlib.sha256(fi.canonical_json(manifest)).hexdigest(), inputs.key, manifest,
                                  inputs.canonical_inputs, gzip.compress(fi.canonical_json(assets), compresslevel=9, mtime=0), code_commit)
    fa.check_artifact(artifact)
    return artifact


def verify_artifact(artifact: fa.FeatureArtifact, geo: Geometry) -> None:
    fa.check_artifact(artifact)
    envelope = fi.read_json(artifact.inputs)
    from app.replays import map_feature_state as state
    reducer = {'guards': state.GUARD_VOCABULARY, 'events': list(state.EVENTS), 'priority': state.PRIORITY,
               'mid_motion': list(state.MID_MOTION), 'max_steps': state.MAX_STEPS}
    if envelope['reducer'] != reducer:
        raise fa.UnsupportedFeatureCompiler('recorded reducer unavailable')
    if any(fi.CONSUMER_VERSIONS.get(name) != version for name, version in envelope['consumers'].items()):
        raise fa.UnsupportedFeatureCompiler('recorded consumer version unavailable')
    inp = fi.FeatureInput(artifact.key, artifact.inputs, b'', '')
    fresh = compile_artifact(geo, inp, artifact.code_commit)
    if fresh.manifest != artifact.manifest or fa.expanded_assets(fresh.assets) != fa.expanded_assets(artifact.assets):
        raise fa.FeatureArtifactCorrupt('definitions do not correspond to compiled archive')


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
            'intended_bundles': sorted(b for b, st in statuses.items() if st.intended),
            'pending': {b: st.reasons for b, st in sorted(statuses.items()) if st.intended and not st.publishable},
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
    floor bindings read from another height asset, and a blocking footprint that leaks a passage diagonally (it splits the ground
    4-connected but not 8-connected, and the engine walks 8-connected)."""
    out = []
    walk = geo.walk.ravel()
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
