"""The map-control drawing page (docs/replay-map-control-plan.md, Stage 6; R2: a local tool, not a site page).

    .venv313\\Scripts\\python.exe scripts\\control_tagger.py [--out <file>] [--tags <tags.json>] [--heights-dir <dir>]
        [--areas-dir <dir>]

Builds one self-contained page (default `%TEMP%\\valo-control-tagger\\control-tagger.html`, never the repo)
with every minimap, its candidate shapes (app/control/geometry.py `candidates`, with the map's own detector
params), the current `app/static/data/control/tags.json` and the Risk 1 kill lines
(`tests/fixtures/control/kill_lines.json`). On the page:

- tag candidates cover, see-over, walkable, glyph or see-across (or untag them);
- paint and erase see-across, cover, can't-walk and uncertain zones with a brush (geometry.masks: cover paint
  blocks sight and walking, can't-walk only walking, uncertain neither), and the buy-phase barrier lines
  (their own mask: the engine gives each team its side of them when they drop; drawn roughly, then
  placed from the replays' round starts by scripts/place_control_barriers.py, whose `--starts` file
  can also be passed here to draw those positions);
- watch the kill-line test live against the 2% bar, with the blocked lines drawn, and the resulting sight and
  walk masks as overlays; undo; tick "cover reviewed" (which clears the map's badge);
- export the whole `tags.json` (every map, unknown fields kept);
- in Chokes mode (timing-gaps spec section 2), select, rename, move (arrow keys), delete (a tombstone) and
  add (paint cells, then "make choke") the map's chokes from `<Map>.chokes.json`, and export that file.
  Copy it over `app/static/data/control/<Map>.chokes.json`: a rename changes no fingerprint, but adding,
  moving or deleting a choke makes that map's timing gaps stale until `scripts/compute_control.py`
  recomputes them. Edited chokes become `source: "hand"`, which re-detection keeps (choke_assets.merge).

- in Map features mode (F; docs/superpowers/plans/2026-10-04-map-interaction-tagger.md), annotate each map's
  gimmicks: doors (drop, switch, proximity, rotating), breakables, switches and triggers linked to what they
  operate, ziplines, ropes and teleporters with their landings and floors, with unknown facts left unresolved.
  The panel's script is `control_tagger_features.js`; its model is `TaggerCore.Features` in the core, checked
  against app/replays/map_feature_schema.py, map_feature_state.py and app/control/features.py
  (tests/replays/test_map_feature_tagger.py). The annotations go into each map's `map_features` in the exported
  tags.json and change no control input until a later engine release enables a feature bundle.
  `--heights-dir <dir>`: read each map's heights from an export of the database's active asset
  (`control_heights.py export`) instead of a committed one.

- in Areas mode (M), start from a walking-distance draft or a saved `<Map>.areas.json` from
  `--areas-dir` (default `%TEMP%\\valo-areas`). Paint or fill names on the walk grid, with a floor
  picker for cells with multiple height bands. Organise names into a tree, confirm regions, and
  load a local reference image or correction pins. Export reviewed paint and copy it to
  `app/static/data/control/<Map>.areas.json` when ready. Export fixture downloads the resolved pins
  as `area_cases.json` for the owner-confirmed test cases. Area edits live in the page until export.

Then copy the export over `app/static/data/control/tags.json` and run `scripts/build_control_geometry.py`: the
masks, index.json and the kill-line results are rebuilt, and every stored round of a changed map goes stale
until `scripts/compute_control.py` recomputes it.

The page embeds Python-computed base masks (opaque pixels, glyphs removed) and the candidate label map, so it
never reads colours back from a canvas; `scripts/control_tagger_core.js` composes tags and paints onto them
the same way geometry.masks does (tests/replays/test_control_tagger.py checks it bit for bit).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.coach import areas, areas_draft  # noqa: E402
from app.control import geometry as cg  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.control.height_motion import coarse_of_fine  # noqa: E402
from app.replays import choke_assets  # noqa: E402
from app.replays import map_feature_schema as ms  # noqa: E402
from app.replays import map_feature_inputs as fi  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "control_tagger.template.html"
CORE_JS = HERE / "control_tagger_core.js"
FEATURES_JS = HERE / "control_tagger_features.js"
AREAS_JS = HERE / "control_tagger_areas.js"
KILL_LINES = WEBAPP_ROOT / "tests" / "fixtures" / "control" / "kill_lines.json"
AREA_FLOOR_PADDING_DM = 10
_LOG = logging.getLogger(__name__)


def rle(values: np.ndarray) -> list[int]:
    """[value, run, value, run, ...] over the flat array."""
    flat = np.asarray(values).ravel()
    if not len(flat):
        return []
    edges = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.r_[0, edges]
    runs = np.diff(np.r_[starts, len(flat)])
    out = np.empty(2 * len(starts), np.int64)
    out[0::2], out[1::2] = flat[starts], runs
    return out.tolist()


def map_data(name: str, entry: dict, lines: list) -> dict:
    png = cg.MINIMAP_DIR / f"{name}.png"
    raw = png.read_bytes()
    rgba = np.array(Image.open(png).convert("RGBA"))
    params = {**cg.DETECTOR_DEFAULTS, **(entry.get("params") or {})}
    labels, candidates = cg.candidates(rgba, entry.get("params") or {})
    base = cg._base_masks(rgba, cg.GLYPH_SATURATION, cg.DETECTOR_DEFAULTS["line_lum"])
    return {"image": base64.b64encode(raw).decode("ascii"), "image_sha": hashlib.sha256(raw).hexdigest()[:12],
            "params": params, "candidates": candidates, "labels": rle(labels), "opaque": rle(base["opaque"].astype(np.uint8)),
            "outline": rle(base["line"].astype(np.uint8)),
            "lines": lines}


def choke_data(name: str, asset_dir: Path = choke_assets.ASSET_DIR) -> dict:
    """The map's `<Map>.chokes.json` as the page edits it: {"chokes": [...], "next_id"} (none: an empty list)."""
    chokes = choke_assets.load(name, asset_dir) or []
    return {"chokes": [asdict(c) for c in chokes], "next_id": choke_assets.load_next_id(name, asset_dir)}


