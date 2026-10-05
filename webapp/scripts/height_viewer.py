"""The height viewer: a read-only page for checking a map's heights by eye
(docs/superpowers/plans/2026-10-05-height-viewer.md; the asset: app/control/heights.py).

    .venv\\Scripts\\python.exe scripts\\height_viewer.py [--map Sunset] [--dir <folder>] [--committed] [--out <file>]

By default it reads every `<Map>.height.npz` (and its `<Map>.height.json` report) in the preview folder
that `build_control_heights.py --preview --out <dir>` writes (default %TEMP%\\valo-replay\\heights-preview).
With --committed it reads the committed assets instead: the maps whose index.json entry has a height_sha.
It writes one self-contained page (default %TEMP%\\valo-height-viewer\\height-viewer.html, never the
repository) and changes nothing else: no asset, no index.json, no tags, no database.

On the page: heights over the minimap (the build picture's colours), hover for each cell's floors, click to
set a reference and read every other floor as higher or lower than it, a same-height layer, the unresolved
areas with the ones that block readiness first, and the kill lines the heights would block.
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
from scipy import ndimage

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control import geometry as cg  # noqa: E402
from app.control import height_build as hb  # noqa: E402
from app.control import heights as hc  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "height_viewer.template.html"
CORE_JS = HERE / "height_viewer_core.js"
TEMP = Path(os.environ.get("TEMP") or tempfile.gettempdir())
DEFAULT_DIR = TEMP / "valo-replay" / "heights-preview"
DEFAULT_OUT = TEMP / "valo-height-viewer" / "height-viewer.html"
EIGHT = np.ones((3, 3), bool)   # 8-connected, as height_build labels areas


def rle(values: np.ndarray) -> list[int]:
    """[value, run, value, run, ...] over the flat array (control_tagger.rle)."""
    flat = np.asarray(values).ravel()
    if not len(flat):
        return []
    starts = np.r_[0, np.flatnonzero(flat[1:] != flat[:-1]) + 1]
    runs = np.diff(np.r_[starts, len(flat)])
    out = np.empty(2 * len(starts), np.int64)
    out[0::2], out[1::2] = flat[starts], runs
    return out.tolist()


def areas(asset: hc.HeightAsset, report: dict | None) -> list[dict]:
    """The unresolved cells as areas, labelled exactly as height_build.readiness labels them, blocking ones
    first: an area blocks when it has more than UNRESOLVED_MAX cells and touches (8-connected) a cell with
    two or more floors. Reasons come from the report's area with the same size and bbox, if any."""
    count = asset.floor_count().ravel()
    by_two = ndimage.binary_dilation((count >= 2).reshape(cg.GRID, cg.GRID), EIGHT).ravel()
    lab, n = ndimage.label(asset.unresolved, EIGHT)
    lab = lab.ravel()
    known = {(a["cells"], tuple(a["bbox"])): a["why"] for a in (report or {}).get("unresolved_areas", [])}
    out = []
    for k in range(1, n + 1):
        cells = np.flatnonzero(lab == k)
        bbox = hb._bbox(cells)
        out.append({"cells": cells.tolist(), "size": int(len(cells)), "bbox": bbox,
                    "blocking": bool(len(cells) > hc.UNRESOLVED_MAX and by_two[cells].any()),
                    "why": known.get((int(len(cells)), tuple(bbox)), {})})
    out.sort(key=lambda a: (not a["blocking"], -a["size"], a["bbox"]))
    return out


SHAPES = {"floors": (cg.GRID, cg.GRID, hc.MAX_FLOORS), "spread": (cg.GRID, cg.GRID, hc.MAX_FLOORS),
          "supported": (cg.GRID, cg.GRID), "unresolved": (cg.GRID, cg.GRID)}


