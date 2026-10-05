"""Map features in control (app/control/features.py; plan docs/superpowers/plans/2026-10-04-map-interaction-tagger.md,
M0 and M5): nothing changes for a map without enabled features, and the compiled feature contracts behave as
the plan's synthetic fixtures require."""

import base64
import copy
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import map_feature_legacy  # noqa: E402

from app.control import features as cf  # noqa: E402
from app.control import geometry as cg  # noqa: E402
from app.replays import map_feature_schema as ms  # noqa: E402
from tests.replays.control_toys import HALL, open_hall, toy_geometry, toy_heights  # noqa: E402

GRID = cg.GRID


def test_every_committed_maps_control_inputs_and_fingerprint_are_as_recorded():
    recorded = json.loads(map_feature_legacy.PATH.read_text(encoding="utf-8"))
    assert recorded["maps"], "the snapshot should cover the committed maps"
    assert map_feature_legacy.snapshot()["maps"] == recorded["maps"]


# ---- W6: rasterising, floors, movement blocks

def U(px):
    """Minimap px -> u/v, exactly (1024 px = 10000 u/v)."""
    return px * 10000 / 1024


def rect(x0, y0, x1, y1):
    return {"type": "polygon", "uv": [[U(x0), U(y0)], [U(x1), U(y0)], [U(x1), U(y1)], [U(x0), U(y1)]]}