def _height_asset(name: str, asset_dir: Path, heights_dir: Path | None) -> hc.HeightAsset | None:
    exported = None if heights_dir is None else Path(heights_dir) / f"{name}.height.npz"
    if exported is not None and exported.is_file():
        return hc.load_asset(exported)
    index_path = asset_dir / "index.json"
    entry = (json.loads(index_path.read_text(encoding="utf-8")).get("maps", {}).get(name) or {}) if index_path.is_file() else {}
    if entry.get("height_sha"):
        return hc.load_asset(asset_dir / f"{name}.height.npz")
    return None


def _floor_data(asset: hc.HeightAsset | None) -> dict:
    if asset is None:
        return {'flat': True, 'height_sha': None}
    return {"flat": False, "height_sha": asset.digest, "origin_z": asset.origin_z, "max_floors": hc.MAX_FLOORS,
            'mixed_grid': bool(asset.refinement and len(asset.fine_cells)),
            'floor_counts': (np.isfinite(asset.floors) & (asset.floors >= 0)).sum(axis=2).ravel().tolist(),
            'unresolved': asset.unresolved.astype('uint8').ravel().tolist(),
            "floors": base64.b64encode(np.ascontiguousarray(asset.floors, dtype="<i2").tobytes()).decode("ascii")}


def floor_data(name: str, asset_dir: Path = cg.ASSET_DIR, heights_dir: Path | None = None) -> dict:
    """Floor counts and unresolved cells for preview, from exported heights if present, else committed heights."""
    return _floor_data(_height_asset(name, asset_dir, heights_dir))