def load_checked(asset_path: Path) -> hc.HeightAsset:
    """The asset, or ValueError: load_asset checks the version but not the shapes."""
    try:
        asset = hc.load_asset(asset_path)
    except Exception as exc:   # BadZipFile, a truncated file, a wrong version: not a usable asset
        raise ValueError(f"{asset_path.name} is not a readable height asset ({exc})") from exc
    for field_name, shape in SHAPES.items():
        if getattr(asset, field_name).shape != shape:
            raise ValueError(f"{asset_path.name}: {field_name} has shape {getattr(asset, field_name).shape}, "
                             f"not {shape}")
    return asset


def usable_report(wrapper, digest: str) -> tuple[dict | None, str | None]:
    """The build's report when it belongs to this asset (its wrapper's height_sha is the asset's digest),
    else None and why. A wrong report must never put its numbers on the page."""
    if wrapper is None:
        return None, "no report"
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("height"), dict) or "height_sha" not in wrapper:
        return None, "not a report (expected {height_sha, height})"
    if wrapper["height_sha"] != digest:
        return None, f"report is for another build ({wrapper['height_sha']}, this asset is {digest})"
    return wrapper["height"], None


def map_payload(name: str, asset_path: Path, wrapper, kind: str, asset_dir: Path = cg.ASSET_DIR) -> dict:
    """Everything the page shows for one map. `wrapper` is the preview .json or the index.json entry (both
    {"height_sha", "height"}), or None. Raises ValueError for an unusable asset, and OSError/KeyError/ValueError
    when the map's committed geometry can't be read."""
    asset = load_checked(asset_path)
    report, report_note = usable_report(wrapper, asset.digest)
    sight = cg.read_mask_png(asset_dir / f"{name}.sight.png")
    walk_px = cg.read_mask_png(asset_dir / f"{name}.walk.png")
    scale = json.loads(cg.MAPS_JSON.read_text(encoding="utf-8"))[name]["xMultiplier"]
    geo = cg.geometry_from_masks(name, sight, walk_px, scale)
    walk_sha = hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12]
    if report is not None:
        summary = {"source": "report", "matches": report.get("matches", asset.meta.get("matches")),
                   "rounds": report.get("rounds", asset.meta.get("rounds")), "supported": report.get("supported"),
                   "ready": report.get("ready"), "not_ready": report.get("not_ready") or []}
        kill_lines = (report.get("kill_lines") or {}).get("examples", [])
    else:   # the build's own rule, against the walk mask as it is now
        share, not_ready = hb.readiness(asset.supported.ravel(), asset.unresolved.ravel(),
                                        asset.floor_count().ravel(), int(geo.walk.sum()))
        summary = {"source": "recomputed", "matches": asset.meta.get("matches"), "rounds": asset.meta.get("rounds"),
                   "supported": round(share, 4), "ready": not not_ready, "not_ready": not_ready}
        kill_lines = None
    return {
        "name": name, "kind": kind,
        "image": base64.b64encode((cg.MINIMAP_DIR / f"{name}.png").read_bytes()).decode("ascii"),
        "cell_m": round(geo.cell_m, 4), "origin_z": asset.origin_z, "max_floors": hc.MAX_FLOORS,
        "step_up_m": hc.STEP_UP_M, "supported_min": hc.HEIGHT_SUPPORTED_MIN,
        "floors": asset.floors.astype(int).ravel().tolist(),     # dm above origin_z, -1 none, cell-major
        "spread": asset.spread.astype(int).ravel().tolist(),
        "supported": rle(asset.supported.astype(np.uint8)),
        "unresolved": rle(asset.unresolved.astype(np.uint8)),
        "walk": rle(geo.walk.astype(np.uint8)),
        "areas": areas(asset, report),
        "kill_lines": kill_lines,
        "summary": summary, "report_note": report_note,
        "height_sha": asset.digest,
        "walk_stale": asset.meta.get("walk_sha") not in (None, walk_sha),
        "preview_min_matches": asset.meta.get("preview_min_matches"),
    }
