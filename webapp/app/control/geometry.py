"""Map geometry for replay map control (docs/replay-map-control-plan.md, "Map geometry"; Stage 1).

Two masks per map, both 1024 x 1024 over the square minimap (the replay's u/v, 0..10000):

- **sight** (True blocks sight): alpha 0, plus colour glyphs (opaque pixels with saturation over
  GLYPH_SATURATION, such as Bind's teleporter lanes), plus shapes tagged `cover`, minus shapes tagged
  `seeacross` and the map's see-across paint (Abyss's drops). `seeover`, `walkable` and `glyph` tags
  never block (a `glyph` shape is only a drawing on the floor).
- **walk** (True is walkable): opaque minus glyphs, minus `cover` shapes. See-across areas stay
  unwalkable: they are drops.

A third, optional mask, **barrier** (`<Map>.barrier.png`, from the `barrier_paint`), marks the
buy-phase barrier lines. It changes neither sight nor walking: the engine uses it once a round, to
give each team the ground on its side of the barriers when they drop.

The hand inputs live in `app/static/data/control/tags.json`. A tag names one of the 0a candidate
detector's shapes by id (`scripts/control_feasibility/geometry.py`); ids depend on the detector's
parameters, so each map's entry keeps the ones it was tagged with, and `masks()` re-runs the detector
with them and refuses a tag whose shape no longer matches. `scripts/build_control_geometry.py` writes
the result as `<Map>.sight.png` and `<Map>.walk.png` beside `tags.json`, so the engine only loads PNGs.

The engine works on a GRID x GRID cell grid (about 1 m). Sight lines are raycast on the full 1024
sight mask with the Q72 corner tolerance: a ray stops once it has crossed more than
CORNER_TOLERANCE_M of wall in total. The cell-to-cell visibility bitsets (each walkable cell's
360-degree view, about 11 MB a map) are built on demand into a local cache keyed by a hash of the
masks and these parameters, never committed (R2).

**Heights** (docs/superpowers/specs/2026-10-01-control-heights-design.md, part 4). A map with a height
asset (`<Map>.height.npz`, app/control/heights.py) works on nodes, not cells: a node is one floor of one
cell. A cell's lowest floor keeps the cell's own flat index (0 .. GRID*GRID-1) and its upper floors are
appended after GRID*GRID, so a map without an asset has exactly the cells it always had, and every
array, bitset and cache key of a flat map is unchanged. With heights, `cast` takes the viewer's eye
height and returns the nodes it sees: after the 2D wall and smoke checks, a node is seen when the slope
from the eye to a body on it clears the horizon (the steepest slope to the ground passed so far) and
misses every upper floor passed (a thin plate at its ground height). Heights only ever remove sight.

Never imported by the web app or the upload worker's server, since `app.control` needs numpy, scipy
and Pillow (tests/replays/test_control_isolation.py): it runs in scripts/compute_control.py and in
the worker's control children (replay_worker/control_job.py), which have their own interpreter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from app.control import heights as hc
from app.replays import format as fmt

GEOMETRY_VERSION = 1
PX = 1024
GRID = 128
CELL = PX // GRID
GLYPH_SATURATION = 80          # Bind's lanes are ~144; spike-site tints reach 41
PAINT_GRID = 256               # see-across paint: 256 x 256 cells of 4 px
TAG_KINDS = ("cover", "seeover", "walkable", "glyph", "seeacross")
# The 0a candidate detector's parameters (geometry.py), used when a map's entry names none.
DETECTOR_DEFAULTS = {"glyph_saturation": GLYPH_SATURATION, "line_lum": 175, "edge_px": 3, "min_closed_px": 4,
                     "min_line_px": 12, "min_edge_closed_px": 60, "min_void_px": 20}

CORNER_TOLERANCE_M = 0.3       # Q72: a line through less wall than this, in total, is clear
RAY_STEP_DEG = 0.5
RAY_STEP_PX = 2
RAY_MAX_PX = 1500
WALL_SIMPLIFY_PX = 4.0         # a utility wall's laid line, kept to within half a cell (~0.5 m)
# The kill-line check (Risk 1) skips this many pixels at each end, like 0a's harness.
LINE_END_SKIP_PX = 3

ASSET_DIR = fmt.STATIC_DIR / "data" / "control"
MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
MINIMAP_DIR = fmt.STATIC_DIR / "img" / "maps"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".control_cache"


class GeometryError(ValueError):
    pass


def cache_dir() -> Path:
    configured = os.environ.get("CONTROL_CACHE_DIR")
    return Path(configured) if configured else DEFAULT_CACHE_DIR


# ---------------------------------------------------------------- masks


def _base_masks(rgba: np.ndarray, glyph_saturation: int, line_lum: int) -> dict[str, np.ndarray]:
    rgb = rgba[..., :3].astype(int)
    opaque_raw = rgba[..., 3] > 0
    saturation = rgb.max(-1) - rgb.min(-1)
    glyph = opaque_raw & (saturation > glyph_saturation)
    opaque = opaque_raw & ~glyph
    line = opaque & (rgb.mean(-1) >= line_lum)
    return {"opaque": opaque, "glyph": glyph, "line": line, "floor": opaque & ~line}


def candidates(rgba: np.ndarray, params: dict) -> tuple[np.ndarray, list[dict]]:
    """The 0a detector's shapes (`scripts/control_feasibility/geometry.py`, same order and ids):
    closed shapes, loose lines away from the map edge, and void inside the map. Returns the id per
    pixel (0 = none) and the list of {id, kind, px, bbox}."""
    p = {**DETECTOR_DEFAULTS, **(params or {})}
    m = _base_masks(rgba, p["glyph_saturation"], p["line_lum"])
    floor, line, opaque = m["floor"], m["line"], m["opaque"]
    lab, n = ndimage.label(floor)
    sizes = ndimage.sum(floor, lab, range(1, n + 1))
    main = int(np.argmax(sizes)) + 1
    small = [i + 1 for i, s in enumerate(sizes) if i + 1 != main and s >= p["min_closed_px"]]
    enclosed = np.isin(lab, small)
    closed = ndimage.binary_dilation(enclosed, iterations=2) & (line | enclosed)
    closed_lab, _ = ndimage.label(closed, structure=np.ones((3, 3)))
    near_void = ndimage.binary_dilation(~opaque, iterations=p["edge_px"])
    loose = line & ~closed & ~near_void
    loose_lab, n_loose = ndimage.label(loose, structure=np.ones((3, 3)))
    loose_sizes = ndimage.sum(loose, loose_lab, range(1, n_loose + 1))
    labels = np.zeros(floor.shape, np.int32)
    out: list[dict] = []

    def add(mask: np.ndarray, kind: str) -> None:
        ys, xs = np.nonzero(mask)
        cid = len(out) + 1
        labels[mask] = cid
        out.append({"id": cid, "kind": kind, "px": int(mask.sum()),
                    "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]})

    def piece(lab_: np.ndarray, sl, i: int) -> np.ndarray:
        mask = np.zeros(lab_.shape, bool)
        mask[sl] = lab_[sl] == i
        return mask

    for i, sl in enumerate(ndimage.find_objects(closed_lab), start=1):
        mask = piece(closed_lab, sl, i)
        if mask.sum() < p["min_edge_closed_px"] and (mask & near_void).any():
            continue  # anti-aliasing specks along a curved wall, not a shape
        add(mask, "closed")
    for i, sl in enumerate(ndimage.find_objects(loose_lab), start=1):
        if loose_sizes[i - 1] < p["min_line_px"]:
            continue
        add(piece(loose_lab, sl, i), "line")
    void = ~opaque
    void_lab, n_void = ndimage.label(void)
    border = set(np.unique(np.r_[void_lab[0], void_lab[-1], void_lab[:, 0], void_lab[:, -1]])) - {0}
    void_sizes = ndimage.sum(void, void_lab, range(1, n_void + 1))
    for i, sl in enumerate(ndimage.find_objects(void_lab), start=1):
        if i in border or void_sizes[i - 1] < p["min_void_px"]:
            continue
        add(piece(void_lab, sl, i), "void")
    return labels, out


def pack_paint(cells: np.ndarray) -> str:
    """A PAINT_GRID x PAINT_GRID cell mask as a paint string (the inverse of `unpack_paint` on its cells)."""
    return base64.b64encode(np.packbits(np.asarray(cells, bool).ravel().astype(np.uint8),
                                        bitorder="little").tobytes()).decode("ascii")


def unpack_paint(b64: str) -> np.ndarray:
    """A see-across paint string (256 x 256 bits, little bit order) as a 1024 x 1024 pixel mask."""
    raw = np.frombuffer(base64.b64decode(b64), np.uint8)
    cells = np.unpackbits(raw, bitorder="little")[: PAINT_GRID * PAINT_GRID].reshape(PAINT_GRID, PAINT_GRID)
    step = PX // PAINT_GRID
    return cells.astype(bool).repeat(step, 0).repeat(step, 1)


def tag_shapes(rgba: np.ndarray, entry: dict) -> dict[str, np.ndarray]:
    """Each tag kind's pixels on this map. Refuses a tag whose candidate no longer matches the one it
    was made on (the minimap or the detector changed under it)."""
    shapes = {kind: np.zeros((PX, PX), bool) for kind in TAG_KINDS}
    tags = entry.get("tags") or []
    if not tags:
        return shapes
    labels, found = candidates(rgba, entry.get("params") or {})
    by_id = {c["id"]: c for c in found}
    for tag in tags:
        if tag.get("tag") not in TAG_KINDS:
            raise GeometryError(f"unknown tag {tag.get('tag')!r} on candidate {tag.get('id')}")
        cand = by_id.get(tag["id"])
        if cand is None or cand["kind"] != tag.get("kind") or cand["bbox"] != tag.get("bbox") \
                or cand["px"] != tag.get("px"):
            raise GeometryError(f"tagged candidate {tag['id']} ({tag.get('kind')}, bbox {tag.get('bbox')}) no longer "
                                f"matches the detector's shape; re-tag this map")
        shapes[tag["tag"]] |= labels == tag["id"]
    return shapes


@dataclass
class MapMasks:
    sight: np.ndarray   # PX x PX, True blocks sight
    walk: np.ndarray    # PX x PX, True is walkable


def masks(rgba: np.ndarray, entry: dict | None = None) -> MapMasks:
    """The sight and traversal masks of one minimap (RGBA, PX x PX) and its `tags.json` entry.

    Besides tags, an entry may carry hand paint (PAINT_GRID bits each, drawn with
    scripts/control_tagger.py): `see_across_paint` opens sight over black; `cover_paint` blocks sight
    and walking like a `cover` tag (see-across can't open it); `cant_walk_paint` only removes walkable
    ground (a drop you can see across, a ledge); `uncertain_paint` changes neither mask (a note of
    where the 2D map is doubtful; build_control_geometry.py counts it)."""
    entry = entry or {}
    base = _base_masks(rgba, GLYPH_SATURATION, DETECTOR_DEFAULTS["line_lum"])
    shapes = tag_shapes(rgba, entry)
    # What each Stage 6 paint means; no map has one yet, so no stored round changes.
    cover = shapes["cover"]
    if entry.get("cover_paint"):
        cover = cover | unpack_paint(entry["cover_paint"])
    sight = ~base["opaque"] | cover
    see_across = shapes["seeacross"].copy()
    if entry.get("see_across_paint"):
        see_across |= unpack_paint(entry["see_across_paint"])
    sight &= ~see_across | cover
    walk = base["opaque"] & ~cover
    if entry.get("cant_walk_paint"):
        walk &= ~unpack_paint(entry["cant_walk_paint"])
    return MapMasks(sight, walk)


def bullet_mask(rgba: np.ndarray, entry: dict | None = None) -> np.ndarray:
    """What stops a shot's tracer on the minimap (PX x PX, True stops it; the 2026-10-05 review, item 11): every
    sight wall, plus the low boxes sight goes over (`seeover` tags) and `bullet_paint` (a box the map only draws
    in outline). Its own mask: the sight mask leaves low boxes out, and the walk mask's holes include open drops
    a shot flies across (`cant_walk_paint` alone never stops one). The viewer's only; control never reads it."""
    entry = entry or {}
    out = masks(rgba, entry).sight | tag_shapes(rgba, entry)["seeover"]
    if entry.get("bullet_paint"):
        out = out | unpack_paint(entry["bullet_paint"])
    return out


def line_blocked(sight: np.ndarray, a: tuple[int, int], b: tuple[int, int],
                 end_skip: int = LINE_END_SKIP_PX) -> bool:
    """The Risk 1 kill-line rule (0a's harness): the pixel line from a to b (x, y), rasterised with
    numpy's linspace and round, ends trimmed, crosses a sight wall."""
    n = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
    if n == 0:
        return False
    xs = np.round(np.linspace(a[0], b[0], n + 1)).astype(int)[end_skip:n + 1 - end_skip]
    ys = np.round(np.linspace(a[1], b[1], n + 1)).astype(int)[end_skip:n + 1 - end_skip]
    return bool(sight[ys, xs].any())


# ---------------------------------------------------------------- geometry


@dataclass
class Geometry:
    name: str
    sight: np.ndarray              # PX x PX, True blocks sight
    walk_px: np.ndarray            # PX x PX
    walk: np.ndarray               # GRID x GRID walkable cells
    m_per_px: float
    uv_per_unit: float             # u/v per world unit (cm)
    tol_hits: int                  # wall samples a ray may cross (Q72)
    specials: list = field(default_factory=list)
    centres: np.ndarray = None     # flat cell -> (x, y) px
    rows: np.ndarray = None        # packed 360-degree visibility per walkable cell
    row_of: np.ndarray = None      # flat cell -> row index, -1 if not walkable
    visibility_source: str | None = None  # "cache" or "built", once loaded
    visibility_s: float = 0.0
    barrier: np.ndarray | None = None     # GRID x GRID: the buy-phase barrier lines (barrier paint), if painted
    barrier_sha: str | None = None        # index.json's `barrier_sha` for it
    # --- nodes (one per cell on a flat map; `attach_heights` adds the upper floors)
    n: int = GRID * GRID                  # node count: every array the engine keeps per place has this length
    node_cell: np.ndarray = None          # node -> flat cell
    node_z: np.ndarray = None             # node -> position-z in metres above the map's lowest floor; NaN if unknown
    node_of: np.ndarray = None            # GRID*GRID x MAX_FLOORS: a cell's nodes, lowest first, -1 for none
    walk_n: np.ndarray = None             # node -> walkable
    unresolved: np.ndarray = None         # flat cell -> walkable with no known height (flat 2D sight and walking)
    heights: object = None                # the map's HeightAsset, or None on a flat map
    height_sha: str | None = None
    height_unknown: int = 0               # walkable cells the asset doesn't know (the walk mask changed since)
    # map features (app/control/features.py): the active generation, only when index.json names one
    features_sha: str | None = None
    features: dict | None = None          # {map, manifest, assets} as published

    @property
    def cell_m(self) -> float:
        return self.m_per_px * CELL

    def node_at(self, cell: int, z_m: float | None) -> int:
        """The node of `cell` a thing at position-z `z_m` is on: its floor nearest at or below z (with
        NODE_SNAP_M of slack), else its lowest. The cell itself on a flat map or with no z."""
        if self.heights is None or z_m is None:
            return cell
        best = cell
        for node in self.node_of[cell]:
            if node >= 0 and self.node_z[node] <= z_m + hc.NODE_SNAP_M:
                best = int(node)     # floors are lowest first, so the last one under z wins
        return best

    def to_nodes(self, cells: np.ndarray) -> np.ndarray:
        """A flat cell mask as a node mask: every floor of each cell."""
        return cells if self.n == GRID * GRID else cells[self.node_cell]

    def to_cells(self, nodes: np.ndarray) -> np.ndarray:
        """A node mask as a flat cell mask: a cell is set when any of its floors is."""
        if self.n == GRID * GRID:
            return nodes
        out = nodes[: GRID * GRID].copy()
        out[self.node_cell[GRID * GRID:][nodes[GRID * GRID:]]] = True
        return out

    def px_of_uv(self, u: float, v: float) -> tuple[float, float]:
        return u * PX / fmt.UV_SCALE, v * PX / fmt.UV_SCALE

    def cell_of_px(self, x: float, y: float) -> int:
        return int(min(max(y, 0), PX - 1) // CELL) * GRID + int(min(max(x, 0), PX - 1) // CELL)


def geometry_from_masks(name: str, sight: np.ndarray, walk_px: np.ndarray, scale: float,
                        specials: list | None = None) -> Geometry:
    """`scale` is the map's `xMultiplier` in maps.json (minimap fraction per world unit)."""
    walk = walk_px.reshape(GRID, CELL, GRID, CELL).mean((1, 3)) > 0.5
    m_per_px = 1 / (scale * PX * 100)
    tol_hits = int(CORNER_TOLERANCE_M / m_per_px // RAY_STEP_PX)
    geo = Geometry(name, sight.astype(bool), walk_px.astype(bool), walk, m_per_px, scale * fmt.UV_SCALE, tol_hits,
                   list(specials or []))
    ys, xs = np.divmod(np.arange(GRID * GRID), GRID)
    geo.centres = np.stack([xs * CELL + CELL / 2, ys * CELL + CELL / 2], 1).astype(np.float32)
    geo.node_cell = np.arange(GRID * GRID, dtype=np.int32)
    geo.walk_n = geo.walk.ravel().copy()
    return geo


def attach_heights(geo: Geometry, asset) -> Geometry:
    """Gives a flat geometry its nodes from a HeightAsset (heights.py). Only walkable cells get heights:
    a walkable cell the asset has no floor for is unresolved (flat 2D sight and walking), whether the
    build said so or the walk mask has changed since (`height_unknown` counts those)."""
    walk = geo.walk.ravel()
    floors = asset.floors.reshape(GRID * GRID, hc.MAX_FLOORS).astype(np.int32)
    floors[~walk] = -1
    count = (floors >= 0).sum(1)
    cells, levels = np.nonzero(floors[:, 1:] >= 0)          # row-major: (cell, floor) order
    extra = len(cells)
    geo.n = GRID * GRID + extra
    geo.node_cell = np.concatenate([np.arange(GRID * GRID), cells]).astype(np.int32)
    geo.node_of = -np.ones((GRID * GRID, hc.MAX_FLOORS), np.int32)
    geo.node_of[:, 0] = np.arange(GRID * GRID)
    geo.node_of[cells, levels + 1] = GRID * GRID + np.arange(extra)
    z = np.full(geo.n, np.nan)
    has = floors[:, 0] >= 0
    z[: GRID * GRID][has] = floors[has, 0] / 10.0
    z[GRID * GRID:] = floors[cells, levels + 1] / 10.0
    geo.node_z = z
    geo.walk_n = np.concatenate([walk, np.ones(extra, bool)])
    geo.unresolved = walk & (count == 0)
    geo.height_unknown = int((geo.unresolved & ~asset.unresolved.ravel()).sum())
    geo.centres = geo.centres[geo.node_cell]
    geo.heights, geo.height_sha = asset, asset.digest
    geo.rows = geo.row_of = None
    return geo


def read_mask_png(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("L")) > 127


def write_mask_png(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(mask.astype(bool)).convert("1").save(path, optimize=True)


def load_tags(asset_dir: Path = ASSET_DIR) -> dict:
    return json.loads((asset_dir / "tags.json").read_text(encoding="utf-8"))


def load_geometry(name: str, asset_dir: Path = ASSET_DIR, heights: Path | None = None) -> Geometry:
    """A map's committed geometry (built by scripts/build_control_geometry.py), with its heights when
    index.json names a height asset (scripts/build_control_heights.py). `heights` loads that asset file
    instead: a preview's, never a committed map's."""
    sight_path, walk_path = asset_dir / f"{name}.sight.png", asset_dir / f"{name}.walk.png"
    if not sight_path.is_file() or not walk_path.is_file():
        raise GeometryError(f"no control geometry for {name!r}; run scripts/build_control_geometry.py")
    scale = json.loads(MAPS_JSON.read_text(encoding="utf-8"))[name]["xMultiplier"]
    entry = load_tags(asset_dir).get("maps", {}).get(name, {})
    geo = geometry_from_masks(name, read_mask_png(sight_path), read_mask_png(walk_path), scale,
                              entry.get("specials") or [])
    barrier_path = asset_dir / f"{name}.barrier.png"
    if barrier_path.is_file():
        barrier_px = read_mask_png(barrier_path)
        geo.barrier = barrier_cells(barrier_px)
        geo.barrier_sha = hashlib.sha256(np.packbits(barrier_px).tobytes()).hexdigest()[:12]
    if heights is not None:
        return attach_heights(geo, hc.load_asset(heights))
    index_path = asset_dir / "index.json"
    row = (json.loads(index_path.read_text(encoding="utf-8")).get("maps", {}).get(name) or {}) if index_path.is_file() else {}
    if row.get("features_sha"):          # never on a committed map in this build: no feature is enabled
        from app.control import features

        geo.features = features.load_generation(asset_dir, row["features_sha"])
        geo.features_sha = row["features_sha"]
    wanted = row.get("height_sha")
    if wanted:
        asset = hc.load_asset(asset_dir / f"{name}.height.npz")
        if asset.digest != wanted:
            raise GeometryError(f"{name}.height.npz is {asset.digest}, index.json says {wanted}; rebuild the heights")
        attach_heights(geo, asset)
    return geo


def barrier_cells(barrier_px: np.ndarray) -> np.ndarray:
    """GRID x GRID: every cell the barrier paint touches (a line one cell wide still cuts a
    4-connected fill)."""
    return barrier_px.reshape(GRID, CELL, GRID, CELL).any((1, 3))


# ---------------------------------------------------------------- sight lines


@dataclass(frozen=True, eq=False)
class Wall:
    """A laid wall of utility (Viper's Toxic Screen) while it is up: a polyline in px that blocks
    sight outright, with no corner tolerance. It rides in the `smokes` lists beside the circles."""
    segs: np.ndarray    # K x 4: x0, y0, x1, y1 px

    @classmethod
    def from_points(cls, points_px, tolerance_px: float = WALL_SIMPLIFY_PX) -> Wall | None:
        """The polyline through the points, simplified (Douglas-Peucker) to within `tolerance_px`:
        the pairwise check costs one pass per segment, and a laid wall is nearly straight."""
        pts = np.asarray(points_px, float)
        if len(pts) < 2:
            return None
        pts = pts[_simplify(pts, tolerance_px)]
        return cls(np.hstack([pts[:-1], pts[1:]]))


def _simplify(pts: np.ndarray, tol: float) -> list[int]:
    """Indices of the points Douglas-Peucker keeps (both ends always)."""
    keep, stack = {0, len(pts) - 1}, [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        (ax, ay), (bx, by) = pts[i], pts[j]
        mid = pts[i + 1:j]
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-9:
            dist = np.hypot(mid[:, 0] - ax, mid[:, 1] - ay)
        else:
            dist = np.abs((bx - ax) * (mid[:, 1] - ay) - (by - ay) * (mid[:, 0] - ax)) / length
        k = int(np.argmax(dist))
        if dist[k] > tol:
            keep.add(i + 1 + k)
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return sorted(keep)


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def wall_hit_px(wall: Wall, x: float, y: float, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """Per ray (unit direction dx, dy from (x, y)): the distance in px to the wall's first segment
    it crosses, or inf."""
    best = np.full(len(dx), np.inf)
    for x0, y0, x1, y1 in wall.segs:
        ex, ey = x1 - x0, y1 - y0
        den = _cross(dx, dy, ex, ey)
        ok = np.abs(den) > 1e-9
        den = np.where(ok, den, 1.0)
        fx, fy = x0 - x, y0 - y
        t = _cross(fx, fy, ex, ey) / den          # along the ray
        s = _cross(fx, fy, dx, dy) / den          # along the segment
        hit = ok & (t >= 0) & (s >= 0) & (s <= 1)
        best = np.where(hit & (t < best), t, best)
    return best


def wall_blocks(p: np.ndarray, q: np.ndarray, wall: Wall) -> np.ndarray:
    """S x N: does the segment from each p (S x 2) to each q (N x 2) cross the wall?"""
    out = np.zeros((len(p), len(q)), bool)
    for x0, y0, x1, y1 in wall.segs:
        ex, ey = x1 - x0, y1 - y0
        # the wall's line must split p from q: only opposite-side pairs are checked further
        sp = _cross(ex, ey, p[:, 0] - x0, p[:, 1] - y0)
        sq = _cross(ex, ey, q[:, 0] - x0, q[:, 1] - y0)
        for ps, qs in ((sp >= 0, sq <= 0), (sp <= 0, sq >= 0)):
            pi, qi = np.flatnonzero(ps), np.flatnonzero(qs)
            if not len(pi) or not len(qi):
                continue
            px_, py_ = p[pi, 0][:, None], p[pi, 1][:, None]
            # the sight line p -> q must split the wall's two ends: cross(q - p, end - p) changes sign
            ax, ay = x0 - px_, y0 - py_
            bx, by = x1 - px_, y1 - py_
            qx, qy = q[qi, 0][None, :] - px_, q[qi, 1][None, :] - py_
            hit = (qx * ay - qy * ax) * (qx * by - qy * bx) <= 0
            # a sight line on the wall's own line runs along it, never through it (as `wall_hit_px`,
            # where a parallel ray never hits): both cross products above are 0 for it
            hit &= ~((sp[pi] == 0)[:, None] & (sq[qi] == 0)[None, :])
            out[np.ix_(pi, qi)] |= hit
    return out


SLAB_SLOTS = 6     # blocked slope intervals kept per ray; more are merged into the last (blocks a little more)
SLOPE_EPS = 1e-9   # a blocked interval's ends count as blocked


def cast(geo: Geometry, x: float, y: float, angles_deg: np.ndarray, smokes: list, eye_z: float | None = None,
         own: int | None = None, record: dict | None = None) -> np.ndarray:
    """The nodes (flat GRID*GRID cells on a flat map) seen from (x, y) px along the given rays. Walls
    stop a ray once it has crossed more than the corner tolerance; a hollow smoke stops it at its edge
    (from inside or out), a solid one as soon as it is inside; a utility `Wall` where it crosses it.
    `smokes` are (x, y, radius px, solid) or `Wall`s.

    On a map with heights, `eye_z` (the eye's position-z in metres, as `node_z`) adds the height test
    (see the module docstring) and `own` is the viewer's own node, the only one of its cell it sees
    (default: the floor under the eye). Without `eye_z` the answer is today's 2D one: every floor of each
    cell seen. A ray that enters an unresolved cell drops the height test from there on, and `record`
    (a dict) counts those rays under "unresolved_rays"."""
    if geo.heights is not None:
        if eye_z is None:
            return geo.to_nodes(_cast_flat(geo, x, y, angles_deg, smokes))
        return _cast_heights(geo, x, y, angles_deg, smokes, float(eye_z), own, record)
    return _cast_flat(geo, x, y, angles_deg, smokes)


def _cast_flat(geo: Geometry, x: float, y: float, angles_deg: np.ndarray, smokes: list) -> np.ndarray:
    """`cast` on cells, in 2D: the whole of it on a flat map."""
    a = np.deg2rad(angles_deg)
    dx, dy = np.cos(a), np.sin(a)
    seen = np.zeros(GRID * GRID, bool)
    live = np.ones(len(a), bool)
    hits = np.zeros(len(a), np.int16)
    reach = np.full(len(a), np.inf)
    for s in smokes:
        if isinstance(s, Wall):
            reach = np.minimum(reach, wall_hit_px(s, x, y, dx, dy))
    smokes = [s for s in smokes if not isinstance(s, Wall)]
    started_in = [(x - s[0]) ** 2 + (y - s[1]) ** 2 < s[2] ** 2 for s in smokes]
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
        seen[(pyc[live] // CELL) * GRID + pxc[live] // CELL] = True
    return seen


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
    src = geo.cell_of_px(x, y)
    own = geo.node_at(src, eye - hc.EYE_M) if own is None else own
    seen[own] = True
    node_of, node_z, unresolved = geo.node_of, geo.node_z, geo.unresolved
    ground = node_z[: GRID * GRID] - hc.STAND_M - hc.LEDGE_M          # NaN where there is no floor
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
            cell = (pyc // CELL) * GRID + pxc // CELL
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


def los(geo: Geometry, a: tuple, b: tuple, smokes: list = (), record: dict | None = None) -> bool:
    """Is the line from the eye at `a` to the point `b` clear? Both are (x px, y px, position-z m) with
    z already at the eye's and the target point's own heights (None for a 2D check, and on a flat map).
    The same rules as `cast`, for one exact line: the 2D walls with the corner tolerance, smokes and wall
    abilities; the ground of every cell between the two ends' cells; and every upper floor on the way,
    the two ends' cells included: a plate blocks when the line is on one side of it where it enters the
    plate's cell (or at the eye) and on the other where it leaves (or at the target). Independent of the
    rays `cast` happens to shoot, so the two check each other."""
    (ax, ay, az), (bx, by, bz) = a, b
    length = math.hypot(bx - ax, by - ay)
    if length < 1e-9:
        # straight up or down: only the cell's own plates can be in the way
        cell = geo.cell_of_px(ax, ay)
        if geo.heights is None or az is None or bz is None or geo.unresolved[cell]:
            return True
        return not any(node >= 0 and (az - (geo.node_z[node] - hc.STAND_M)) * (bz - (geo.node_z[node] - hc.STAND_M)) < 0
                       for node in geo.node_of[cell, 1:])
    ux, uy = (bx - ax) / length, (by - ay) / length
    circles = [s for s in smokes if not isinstance(s, Wall)]
    for wall in (s for s in smokes if isinstance(s, Wall)):
        if wall_hit_px(wall, ax, ay, np.array([ux]), np.array([uy]))[0] < length:
            return False
    started_in = [(ax - s[0]) ** 2 + (ay - s[1]) ** 2 < s[2] ** 2 for s in circles]
    heights = geo.heights is not None and az is not None and bz is not None
    src, dst = geo.cell_of_px(ax, ay), geo.cell_of_px(bx, by)
    total = length * geo.m_per_px
    slope = (bz - az) / total if heights else 0.0
    step_m = RAY_STEP_PX * geo.m_per_px
    hits, flat = 0, False
    spans: list[list] = []                       # [cell, first d, last d] in order along the line
    for step in range(0, int(length) + 1, RAY_STEP_PX):
        px, py = ax + ux * step, ay + uy * step
        pxc, pyc = int(min(max(px, 0), PX - 1)), int(min(max(py, 0), PX - 1))
        if step > 3:
            hits += bool(geo.sight[pyc, pxc])
            if hits > geo.tol_hits:
                return False
        for (sx, sy, r, solid), inside0 in zip(circles, started_in):
            inside = (px - sx) ** 2 + (py - sy) ** 2 < r * r
            if (inside if solid and step > 0 else inside != inside0):
                return False
        if not heights or flat:
            continue
        cell = (pyc // CELL) * GRID + pxc // CELL
        d = step * geo.m_per_px
        if not spans or spans[-1][0] != cell:
            if geo.unresolved[cell]:
                flat = True                      # 2D from here on, as `cast`
                if record is not None:
                    record["unresolved_rays"] = record.get("unresolved_rays", 0) + 1
                continue
            spans.append([cell, d, d])
        spans[-1][2] = d
        if cell not in (src, dst) and d > 0:
            ground = geo.node_z[cell] - hc.STAND_M - hc.LEDGE_M
            if not math.isnan(ground) and (ground - az) / d > slope:
                return False
    for cell, first, last in spans:
        # where the line is inside the plate's own cell, exactly: the same both ways along the line, and
        # never past the cell's edge (a sample step of padding once blocked a crossing beside a slab)
        t0, t1 = 0.0, length
        for p, u, c in ((ax, ux, cell % GRID), (ay, uy, cell // GRID)):
            if abs(u) > 1e-12:
                near, far = sorted(((c * CELL - p) / u, ((c + 1) * CELL - p) / u))
                t0, t1 = max(t0, near), min(t1, far)
        if t1 < t0:
            t0, t1 = first / geo.m_per_px, last / geo.m_per_px     # a clipped sample off the map's edge
        d0 = 0.0 if cell == src else t0 * geo.m_per_px
        d1 = total if cell == dst else t1 * geo.m_per_px
        h0, h1 = az + slope * d0, az + slope * d1
        for node in geo.node_of[cell, 1:]:
            if node >= 0:
                plate = geo.node_z[node] - hc.STAND_M
                if (h0 - plate) * (h1 - plate) < 0:
                    return False
    return True


def cache_key(geo: Geometry) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps({"v": GEOMETRY_VERSION, "px": PX, "grid": GRID, "ray_step_deg": RAY_STEP_DEG,
                              "ray_step_px": RAY_STEP_PX, "ray_max_px": RAY_MAX_PX, "tol_hits": geo.tol_hits},
                             sort_keys=True).encode("ascii"))
    digest.update(np.packbits(geo.sight).tobytes())
    digest.update(np.packbits(geo.walk_px).tobytes())
    if geo.heights is not None:      # a flat map's key is what it always was
        digest.update(json.dumps({"height": geo.height_sha, "eye": hc.EYE_M, "body": hc.BODY_M, "stand": hc.STAND_M,
                                  "ledge": hc.LEDGE_M, "snap": hc.NODE_SNAP_M, "slabs": SLAB_SLOTS},
                                 sort_keys=True).encode("ascii"))
    return digest.hexdigest()


def build_visibility(geo: Geometry) -> tuple[np.ndarray, np.ndarray]:
    """Each walkable node's 360-degree view (packed bits over the nodes), and node -> row (-1 off the
    walk). On a flat map a node is a cell. With heights the view is from a standing eye on that floor; an
    unresolved cell's is the 2D one."""
    nodes = np.flatnonzero(geo.walk_n)
    rows = np.zeros((len(nodes), (geo.n + 7) // 8), np.uint8)
    angles = np.arange(0, 360, RAY_STEP_DEG)
    for i, c in enumerate(nodes):
        x, y = geo.centres[c]
        if geo.heights is None or np.isnan(geo.node_z[c]):
            rows[i] = np.packbits(cast(geo, float(x), float(y), angles, []))
        else:
            rows[i] = np.packbits(cast(geo, float(x), float(y), angles, [], eye_z=geo.node_z[c] + hc.EYE_M, own=int(c)))
    row_of = -np.ones(geo.n, np.int32)
    row_of[nodes] = np.arange(len(nodes))
    return rows, row_of


def visibility(geo: Geometry, directory: Path | None = None) -> Geometry:
    """Loads the map's visibility bitsets from the cache, or builds and caches them. Sets
    `visibility_source` to "cache" or "built"."""
    if geo.rows is not None:
        return geo
    directory = directory or cache_dir()
    path = directory / f"{geo.name}.{cache_key(geo)[:16]}.npz"
    started = time.perf_counter()
    if path.is_file():
        with np.load(path) as z:
            geo.rows, geo.row_of = z["rows"], z["row_of"]
        geo.visibility_source = "cache"
    else:
        geo.rows, geo.row_of = build_visibility(geo)
        directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, rows=geo.rows, row_of=geo.row_of)
        os.replace(tmp, path)
        geo.visibility_source = "built"
    geo.visibility_s = time.perf_counter() - started
    return geo