def grid_rect(x0, y0, x1, y1):
    out = np.zeros((GRID, GRID), bool)
    out[y0 // 8:y1 // 8, x0 // 8:x1 // 8] = True
    return out


def test_raster_point_polyline_polygon_and_paint():
    pt = cf.raster({"type": "point", "uv": [U(10), U(21)]})
    assert np.argwhere(pt).tolist() == [[5, 2]]
    assert cf.raster({"type": "point", "uv": [10000, 10000]})[255, 255]
    poly = cf.raster(rect(40, 80, 80, 100))
    expected = np.zeros((256, 256), bool)
    expected[20:25, 10:20] = True
    assert (poly == expected).all()
    # a horizontal line along cell centres with no width covers exactly that row of cells
    line = cf.raster({"type": "polyline", "uv": [[U(2), U(42)], [U(62), U(42)]]})
    assert np.argwhere(line).tolist() == [[10, c] for c in range(16)]
    wide = cf.raster({"type": "polyline", "uv": [[U(2), U(42)], [U(62), U(42)]], "width": U(8)})
    assert sorted({r for r, _ in np.argwhere(wide).tolist()}) == [9, 10, 11]
    # an even-odd polygon: a ring's hole stays empty
    ring = {"type": "polygon", "uv": [[0, 0], [U(64), 0], [U(64), U(64)], [0, U(64)], [0, 0],
                                      [U(16), U(16)], [U(16), U(48)], [U(48), U(48)], [U(48), U(16)], [U(16), U(16)]]}
    r = cf.raster(ring)
    assert r[1, 1] and not r[8, 8] and r.sum() == 16 * 16 - 8 * 8
    paint = np.zeros((256, 256), bool)
    paint[3, 7] = paint[200, 100] = True
    assert (cf.raster({"type": "paint", "cells": cg.pack_paint(paint)}) == paint).all()
    assert not cf.raster(None).any()
    with pytest.raises(ValueError):
        cf.raster({"type": "blob"})


def test_to_grid_covers_a_cell_when_any_paint_cell_is_set():
    cells = np.zeros((256, 256), bool)
    cells[1, 1] = True
    g = cf.to_grid(cells)
    assert g[0, 0] and g.sum() == 1
    assert cf.to_px(cells)[4:8, 4:8].all() and cf.to_px(cells).sum() == 16


def feature(fid, footprint, floors=None, state="closed"):
    f = {"id": fid, "states": [{"name": "closed", "blocks_movement": True, "blocks_sight": True, "footprint": footprint},
                               {"name": "open", "blocks_movement": False, "blocks_sight": False}],
         "initial_state": state, "transitions": []}
    if floors is not None:
        f["floors"] = floors
    return f


def test_a_door_blocks_its_cells_on_a_flat_map_and_nothing_when_open():
    geo = open_hall()
    mf = {**ms.empty(), "features": [feature("feature-1", rect(200, 96, 208, 296))]}
    fx = cf.movement_blocks(geo, mf)
    assert fx.pending == [] and (fx.blocked.reshape(GRID, GRID) == grid_rect(200, 96, 208, 296)).all()
    assert not cf.movement_blocks(geo, mf, {"feature-1": "open"}).blocked.any()
    assert cf.movement_blocks(geo, mf, {"feature-1": "ajar"}).pending


def bridge():
    """A plateau 4 m up west of x 240; a bridge 4 m up over ground at 0 m from x 240 to 288 (two floors there)."""
    return toy_heights("Bridge", [HALL], ground=[((96, 96, 240, 296), 4.0)], upper=[((240, 96, 288, 296), 4.0)])


def floors_for(geo):
    return [{"id": "floor-1", "label": "ground", "z_band": [-0.5, 1.0], "height_sha": geo.height_sha},
            {"id": "floor-2", "label": "bridge", "z_band": [3.0, 5.0], "height_sha": geo.height_sha},
            {"id": "floor-3", "label": "manual", "z_band": None, "height_sha": None},
            {"id": "floor-4", "label": "both", "z_band": [-1.0, 5.0], "height_sha": geo.height_sha},
            {"id": "floor-5", "label": "stale", "z_band": [-0.5, 1.0], "height_sha": "000000000000"}]


def test_a_stacked_floor_door_blocks_the_lower_floor_and_leaves_the_bridge_open():
    geo = bridge()
    door = rect(256, 160, 264, 232)
    cells = np.flatnonzero(grid_rect(256, 160, 264, 232).ravel())
    lower = geo.node_of[cells, 0]
    upper = geo.node_of[cells, 1]
    assert (upper >= 0).all() and (geo.node_z[upper] == 4.0).all() and (geo.node_z[lower] == 0.0).all()
    mf = {**ms.empty(), "floors": floors_for(geo), "features": [feature("feature-1", door, ["floor-1"])]}
    fx = cf.movement_blocks(geo, mf)
    assert fx.pending == []
    assert fx.blocked[lower].all() and not fx.blocked[upper].any() and fx.blocked.sum() == len(cells)
    both = cf.movement_blocks(geo, {**mf, "features": [feature("feature-1", door, ["floor-1", "floor-2"])]})
    assert both.blocked[lower].all() and both.blocked[upper].all()
    # never every floor by default: unresolved, unbanded, stale or ambiguous bindings block nothing and say why
    for floors in (None, [], ["floor-3"], ["floor-5"], ["floor-4"]):
        fx = cf.movement_blocks(geo, {**mf, "features": [feature("feature-1", door, floors)]})
        assert not fx.blocked.any() and fx.pending, floors
    # one base domain: the node count and indices are the geometry's, whatever the state
    assert cf.movement_blocks(geo, mf, {"feature-1": "open"}).blocked.shape == (geo.n,)


def test_compose_masks_draws_states_without_touching_the_inputs():
    sight = np.zeros((1024, 1024), bool)
    walk = np.ones((1024, 1024), bool)
    mf = {**ms.empty(), "features": [feature("feature-1", rect(400, 400, 440, 404))]}
    mf["features"][0]["states"][0]["sight"] = [{"geometry": {"type": "polyline", "uv": [[U(600), U(602)], [U(640), U(602)]]},
                                                "bounds": {"ref": "all_height"}},
                                               {"geometry": {"type": "polyline", "uv": [[U(600), U(702)], [U(640), U(702)]]},
                                                "bounds": {"ref": "unresolved"}}]
    s, w = cf.compose_masks(sight, walk, mf)
    assert not sight.any() and walk.all()
    assert (~w).sum() == 40 * 4 and s[400:404, 400:440].all() and s[600:604, 600:640].all()
    assert not s[700:704].any(), "an unresolved occluder draws nothing"
    s2, w2 = cf.compose_masks(sight, walk, mf, {"feature-1": "open"})
    assert not s2.any() and w2.all()


def test_diagnose_flags_diagonal_leaks_off_ground_endpoints_and_stale_floors():
    geo = open_hall()
    diag = {"type": "polyline", "uv": [[U(200), U(96)], [U(400), U(296)]]}
    band = rect(200, 96, 216, 296)
    mf = {**ms.empty(), "features": [feature("feature-1", diag), feature("feature-2", band)],
          "routes": [{"id": "route-3", "endpoints": [{"id": "a", "uv": [U(150), U(150)]}, {"id": "b", "uv": [U(600), U(600)]}]}]}
    got = {(d["where"], d["code"]) for d in cf.diagnose(geo, mf)}
    assert got == {("feature-1.states.closed", "diagonal_leak"), ("route-3.b", "off_ground")}
    stacked = bridge()
    got = {(d["where"], d["code"]) for d in cf.diagnose(stacked, {**ms.empty(), "floors": floors_for(stacked)})}
    assert got == {("floor-5", "stale_floor")}
