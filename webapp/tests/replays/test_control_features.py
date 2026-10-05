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


# ---- W17: freshness inputs and cache keys

def test_a_feature_generation_joins_the_inputs_only_when_a_map_has_one(monkeypatch):
    from app.control import task as ct
    from app.replays import control_format as cfmt
    from app.services import replay_control as rc

    recorded = json.loads(map_feature_legacy.PATH.read_text(encoding="utf-8"))
    name = "Ascent"
    index, tags, maps = rc._assets()
    monkeypatch.setattr(rc, "_assets", lambda: ({**index, name: {**index[name], "features_sha": "feat0000feat0000"}}, tags, maps))
    with_features = rc.geometry_inputs(name)
    assert with_features == {**recorded["maps"][name]["geometry_inputs"], "features": "feat0000feat0000"}
    assert cfmt.fingerprint(recorded["recipe"], "0" * 64, recorded["link"], with_features) != recorded["maps"][name]["fingerprint"]
    assert rc.geometry_inputs("Bind") == recorded["maps"]["Bind"]["geometry_inputs"], "other maps are untouched"
    geo = open_hall()
    assert "features" not in ct.geometry_used(geo)
    geo2 = copy.copy(geo)
    geo2.features_sha = "feat0000feat0000"
    assert ct.geometry_used(geo2)["features"] == "feat0000feat0000"


def test_the_asset_cache_rereads_a_new_generation(tmp_path, monkeypatch):
    import shutil

    from app.services import replay_control as rc

    for name in ("index.json", "tags.json"):
        shutil.copy(cg.ASSET_DIR / name, tmp_path / name)
    monkeypatch.setattr(rc, "CONTROL_DIR", tmp_path)
    rc._assets.cache_clear()
    try:
        assert "features" not in rc.geometry_inputs("Ascent")
        index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
        index["maps"]["Ascent"]["features_sha"] = "feat0000feat0000"
        (tmp_path / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
        assert rc.geometry_inputs("Ascent")["features"] == "feat0000feat0000", "no restart needed"
    finally:
        rc._assets.cache_clear()


def test_the_worker_geometry_cache_is_keyed_by_generation(tmp_path, monkeypatch):
    from app.control import task as ct

    loads = []

    def fake_load(name, heights=None):
        loads.append(name)
        g = copy.copy(open_hall())
        g.name = name
        return g

    monkeypatch.setattr(cg, "load_geometry", fake_load)
    monkeypatch.setattr(cg, "visibility", lambda g, d=None: g)
    monkeypatch.setattr(ct, "_GEOMETRY", {})
    sha = {"value": None}
    monkeypatch.setattr(cf, "active_sha", lambda name, asset_dir=None: sha["value"])
    ct._load("Toy")
    ct._load("Toy")
    assert loads == ["Toy"] and list(ct._GEOMETRY) == ["Toy"], "no generation: keyed by name, as before"
    sha["value"] = "gen1"
    ct._load("Toy")
    sha["value"] = "gen2"
    ct._load("Toy")
    ct._load("Toy")
    assert loads == ["Toy", "Toy", "Toy"] and ("Toy", None, "gen2") in ct._GEOMETRY


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


# ---- W8: base reconciliation and activation bundles

KNOWN = {"status": "known", "value": 1.0, "unit": "s"}


def breakable(fid, block, *, ground=None, remove=None, binding=None, reclassify=None, floors=None):
    f = {"id": fid, "states": [{"name": "intact", "blocks_movement": True, "blocks_sight": True, "footprint": block,
                                "sight_bounds": {"ref": "all_height"}},
                               {"name": "broken", "blocks_movement": False, "blocks_sight": False, "terminal": True}],
         "initial_state": "intact", "transitions": [{"id": "break", "from": "*", "event": "destroy", "to": "broken"}],
         "base_edits": {"potential_ground": ground, "remove_sight": remove, "ground_binding": binding,
                        "reclassify": reclassify or []}}
    if floors is not None:
        f["floors"] = floors
    return f


def baked_in():
    """A floor with a block the minimap left transparent (x 200-232, y 100-140 px: unwalkable, blocks sight), a
    permanent wall right beside it (x 232-240), and legacy cover paint over the block's top rows (y 100-108)."""
    sight = np.zeros((1024, 1024), bool)
    walk = np.zeros((1024, 1024), bool)
    walk[96:296, 96:416] = True
    sight[100:140, 200:240] = True
    walk[100:140, 200:240] = False
    cover = np.zeros((1024, 1024), bool)
    cover[100:108, 200:232] = True
    return sight, walk, {"cover_paint": cover}


def block_mf(**over):
    block = rect(200, 100, 232, 140)
    f = breakable("feature-1", block, ground=block, remove=block, binding="floor-1",
                  reclassify=[{"source": "cover_paint", "geometry": rect(200, 100, 232, 108)}])
    f.update(over)
    return {**ms.empty(), "floors": [{"id": "floor-1", "label": "ground", "z_band": None}], "features": [f],
            "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": True, "runtime_consumer": "test"}]}


