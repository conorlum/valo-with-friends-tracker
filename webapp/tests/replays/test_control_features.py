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

from map_feature_artifact_toys import geometry_case, source_case

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


def _toy_assets(folder: Path, revision: str, features: str | None = None) -> None:
    entry = {"sight_sha": revision, "walk_sha": revision}
    if features:
        entry["features_sha"] = features
    (folder / "index.json").write_text(json.dumps({"maps": {"Toy": entry}}), encoding="utf-8")
    (folder / "tags.json").write_text(json.dumps({"maps": {"Toy": {"specials": [{"revision": revision}]}}}),
                                      encoding="utf-8")


@pytest.fixture
def toy_asset_dir(tmp_path, monkeypatch):
    from app.services import replay_control as rc

    _toy_assets(tmp_path, "old")
    (tmp_path / "maps.json").write_text(json.dumps({"Toy": {"xMultiplier": 1}}), encoding="utf-8")
    monkeypatch.setattr(rc, "CONTROL_DIR", tmp_path)
    monkeypatch.setattr(rc, "MAPS_JSON", tmp_path / "maps.json")
    rc._assets.cache_clear()
    yield tmp_path
    rc._assets.cache_clear()


def test_a_half_written_tags_file_keeps_the_last_consistent_snapshot(toy_asset_dir):
    from app.services import replay_control as rc

    before = rc.geometry_inputs("Toy")
    assert before["specials"] == [{"revision": "old"}]
    (toy_asset_dir / "tags.json").write_text('{"maps": {"Toy": {"spec', encoding="utf-8")   # a writer mid-write
    assert rc.geometry_inputs("Toy") == before, "a transient partial file keeps the last valid inputs"
    _toy_assets(toy_asset_dir, "new")
    assert rc.geometry_inputs("Toy")["specials"] == [{"revision": "new"}], "once whole, it is read"


