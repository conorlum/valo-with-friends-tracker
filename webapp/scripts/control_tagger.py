"""The map-control drawing page (docs/replay-map-control-plan.md, Stage 6; R2: a local tool, not a site page).

    .venv313\\Scripts\\python.exe scripts\\control_tagger.py [--out <file>] [--tags <tags.json>]

Builds one self-contained page (default `%TEMP%\\valo-control-tagger\\control-tagger.html`, never the repo)
with every minimap, its candidate shapes (app/control/geometry.py `candidates`, with the map's own detector
params), the current `app/static/data/control/tags.json` and the Risk 1 kill lines
(`tests/fixtures/control/kill_lines.json`). On the page:

- tag candidates cover, see-over, walkable, glyph or see-across (or untag them);
- paint and erase see-across, cover, can't-walk and uncertain zones with a brush (geometry.masks: cover paint
  blocks sight and walking, can't-walk only walking, uncertain neither), and the buy-phase barrier lines
  (their own mask: the engine gives each team its side of them when they drop);
- watch the kill-line test live against the 2% bar, with the blocked lines drawn, and the resulting sight and
  walk masks as overlays; undo; tick "cover reviewed" (which clears the map's badge);
- export the whole `tags.json` (every map, unknown fields kept).

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
from pathlib import Path

import numpy as np
from PIL import Image

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import geometry as cg  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "control_tagger.template.html"
CORE_JS = HERE / "control_tagger_core.js"
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


def build(tags: dict, lines_by_map: dict, names: list[str] | None = None) -> dict:
    names = names or sorted(p.stem for p in cg.MINIMAP_DIR.glob("*.png")
                            if p.stem in json.loads(cg.MAPS_JSON.read_text(encoding="utf-8")))
    return {name: map_data(name, (tags.get("maps") or {}).get(name) or {}, lines_by_map.get(name) or [])
            for name in names}


def render(tags: dict, maps: dict) -> str:
    data = json.dumps({"tags": tags, "maps": maps}, separators=(",", ":")).replace("</", "<\\/")
    core = CORE_JS.read_text(encoding="utf-8")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*CORE*/", core).replace("/*DATA*/null", data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="the page to write (default under %%TEMP%%)")
    parser.add_argument("--tags", type=Path, default=cg.ASSET_DIR / "tags.json", help="the tags.json to start from")
    parser.add_argument("--map", action="append", help="only this map (repeatable)")
    args = parser.parse_args(argv)
    tags = json.loads(args.tags.read_text(encoding="utf-8"))
    lines = json.loads(KILL_LINES.read_text(encoding="utf-8"))["maps"] if KILL_LINES.is_file() else {}
    maps = build(tags, lines, args.map)
    out = args.out or Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "valo-control-tagger" / "control-tagger.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(tags, maps), encoding="utf-8")
    print(f"{len(maps)} maps -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