def test_a_publishable_bundle_restores_its_own_ground_and_sight_and_nothing_else():
    sight, walk, legacy = baked_in()
    mf = block_mf()
    [st] = cf.bundle_status(None, mf, legacy, consumers=frozenset({"test"})).values()
    assert st.publishable and st.reasons == [] and st.opens == {"ground": 32 * 40, "sight": 32 * 40}
    s, w, opened = cf.reconcile(sight, walk, mf, {"bundle-2": st})
    assert opened == {"bundle-2": {"ground": 32 * 40, "sight": 32 * 40}}
    assert w[100:140, 200:232].all() and not s[100:140, 200:232].any()
    assert s[100:140, 232:240].all() and not w[100:140, 232:240].any(), "the permanent wall stays"
    assert sight[100:140, 200:232].all() and not walk[100:140, 200:232].any(), "inputs untouched"


def test_pending_bundles_publish_none_of_their_base_edits():
    sight, walk, legacy = baked_in()
    test = frozenset({"test"})
    cases = {
        "no consumer registered": (block_mf(), cf.RUNTIME_CONSUMERS),
        "not enabled": ({**block_mf(), "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": False,
                                                    "runtime_consumer": "test"}]}, test),
        "legacy overlap not reclassified": (block_mf(base_edits={**block_mf()["features"][0]["base_edits"], "reclassify": []}), test),
        "reclassified only in part": (block_mf(base_edits={**block_mf()["features"][0]["base_edits"],
                                                            "reclassify": [{"source": "cover_paint", "geometry": rect(200, 100, 216, 108)}]}), test),
        "ground without a floor binding": (block_mf(base_edits={**block_mf()["features"][0]["base_edits"], "ground_binding": None}), test),
        "behaviour unresolved": (block_mf(initial_state=None), test),
    }
    for name, (mf, consumers) in cases.items():
        statuses = cf.bundle_status(None, mf, legacy, consumers=consumers)
        assert not statuses["bundle-2"].publishable and statuses["bundle-2"].reasons, name
        s, w, opened = cf.reconcile(sight, walk, mf, statuses)
        assert (s == sight).all() and (w == walk).all() and opened == {}, name
    # a test bundle with every reason fixed is publishable (the cases above differ from it in one thing each)
    assert cf.bundle_status(None, block_mf(), legacy, consumers=test)["bundle-2"].publishable