def features_data(
    names: list[str], heights_dir: Path | None = None, *, assets: dict[str, hc.HeightAsset | None] | None = None,
) -> dict:
    """What the features panel needs from the Python contract (app/replays/map_feature_schema.py), so the page
    never keeps its own copy: the schema version, the presets and route templates, each map's checklist seed and
    its floor data (from `heights_dir` when given, see floor_data)."""
    if assets is None:
        assets = {name: _height_asset(name, cg.ASSET_DIR, heights_dir) for name in names}
    return {"schema_version": ms.SCHEMA_VERSION, "presets": {p: ms.preset(p) for p in ms.PRESETS},
            "routes": {p: ms.route_template(kind) for p, kind in ms.ROUTE_PRESET.items()},
            "seeds": {n: ms.checklist_seed(n) for n in names}, "no_features_note": ms.NO_FEATURES_NOTE,
            "floors": {n: _floor_data(assets[n]) for n in names}}


def _area_floors(asset: hc.HeightAsset | None, walk: np.ndarray) -> dict:
    if asset is None:
        return {}
    coarse = asset.floors.reshape(-1, hc.MAX_FLOORS)
    multiple = np.flatnonzero(walk & (asset.floor_count().ravel() > 1))
    spans = {int(cell): [[int(z), int(z)] for z in coarse[cell] if z >= 0] for cell in multiple}
    # Refined assets remove the coarse column at these cells. Project child heights to the paint grid,
    # grouping nearby heights into one floor so slope differences across children do not add floors.
    if asset.fine_cells is not None:
        refined = defaultdict(set)
        for child, floors in zip(asset.fine_cells, asset.fine_floors):
            cell = coarse_of_fine(child)
            if walk[cell]:
                refined[cell].update(int(z) for z in floors if z >= 0)
        for cell, heights in refined.items():
            levels = []
            for z in sorted(heights):
                if not levels or z - levels[-1][1] >= hc.FLOOR_SEP_M * 10:
                    levels.append([z, z])
                else:
                    levels[-1][1] = z
            if len(levels) > 1:
                spans[cell] = levels
    origin = asset.origin_z
    return {
        str(cell): [[origin + lo - AREA_FLOOR_PADDING_DM, origin + hi + AREA_FLOOR_PADDING_DM]
                    for lo, hi in spans[cell]]
        for cell in sorted(spans)
    }


def _existing_areas(name: str, areas_dir: Path | None) -> dict | None:
    directory = Path(areas_dir) if areas_dir is not None else (
        Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-areas"
    )
    path = directory / f"{name}.areas.json"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        errors = areas.validate(obj)
        if errors:
            raise ValueError(errors[0])
        if obj["map"] != name:
            raise ValueError("map does not match the filename")
        return obj
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as exc:
        _LOG.warning("Ignoring tagger areas file %s: %s", path, exc)
        return None


def _areas_data(name: str, asset: hc.HeightAsset | None, areas_dir: Path | None) -> dict:
    walk = cg.load_base_geometry(name).walk.ravel()
    labels = areas.callouts(name)
    return {"map": name, "grid": cg.GRID, "walk": areas_draft.encode_cells(walk), "labels": labels,
            "draft": areas_draft.draft(name, walk, labels), "existing": _existing_areas(name, areas_dir),
            "floors": _area_floors(asset, walk)}


def areas_data(name: str, heights_dir: Path | None = None, areas_dir: Path | None = None) -> dict:
    """The draft, walk grid, world-height bands and any saved file for one map."""
    return _areas_data(name, _height_asset(name, cg.ASSET_DIR, heights_dir), areas_dir)


def build(tags: dict, lines_by_map: dict, names: list[str] | None = None, starts: dict | None = None) -> dict:
    names = names or sorted(p.stem for p in cg.MINIMAP_DIR.glob("*.png")
                            if p.stem in json.loads(cg.MAPS_JSON.read_text(encoding="utf-8")))
    maps = {name: map_data(name, (tags.get("maps") or {}).get(name) or {}, lines_by_map.get(name) or [])
            for name in names}
    for name in maps:
        maps[name]["chokes"] = choke_data(name)
    for name, rows in (starts or {}).items():
        if name in maps:   # [1 attack | 0 defense, x px, y px]
            maps[name]["starts"] = [[1 if side == "attack" else 0, round(x, 1), round(y, 1)] for side, x, y in rows]
    return maps


