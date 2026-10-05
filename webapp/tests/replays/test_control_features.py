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


# ---- W7: bounded sight

def mask_px(x0, y0, x1, y1):
    m = np.zeros((1024, 1024), bool)
    m[y0:y1, x0:x1] = True
    return m


def test_the_vertical_intersection_rule():
    door = cf.BoundedOccluder("feature-1", mask_px(100, 0, 104, 1024), bottom=0.0, top=2.0)
    targets = np.array([[200, 50, 1.0],      # at the door's height all the way: blocked
                        [200, 50, 5.0],      # from z 1 up to 5: crosses x 100-104 at about z 3, above it
                        [200, 50, -3.0],     # dips below it
                        [90, 50, 1.0],       # never reaches the door
                        [200, 50, np.nan]], float)   # no height: the 2D answer, counted
    rec = {}
    got = cf.blocked_lines([door], (50.0, 50.0, 1.0), targets, rec).tolist()
    assert got == [True, False, False, False, True] and rec == {"flat_occluder_tests": 1}
    # the band is [bottom, top): a line exactly at the top passes, exactly at the bottom is blocked
    assert cf.blocked_lines([door], (50.0, 50.0, 2.0), np.array([[200, 50, 2.0]]), None).tolist() == [False]
    assert cf.blocked_lines([door], (50.0, 50.0, 0.0), np.array([[200, 50, 0.0]]), None).tolist() == [True]
    every = cf.BoundedOccluder("feature-1", mask_px(100, 0, 104, 1024), all_height=True)
    assert cf.blocked_lines([every], (50.0, 50.0, 1.0), targets, None).tolist() == [True, True, True, False, True]
    # straight up through the door's own pixel: blocked when the span meets the band, else clear
    up = np.array([[101, 50, 5.0], [101, 50, 2.5]], float)
    assert cf.blocked_lines([door], (101.0, 50.0, 1.5), up, None).tolist() == [True, True]
    assert cf.blocked_lines([door], (101.0, 50.0, 2.2), up, None).tolist() == [False, False]
    assert cf.blocked_lines([door], (101.0, 50.0, -4.0), np.array([[101, 50, -1.0]]), None).tolist() == [False]


def door_mf(geo, floor, bottom, top, ref="ground"):
    occ = {"geometry": rect(256, 160, 264, 232),
           "bounds": {"ref": ref, "floor": floor, "bottom": {"status": "known", "value": bottom, "unit": "m"},
                      "top": {"status": "known", "value": top, "unit": "m"}}}
    f = feature("feature-1", None, [floor])
    f["states"][0]["sight"] = [occ]
    return {**ms.empty(), "floors": floors_for(geo), "features": [f]}


def test_bounds_resolve_on_their_floor_and_unknowns_stay_pending():
    geo = bridge()
    [low], pending = cf.sight_occluders(geo, door_mf(geo, "floor-1", 0, 3.2))
    assert pending == [] and (low.bottom, low.top) == pytest.approx((-0.9, 2.3))
    [high], _ = cf.sight_occluders(geo, door_mf(geo, "floor-2", 0, 2.5))
    assert (high.bottom, high.top) == pytest.approx((3.1, 5.6))
    world = door_mf(geo, "floor-1", 1.0, 2.0, ref="world")
    [w], _ = cf.sight_occluders(geo, world)
    assert (w.bottom, w.top) == pytest.approx((1.0 - geo.heights.origin_z / 10, 2.0 - geo.heights.origin_z / 10))
    for mf in (door_mf(geo, "floor-3", 0, 3), door_mf(geo, "floor-5", 0, 3), door_mf(geo, "floor-4", 0, 3)):
        occluders, pending = cf.sight_occluders(geo, mf)          # unbanded, stale, ambiguous
        assert occluders == [] and len(pending) == 1 and "floor" in pending[0]
    unres = door_mf(geo, "floor-1", 0, 3)
    unres["features"][0]["states"][0]["sight"][0]["bounds"] = {"ref": "unresolved"}
    assert cf.sight_occluders(geo, unres)[0] == [] and cf.sight_occluders(geo, unres)[1]
    # an open door blocks nothing; a footprint blocks sight only under the state's own bounds
    assert cf.sight_occluders(geo, door_mf(geo, "floor-1", 0, 3.2), {"feature-1": "open"}) == ([], [])
    body = {**ms.empty(), "floors": floors_for(geo), "features": [feature("feature-1", rect(256, 160, 264, 232), ["floor-1"])]}
    assert cf.sight_occluders(geo, body)[0] == [] and cf.sight_occluders(geo, body)[1]
    body["features"][0]["states"][0]["sight_bounds"] = {"ref": "all_height"}
    assert cf.sight_occluders(geo, body)[0][0].all_height
    # a flat map has no heights: resolved bounds block in 2D
    flat = cf.sight_occluders(open_hall(), {**door_mf(geo, "floor-1", 0, 3.2), "floors": []})
    assert flat[1] == [] and flat[0][0].all_height