def test_overlapping_features_compose_and_must_share_a_bundle():
    sight, walk, legacy = baked_in()
    mf = block_mf()
    door = feature("feature-3", rect(200, 120, 232, 128))          # a door inside the block's area, no base edits
    mf["features"].append(door)
    st = cf.bundle_status(None, mf, legacy, consumers=frozenset({"test"}))
    assert st["bundle-2"].publishable, "a feature with no base edits doesn't overlap any"
    s, w, _ = cf.reconcile(sight, walk, mf, st)
    # breaking the block opens its ground; the door, closed, still blocks its own cells
    geo = cg.geometry_from_masks("Baked", s, w, 7e-5)
    blocks = cf.movement_blocks(geo, mf, {"feature-1": "broken", "feature-3": "closed"}).blocked.reshape(GRID, GRID)
    assert blocks[15, 25:29].all() and not blocks[13, 25:29].any()
    both = cf.movement_blocks(geo, mf, {"feature-1": "intact", "feature-3": "closed"}).blocked.reshape(GRID, GRID)
    assert both[12:17, 25:29].all()
    # a second feature whose own base edits overlap the block's must join its bundle
    other = breakable("feature-4", rect(216, 100, 240, 140), remove=rect(216, 100, 240, 140))
    mf["features"].append(other)
    st = cf.bundle_status(None, mf, legacy, consumers=frozenset({"test"}))
    assert not st["bundle-2"].publishable and any("feature-4" in r for r in st["bundle-2"].reasons)
    mf["bundles"][0]["members"].append("feature-4")
    other["base_edits"]["reclassify"] = [{"source": "cover_paint", "geometry": rect(216, 100, 240, 108)}]
    assert cf.bundle_status(None, mf, legacy, consumers=frozenset({"test"}))["bundle-2"].publishable


def test_on_a_height_map_bindings_must_be_verified_and_restored_ground_must_be_in_the_asset():
    geo = bridge()
    test = frozenset({"test"})
    on_floor = rect(304, 160, 320, 176)                       # walkable ground at 0 m: in the asset
    off_map = rect(600, 600, 616, 616)                         # not walkable: the asset has no floor there

    def mf(ground, binding, floors):
        f = breakable("feature-1", ground, ground=ground, binding=binding, floors=floors)
        return {**ms.empty(), "floors": floors_for(geo), "features": [f],
                "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": True, "runtime_consumer": "test"}]}

    assert cf.bundle_status(geo, mf(on_floor, "floor-1", ["floor-1"]), consumers=test)["bundle-2"].publishable
    for args, why in (((on_floor, "floor-5", ["floor-1"]), "not verified"), ((on_floor, "floor-1", ["floor-3"]), "not verified"),
                      ((off_map, "floor-1", ["floor-1"]), "rebuild the heights")):
        st = cf.bundle_status(geo, mf(*args), consumers=test)["bundle-2"]
        assert not st.publishable and any(why in r for r in st.reasons), (args, st.reasons)
    # the node domain never depends on a feature's state
    m = mf(on_floor, "floor-1", ["floor-1"])
    shapes = {cf.movement_blocks(geo, m, {"feature-1": s}).blocked.shape for s in ("intact", "broken")}
    assert shapes == {(geo.n,)}


# ---- W9: directed traversal

def secs(v):
    return {"status": "known", "value": v, "unit": "s"}


UNRES = {"status": "unresolved"}


def rope(geo, up=("floor-1", "floor-2"), back=UNRES):
    return {**ms.empty(), "floors": floors_for(geo), "routes": [{
        "id": "route-1", "kind": "rope", "access": "endpoint_only", "in_transit": "complete",
        "endpoints": [{"id": "a", "uv": [U(264), U(196)], "floor": up[0]}, {"id": "b", "uv": [U(264), U(196)], "floor": up[1]}],
        "directions": [{"from": "a", "to": "b", "entry": secs(0.5), "transit": secs(2.0)},
                       {"from": "b", "to": "a", "entry": secs(0.3), "transit": back}]}]}


def test_a_same_position_rope_joins_exactly_its_two_floors():
    geo = bridge()
    node = lambda x, f=0: int(geo.node_of[geo.cell_of_px(x, 196), f])     # noqa: E731
    arcs, pending = cf.compile_routes(geo, rope(geo))
    lower, upper = node(264, 0), node(264, 1)
    assert [(a.src, a.dst) for a in arcs] == [(lower, upper), (upper, lower)]
    assert pending == ["route-1: b -> a travel time unresolved"]
    east, plateau = node(400), node(150)
    plain = cf.TraversalGraph(geo, [])
    assert not plain.reachable(east)[plateau], "the ground and the plateau don't join by walking"
    g = cf.TraversalGraph(geo, arcs)
    assert g.reachable(east)[plateau] and g.reachable(plateau)[east], "reachability counts a known arc"
    t = g.time_seconds(east, 5.0)
    walk_to_rope = plain.walk_metres(east)[lower] / 5.0
    assert t[upper] == pytest.approx(walk_to_rope + 2.5) and np.isfinite(t[plateau])
    assert np.isinf(g.time_seconds(plateau, 5.0)[east]), "an unknown transit is never a free step"
    assert [a.dst for a in g.unresolved] == [lower]
    assert np.isinf(g.walk_metres(east)[plateau]), "walking metres never ride a route"
    # same place on the same floor is no route; an unbound floor is pending, never every floor
    arcs, pending = cf.compile_routes(geo, rope(geo, up=("floor-1", "floor-1")))
    assert arcs == [] and all("joins a node to itself" in p for p in pending)
    for floors in (("floor-3", "floor-2"), ({"status": "unresolved"}, "floor-2"), ("floor-4", "floor-2")):
        arcs, pending = cf.compile_routes(geo, rope(geo, up=floors))
        assert arcs == [] and any(p.startswith("route-1.a") for p in pending), floors


