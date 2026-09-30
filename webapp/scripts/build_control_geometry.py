"""Builds map control's geometry assets (docs/replay-map-control-plan.md, Stage 1).

For every minimap in `app/static/img/maps/` (or `--map`), from it and its entry in
`app/static/data/control/tags.json`:

- `<Map>.sight.png` and `<Map>.walk.png` (1024 px, 1-bit) beside `tags.json`: the sight mask (walls,
  glyphs, cover; see-across opened) and the traversal mask (app/control/geometry.py);
- `<Map>.barrier.png`, when the map has a barrier paint: the buy-phase barrier lines;
- `index.json`: per map the mask hashes, walkable cells, cell size, the "cover not reviewed" badge
  (`cover_reviewed`), the specials, and the Risk 1 kill-line result from
  `tests/fixtures/control/kill_lines.json` (blocked share against the 2% bar), when the map has lines.

With `--bitsets`, also builds (or finds) each map's cell-to-cell visibility bitsets in the local cache
(`webapp/.control_cache/`, or `CONTROL_CACHE_DIR`). They are never committed (R2): about 11 MB a map,
and several minutes each to build.

    .venv313\\Scripts\\python.exe scripts\\build_control_geometry.py [--map Ascent] [--bitsets]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import geometry as cg  # noqa: E402

KILL_LINES = WEBAPP_ROOT / "tests" / "fixtures" / "control" / "kill_lines.json"
RISK1_BAR = 0.02


def kill_line_result(sight: np.ndarray, lines: list) -> dict:
    blocked = sum(cg.line_blocked(sight, (ax, ay), (bx, by)) for ax, ay, bx, by in lines)
    share = blocked / len(lines)
    return {"qualifying": len(lines), "blocked": blocked, "share": round(share, 4), "passes": share <= RISK1_BAR}


def build_map(name: str, entry: dict, lines: list | None, asset_dir: Path) -> dict:
    png = cg.MINIMAP_DIR / f"{name}.png"
    image_sha = hashlib.sha256(png.read_bytes()).hexdigest()[:12]
    if entry.get("image_sha") and entry["image_sha"] != image_sha:
        raise cg.GeometryError(f"{name}'s minimap changed since it was tagged ({entry['image_sha']} -> {image_sha})")
    rgba = np.array(Image.open(png).convert("RGBA"))
    m = cg.masks(rgba, entry)
    cg.write_mask_png(asset_dir / f"{name}.sight.png", m.sight)
    cg.write_mask_png(asset_dir / f"{name}.walk.png", m.walk)
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))[name]["xMultiplier"]
    geo = cg.geometry_from_masks(name, m.sight, m.walk, scale)
    row = {"image_sha": image_sha,
           "sight_sha": hashlib.sha256(np.packbits(m.sight).tobytes()).hexdigest()[:12],
           "walk_sha": hashlib.sha256(np.packbits(m.walk).tobytes()).hexdigest()[:12],
           "walkable_cells": int(geo.walk.sum()), "cell_m": round(geo.cell_m, 3),
           "tags": len(entry.get("tags") or []), "see_across_paint": bool(entry.get("see_across_paint")),
           "cover_reviewed": bool(entry.get("cover_reviewed")), "specials": entry.get("specials") or [],
           "kill_lines": kill_line_result(m.sight, lines) if lines else None}
    # The buy-phase barriers (their own mask; neither sight nor walk): only when painted.
    barrier_path = asset_dir / f"{name}.barrier.png"
    if entry.get("barrier_paint"):
        barrier = cg.unpack_paint(entry["barrier_paint"])
        cg.write_mask_png(barrier_path, barrier)
        row["barrier_sha"] = hashlib.sha256(np.packbits(barrier).tobytes()).hexdigest()[:12]
    elif barrier_path.is_file():
        barrier_path.unlink()
    # The Stage 6 paints, only when a map has any (so earlier entries stay as they were).
    paints = [key for key in ("cover_paint", "cant_walk_paint", "uncertain_paint", "barrier_paint") if entry.get(key)]
    if paints:
        row["paints"] = paints
    if entry.get("uncertain_paint"):
        row["uncertain_px"] = int(cg.unpack_paint(entry["uncertain_paint"]).sum())
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", action="append", help="only this map (repeatable); the index keeps the others")
    ap.add_argument("--bitsets", action="store_true", help="also build or find the local visibility bitsets")
    args = ap.parse_args()
    asset_dir = cg.ASSET_DIR
    tags = cg.load_tags(asset_dir)
    lines = json.loads(KILL_LINES.read_text(encoding="utf-8"))["maps"] if KILL_LINES.is_file() else {}
    maps_table = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))
    names = sorted(p.stem for p in cg.MINIMAP_DIR.glob("*.png") if p.stem in maps_table)
    wanted = args.map or names
    unknown = sorted(set(wanted) - set(names))
    if unknown:
        raise SystemExit(f"no minimap for {unknown}")
    index_path = asset_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"maps": {}}
    index.update({"version": 1, "geometry_version": cg.GEOMETRY_VERSION, "grid": cg.GRID, "px": cg.PX})
    for name in wanted:
        row = build_map(name, tags.get("maps", {}).get(name, {}), lines.get(name), asset_dir)
        index["maps"][name] = row
        kl = row["kill_lines"]
        verdict = f"kill lines {kl['blocked']}/{kl['qualifying']} blocked ({kl['share']:.1%})" if kl else "no kill lines"
        print(f"{name}: {row['walkable_cells']} walkable cells, {row['tags']} tags, {verdict}", flush=True)
        if args.bitsets:
            geo = cg.load_geometry(name, asset_dir)
            started = time.perf_counter()
            cg.visibility(geo)
            print(f"  visibility: {geo.visibility_source} in {time.perf_counter() - started:.1f}s "
                  f"({geo.rows.nbytes / 1e6:.1f} MB, key {cg.cache_key(geo)[:16]})", flush=True)
    index["maps"] = dict(sorted(index["maps"].items()))
    index_path.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