def views(geo, viewer, target, occluders):
    """(cast_with, los_with, seen_from_with) for one viewer node and one target node."""
    from app.control import heights as hc

    vx, vy = (float(c) for c in geo.centres[viewer])
    tx, ty = (float(c) for c in geo.centres[target])
    eye, body = geo.node_z[viewer] + hc.EYE_M, geo.node_z[target] + hc.BODY_M
    cast = cf.cast_with(geo, vx, vy, np.arange(0, 360, cg.RAY_STEP_DEG), [], occluders, eye_z=eye, own=viewer)[target]
    los = cf.los_with(geo, (vx, vy, eye), (tx, ty, body), [], occluders)
    seen = cf.seen_from_with(geo, np.array([viewer]), [], occluders)[target]
    return bool(cast), bool(los), bool(seen)


def test_a_stacked_floor_door_hides_only_its_own_floor_in_cast_los_and_seen_from():
    geo = bridge()
    node = lambda x, f: int(geo.node_of[geo.cell_of_px(x, 196), f])        # noqa: E731
    ground = (node(300, 0), node(244, 0))      # along the ground, under the bridge, across the door's cells
    deck = (node(284, 1), node(244, 1))        # along the bridge, over the same cells
    for pair in (ground, deck):
        assert views(geo, *pair, []) == (True, True, True), "the base map sees both lines"
    [low], _ = cf.sight_occluders(geo, door_mf(geo, "floor-1", 0, 3.2))
    [high], _ = cf.sight_occluders(geo, door_mf(geo, "floor-2", 0, 2.5))
    assert views(geo, *ground, [low]) == (False, False, False)
    assert views(geo, *deck, [low]) == (True, True, True), "a lower-floor door doesn't hide the bridge above it"
    assert views(geo, *ground, [high]) == (True, True, True), "a door on the bridge doesn't hide the tunnel below"
    assert views(geo, *deck, [high]) == (False, False, False)
    # the ground door through every height is a 2D wall again
    every = cf.BoundedOccluder("x", low.mask, all_height=True)
    assert views(geo, *deck, [every]) == (False, False, False)


def test_seen_from_with_needs_one_clear_source_and_keeps_skipped_targets():
    geo = bridge()
    node = lambda x, y, f=0: int(geo.node_of[geo.cell_of_px(x, y), f])     # noqa: E731
    [low], _ = cf.sight_occluders(geo, door_mf(geo, "floor-1", 0, 3.2))
    target = node(244, 196)
    behind, beside = node(300, 196), node(244, 280)
    both = cf.seen_from_with(geo, np.array([behind, beside]), [], [low])
    assert both[target] and not cf.seen_from_with(geo, np.array([behind]), [], [low])[target]
    skip = np.zeros(geo.n, bool)
    skip[target] = True
    assert cf.seen_from_with(geo, np.array([behind]), [], [low], skip=skip)[target]
    # never more than the static rows
    static = __import__("app.control.engine", fromlist=["seen_from"]).seen_from(geo, np.array([behind, beside]), [])
    assert not (both & ~static).any()
