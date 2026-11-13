"""Viewer-independent minimap areas, refreshed when reviewed paint changes."""

import json
from functools import lru_cache

from app.coach import areas
from app.coach.areas_draft import encode_cells
from app.replays import control_format as cf
from app.replays import format as fmt
from app.replays import layouts
from app.replays.map_feature_sources import png_mask

MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
CONTROL_DIR = fmt.STATIC_DIR / "data" / "control"
NAME_FIELDS = ("id", "name", "parent", "super")


@lru_cache(maxsize=1)
def _maps():
    return json.loads(MAPS_JSON.read_text(encoding="utf-8"))


def _known_keys() -> set[str]:
    """Every key the viewer may ask for: the maps, and every layout asset key with control assets (a later layout
    such as Corrode_13_10 is in control/index.json, never in maps.json)."""
    keys = set(_maps())
    try:
        keys.update(key for key in layouts.control_layouts(CONTROL_DIR / "index.json")
                    if layouts.ASSET_KEY.match(key) and layouts.map_of(key) in keys)
    except (OSError, ValueError, KeyError):
        pass
    return keys


@lru_cache(maxsize=32)
def _walk_cells(map_name):
    # Match geometry_from_masks' strict majority of the 8x8 pixels in each cell,
    # without importing the control engine into the web process.
    pixels = png_mask((CONTROL_DIR / f"{map_name}.walk.png").read_bytes())
    width = 1024
    step = width // cf.GRID
    return tuple(
        y * cf.GRID + x
        for y in range(cf.GRID) for x in range(cf.GRID)
        if sum(sum(pixels[row * width + x * step:row * width + (x + 1) * step])
               for row in range(y * step, (y + 1) * step)) > step * step / 2
    )


def payload(map_name: str) -> dict:
    """The areas of one layout, by its asset key (the base layout's key is its map name)."""
    # Check membership before building any path from the requested key.
    if map_name not in _known_keys():
        raise KeyError(map_name)
    painted = areas.load_painted(map_name)
    return _payload(map_name, painted.raw if painted is not None else None)


@lru_cache(maxsize=32)
def _payload(map_name: str, raw: bytes | None) -> dict:
    """Cache by the same validated bytes that supply the payload."""
    if raw is not None:
        painted = json.loads(raw)
        names = sorted(painted["names"], key=lambda row: row["id"])
        return {
            "map": map_name, "approximate": False,
            "names": [{key: row[key] for key in NAME_FIELDS} for row in names],
            "cells": tuple(map(tuple, painted["cells"])),
            "bands": {int(cell): tuple(map(tuple, sorted(rows))) for cell, rows in painted["bands"].items()},
        }

    labels = areas.callouts(map_name)
    names = [{"id": i, "name": label["name"], "parent": None, "super": label["super"]}
             for i, label in enumerate(labels, 1)]
    cells = [0] * cf.GRID ** 2
    if labels:
        for cell in _walk_cells(map_name):
            u = (cell % cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
            v = (cell // cf.GRID + 0.5) / cf.GRID * fmt.UV_SCALE
            # Keep the callout table's tie order, just like areas._nearest_label.
            cells[cell] = min(range(len(labels)), key=lambda i:
                              (labels[i]["u"] - u) ** 2 + (labels[i]["v"] - v) ** 2) + 1
    return {"map": map_name, "approximate": True, "names": names,
            "cells": encode_cells(cells), "bands": {}}
