"""Map geometry for replay map control (docs/replay-map-control-plan.md, "Map geometry"; Stage 1).

Two masks per map, both 1024 x 1024 over the square minimap (the replay's u/v, 0..10000):

- **sight** (True blocks sight): alpha 0, plus colour glyphs (opaque pixels with saturation over
  GLYPH_SATURATION, such as Bind's teleporter lanes), plus shapes tagged `cover`, minus shapes tagged
  `seeacross` and the map's see-across paint (Abyss's drops). `seeover`, `walkable` and `glyph` tags
  never block (a `glyph` shape is only a drawing on the floor).
- **walk** (True is walkable): opaque minus glyphs, minus `cover` shapes. See-across areas stay
  unwalkable: they are drops.

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

Local tooling only: neither the web app nor the upload worker imports `app.control`, which needs
numpy, scipy and Pillow (tests/replays/test_control_isolation.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

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

    @property
    def cell_m(self) -> float:
        return self.m_per_px * CELL

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
    return geo


def read_mask_png(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("L")) > 127


def write_mask_png(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(mask.astype(bool)).convert("1").save(path, optimize=True)


def load_tags(asset_dir: Path = ASSET_DIR) -> dict:
    return json.loads((asset_dir / "tags.json").read_text(encoding="utf-8"))


def load_geometry(name: str, asset_dir: Path = ASSET_DIR) -> Geometry:
    """A map's committed geometry (built by scripts/build_control_geometry.py)."""
    sight_path, walk_path = asset_dir / f"{name}.sight.png", asset_dir / f"{name}.walk.png"
    if not sight_path.is_file() or not walk_path.is_file():
        raise GeometryError(f"no control geometry for {name!r}; run scripts/build_control_geometry.py")
    scale = json.loads(MAPS_JSON.read_text(encoding="utf-8"))[name]["xMultiplier"]
    entry = load_tags(asset_dir).get("maps", {}).get(name, {})
    return geometry_from_masks(name, read_mask_png(sight_path), read_mask_png(walk_path), scale,
                               entry.get("specials") or [])


# ---------------------------------------------------------------- sight lines


def cast(geo: Geometry, x: float, y: float, angles_deg: np.ndarray, smokes: list) -> np.ndarray:
    """Flat GRID*GRID cells seen from (x, y) px along the given rays. Walls stop a ray once it has
    crossed more than the corner tolerance; a hollow smoke stops it at its edge (from inside or out),
    a solid one as soon as it is inside. `smokes` are (x, y, radius px, solid)."""
    a = np.deg2rad(angles_deg)
    dx, dy = np.cos(a), np.sin(a)
    seen = np.zeros(GRID * GRID, bool)
    live = np.ones(len(a), bool)
    hits = np.zeros(len(a), np.int16)
    started_in = [(x - s[0]) ** 2 + (y - s[1]) ** 2 < s[2] ** 2 for s in smokes]
    for step in range(0, RAY_MAX_PX, RAY_STEP_PX):
        px = x + dx * step
        py = y + dy * step
        live &= (px >= 0) & (px < PX) & (py >= 0) & (py < PX)
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


def cache_key(geo: Geometry) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps({"v": GEOMETRY_VERSION, "px": PX, "grid": GRID, "ray_step_deg": RAY_STEP_DEG,
                              "ray_step_px": RAY_STEP_PX, "ray_max_px": RAY_MAX_PX, "tol_hits": geo.tol_hits},
                             sort_keys=True).encode("ascii"))
    digest.update(np.packbits(geo.sight).tobytes())
    digest.update(np.packbits(geo.walk_px).tobytes())
    return digest.hexdigest()


def build_visibility(geo: Geometry) -> tuple[np.ndarray, np.ndarray]:
    """Each walkable cell's 360-degree view (packed bits), and flat cell -> row (-1 off the walk)."""
    cells = np.flatnonzero(geo.walk.ravel())
    rows = np.zeros((len(cells), GRID * GRID // 8), np.uint8)
    angles = np.arange(0, 360, RAY_STEP_DEG)
    for i, c in enumerate(cells):
        x, y = geo.centres[c]
        rows[i] = np.packbits(cast(geo, float(x), float(y), angles, []))
    row_of = -np.ones(GRID * GRID, np.int32)
    row_of[cells] = np.arange(len(cells))
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
