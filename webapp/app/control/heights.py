"""Map heights for map control: the constants and the per-map asset
(docs/superpowers/specs/2026-10-01-control-heights-design.md, parts 3 and 4).

A map's heights are `<Map>.height.npz` beside its sight and walk masks, built from the stored rounds by
scripts/build_control_heights.py (app/control/height_build.py) and committed like the masks. A map without
one stays flat 2D, exactly as before.

**The reference height.** Every height is a player's position z, not their feet: a floor's height is the z
of a player standing on it. An eye is position-z + EYE_M, a body is position-z + BODY_M, and the ground
under a floor (what blocks a sight line) is position-z - STAND_M.

**The asset** (all heights in whole decimetres above `origin_z`, the map's lowest floor in world dm):

- `floors`, GRID x GRID x MAX_FLOORS int16: each cell's floors, lowest first, -1 for none;
- `spread`, same shape: each floor's 10th-to-90th percentile spread;
- `supported`, GRID x GRID bool: the cell's floors come from enough stands (not filled from neighbours);
- `unresolved`, GRID x GRID bool: a walkable cell the build couldn't resolve, which uses today's flat
  sight and walking;
- `edges`, K x 4 int32 (cell a, floor a, cell b, floor b): a walk from floor a of cell a to floor b of the
  neighbouring cell b, one row per direction (a drop is one row);
- `meta`: `origin_z`, the units, STAND_M, the build's counts and the walk mask's hash it was built on.

Every value here is a start value (the spec's "Open questions"): tuned on real data, each change reported
with numbers. This module imports nothing from app.control, so geometry.py can read it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HEIGHT_VERSION = 1
MAX_FLOORS = 3

# Every value from here to KILL_SAMPLE_S is a start value (approved 2026-10-02, to be tuned on real
# data). STAND_APEX_S, PLATFORMS, PLATFORM_R_M and NODE_SNAP_M are not in the spec: they fill gaps it left.

# --- sight (part 4)
EYE_M = 0.7                # eye above the position (about 1.6 m above the feet)
BODY_M = 0.3               # the body point a viewer has to see (about 1.2 m above the feet)
STAND_M = 0.9              # the position above the feet: the ground under a floor is this far below it
DEVICE_EYE_M = 0.0         # a camera's or turret's eye above its own z
LEDGE_M = 0.1              # ground is lowered this much as an obstacle, so a flat floor doesn't hide itself
NODE_SNAP_M = 0.5          # a z this far below a floor still stands on it (crouching, a sample mid-step)

# --- the build (part 3)
STAND_S = 0.3              # a stand is at least this long ...
STAND_TOL_M = 0.15         # ... with z this close to the run's median
STAND_APEX_S = 0.6         # a stand shorter than this with lower ground on both sides is a jump's top
FLOOR_TOL_M = 0.5          # a floor's stands lie this close to its median
FLOOR_SEP_M = 2.0          # two floors of one cell are at least this far apart
FLOOR_MIN_STANDS = 5
FLOOR_MIN_ROUNDS = 3
FLOOR_MIN_MATCHES = 2
FLOOR_SPREAD_MAX_M = 0.8   # a floor whose 10th-90th percentile spread is wider is unresolved (a steep ramp)
FILL_R = 2                 # an unsampled cell looks this many cells away (by walking) for floors
FILL_MIN_NEIGHBOURS = 2
FILL_TOL_M = 0.5
CONNECT_S = 1.0            # two stands this close in time on neighbouring cells are a walk between them
CONNECT_MIN_ROUNDS = 2
STEP_UP_M = 0.7            # neighbouring floors this close connect both ways; a bigger step needs to be seen
PLATFORM_R_M = 6.0         # stands this close to a live temporary platform are dropped
# `<code>_<name>` of what players can stand on for a while (the viewer's ability catalog, replay.js)
PLATFORMS = ("Thorne_E_Wall_Fortifying", "Thorne_E_Wall_Segment_Fortifying")

# --- readiness
HEIGHT_SUPPORTED_MIN = 0.60   # share of walkable cells with a supported floor
UNRESOLVED_MAX = 12           # the largest unresolved area (cells) allowed to touch a cell with two floors

# --- trips (part 4)
TRIP_HIT_M = 0.1           # a wire stops where the ground comes this close to it
TRIP_REACH_M = 10.0

# --- the kill-line check
KILL_LINE_BAR = 0.02
KILL_SAMPLE_S = 0.25       # killer and victim each need a position with z this close to the kill


class HeightError(ValueError):
    pass


@dataclass
class HeightAsset:
    floors: np.ndarray          # GRID x GRID x MAX_FLOORS int16, dm above origin_z, -1 none
    spread: np.ndarray          # same shape, dm
    supported: np.ndarray       # GRID x GRID bool
    unresolved: np.ndarray      # GRID x GRID bool
    edges: np.ndarray           # K x 4 int32: cell a, floor a, cell b, floor b
    meta: dict = field(default_factory=dict)

    @property
    def origin_z(self) -> int:
        return int(self.meta.get("origin_z", 0))

    @property
    def digest(self) -> str:
        """12 hex over everything the engine reads, in a fixed order (not the file's bytes)."""
        h = hashlib.sha256()
        h.update(json.dumps({"v": HEIGHT_VERSION, "origin_z": self.origin_z, "units": self.meta.get("units", "dm"),
                             "stand_m": self.meta.get("stand_m", STAND_M)}, sort_keys=True).encode("ascii"))
        for name, kind in (("floors", "<i2"), ("unresolved", "u1"), ("edges", "<i4")):
            h.update(name.encode("ascii") + b"\0" + np.ascontiguousarray(getattr(self, name), dtype=kind).tobytes())
        return h.hexdigest()[:12]

    def floor_count(self) -> np.ndarray:
        """GRID x GRID: how many floors each cell has."""
        return (self.floors >= 0).sum(-1)


def save_asset(path: Path, asset: HeightAsset) -> None:
    meta = {"version": HEIGHT_VERSION, "units": "dm", "stand_m": STAND_M, **asset.meta}
    np.savez_compressed(path, floors=asset.floors.astype("<i2"), spread=asset.spread.astype("<i2"),
                        supported=asset.supported.astype(bool), unresolved=asset.unresolved.astype(bool),
                        edges=asset.edges.astype("<i4").reshape(-1, 4),
                        meta=np.frombuffer(json.dumps(meta, sort_keys=True).encode("utf-8"), np.uint8))


def load_asset(path: Path) -> HeightAsset:
    with np.load(path) as z:
        meta = json.loads(bytes(z["meta"]).decode("utf-8"))
        if meta.get("version") != HEIGHT_VERSION:
            raise HeightError(f"{path.name}: height asset version {meta.get('version')} is not {HEIGHT_VERSION}")
        return HeightAsset(z["floors"].astype(np.int16), z["spread"].astype(np.int16), z["supported"].astype(bool),
                           z["unresolved"].astype(bool), z["edges"].astype(np.int32).reshape(-1, 4), meta)