def rooms():
    """Two rooms with no walk between them (x 96-200 and 300-416 px)."""
    return toy_geometry("TwoRooms", [(96, 96, 200, 296), (300, 96, 416, 296)])


def zipline(access="endpoint_only", in_transit="complete", states=None, extra=()):
    route = {"id": "route-1", "kind": "zipline", "owner": "feature-9", "access": access, "in_transit": in_transit,
             "states": states,
             "endpoints": [{"id": "a", "uv": [U(150), U(196)]}, {"id": "b", "uv": [U(350), U(196)]}],
             "directions": [{"from": "a", "to": "b", "entry": secs(0.5), "transit": secs(2.0)}, *extra]}
    return {**ms.empty(), "routes": [route]}


def test_a_one_way_zipline_endpoint_only_versus_intermediate_access():
    geo = rooms()
    a, b, far = geo.cell_of_px(150, 196), geo.cell_of_px(350, 196), geo.cell_of_px(380, 150)
    arcs, pending = cf.compile_routes(geo, zipline())
    assert pending == [] and [(x.src, x.dst) for x in arcs] == [(a, b)]
    g = cf.TraversalGraph(geo, arcs)
    assert g.reachable(a)[far] and not g.reachable(b)[a], "one way"
    back = g.reverse()
    assert back.reachable(b)[a] and not back.reachable(a)[b], "a return path searches the reversed graph"
    t = g.time_seconds(a, 5.0)
    assert t[b] == pytest.approx(2.5)
    walk_b_far = cf.TraversalGraph(geo, []).walk_metres(b)[far] / 5.0
    assert t[far] == pytest.approx(2.5 + walk_b_far), "endpoint-only: no getting off part way"
    site = {"sites": [{"id": "m", "uv": [U(380), U(150)]}]}
    arcs2, _ = cf.compile_routes(geo, zipline(access=site, extra=[{"from": "a", "to": "m", "entry": secs(0.5), "transit": secs(1.0)}]))
    t2 = cf.TraversalGraph(geo, arcs2).time_seconds(a, 5.0)
    assert t2[far] == pytest.approx(1.5) and t2[b] == pytest.approx(2.5)
    # the zipline's own line is drawing: it opens no ground between the rooms
    assert not cf.TraversalGraph(geo, arcs).reachable(geo.cell_of_px(250, 196)).sum() > 1