def test_a_half_written_file_with_no_snapshot_yet_is_an_error(toy_asset_dir):
    from app.services import replay_control as rc

    (toy_asset_dir / "tags.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        rc.geometry_inputs("Toy")


@pytest.mark.parametrize("features", [None, "feat0000feat0000"], ids=["no-generation", "generation"])
def test_atomic_replacements_during_a_read_never_mix_generations(toy_asset_dir, monkeypatch, features):
    """The index and tags are replaced (atomically, one after the other) while the reader is between them: the
    reader must answer with one generation's inputs, never old masks with new specials."""
    import os

    from app.services import replay_control as rc

    assert rc.geometry_inputs("Toy")["sight"] == "old"
    (toy_asset_dir / "maps.json").write_text(json.dumps({"Toy": {"xMultiplier": 1, "edited": True}}), encoding="utf-8")
    raced = []

    def replace_both():
        if raced:
            return
        raced.append(True)
        staging = toy_asset_dir / "staging"
        staging.mkdir()
        _toy_assets(staging, "new", features)
        for name in ("index.json", "tags.json"):
            os.replace(staging / name, toy_asset_dir / name)

    for method in ("read_bytes", "read_text"):
        original = getattr(Path, method)

        def hooked(self, *a, _original=original, **k):
            if self == toy_asset_dir / "tags.json":
                replace_both()
            return _original(self, *a, **k)

        monkeypatch.setattr(Path, method, hooked)
    got = rc.geometry_inputs("Toy")
    assert raced, "the race happened"
    want = {"sight": "new", "walk": "new", "barrier": None, "specials": [{"revision": "new"}], "scale": 1}
    if features:
        want["features"] = features
    assert got == want


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


# ---- W18: generations, verification, storage rejection

@pytest.fixture
def published(tmp_path, monkeypatch):
    """A copy of Ascent's committed assets in tmp, with one enabled feature (a registered test consumer) and
    its generation published. Never the committed asset folder."""
    import shutil

    for name in ("Ascent.sight.png", "Ascent.walk.png", "Ascent.barrier.png", "index.json", "tags.json"):
        if (cg.ASSET_DIR / name).is_file():
            shutil.copy(cg.ASSET_DIR / name, tmp_path / name)
    monkeypatch.setattr(cf, "RUNTIME_CONSUMERS", frozenset({"test"}))
    monkeypatch.setattr(cg, "load_tags", lambda asset_dir=tmp_path: json.loads((tmp_path / "tags.json").read_text(encoding="utf-8")))
    geo0 = cg.load_geometry("Ascent", tmp_path)
    ys, xs = np.nonzero(geo0.walk_px & ~geo0.sight)
    y, x = int(ys[len(ys) // 2]) // 8 * 8, int(xs[len(xs) // 2]) // 8 * 8
    mf = {**ms.empty(), "features": [breakable("feature-1", rect(x, y, x + 16, y + 16))],
          "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": True, "runtime_consumer": "test"}]}
    mf["features"][0]["base_edits"] = {}
    tags = json.loads((tmp_path / "tags.json").read_text(encoding="utf-8"))
    tags["maps"]["Ascent"]["map_features"] = mf
    (tmp_path / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    entry = tags["maps"]["Ascent"]
    m = cf.manifest(geo0, mf, cf.legacy_masks(entry))
    assets = cf.compile_assets(geo0, mf, cf.bundle_status(geo0, mf, cf.legacy_masks(entry)))
    sha = cf.publish_generation(tmp_path, "Ascent", m, assets)
    return tmp_path, sha, mf


def test_a_published_generation_loads_verifies_and_reports_itself(published):
    from app.control import task as ct

    folder, sha, _ = published
    assert sha == cf.manifest_digest(json.loads((folder / "features" / f"{sha}.json").read_text(encoding="utf-8"))["manifest"])
    assert cf.active_sha("Ascent", folder) == sha
    geo = cg.load_geometry("Ascent", folder)
    assert geo.features_sha == sha and ct.geometry_used(geo)["features"] == sha
    ct.verify_features(geo)                                   # current definitions compile to it
    blocked = geo.features["assets"]["states"]["feature-1:intact"]["blocked"]
    assert blocked and not geo.features["assets"]["states"]["feature-1:broken"]["blocked"]


def test_stale_definitions_tampered_assets_and_the_wrong_generation_are_refused(published):
    from app.control import task as ct

    folder, sha, mf = published
    geo = cg.load_geometry("Ascent", folder)
    with pytest.raises(cg.GeometryError, match="expected feature generation"):
        ct.verify_features(geo, "0123456789abcdef", full=False)
    # the definitions changed after publication (an edit while a generation is live)
    tags = json.loads((folder / "tags.json").read_text(encoding="utf-8"))
    tags["maps"]["Ascent"]["map_features"]["features"][0]["states"][0]["footprint"]["uv"][0][0] -= 200
    (folder / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    with pytest.raises(cg.GeometryError, match="stale"):
        ct.verify_features(geo)
    # compiled bytes that don't match the manifest, and a manifest that doesn't match the file's name
    path = folder / "features" / f"{sha}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["assets"]["states"]["feature-1:intact"]["blocked"].pop()
    path.write_text(json.dumps(body), encoding="utf-8")
    assert cf.verify(body["manifest"], cf.load_generation(folder, sha)["assets"])
    body["manifest"]["compiler"] += 1
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(cg.GeometryError, match="hashes to"):
        cf.load_generation(folder, sha)


def test_the_wrong_generation_is_the_machines_failure_not_the_rounds(published, monkeypatch):
    from app.control import task as ct

    folder, sha, _ = published
    geo = cg.load_geometry("Ascent", folder)
    monkeypatch.setattr(ct, "_load", lambda name, heights=None, digest=None: geo)
    out = ct.compute_task({"key": "k", "map": "Ascent", "blob": b"", "link": {"sides": {}, "db_deaths": []},
                           "features": "0123456789abcdef"})
    assert out["status"] == "failed" and out["error_kind"] == "infra" and "expected feature generation" in out["error"]


def test_a_cached_generation_is_verified_again_when_its_definitions_change(published, monkeypatch):
    """The pointer stays put while tags.json's runtime definitions change: the worker's cached geometry must be
    refused (the machine's failure) as an explicit verify_features would, not reused; an editorial edit is
    still accepted from the cache; restoring the definitions loads again."""
    from app.control import task as ct

    folder, sha, _ = published
    real_load, real_active = cg.load_geometry, cf.active_sha
    loads = []

    def load(name, heights=None):
        loads.append(name)
        return real_load(name, folder, heights=heights)

    monkeypatch.setattr(cg, "load_geometry", load)
    monkeypatch.setattr(cf, "active_sha", lambda name, asset_dir=None: real_active(name, folder))
    monkeypatch.setattr(cg, "visibility", lambda g, asset_dir=None: g)
    monkeypatch.setattr(ct, "_GEOMETRY", {})
    monkeypatch.setattr(ct, "_VERIFIED", {})
    first = ct._load("Ascent")
    assert first.features_sha == sha
    original = (folder / "tags.json").read_text(encoding="utf-8")
    tags = json.loads(original)

    tags["maps"]["Ascent"]["map_features"]["features"][0]["name"] = "renamed"          # editorial
    (folder / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    assert ct._load("Ascent") is first and loads == ["Ascent"]

    tags["maps"]["Ascent"]["map_features"]["features"][0]["initial_state"] = "broken"  # runtime
    (folder / "tags.json").write_text(json.dumps(tags), encoding="utf-8")
    with pytest.raises(cg.GeometryError, match="stale"):
        ct._load("Ascent")
    with pytest.raises(cg.GeometryError, match="stale"):
        ct.verify_features(first)
    out = ct.compute_task({"key": "k", "map": "Ascent", "blob": b"", "link": {"sides": {}, "db_deaths": []}})
    assert out["status"] == "failed" and out["error_kind"] == "infra" and "stale" in out["error"]

    (folder / "tags.json").write_text(original, encoding="utf-8")
    assert ct._load("Ascent").features_sha == sha

    (folder / "tags.json").write_text(original[: len(original) // 2], encoding="utf-8")       # caught mid-write
    out = ct.compute_task({"key": "k", "map": "Ascent", "blob": b"", "link": {"sides": {}, "db_deaths": []}})
    assert out["status"] == "failed" and out["error_kind"] == "infra", "an unreadable tags.json is the machine's"
    (folder / "tags.json").write_text(original, encoding="utf-8")
    assert ct._load("Ascent").features_sha == sha


def test_a_map_without_a_generation_never_reads_its_definitions_on_a_cache_hit(monkeypatch):
    from app.control import task as ct

    geo = copy.copy(open_hall())
    geo.name = "Toy"
    monkeypatch.setattr(cg, "load_geometry", lambda name, heights=None: geo)
    monkeypatch.setattr(cg, "visibility", lambda g, d=None: g)
    monkeypatch.setattr(cf, "active_sha", lambda name, asset_dir=None: None)
    monkeypatch.setattr(ct, "_GEOMETRY", {})
    ct._load("Toy")

    def no_tags(*a, **k):
        raise AssertionError("no generation: nothing to verify")

    monkeypatch.setattr(cg, "load_tags", no_tags)
    assert ct._load("Toy") is geo and list(ct._GEOMETRY) == ["Toy"]


def test_an_interrupted_publication_leaves_the_last_complete_generation(published, monkeypatch):
    import os

    folder, sha, mf = published
    geo = cg.load_geometry("Ascent", folder)
    changed = copy.deepcopy(mf)
    changed["features"][0]["states"][0]["footprint"]["uv"][0][0] -= 100
    m2 = cf.manifest(geo, changed)
    a2 = cf.compile_assets(geo, changed, cf.bundle_status(geo, changed))
    real = os.replace

    def flaky(src, dst):
        if str(dst).endswith("index.json"):
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        cf.publish_generation(folder, "Ascent", m2, a2)
    assert cf.active_sha("Ascent", folder) == sha and cg.load_geometry("Ascent", folder).features_sha == sha


def test_a_failed_publication_never_touches_a_published_generation(published):
    """Republishing the pointer's manifest with assets that don't match it is refused before anything is
    written: the previous pointer and the previous generation's bytes stay exactly as they were, and verify."""
    from app.control.geometry import GeometryError

    folder, sha, _ = published
    path = folder / "features" / f"{sha}.json"
    index_before = (folder / "index.json").read_bytes()
    bytes_before = path.read_bytes()
    body = cf.load_generation(folder, sha)
    wrong = copy.deepcopy(body["assets"])
    wrong["states"]["feature-1:intact"]["blocked"] = [999]
    with pytest.raises(GeometryError):
        cf.publish_generation(folder, "Ascent", body["manifest"], wrong)
    assert (folder / "index.json").read_bytes() == index_before
    assert path.read_bytes() == bytes_before, "the content-addressed file is immutable"
    assert cf.verify(body["manifest"], cf.load_generation(folder, sha)["assets"]) == []
    assert sorted(p.name for p in (folder / "features").iterdir()) == [path.name], "no staging file left behind"
    # republishing the same, valid generation leaves its bytes alone and keeps the pointer on it
    assert cf.publish_generation(folder, "Ascent", body["manifest"], body["assets"]) == sha
    assert path.read_bytes() == bytes_before and cf.active_sha("Ascent", folder) == sha


def test_the_local_store_refuses_a_result_from_another_generation(monkeypatch):
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    import compute_control

    from app.services import replay_control as rc
    from app.services import replay_control_store as store

    calls = []
    monkeypatch.setattr(store, "store_round", lambda *a, **k: calls.append(a) or "stored")
    planned = type("P", (), {"replay_id": 1, "round_number": 2, "fingerprint": "f" * 16, "map_name": "Ascent"})()
    ok = {"status": "ok", "geometry": {"sight": "x"}}
    assert compute_control.store_result(None, planned, ok) == "stored", "no features anywhere: as before"
    monkeypatch.setattr(rc, "geometry_inputs", lambda name, heights=None: {"features": "gen2"})
    assert compute_control.store_result(None, planned, ok) == "skipped: its feature inputs changed while computing"
    assert compute_control.store_result(None, planned, {**ok, "geometry": {"features": "gen1"}}).startswith("skipped")
    assert compute_control.store_result(None, planned, {**ok, "geometry": {"features": "gen2"}}) == "stored"
    assert compute_control.store_result(None, planned, {"status": "failed", "error": "x"}) == "stored", "failures still stored"
    assert len(calls) == 3


def test_rebuilding_the_masks_keeps_the_generation_pointer():
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    import build_control_geometry as bcg

    row = {"sight_sha": "a"}
    bcg.keep_features(row, {"features_sha": "gen1", "height_sha": "h", "sight_sha": "old"})
    assert row == {"sight_sha": "a", "features_sha": "gen1"}


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
    origin = geo.heights.origin_z
    return [{"id": "floor-1", "label": "ground", "z_band": [-0.5, 1.0], "height_sha": geo.height_sha, "origin_z": origin},
            {"id": "floor-2", "label": "bridge", "z_band": [3.0, 5.0], "height_sha": geo.height_sha, "origin_z": origin},
            {"id": "floor-3", "label": "manual", "z_band": None, "height_sha": None},
            {"id": "floor-4", "label": "both", "z_band": [-1.0, 5.0], "height_sha": geo.height_sha, "origin_z": origin},
            # read from an earlier asset whose origin was recorded: rebased, and it binds
            {"id": "floor-5", "label": "rebound", "z_band": [-0.5, 1.0], "height_sha": "000000000000", "origin_z": origin},
            # read from an earlier asset, origin unknown: nobody can say what its numbers mean now
            {"id": "floor-6", "label": "unframed", "z_band": [-0.5, 1.0], "height_sha": "000000000000"}]


def test_a_stacked_floor_door_blocks_the_lower_floor_and_leaves_the_bridge_open():
    geo = bridge()
    door = rect(256, 160, 264, 232)
    cells = np.flatnonzero(grid_rect(256, 160, 264, 232).ravel())
    lower = geo.node_of[cells, 0]
    upper = geo.node_of[cells, 1]
    assert (upper >= 0).all() and (geo.node_z[upper] == 4.0).all() and (geo.node_z[lower] == 0.0).all()
    mf = {**ms.empty(), "floors": floors_for(geo), "features": [feature("feature-1", door, ["floor-1"])]}
    fx = cf.movement_blocks(geo, mf)
    assert fx.pending and not fx.blocked.any(), 'a multi-floor footprint is pending as a whole'
    both = cf.movement_blocks(geo, {**mf, "features": [feature("feature-1", door, ["floor-1", "floor-2"])]})
    assert not both.blocked.any() and both.pending
    # Authored selectors cannot bypass ambiguity.
    for floors in (None, [], ["floor-3"], ["floor-6"], ["floor-4"]):
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


def test_diagnose_flags_diagonal_leaks_off_ground_endpoints_and_floors_read_from_another_asset():
    geo = open_hall()
    diag = {"type": "polyline", "uv": [[U(200), U(96)], [U(400), U(296)]]}
    band = rect(200, 96, 216, 296)
    mf = {**ms.empty(), "features": [feature("feature-1", diag), feature("feature-2", band)],
          "routes": [{"id": "route-3", "endpoints": [{"id": "a", "uv": [U(150), U(150)]}, {"id": "b", "uv": [U(600), U(600)]}]}]}
    got = {(d["where"], d["code"]) for d in cf.diagnose(geo, mf)}
    assert got == {("feature-1.states.closed", "diagonal_leak"), ("route-3.b", "off_ground")}
    stacked = bridge()
    got = {(d["where"], d["code"]) for d in cf.diagnose(stacked, {**ms.empty(), "floors": floors_for(stacked)})}
    assert got == set(), 'deprecated floor catalogue has no runtime diagnostics'


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


def test_the_vertical_rule_covers_every_height_occluders_and_2d_fallbacks():
    """One rule for every occluder: a straight-up line under an all-height occluder's pixel is blocked (its
    span meets every band), alone or batched with other targets; with no height at either end it is the 2D
    answer, for bounded occluders too; off the mask it is clear."""
    pixel = np.zeros((1024, 1024), bool)
    pixel[10, 10] = True
    every = cf.BoundedOccluder("feature-1", pixel, all_height=True)
    bounded = cf.BoundedOccluder("feature-1", pixel, bottom=0.0, top=5.0)
    up = np.array([[10, 10, 4.0]])
    assert cf.blocked_lines([every], (10.0, 10.0, 1.0), up).tolist() == [True]
    assert cf.blocked_lines([bounded], (10.0, 10.0, 1.0), up).tolist() == [True]
    batch = np.array([[10, 10, 4.0], [300, 10, 1.0]])
    assert cf.blocked_lines([every], (10.0, 10.0, 1.0), batch).tolist() == [True, False]
    assert cf.blocked_lines([every], (11.0, 10.0, 1.0), np.array([[11, 10, 4.0]])).tolist() == [False], "off the mask"
    # the 2D fallback: no height at the eye, the target or either; counted
    for eye_z, target_z in ((None, 4.0), (1.0, np.nan), (None, np.nan)):
        for occ in (every, bounded):
            rec = {}
            got = cf.blocked_lines([occ], (10.0, 10.0, eye_z), np.array([[10, 10, target_z]]), rec).tolist()
            assert got == [True] and rec == {"flat_occluder_tests": 1}, (eye_z, target_z, occ.all_height)
    # [bottom, top) still holds straight up: a span touching only the top is clear, only the bottom blocked
    assert cf.blocked_lines([bounded], (10.0, 10.0, 5.0), np.array([[10, 10, 9.0]])).tolist() == [False]
    assert cf.blocked_lines([bounded], (10.0, 10.0, -3.0), np.array([[10, 10, 0.0]])).tolist() == [True]


def door_mf(geo, floor, bottom, top, ref="ground"):
    occ = {"geometry": rect(256, 160, 264, 232),
           "bounds": {"ref": ref, "floor": floor, "bottom": {"status": "known", "value": bottom, "unit": "m"},
                      "top": {"status": "known", "value": top, "unit": "m"}}}
    f = feature("feature-1", None, [floor])
    f["states"][0]["sight"] = [occ]
    return {**ms.empty(), "floors": floors_for(geo), "features": [f]}


def test_bounds_resolve_on_their_floor_and_unknowns_stay_pending():
    geo = geometry_case()
    def sole_mf(floor, bottom=0, top=3.2, ref='ground'):
        mf = source_case()['map_features']
        mf['features'] = mf['features'][:1]
        mf['features'][0]['states'][0]['sight_bounds'] = {
            'ref': ref, 'floor': floor, 'bottom': ms.known(bottom, 'm'), 'top': ms.known(top, 'm')}
        return mf
    [low], pending = cf.sight_occluders(geo, sole_mf('floor-99'))
    assert pending == [] and (low.bottom, low.top) == pytest.approx((-0.9, 2.3))
    [high], _ = cf.sight_occluders(geometry_case(ground_dm=40), sole_mf('floor-2', top=2.5))
    assert (high.bottom, high.top) == pytest.approx((3.1, 5.6))
    world = sole_mf('floor-1', 1.0, 2.0, ref='world')
    [w], _ = cf.sight_occluders(geo, world)
    assert (w.bottom, w.top) == pytest.approx((1.0 - geo.heights.origin_z / 10, 2.0 - geo.heights.origin_z / 10))
    for pending_geo in (geometry_case(multi=True), geometry_case(unresolved=True)):
        occluders, pending = cf.sight_occluders(pending_geo, sole_mf(None))
        assert occluders == [] and pending
    unres = sole_mf(None)
    unres['features'][0]['states'][0]['sight_bounds'] = {'ref': 'unresolved'}
    assert cf.sight_occluders(geo, unres)[0] == [] and cf.sight_occluders(geo, unres)[1]
    # an open door blocks nothing; a footprint blocks sight only under the state's own bounds
    assert cf.sight_occluders(geo, sole_mf(None), {"feature-1": "open"}) == ([], [])
    body = sole_mf(None)
    body['features'][0]['states'][0].pop('sight_bounds')
    assert cf.sight_occluders(geo, body)[0] == [] and cf.sight_occluders(geo, body)[1]
    body["features"][0]["states"][0]["sight_bounds"] = {"ref": "all_height"}
    assert cf.sight_occluders(geo, body)[0][0].all_height
    # a flat map has no heights: resolved bounds block in 2D
    flat = cf.sight_occluders(geometry_case(flat=True), sole_mf(None))
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
    # Sight/traversal policy stays unchanged; these are already placed bounded occluders.
    low = cf.BoundedOccluder('low', mask_px(256, 160, 264, 232), -0.9, 2.3)
    high = cf.BoundedOccluder('high', low.mask, 3.1, 5.6)
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
    low = cf.BoundedOccluder('low', mask_px(256, 160, 264, 232), -0.9, 2.3)
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
    for binding in (None, 'floor-6', 'floor-99'):
        assert cf.bundle_status(geo, mf(on_floor, binding, None), consumers=test)['bundle-2'].publishable
    for args, why in (((off_map, "floor-1", ["floor-1"]), "off_ground"),):
        st = cf.bundle_status(geo, mf(*args), consumers=test)["bundle-2"]
        assert not st.publishable and any(why in r for r in st.reasons), (args, st.reasons)
    # the node domain never depends on a feature's state
    m = mf(on_floor, "floor-1", ["floor-1"])
    shapes = {cf.movement_blocks(geo, m, {"feature-1": s}).blocked.shape for s in ("intact", "broken")}
    assert shapes == {(geo.n,)}


def test_a_bundle_whose_states_bind_no_floor_publishes_nothing():
    """An enabled bundle removing baked-in sight on a height map, whose blocking state's floors are unresolved,
    empty, unbanded, unframed, missing or ambiguous: movement_blocks blocks nothing there, so the bundle is pending
    and none of its base edits reaches the base domain, the compiled assets or the manifest."""
    geo = bridge()
    test = frozenset({"test"})
    block = rect(256, 160, 264, 232)

    def mf(floors, sight_bounds=None):
        f = breakable("feature-1", block, remove=block, floors=floors)
        if sight_bounds is not None:
            f["states"][0]["sight_bounds"] = sight_bounds
        return {**ms.empty(), "floors": floors_for(geo), "features": [f],
                "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": True, "runtime_consumer": "test"}]}

    good = mf(["floor-1"])
    good['features'][0]['states'][0]['footprint'] = rect(304, 160, 312, 168)
    good['features'][0]['base_edits']['remove_sight'] = rect(304, 160, 312, 168)
    assert cf.bundle_status(geo, good, consumers=test)["bundle-2"].publishable, 'sole-floor control'
    sight = np.zeros((1024, 1024), bool)
    sight[160:232, 256:264] = True
    walk = np.ones((1024, 1024), bool)
    cases = {"unresolved": {"status": "unresolved"}, "absent": None, "empty": [], "unbanded": ["floor-3"],
             "unframed": ["floor-6"], "ambiguous": ["floor-4"], "missing": ["floor-9"],
             "unresolved sight bounds": (["floor-1"], {"ref": "unresolved"})}
    for name, floors in cases.items():
        m = mf(*floors) if isinstance(floors, tuple) else mf(floors)
        assert floors is not None or "floors" not in m["features"][0]
        statuses = cf.bundle_status(geo, m, consumers=test)
        st = statuses["bundle-2"]
        assert not st.publishable and st.reasons, (name, st.reasons)
        s, w, opened = cf.reconcile(sight, walk, m, statuses)
        assert opened == {} and (s == sight).all() and (w == walk).all(), name
        assert cf.compile_assets(geo, m, statuses) is None and cf.manifest(geo, m, consumers=test) is None, name


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


def test_pending_rope_cannot_bypass_placement_and_transport_policy_is_preserved():
    geo = bridge()
    node = lambda x, f=0: int(geo.node_of[geo.cell_of_px(x, 196), f])     # noqa: E731
    arcs, pending = cf.compile_routes(geo, rope(geo))
    lower, upper = node(264, 0), node(264, 1)
    assert arcs == [] and pending, 'multi-floor endpoints cannot be manually selected'
    # Keep the independent directed transport/time policy proof using already placed arcs.
    arcs = [cf.Arc('route-1', lower, upper, 0.5, 2.0, None),
            cf.Arc('route-1', upper, lower, 0.3, None, None)]
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
    assert arcs == [] and pending
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


# ---------------------------------------------------------------- a floor binding across a height rebuild
# (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 5)


def deeper_bridge():
    """bridge() after a rebuild found ground 2 m lower in the far corner: every floor is where it was in the
    world, and the map's lowest floor, the frame `node_z` is measured from, is 2 m lower."""
    return toy_heights("BridgeDeeper", [HALL], ground=[((96, 96, 240, 296), 4.0), ((400, 280, 416, 296), -2.0)],
                       upper=[((240, 96, 288, 296), 4.0)])


def test_a_band_is_rebased_when_a_rebuild_moves_the_maps_lowest_floor():
    old, new = bridge(), deeper_bridge()
    assert old.heights.origin_z - new.heights.origin_z == 20 and old.height_sha != new.height_sha
    door = rect(304, 160, 312, 232)
    cells = np.flatnonzero(grid_rect(304, 160, 312, 232).ravel())
    read_on_old = floors_for(old)                        # what the tagger saved before the rebuild
    for name, floor, level in (("ground", "floor-1", 0), ("obsolete bridge selector", "floor-2", 0)):
        mf = {**ms.empty(), "floors": read_on_old, "features": [feature("feature-1", door, [floor])]}
        before, after = cf.movement_blocks(old, mf), cf.movement_blocks(new, mf)
        assert before.pending == [] and after.pending == [], name
        assert sorted(old.node_cell[np.flatnonzero(before.blocked)]) == sorted(cells.tolist()), name
        assert sorted(new.node_cell[np.flatnonzero(after.blocked)]) == sorted(cells.tolist()), name
        assert (np.flatnonzero(after.blocked) == new.node_of[cells, level]).all(), "the same physical floor"
    # the same numbers read in the new frame without rebasing would be wrong: the ground band [-0.5, 1.0] holds
    # no floor of these cells there (the ground is at 2.0 m, the bridge at 6.0 m)
    assert not ((new.node_z[new.node_of[cells, 0]] >= -0.5) & (new.node_z[new.node_of[cells, 0]] <= 1.0)).any()
    assert cf._band_shift(new, read_on_old[0]) == pytest.approx(2.0) and cf._band_shift(old, read_on_old[0]) == 0.0


def test_a_band_with_no_recorded_origin_binds_only_while_its_asset_is_the_maps():
    old, new = bridge(), deeper_bridge()
    door = rect(304, 160, 312, 232)
    legacy = [{"id": "floor-1", "label": "ground", "z_band": [-0.5, 1.0], "height_sha": old.height_sha}]
    mf = {**ms.empty(), "floors": legacy, "features": [feature("feature-1", door, ["floor-1"])]}
    assert cf.movement_blocks(old, mf).pending == [] and cf.movement_blocks(old, mf).blocked.any()
    after = cf.movement_blocks(new, mf)
    assert after.blocked.any() and after.pending == [], 'retired selectors do not prevent sole-floor recovery'
    assert cf.diagnose(new, {**ms.empty(), 'floors': legacy}) == []
    assert cf._band_shift(new, legacy[0]) is None


def test_a_floor_that_moved_out_of_its_band_is_pending_and_the_rest_still_bind():
    old = bridge()
    lowered = toy_heights("BridgeLower", [HALL], ground=[((96, 96, 240, 296), 4.0)], upper=[((240, 96, 288, 296), 1.5)])
    door = rect(256, 160, 264, 232)
    mf = {**ms.empty(), "floors": floors_for(old),
          "features": [feature("feature-1", door, ["floor-1"]), feature("feature-2", door, ["floor-2"])]}
    for f in mf["features"]:
        f["states"][0]["sight_bounds"] = {"ref": "all_height"}     # its sight resolved: only the binding is in question
    assert cf.state_problems(lowered, mf, mf['features'][0]), 'two physical floors remain ambiguous'
    assert cf.state_problems(lowered, mf, mf['features'][1]), 'authored bands cannot choose either floor'


def test_a_published_generation_does_not_survive_a_new_height_digest():
    # The limitation this plan leaves, pinned: a generation's manifest names the heights it was compiled
    # against, so the same definitions on rebuilt heights are another generation. Until generations can be
    # recompiled, a map that has one is never rebuilt automatically (tests/replays/test_heights_remote.py) and
    # `activate` refuses it (tests/replays/test_control_heights_db.py).
    old, new = bridge(), deeper_bridge()
    test = frozenset({"test"})
    block = rect(304, 160, 312, 168)
    f = breakable("feature-1", block, remove=block, floors=["floor-1"])
    mf = {**ms.empty(), "floors": floors_for(old), "features": [f],
          "bundles": [{"id": "bundle-2", "members": ["feature-1"], "enabled": True, "runtime_consumer": "test"}]}
    before, after = cf.manifest(old, mf, consumers=test), cf.manifest(new, mf, consumers=test)
    assert before is not None and after is not None, "the bundle is publishable on both: its floor rebased"
    assert before["height"] == old.height_sha and after["height"] == new.height_sha
    assert cf.manifest_digest(before) != cf.manifest_digest(after)
    assets = cf.compile_assets(old, mf, cf.bundle_status(old, mf, consumers=test))
    assert cf.verify(before, assets, new, mf, consumers=test), "verification of the old generation on the new heights fails"
# Automatic placement: every required shape/dependency participates, never a synthetic floor.
def test_synthetic_lowest_node_is_not_a_measured_floor():
    geo = geometry_case(unresolved=True)
    cell = 40 * 128 + 40
    assert geo.node_of[cell, 0] >= 0
    bound = cf.resolve_cells(geo, [cell], 'features.feature-1.states.closed.footprint')
    assert not bound.ok and bound.nodes == ()
    assert bound.reasons[0]['code'] in {'missing_floor', 'unresolved_height'}
    assert bound.reasons[0]['cells'] == [cell]
    assert bound.reasons[0]['floor_counts'] == [0]


def test_sole_floor_binding_follows_changed_ground_and_ignores_authored_selectors():
    mf = source_case()['map_features']
    mf['features'][0]['floors'] = ['floor-99']
    for ground in (0, 20):
        geo = geometry_case(ground_dm=ground)
        p = cf.placement(geo, mf)['feature-1']
        assert p.ok and p.nodes == (5160,)
        assert p.ground_m == pytest.approx((ground / 10 - 0.9,))
        assert cf.movement_blocks(geo, mf).blocked[5160]


@pytest.mark.parametrize('part', ['movement', 'sight', 'phase', 'trigger', 'endpoint', 'access', 'ground'])
def test_one_bad_required_part_disables_its_whole_bundle_only(part):
    mf = source_case()['map_features']
    mf['bundles'][1]['enabled'] = True
    f = mf['features'][0]
    good = copy.deepcopy(f['states'][0]['footprint'])
    # feature 1 otherwise lives on sole floor cell 41; bad part lives on multi-floor cell 40.
    safe = rect(328, 320, 336, 328)
    f['states'][0]['footprint'] = safe
    if part == 'movement':
        f['states'][0]['footprint'] = good
    elif part == 'sight':
        f['states'][0]['sight'] = [{'geometry': good, 'bounds': {'ref': 'all_height'}}]
    elif part == 'phase':
        f['rotation'] = {'phases': [{'footprint': good}]}
    elif part == 'trigger':
        mf['triggers'] = [{'id': 'trigger-1', 'type': 'switch', 'geometry': good,
                           'targets': [{'feature': 'feature-1', 'event': 'switch'}]}]
    elif part in ('endpoint', 'access'):
        mf['routes'] = [{'id': 'route-1', 'kind': 'zipline', 'owner': 'feature-1',
                         'endpoints': [{'id': 'a', 'uv': [U(332), U(324)]},
                                       {'id': 'b', 'uv': [U(340), U(324)]}],
                         'directions': [{'from': 'a', 'to': 'b', 'entry': secs(1), 'transit': secs(1)}]}]
        if part == 'endpoint':
            mf['routes'][0]['endpoints'][0]['uv'] = [U(324), U(324)]
        else:
            mf['routes'][0]['access'] = {'sites': [{'id': 'm', 'uv': [U(324), U(324)]}]}
    else:
        f['base_edits'] = {'potential_ground': good}
    geo = geometry_case(multi=True)
    p = cf.placement(geo, mf)['feature-1']
    assert not p.ok and p.nodes == ()
    assert any(r['code'] == 'multi_floor' and r['cells'] == [5160]
               and r['floor_counts'] == [2] and r['cell_count'] == 1 for r in p.reasons)
    statuses = cf.bundle_status(geo, mf, consumers=frozenset({'test'}))
    assert not statuses['bundle-1'].publishable and statuses['bundle-2'].publishable
    assets = cf.compile_assets(geo, mf, statuses)
    assert set(assets['states']) == {'feature-2:closed', 'feature-2:open'}
    assert cf.placement(geometry_case(), mf)['feature-1'].ok


def test_flat_candidate_ground_is_private_and_pending_edits_do_not_escape():
    sight, walk, legacy = baked_in()
    geo = cg.geometry_from_masks('Flat', sight, walk, 7e-5, [])
    mf = block_mf()
    statuses = cf.bundle_status(geo, mf, legacy, consumers=frozenset({'test'}))
    assert statuses['bundle-2'].publishable, statuses['bundle-2'].reasons
    assert cf.compile_assets(geo, mf, statuses)['states']['feature-1:intact']['blocked']
    mf['triggers'] = [{'id': 'trigger-1', 'geometry': {'type': 'point', 'uv': [U(600), U(600)]},
                       'targets': [{'feature': 'feature-1', 'event': 'shoot'}]}]
    statuses = cf.bundle_status(geo, mf, legacy, consumers=frozenset({'test'}))
    assert not statuses['bundle-2'].publishable
    s, w, opened = cf.reconcile(sight, walk, mf, statuses)
    assert opened == {} and np.array_equal(s, sight) and np.array_equal(w, walk)


def test_placement_reports_empty_invalid_and_off_ground_without_clamping():
    geo = geometry_case(flat=True)
    for cells, code in (([], 'empty_geometry'), ([-1, 16384], 'off_map'), ([0], 'off_ground')):
        p = cf.resolve_cells(geo, cells, 'test')
        assert not p.ok and p.nodes == () and any(r['code'] == code for r in p.reasons)
    mf = source_case()['map_features']
    mf['features'][0]['states'][0]['footprint'] = {'type': 'point', 'uv': [10001, 10001]}
    p = cf.placement(geo, mf)['feature-1']
    assert not p.ok and any(r['code'] == 'off_map' for r in p.reasons)
