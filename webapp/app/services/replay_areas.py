"""Viewer-independent minimap areas, refreshed when reviewed paint changes."""

import json
from functools import lru_cache

from app.coach import areas
from app.coach.areas_draft import encode_cells
from app.replays import control_format as cf
from app.replays import format as fmt
from app.replays.map_feature_sources import png_mask

MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
CONTROL_DIR = fmt.STATIC_DIR / "data" / "control"
NAME_FIELDS = ("id", "name", "parent", "super")


@lru_cache(maxsize=1)
def _maps():
    return json.loads(MAPS_JSON.read_text(encoding="utf-8"))


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
    # Check membership before building any path from the requested map name.
    if map_name not in _maps():
        raise KeyError(map_name)
    return _payload(map_name, areas.table_hash())


@lru_cache(maxsize=32)
def _payload(map_name: str, table_hash: str | None) -> dict:
    """Cache both painted and approximate data by map and the reviewed files' hash."""
    painted = areas.load_painted(map_name)
    if painted is not None:
        return {
            "map": map_name, "approximate": False,
            "names": [{key: row[key] for key in NAME_FIELDS} for row in painted.names.values()],
            "cells": painted.cell_runs, "bands": painted.bands,
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
