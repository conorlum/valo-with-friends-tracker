"""Where a spike lands from its planter: the first clear step along the planter's final aim.

The game places the spike 0.7 m in front of the planter, or 0.4 m, 0.2 m or at their feet when a
wall or the edge of the plant site is in the way (measured on replay Bomb positions). The committed
`app/data/plant_zones.json` (`scripts/build_plant_zones.py`) holds each map's plant zone and walkable
ground as run-length grids, so this module needs no image or array library. A map without a checked
zone is not covered, and its caller keeps the plain 0.7 m position.
"""
from functools import lru_cache
import json
from pathlib import Path

STEPS_M = (0.7, 0.4, 0.2)
WALL_MARGIN_M = 0.6
PATH_SAMPLES = 8
GRID_SIZE = 1024
UV_SCALE = 10000
ZONES_PATH = Path(__file__).resolve().parents[1] / "data" / "plant_zones.json"


def encode_runs(values):
    """Alternating run lengths of a 0/1 sequence, starting with a run of zeros (possibly empty)."""
    runs, current, length = [], 0, 0
    for value in values:
        value = 1 if value else 0
        if value == current:
            length += 1
        else:
            runs.append(length)
            current, length = value, 1
    runs.append(length)
    return runs


def decode_runs(runs, size):
    out = bytearray(size)
    position, value = 0, 0
    for length in runs:
        if value:
            out[position:position + length] = b"\x01" * length
        position += length
        value = 1 - value
    if position != size:
        raise ValueError("plant zone runs do not fill the grid")
    return out


@lru_cache(maxsize=1)
def _zones_file():
    if not ZONES_PATH.exists():
        return {"maps": {}}
    return json.loads(ZONES_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=32)
def _grids(asset_key):
    """(x0, y0, width, height, zone, walk) for a covered layout, else None."""
    entry = _zones_file().get("maps", {}).get(asset_key)
    if entry is None:
        return None
    x0, y0, x1, y1 = entry["box"]
    width, height = x1 - x0, y1 - y0
    return (x0, y0, width, height, decode_runs(entry["zone"], width * height),
            decode_runs(entry["walk"], width * height))


def covers(asset_key):
    return asset_key is not None and _grids(asset_key) is not None


def _on(grids, cells, point):
    x0, y0, width, height = grids[:4]
    x = int(point[0] / UV_SCALE * GRID_SIZE) - x0
    y = int(point[1] / UV_SCALE * GRID_SIZE) - y0
    return 0 <= x < width and 0 <= y < height and bool(cells[y * width + x])


def pick_step(asset_key, point_at):
    """The spike's distance in metres; `point_at(metres)` is the (u, v) that far along the aim.

    A step needs its landing point inside the plant zone and walkable ground along the way, out to
    `WALL_MARGIN_M` past it. A planter on top of a box is not on the walk grid, so only the zone
    applies to them. When no step fits, the spike is at the planter's feet.
    """
    grids = _grids(asset_key)
    if grids is None:
        raise KeyError(asset_key)
    zone, walk = grids[4], grids[5]
    on_ground = _on(grids, walk, point_at(0))
    for step in STEPS_M:
        if not _on(grids, zone, point_at(step)):
            continue
        reach = step + WALL_MARGIN_M
        if not on_ground or all(_on(grids, walk, point_at(reach * i / PATH_SAMPLES))
                                for i in range(1, PATH_SAMPLES + 1)):
            return step
    return 0.0
