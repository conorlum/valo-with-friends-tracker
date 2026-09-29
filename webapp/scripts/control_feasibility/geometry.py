"""Stage 0a (docs/replay-map-control-plan.md, Q53): alpha masks and cover candidates for every map.

Feasibility tooling, not product code. For each minimap `app/static/img/maps/<Map>.png`:

- **glyph:** opaque pixels with strong colour (saturation > GLYPH_SATURATION), such as Bind's
  green teleporter lanes. They are treated as void: not walkable, not seen across.
- **sight walls / traversal:** alpha 0 plus glyphs. (Tagged cover is added in Stage 1.)
- **candidates:** shapes a person tags as cover, see-over, walkable or glyph:
  - *closed:* a floor region cut off from the main floor by drawn lines, with its outline;
    nested outlines merge into one candidate;
  - *line:* drawn lines away from the map edge (more than EDGE_PX from the void) that aren't
    part of a closed candidate: thin walls, railings, ledges, elevation steps.

Writes `<out>/geometry/<Map>.labels.png` (candidate id per pixel in R + 256*G, 0 = none),
`<Map>.json` (candidate list and mask summary) and `<Map>.render.png` (for the report).

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\geometry.py <out_dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

MAPS_DIR = Path(__file__).resolve().parents[2] / "app" / "static" / "img" / "maps"
GLYPH_SATURATION = 40
LINE_LUM = 175
EDGE_PX = 3
MIN_CLOSED_PX = 4
MIN_LINE_PX = 12
# A closed shape this small that touches the edge band is anti-aliasing on a curved wall.
MIN_EDGE_CLOSED_PX = 60


def masks(rgba: np.ndarray) -> dict[str, np.ndarray]:
    rgb = rgba[..., :3].astype(int)
    opaque_raw = rgba[..., 3] > 0
    saturation = rgb.max(-1) - rgb.min(-1)
    glyph = opaque_raw & (saturation > GLYPH_SATURATION)
    opaque = opaque_raw & ~glyph
    line = opaque & (rgb.mean(-1) >= LINE_LUM)
    return {"opaque": opaque, "glyph": glyph, "line": line, "floor": opaque & ~line}


def candidates(m: dict[str, np.ndarray]) -> tuple[np.ndarray, list[dict]]:
    floor, line, opaque = m["floor"], m["line"], m["opaque"]
    lab, n = ndimage.label(floor)
    sizes = ndimage.sum(floor, lab, range(1, n + 1))
    main = int(np.argmax(sizes)) + 1
    small = [i + 1 for i, s in enumerate(sizes) if i + 1 != main and s >= MIN_CLOSED_PX]
    enclosed = np.isin(lab, small)
    closed = ndimage.binary_dilation(enclosed, iterations=2) & (line | enclosed)
    closed_lab, n_closed = ndimage.label(closed, structure=np.ones((3, 3)))
    near_void = ndimage.binary_dilation(~opaque, iterations=EDGE_PX)
    loose = line & ~closed & ~near_void
    loose_lab, n_loose = ndimage.label(loose, structure=np.ones((3, 3)))
    loose_sizes = ndimage.sum(loose, loose_lab, range(1, n_loose + 1))
    labels = np.zeros(floor.shape, np.int32)
    out: list[dict] = []

    def add(mask: np.ndarray, kind: str) -> None:
        ys, xs = np.nonzero(mask)
        cid = len(out) + 1
        labels[mask] = cid
        interior = int((mask & floor).sum())
        out.append({"id": cid, "kind": kind, "px": int(mask.sum()), "interior_px": interior,
                    "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                    "centre": [round(float(xs.mean()), 1), round(float(ys.mean()), 1)]})

    for i, sl in enumerate(ndimage.find_objects(closed_lab), start=1):
        mask = np.zeros_like(closed)
        mask[sl] = closed_lab[sl] == i
        if mask.sum() < MIN_EDGE_CLOSED_PX and (mask & near_void).any():
            continue  # anti-aliasing specks along a curved wall, not a shape
        add(mask, "closed")
    for i, sl in enumerate(ndimage.find_objects(loose_lab), start=1):
        if loose_sizes[i - 1] < MIN_LINE_PX:
            continue
        mask = np.zeros_like(loose)
        mask[sl] = loose_lab[sl] == i
        add(mask, "line")
    return labels, out


def render(rgba: np.ndarray, m: dict[str, np.ndarray], labels: np.ndarray, cands: list[dict]) -> Image.Image:
    base = np.zeros(rgba.shape[:2] + (3,), np.uint8)
    base[m["opaque"]] = (70, 70, 70)
    base[m["glyph"]] = (120, 40, 140)
    colours = {"closed": (40, 110, 230), "line": (240, 170, 30)}
    for c in cands:
        base[labels == c["id"]] = colours[c["kind"]]
    return Image.fromarray(base)


def main(out_dir: Path) -> None:
    geo = out_dir / "geometry"
    geo.mkdir(parents=True, exist_ok=True)
    index = {}
    for png in sorted(MAPS_DIR.glob("*.png")):
        name = png.stem
        rgba = np.array(Image.open(png).convert("RGBA"))
        m = masks(rgba)
        labels, cands = candidates(m)
        enc = np.zeros(labels.shape + (3,), np.uint8)
        enc[..., 0] = labels & 255
        enc[..., 1] = (labels >> 8) & 255
        Image.fromarray(enc).save(geo / f"{name}.labels.png")
        render(rgba, m, labels, cands).save(geo / f"{name}.render.png")
        Image.fromarray((m["opaque"] * 255).astype(np.uint8)).save(geo / f"{name}.walk.png")
        digest = hashlib.sha256(png.read_bytes()).hexdigest()[:12]
        summary = {"map": name, "image_sha": digest,
                   "params": {"glyph_saturation": GLYPH_SATURATION, "line_lum": LINE_LUM, "edge_px": EDGE_PX,
                              "min_closed_px": MIN_CLOSED_PX, "min_line_px": MIN_LINE_PX,
                              "min_edge_closed_px": MIN_EDGE_CLOSED_PX},
                   "opaque_px": int(m["opaque"].sum()), "glyph_px": int(m["glyph"].sum()),
                   "line_px": int(m["line"].sum()),
                   "closed": sum(c["kind"] == "closed" for c in cands),
                   "lines": sum(c["kind"] == "line" for c in cands), "candidates": cands}
        (geo / f"{name}.json").write_text(json.dumps(summary))
        index[name] = {k: summary[k] for k in ("image_sha", "opaque_px", "glyph_px", "closed", "lines")}
        print(f"{name}: {summary['closed']} closed, {summary['lines']} line candidates, "
              f"{summary['glyph_px']} glyph px", flush=True)
    (geo / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