def test_availability_states_and_in_transit_policies():
    geo = rooms()
    a, b = geo.cell_of_px(150, 196), geo.cell_of_px(350, 196)
    when = {"route-1": [(10.0, 20.0)]}
    g = cf.TraversalGraph(geo, cf.compile_routes(geo, zipline())[0])
    assert g.arrival_times(a, 5.0, 5.0, when)[b] == pytest.approx(12.5), "waits for it to open"
    assert g.arrival_times(a, 19.0, 5.0, when)[b] == pytest.approx(21.5), "complete: closing mid-ride still arrives"
    assert g.arrival_times(a, 25.0, 5.0, when)[b] == np.inf
    abort = cf.TraversalGraph(geo, cf.compile_routes(geo, zipline(in_transit="abort"))[0])
    assert abort.arrival_times(a, 19.0, 5.0, when)[b] == np.inf
    assert abort.arrival_times(a, 12.0, 5.0, when)[b] == pytest.approx(14.5)
    unres = cf.TraversalGraph(geo, cf.compile_routes(geo, zipline(in_transit="unresolved"))[0])
    assert unres.arrival_times(a, 12.0, 5.0, {})[b] == np.inf, "an unresolved in-transit policy stays out of time"
    with pytest.raises(ValueError):
        g.reverse().arrival_times(a, 0.0, 5.0, {})
    gated = cf.compile_routes(geo, zipline(states=["open"]))[0]
    assert not cf.TraversalGraph(geo, gated, states={"feature-9": "closed"}).reachable(a)[b]
    assert cf.TraversalGraph(geo, gated, states={"feature-9": "open"}).reachable(a)[b]
    blocked = np.zeros(geo.n, bool)
    blocked[b] = True
    assert not cf.TraversalGraph(geo, cf.compile_routes(geo, zipline())[0], blocked=blocked).reachable(a)[b]


def manifest_mf():
    block = rect(200, 100, 232, 140)
    f = breakable("feature-1", block)
    f["base_edits"] = {}
    f["name"], f["notes"] = "Block", "toy"
    door = feature("feature-3", rect(300, 100, 308, 140))
    return {**ms.empty(), "features": [f, door], "checklist": {"x": {"status": "in_progress"}},
            "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": True, "runtime_consumer": "test"}]}


TEST = frozenset({"test"})


def test_the_manifest_is_absent_without_enabled_features_and_for_every_committed_map():
    geo = open_hall()
    assert cf.manifest(geo, None) is None and cf.manifest(geo, ms.empty()) is None
    assert cf.manifest(geo, manifest_mf()) is None, "no registered consumer in this build: nothing is enabled"
    tags = cg.load_tags()
    assert all(cf.manifest(geo, (e or {}).get("map_features")) is None for e in tags["maps"].values())


def test_the_manifest_tracks_runtime_inputs_and_ignores_editorial_ones(monkeypatch):
    geo = open_hall()
    mf = manifest_mf()
    base = cf.manifest(geo, mf, consumers=TEST)
    assert base["bundles"] == ["bundle-2"] and base["consumers"] == ["test"] and base["height"] is None
    assert set(base["compiled"]) == {"arcs", "nodes", "states"}
    for edit in (lambda m: m["features"][0].update(name="Renamed", notes="other", review="user_reviewed"),
                 lambda m: m.update(checklist={}, next_id=50),
                 lambda m: m["features"][1].update(initial_state="open")):          # not in an enabled bundle
        m = copy.deepcopy(mf)
        edit(m)
        assert cf.manifest(geo, m, consumers=TEST) == base
    for edit in (lambda m: m["features"][0]["states"][0].update(footprint=rect(200, 100, 240, 140)),
                 lambda m: m["features"][0]["transitions"][0].update(to="intact"),
                 lambda m: m["bundles"][0]["members"].append("feature-3")):
        m = copy.deepcopy(mf)
        edit(m)
        assert cf.manifest_digest(cf.manifest(geo, m, consumers=TEST)) != cf.manifest_digest(base)
    monkeypatch.setattr(cf, "COMPILER_VERSION", cf.COMPILER_VERSION + 1)
    assert cf.manifest(geo, mf, consumers=TEST)["compiler"] != base["compiler"]


def test_verification_rehashes_loaded_assets_and_recompiles_definitions():
    geo = open_hall()
    mf = manifest_mf()
    statuses = cf.bundle_status(geo, mf, consumers=TEST)
    assets = cf.compile_assets(geo, mf, statuses)
    expected = cf.manifest(geo, mf, consumers=TEST)
    assert cf.verify(expected, assets) == [] and cf.verify(expected, assets, geo, mf, consumers=TEST) == []
    tampered = copy.deepcopy(assets)
    tampered["states"]["feature-1:intact"]["blocked"].pop()
    assert any(p.startswith("compiled states") for p in cf.verify(expected, tampered))
    edited = copy.deepcopy(mf)
    edited["features"][0]["states"][0]["footprint"] = rect(200, 100, 240, 140)
    assert any("definitions" in p for p in cf.verify(expected, assets, geo, edited, consumers=TEST))
    assert cf.verify(None, None) == [] and cf.verify(None, assets) and cf.verify(expected, None)


