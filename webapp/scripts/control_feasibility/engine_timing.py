"""Stage 0b groundwork (docs/replay-map-control-plan.md, Risk 2): what the core per-tick work costs.

Feasibility tooling, not product code, and not the control rules: it times the expensive parts
on one real round with the walls-only mask and no smokes.

1. Cell-to-cell visibility at 128 (one 360-degree raycast per walkable cell), packed bits.
2. Per tick: each alive player's 103-degree view, each team's watched cells, the enemy free
   space (flood fill), Safe = cells no free cell sees (OR of visibility rows).
3. The full counterfactual: per player, both teams' fills and Safe again without them.

    .venv313\\Scripts\\python.exe scripts\\control_feasibility\\engine_timing.py <out_dir> <bundle.json> <round> [ticks]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

G = 128
CELL = 1024 // G
FOV_HALF = 51.5


def cast(wall: np.ndarray, x: float, y: float, yaw: float, half: float, step_deg: float) -> np.ndarray:
    """Cells seen from (x, y) px within yaw +/- half, walls stop each ray."""
    angles = np.deg2rad(yaw + np.arange(-half, half + 1e-9, step_deg))
    dx, dy = np.cos(angles), np.sin(angles)
    seen = np.zeros((G, G), bool)
    live = np.ones(len(angles), bool)
    for s in range(0, 1500, 2):
        px = (x + dx * s).astype(np.int32)
        py = (y + dy * s).astype(np.int32)
        live &= (px >= 0) & (px < 1024) & (py >= 0) & (py < 1024)
        pxc, pyc = np.clip(px, 0, 1023), np.clip(py, 0, 1023)
        if s > 3:
            live &= ~wall[pyc, pxc]
        if not live.any():
            break
        seen[pyc[live] // CELL, pxc[live] // CELL] = True
    return seen


def visibility(wall: np.ndarray, walk: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cells = np.argwhere(walk)
    rows = np.zeros((len(cells), G * G // 8), np.uint8)
    for i, (cy, cx) in enumerate(cells):
        rows[i] = np.packbits(cast(wall, cx * CELL + CELL / 2, cy * CELL + CELL / 2, 0, 180, 0.5).ravel())
    index = -np.ones((G, G), np.int32)
    index[cells[:, 0], cells[:, 1]] = np.arange(len(cells))
    return rows, index


def free_space(walk: np.ndarray, watched: np.ndarray, sources: list[tuple[int, int]]) -> np.ndarray:
    open_ = walk & ~watched
    for cy, cx in sources:
        open_[cy, cx] = True  # a fill starts where they stand, watched or not
    lab, _ = ndimage.label(open_)
    ids = {lab[cy, cx] for cy, cx in sources} - {0}
    return np.isin(lab, list(ids))


def safe(free: np.ndarray, rows: np.ndarray, index: np.ndarray, walk: np.ndarray) -> np.ndarray:
    idx = index[free]
    idx = idx[idx >= 0]
    if not len(idx):
        return walk.copy()
    seen = np.unpackbits(np.bitwise_or.reduce(rows[idx], axis=0))[: G * G].reshape(G, G).astype(bool)
    return walk & ~seen


def decode(arr):
    return np.cumsum(np.asarray(arr, dtype=np.int64))


def main(out: Path, bundle_path: Path, round_n: str, n_ticks: int) -> None:
    bundle = json.loads(bundle_path.read_text())
    name = bundle["match"]["map"]
    blob = bundle["rounds"][round_n]
    walk_px = np.array(Image.open(out / "geometry" / f"{name}.walk.png")) > 0
    wall = ~walk_px
    walk = walk_px.reshape(G, CELL, G, CELL).mean((1, 3)) > 0.5
    started = time.time()
    rows, index = visibility(wall, walk)
    vis_s = time.time() - started
    print(f"{name}: {walk.sum()} walkable cells, visibility {vis_s:.0f}s, {rows.nbytes / 1e6:.1f} MB", flush=True)

    hz = blob["hz"]
    tracks = {int(s): [(g["t0"], decode(g["u"]), decode(g["v"]), np.mod(decode(g["yaw"]), 360)) for g in segs]
              for s, segs in blob["tracks"].items()}
    side = {p["slot"]: p["side"] for p in blob["players"]}
    lives = {int(s): v for s, v in blob["alive"].items()}

    def at(slot, t):
        for t0, u, v, y in tracks.get(slot, []):
            i = int(round((t - t0) * hz))
            if 0 <= i < len(u):
                return u[i] / 10000 * 1024, v[i] / 10000 * 1024, float(y[i])
        return None

    t_end = blob["t_decided"] or blob["t_end"]
    times = {"vision": 0.0, "base": 0.0, "counterfactual": 0.0}
    ticks = 0
    for t in np.linspace(2.0, t_end - 0.5, n_ticks):
        alive = [s for s, ivs in lives.items()
                 if any(a <= t < (b if b is not None else 1e9) for a, b, _ in ivs)]
        pos = {s: at(s, t) for s in alive}
        pos = {s: p for s, p in pos.items() if p is not None}
        t0 = time.perf_counter()
        view = {s: cast(wall, p[0], p[1], p[2], FOV_HALF, 0.5) for s, p in pos.items()}
        t1 = time.perf_counter()

        def team_state(team: str, removed: int | None = None) -> np.ndarray:
            enemy = "B" if team == "A" else "A"
            watched = np.zeros((G, G), bool)
            for s, v in view.items():
                if side.get(s) == team and s != removed:
                    watched |= v
            sources = [(int(p[1]) // CELL, int(p[0]) // CELL) for s, p in pos.items()
                       if side.get(s) == enemy and s != removed]
            if not sources:
                return walk.copy()
            return safe(free_space(walk, watched, sources), rows, index, walk)

        base = {team: team_state(team) for team in ("A", "B")}
        t2 = time.perf_counter()
        for s in pos:
            for team in ("A", "B"):
                team_state(team, removed=s)
        t3 = time.perf_counter()
        times["vision"] += t1 - t0
        times["base"] += t2 - t1
        times["counterfactual"] += t3 - t2
        ticks += 1
    per = {k: v / ticks * 1000 for k, v in times.items()}
    round_s = t_end
    result = {"map": name, "round": round_n, "walkable_cells": int(walk.sum()), "visibility_build_s": round(vis_s, 1),
              "visibility_mb": round(rows.nbytes / 1e6, 1), "ticks_timed": ticks, "ms_per_tick": {k: round(v, 1) for k, v in per.items()},
              "round_seconds": round(round_s, 1)}
    for hz_cf in (16, 2, 1):
        base_s = (per["vision"] + per["base"]) / 1000 * 16 * round_s
        cf_s = per["counterfactual"] / 1000 * hz_cf * round_s
        result[f"round_cost_s_cf_{hz_cf}hz"] = round(base_s + cf_s, 1)
    print(json.dumps(result, indent=1), flush=True)
    (out / f"engine_timing_{name}_r{round_n}.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 60)
