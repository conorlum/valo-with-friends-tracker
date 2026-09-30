"""Stage 0a (docs/replay-map-control-plan.md): turn rough see-across brush strokes into a clean mask.

Feasibility tooling, not product code. A person marks a drop by painting roughly along the map's
edge. Here, every black pixel outside the map belongs to its nearest stretch of map edge, and is
see-across when that stretch was painted (within SLOP_PX). So the whole drop beyond a painted edge
opens, unpainted edges stay walls, and paint that spilled onto the floor doesn't matter.

Rewrites each map's `see_across_paint` in the tags file (256 x 256 cells of 4 px, a cell set when
any of it is see-across), keeps the stroke as `see_across_strokes`, and renders a check image.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\clean_paint.py <out_dir> <control-tags.json>
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

P, CELL = 256, 4
SLOP_PX = 3


def unpack(b64: str) -> np.ndarray:
    raw = np.frombuffer(base64.b64decode(b64), np.uint8)
    return np.unpackbits(raw, bitorder="little")[: P * P].reshape(P, P).astype(bool)


def pack(cells: np.ndarray) -> str:
    return base64.b64encode(np.packbits(cells.ravel().astype(np.uint8), bitorder="little").tobytes()).decode()


def clean(strokes: np.ndarray, walk: np.ndarray, labels: np.ndarray) -> np.ndarray:
    paint = strokes.repeat(CELL, 0).repeat(CELL, 1)
    outer = ~walk & (labels == 0)
    edge = outer & ndimage.binary_dilation(walk)
    painted_edge = edge & ndimage.binary_dilation(paint, iterations=SLOP_PX)
    _, (iy, ix) = ndimage.distance_transform_edt(~edge, return_indices=True)
    see = outer & painted_edge[iy, ix]
    # keep what was painted on inner black areas too (they are candidates, but paint also works there)
    see |= paint & ~walk & (labels > 0)
    return see


def main(out: Path, tags_path: Path) -> None:
    geo = out / "geometry"
    tags = json.loads(tags_path.read_text())
    maps_dir = Path(__file__).resolve().parents[2] / "app" / "static" / "img" / "maps"
    for name, entry in tags["maps"].items():
        strokes_b64 = entry.get("see_across_strokes") or entry.get("see_across_paint")
        if not strokes_b64:
            continue
        strokes = unpack(strokes_b64)
        walk = np.array(Image.open(geo / f"{name}.walk.png")) > 0
        enc = np.array(Image.open(geo / f"{name}.labels.png")).astype(np.int32)
        labels = enc[..., 0] + 256 * enc[..., 1]
        see = clean(strokes, walk, labels)
        # any see-across pixel sets its cell: paint over floor is harmless, and a majority rule
        # drops the 1-3 px rim of black right inside a drop's edge
        cells = see.reshape(P, CELL, P, CELL).any((1, 3))
        entry["see_across_strokes"] = strokes_b64
        entry["see_across_paint"] = pack(cells)
        entry["see_across_note"] = (f"cleaned by clean_paint.py: outside black pixels whose nearest map edge was painted "
                                    f"(slop {SLOP_PX} px); the raw brush strokes are see_across_strokes")
        img = np.array(Image.alpha_composite(Image.new("RGBA", (1024, 1024), (20, 20, 20, 255)),
                                             Image.open(maps_dir / f"{name}.png").convert("RGBA")).convert("RGB")).astype(float)
        mask = cells.repeat(CELL, 0).repeat(CELL, 1)
        img[mask] = img[mask] * 0.35 + np.array([190, 110, 255]) * 0.65
        Image.fromarray(img.astype(np.uint8)).save(out / f"{name}.see_across.png")
        print(f"{name}: strokes {int(strokes.sum())} cells -> see-across {int(cells.sum())} cells", flush=True)
    tags_path.write_text(json.dumps(tags, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