def test_routes_never_touch_the_maps_legacy_specials():
    from app.control import engine

    specials = [{"a": [int(U(150)), int(U(196))], "b": [int(U(350)), int(U(196))], "one_way": True}]
    geo = toy_geometry("TwoRoomsSpecial", [(96, 96, 200, 296), (300, 96, 416, 296)], specials=specials)
    before = engine.special_links(geo)
    assert cf.compile_routes(geo, ms.empty()) == ([], [])
    cf.TraversalGraph(geo, cf.compile_routes(geo, zipline())[0]).reachable(geo.cell_of_px(150, 196))
    assert engine.special_links(geo) == before == [(geo.cell_of_px(150, 196), geo.cell_of_px(350, 196), True)]


# ---- W16: rotation poses; permanent kill-line checks ignore features

def rotating(direction="ccw", start=0, end=90, phases=()):
    return {"id": "feature-1", "rotation": {
        "pivot": {"type": "point", "uv": [5000, 5000]}, "panel": {"type": "polyline", "uv": [[5000, 5000], [5300, 5000]]},
        "direction": {"status": "known", "value": direction, "unit": "dir"} if direction else {"status": "unresolved"},
        "start_deg": {"status": "known", "value": start, "unit": "deg"}, "end_deg": {"status": "known", "value": end, "unit": "deg"},
        "phases": list(phases)}}


def test_rotation_poses_turn_about_the_pivot_and_phases_override():
    assert cf.rotation_pose(rotating(), 0.0)["uv"] == [[5000, 5000], [5300, 5000]]
    up = cf.rotation_pose(rotating("ccw"), 1.0)["uv"][1]          # anticlockwise on the minimap (y down): up
    assert up == pytest.approx([5000, 4700])
    down = cf.rotation_pose(rotating("cw"), 1.0)["uv"][1]
    assert down == pytest.approx([5000, 5300])
    half = cf.rotation_pose(rotating("cw", 0, 180), 0.5)["uv"][1]
    assert half == pytest.approx([5000, 5300])
    assert cf.rotation_pose(rotating(direction=None), 0.5) is None, "an unknown direction is never guessed"
    no_end = rotating()
    no_end["rotation"]["end_deg"] = {"status": "unresolved"}
    assert cf.rotation_pose(no_end, 0.5) is None
    custom = {"type": "polyline", "uv": [[5000, 5000], [5100, 5200]]}
    phased = rotating(phases=[{"at": 0.5, "panel": custom}, {"at": 0.0, "panel": {"type": "polyline", "uv": [[1, 1], [2, 2]]}}])
    assert cf.rotation_pose(phased, 0.7) == custom and cf.rotation_pose(phased, 0.2)["uv"] == [[1, 1], [2, 2]]


def test_permanent_kill_line_masks_ignore_feature_annotations():
    from PIL import Image

    tags = cg.load_tags()
    entry = tags["maps"]["Ascent"]
    rgba = np.array(Image.open(cg.MINIMAP_DIR / "Ascent.png").convert("RGBA"))
    catalogue = json.loads((HERE.parent / "fixtures" / "control" / "map_features" / "catalogue_full.json").read_text(encoding="utf-8"))
    mf = catalogue["maps"]["Ascent"]["map_features"]
    mf["features"][0]["initial_state"] = "closed"      # a door forced closed in the page
    plain, annotated = cg.masks(rgba, entry), cg.masks(rgba, {**entry, "map_features": mf})
    ys, xs = np.nonzero(plain.walk & ~plain.sight)       # put the door on open floor
    y, x = int(ys[len(ys) // 2]) // 4 * 4, int(xs[len(xs) // 2]) // 4 * 4
    mf["features"][0]["states"][0]["footprint"] = rect(x, y, x + 8, y + 8)
    assert (plain.sight == annotated.sight).all() and (plain.walk == annotated.walk).all()
    # while the preview's composition does block with it closed
    s, _ = cf.compose_masks(plain.sight, plain.walk, mf)
    assert (s & ~plain.sight).any()