def render(
    tags: dict, maps: dict, heights_dir: Path | None = None, diagnostics: dict | None = None, *,
    areas_dir: Path | None = None,
) -> str:
    assets = {name: _height_asset(name, cg.ASSET_DIR, heights_dir) for name in maps}
    features = features_data(sorted(maps), assets=assets)
    if diagnostics is not None:
        name = diagnostics.get('map')
        if name not in maps or diagnostics.get('editor_entry') != tags.get('maps', {}).get(name):
            raise ValueError('Diagnostic report map/annotation identity does not match this page')
        if diagnostics.get('context', {}).get('image_sha') != maps[name]['image_sha']:
            raise ValueError('Diagnostic report image identity does not match the page image')
        floor = features['floors'][name]
        if diagnostics.get('height') != (floor.get('height_sha') or 'flat'):
            raise ValueError('Diagnostic report height does not match the page height context')
        if (diagnostics.get('schema'), diagnostics.get('compiler'), diagnostics.get('normalization')) != (ms.SCHEMA_VERSION, fi.FEATURE_COMPILER_VERSION, fi.FEATURE_NORMALIZATION_VERSION):
            raise ValueError('Diagnostic report versions are stale; regenerate it')
        features['diagnostics'] = {name: diagnostics}
    maps = {name: {**data, "areas": _areas_data(name, assets[name], areas_dir)} for name, data in maps.items()}
    data = json.dumps({"tags": tags, "maps": maps, "features": features},
                      separators=(",", ":")).replace("</", "<\\/")
    core = CORE_JS.read_text(encoding="utf-8")
    ui = FEATURES_JS.read_text(encoding="utf-8")
    areas_ui = AREAS_JS.read_text(encoding="utf-8")
    return (TEMPLATE.read_text(encoding="utf-8").replace("/*CORE*/", core).replace("/*FEATURES_UI*/", ui)
            .replace("/*AREAS_UI*/", areas_ui).replace("/*DATA*/null", data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="the page to write (default under %%TEMP%%)")
    parser.add_argument("--tags", type=Path, default=cg.ASSET_DIR / "tags.json", help="the tags.json to start from")
    parser.add_argument("--map", action="append", help="only this map (repeatable)")
    parser.add_argument("--starts", type=Path, help="round-start positions to draw, {map: [[side, x px, y px], ...]} "
                                                    "(players at t = 0 stand pressed against the barriers)")
    parser.add_argument("--heights-dir", type=Path, help="read each map's heights from an export of the database's "
                                                         "active asset (control_heights.py export)")
    parser.add_argument("--areas-dir", type=Path, help="read saved areas files from this directory (default %%TEMP%%\\valo-areas)")
    parser.add_argument('--diagnostics', type=Path, help='attach an exact compiler diagnostic report; identity/height mismatches are refused')
    args = parser.parse_args(argv)
    tags = json.loads(args.tags.read_text(encoding="utf-8"))
    lines = json.loads(KILL_LINES.read_text(encoding="utf-8"))["maps"] if KILL_LINES.is_file() else {}
    starts = json.loads(args.starts.read_text(encoding="utf-8")) if args.starts else None
    maps = build(tags, lines, args.map, starts)
    out = args.out or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-control-tagger" / "control-tagger.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    diagnostics = json.loads(args.diagnostics.read_text(encoding='utf-8')) if args.diagnostics else None
    try:
        page = render(tags, maps, args.heights_dir, diagnostics, areas_dir=args.areas_dir)
    except ValueError as exc:
        parser.error(str(exc))
    out.write_text(page, encoding="utf-8")
    print(f"{len(maps)} maps -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
