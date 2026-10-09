"""Validates a worker height asset against this deploy's map and check set in a child process.

    <python> -m app.control.height_verify --map Ascent --asset-dir <dir> --must-block <file> < asset.npz

Raw arrays are checked before load_asset can cast or reshape them. The description then derives the real
walkable count, readiness and must-block outcomes; a report cannot declare those facts itself. The web app
uses app/services/control_heights.py without importing numpy or app.control in its own process.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


def describe(data: bytes, map_name: str, *, asset_dir: Path | None = None, must_block: Path | None = None) -> dict:
    try:
        import numpy as np
        from app.control import geometry as cg, height_build as hb, heights as hc
        from app.replays import height_inputs as hi

        directory = Path(asset_dir or hi.CONTROL_DIR)
        geo = cg.load_base_geometry(map_name, directory)
        walk = geo.walk
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "asset.height.npz"
            path.write_bytes(data)
            with np.load(path, allow_pickle=False) as raw:
                shapes = {"floors": (cg.GRID, cg.GRID, hc.MAX_FLOORS),
                          "spread": (cg.GRID, cg.GRID, hc.MAX_FLOORS),
                          "supported": (cg.GRID, cg.GRID), "unresolved": (cg.GRID, cg.GRID),
                          "kind": (cg.GRID, cg.GRID)}
                dtypes = {"floors": np.dtype("<i2"), "spread": np.dtype("<i2"), "supported": np.dtype(bool),
                          "unresolved": np.dtype(bool), "kind": np.dtype("i1"), "edges": np.dtype("<i4")}
                arrays = {name: raw[name] for name in dtypes}
                for name, shape in shapes.items():
                    if arrays[name].shape != shape:
                        raise ValueError(f"{name} has shape {arrays[name].shape}, not {shape}")
                for name, dtype in dtypes.items():
                    if arrays[name].dtype != dtype:
                        raise ValueError(f"{name} has dtype {arrays[name].dtype}, not {dtype}")
                edges = arrays["edges"]
                if edges.ndim != 2 or edges.shape[1] != 5:
                    raise ValueError(f"edges has shape {edges.shape}, not K x 5")
                meta = json.loads(bytes(raw["meta"]).decode("utf-8"))
                if not isinstance(meta, dict) or type(meta.get("origin_z")) is not int \
                        or meta.get("units") != "dm" or meta.get("stand_m") != hc.STAND_M:
                    raise ValueError("invalid height frame or units")
                floors, spread = arrays["floors"], arrays["spread"]
                has = floors >= 0
                if (floors < -1).any() or (has[..., 1:] & ~has[..., :-1]).any() \
                        or (has[..., 1:] & (floors[..., 1:] <= floors[..., :-1])).any():
                    raise ValueError("floors must be contiguous, increasing, and use -1 for missing")
                if (spread < 0).any():
                    raise ValueError("negative floor spread")
                supported, unresolved, kind = arrays["supported"], arrays["unresolved"], arrays["kind"]
                if (has[..., 0] & ~walk).any() or (supported & (~walk | ~has[..., 0])).any() \
                        or (unresolved & (~walk | has[..., 0])).any():
                    raise ValueError("floor/support/unresolved cells disagree with the walk mask or floors")
                if not np.isin(kind, [hc.KIND_NONE, hc.KIND_STANDS, hc.KIND_WALKS, hc.KIND_FILLED,
                                     hc.KIND_GRADIENT]).all():
                    raise ValueError("unknown ground kind")
                flat = floors.reshape(-1, hc.MAX_FLOORS)
                if len(edges):
                    a, fa, b, fb, how = edges.T
                    if ((a < 0) | (a >= cg.GRID ** 2) | (b < 0) | (b >= cg.GRID ** 2)).any():
                        raise ValueError("edge cell out of bounds")
                    if ((fa < 0) | (fa >= hc.MAX_FLOORS) | (fb < 0) | (fb >= hc.MAX_FLOORS)).any():
                        raise ValueError("edge floor out of bounds")
                    if ((flat[a, fa] < 0) | (flat[b, fb] < 0)).any():
                        raise ValueError("edge names a missing floor")
                    if ((a == b) | (np.abs(a % cg.GRID - b % cg.GRID) > 1)
                            | (np.abs(a // cg.GRID - b // cg.GRID) > 1)).any():
                        raise ValueError("edge cells are not distinct eight-neighbours")
                    if not np.isin(how, [hc.EDGE_STEP, hc.EDGE_SLIDE, hc.EDGE_FALL]).all():
                        raise ValueError("unknown edge kind")
                    if len(np.unique(edges[:, :4], axis=0)) != len(edges):
                        raise ValueError("duplicate edge endpoints")
            asset = hc.load_asset(path)  # version check, after validation without lossy casts/reshapes
        count = asset.floor_count().ravel()
        share, reasons = hb.readiness(asset.supported.ravel(), asset.unresolved.ravel(), count, int(walk.sum()))
        lines, sha = hi.must_block_set(must_block)
        must = {"set": sha, **hb.must_block_check(lines, cg.attach_heights(geo, asset), map_name)}
        return {"digest": asset.digest, "version": int(asset.meta["version"]), "map": map_name,
                "walkable_cells": int(walk.sum()), "supported_cells": int(asset.supported.sum()),
                "floor_cells": int((count > 0).sum()), "supported": round(share, 4),
                "ready": not reasons, "not_ready": reasons, "must_block": must}
    except Exception as error:  # noqa: BLE001 - unreadable asset, map or check set: trust nothing
        return {"error": f"{type(error).__name__}: {error}"[:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--must-block", type=Path, required=True)
    args = parser.parse_args()
    sys.stdout.write(json.dumps(describe(sys.stdin.buffer.read(), args.map, asset_dir=args.asset_dir,
                                       must_block=args.must_block)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
