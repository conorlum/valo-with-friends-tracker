"""The map-control drawing page (docs/replay-map-control-plan.md, Stage 6; R2: a local tool, not a site page).

    .venv313\\Scripts\\python.exe scripts\\control_tagger.py [--out <file>] [--tags <tags.json>] [--heights-dir <dir>]

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
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
from PIL import Image

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import geometry as cg  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.replays import choke_assets  # noqa: E402
from app.replays import map_feature_schema as ms  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "control_tagger.template.html"
CORE_JS = HERE / "control_tagger_core.js"
FEATURES_JS = HERE / "control_tagger_features.js"
KILL_LINES = WEBAPP_ROOT / "tests" / "fixtures" / "control" / "kill_lines.json"


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
            "lines": lines}


def choke_data(name: str, asset_dir: Path = choke_assets.ASSET_DIR) -> dict:
    """The map's `<Map>.chokes.json` as the page edits it: {"chokes": [...], "next_id"} (none: an empty list)."""
    chokes = choke_assets.load(name, asset_dir) or []
    return {"chokes": [asdict(c) for c in chokes], "next_id": choke_assets.load_next_id(name, asset_dir)}


def floor_data(name: str, asset_dir: Path = cg.ASSET_DIR, heights_dir: Path | None = None) -> dict | None:
    """Actual asset floor counts and unresolved cells for automatic preview. From `heights_dir` when it
    holds `<Map>.height.npz` (the active asset, written by `control_heights.py export`: a map's heights live in
    the database once the replay worker builds them), else the committed asset. None for a map without heights:
    preview explicitly uses flat placement."""
    exported = None if heights_dir is None else Path(heights_dir) / f"{name}.height.npz"
    if exported is not None and exported.is_file():
        asset = hc.load_asset(exported)
    else:
        index_path = asset_dir / "index.json"
        entry = (json.loads(index_path.read_text(encoding="utf-8")).get("maps", {}).get(name) or {}) if index_path.is_file() else {}
        if not entry.get("height_sha"):
            return {'flat': True, 'height_sha': None}
        asset = hc.load_asset(asset_dir / f"{name}.height.npz")
    return {"flat": False, "height_sha": asset.digest, "origin_z": asset.origin_z, "max_floors": hc.MAX_FLOORS,
            'floor_counts': (np.isfinite(asset.floors) & (asset.floors >= 0)).sum(axis=2).ravel().tolist(),
            'unresolved': asset.unresolved.astype('uint8').ravel().tolist(),
            "floors": base64.b64encode(np.ascontiguousarray(asset.floors, dtype="<i2").tobytes()).decode("ascii")}


def features_data(names: list[str], heights_dir: Path | None = None) -> dict:
    """What the features panel needs from the Python contract (app/replays/map_feature_schema.py), so the page
    never keeps its own copy: the schema version, the presets and route templates, each map's checklist seed and
    its floor data (from `heights_dir` when given, see floor_data)."""
    return {"schema_version": ms.SCHEMA_VERSION, "presets": {p: ms.preset(p) for p in ms.PRESETS},
            "routes": {p: ms.route_template(kind) for p, kind in ms.ROUTE_PRESET.items()},
            "seeds": {n: ms.checklist_seed(n) for n in names}, "no_features_note": ms.NO_FEATURES_NOTE,
            "floors": {n: floor_data(n, heights_dir=heights_dir) for n in names}}


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


def render(tags: dict, maps: dict, heights_dir: Path | None = None) -> str:
    data = json.dumps({"tags": tags, "maps": maps, "features": features_data(sorted(maps), heights_dir)},
                      separators=(",", ":")).replace("</", "<\\/")
    core = CORE_JS.read_text(encoding="utf-8")
    ui = FEATURES_JS.read_text(encoding="utf-8")
    return (TEMPLATE.read_text(encoding="utf-8").replace("/*CORE*/", core).replace("/*FEATURES_UI*/", ui)
            .replace("/*DATA*/null", data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="the page to write (default under %%TEMP%%)")
    parser.add_argument("--tags", type=Path, default=cg.ASSET_DIR / "tags.json", help="the tags.json to start from")
    parser.add_argument("--map", action="append", help="only this map (repeatable)")
    parser.add_argument("--starts", type=Path, help="round-start positions to draw, {map: [[side, x px, y px], ...]} "
                                                    "(players at t = 0 stand pressed against the barriers)")
    parser.add_argument("--heights-dir", type=Path, help="read each map's heights from an export of the database's "
                                                         "active asset (control_heights.py export)")
    args = parser.parse_args(argv)
    tags = json.loads(args.tags.read_text(encoding="utf-8"))
    lines = json.loads(KILL_LINES.read_text(encoding="utf-8"))["maps"] if KILL_LINES.is_file() else {}
    starts = json.loads(args.starts.read_text(encoding="utf-8")) if args.starts else None
    maps = build(tags, lines, args.map, starts)
    out = args.out or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-control-tagger" / "control-tagger.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(tags, maps, args.heights_dir), encoding="utf-8")
    print(f"{len(maps)} maps -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
