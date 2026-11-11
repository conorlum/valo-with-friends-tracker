"""Height inputs track each layout without changing single-layout identities."""

import hashlib
import json

import numpy as np
import pytest

from app.control import geometry as cg
from app.replays import height_inputs as hi, layouts
from tests.replays import layout_golden as golden


@pytest.fixture
def layout_assets(tmp_path, monkeypatch):
    keys = ("Toy", "Toy_13_10")
    walk = np.ones((8, 8), dtype=bool)
    regions = np.zeros_like(walk)
    regions[2:4, 2:4] = True
    for key in keys:
        cg.write_mask_png(tmp_path / f"{key}.walk.png", walk)
        cg.write_mask_png(tmp_path / f"{key}.sight.png", ~walk)
    (tmp_path / "regions").mkdir()
    cg.write_mask_png(tmp_path / "regions/Toy_13_10.png", regions)
    (tmp_path / "index.json").write_text(json.dumps({"maps": {
        key: {"sight_sha": key + "-sight", "walk_sha": key + "-walk"} for key in keys
    }}), encoding="utf-8")
    (tmp_path / "maps.json").write_text(
        json.dumps({"Toy": {"xMultiplier": 7e-5}}), encoding="utf-8")
    registry = {"maps": {"Toy": [
        {"id": "Toy@base", "key": "Toy", "start": None, "drawn_from": "13.08", "regions": None},
        {"id": "Toy@13.10", "key": "Toy_13_10", "start": "13.10", "drawn_from": "13.10",
         "regions": "regions/Toy_13_10.png"},
    ]}}
    monkeypatch.setattr(hi, "CONTROL_DIR", tmp_path)
    return tmp_path, registry


@pytest.mark.parametrize("map_name", sorted(layouts.load_registry()["maps"]))
def test_single_layout_manifest_keeps_golden_digest(map_name):
    expected = json.loads(golden.GOLDEN_PATH.read_text(encoding="utf-8"))
    regions = hi.regions_for(map_name, [])
    assert regions is None
    manifest = hi.manifest(map_name, golden.FIXED_REPLAYS, hi.geometry_identity(map_name),
                           golden._must_block_sha_lf(), regions=regions)
    assert "regions" not in manifest
    assert hi.digest(manifest) == expected["maps"][map_name]["height_manifest_digest"]


@pytest.mark.parametrize("asset_key,from_key", [("Toy", "Toy_13_10"), ("Toy_13_10", "Toy")])
def test_multi_layout_manifest_records_masks_and_sorted_pool(layout_assets, asset_key, from_key):
    folder, registry = layout_assets
    pool = [("BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB", from_key),
            ("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", from_key)]
    mask_sha = hashlib.sha256((folder / "regions/Toy_13_10.png").read_bytes()).hexdigest()[:12]
    regions = hi.regions_for(asset_key, pool, registry=registry, control_dir=folder)
    assert regions == {"masks": [["Toy", None], ["Toy_13_10", mask_sha]],
                       "pool": [[uuid.lower(), key] for uuid, key in reversed(pool)]}
    geometry = hi.geometry_identity(asset_key, folder, folder / "maps.json")
    manifest = hi.manifest(asset_key, golden.FIXED_REPLAYS, geometry, "checks", regions=regions)
    assert manifest["regions"] == regions
    assert hi.regions_for(asset_key, pool[::-1], registry=registry) == regions
    assert hi.regions_for(asset_key, [], registry=registry)["pool"] == []


def test_changed_region_bytes_move_manifest_digest(layout_assets):
    folder, registry = layout_assets
    geometry = hi.geometry_identity("Toy_13_10", folder, folder / "maps.json")

    def manifest():
        return hi.manifest("Toy_13_10", golden.FIXED_REPLAYS, geometry, "checks",
                           regions=hi.regions_for("Toy_13_10", [], registry=registry))

    before = hi.digest(manifest())
    path = folder / "regions/Toy_13_10.png"
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    assert hi.digest(manifest()) != before


def test_layout_check_sets_are_separate_and_explicit_path_wins(layout_assets, monkeypatch):
    folder, _ = layout_assets
    base = folder / "must_block.json"
    base.write_text('{"lines": []}', encoding="utf-8")
    monkeypatch.setattr(hi, "MUST_BLOCK", base)
    base_sha = hashlib.sha256(base.read_bytes()).hexdigest()[:12]
    assert hi.must_block_path(None) == hi.must_block_path("Toy") == base
    assert hi.must_block_sha() == hi.must_block_sha(key="Toy") == base_sha
    later = folder / "must_block.Toy_13_10.json"
    later.write_text('{"lines": [{"map": "Toy_13_10"}]}', encoding="utf-8")
    assert hi.must_block_path("Toy_13_10") == later
    assert hi.must_block_set(key="Toy_13_10") == (
        [{"map": "Toy_13_10"}], hashlib.sha256(later.read_bytes()).hexdigest()[:12])
    assert hi.must_block_sha() == hi.must_block_sha(key="Toy") == base_sha
    assert hi.must_block_set(base, key="Toy_13_10") == ([], base_sha)
    assert hi.must_block_sha(base, key="Toy_13_10") == base_sha
    assert hi.must_block_sha(key="Toy_13_12") is None
    with pytest.raises(hi.CheckSetError, match="missing or unreadable"):
        hi.must_block_set(key="Toy_13_12")


def test_later_geometry_uses_map_scale_and_layout_masks(layout_assets):
    folder, _ = layout_assets
    assert hi.geometry_identity("Toy_13_10", folder, folder / "maps.json") == {
        "sight": "Toy_13_10-sight", "walk": "Toy_13_10-walk", "scale": 7e-5}


@pytest.mark.parametrize("first,second,expected", [
    ("Toy", "Toy_13_12", ["Toy_13_10", "Toy_13_12"]),
    ("Toy_13_12", "Toy", ["Toy_13_10", "Toy_13_12"]),
    ("Toy_13_10", "Toy_13_12", ["Toy_13_12"]),
    ("Toy_13_12", "Toy_13_10", ["Toy_13_12"]),
    ("Toy_13_10", "Toy_13_10", []),
])
def test_changed_between_includes_masks_after_earlier_layout(layout_assets, first, second, expected):
    _, registry = layout_assets
    registry["maps"]["Toy"].append({
        "id": "Toy@13.12", "key": "Toy_13_12", "start": "13.12", "drawn_from": "13.12",
        "regions": "regions/Toy_13_12.png"})
    assert hi.changed_between("Toy", first, second, registry=registry) == expected


def test_changed_between_skips_layouts_without_region_paths(layout_assets):
    _, registry = layout_assets
    registry["maps"]["Toy"][1]["regions"] = None
    assert hi.changed_between("Toy", "Toy", "Toy_13_10", registry=registry) == []
    assert hi.region_masks("Toy", registry=registry) == [("Toy", None), ("Toy_13_10", None)]


@pytest.mark.parametrize("unreadable", [False, True])
def test_unavailable_region_mask_blocks_build(layout_assets, unreadable):
    folder, registry = layout_assets
    path = folder / "regions/Toy_13_10.png"
    path.unlink()
    if unreadable:
        path.mkdir()
    with pytest.raises(hi.CheckSetError, match="Toy_13_10.png"):
        hi.region_masks("Toy", registry=registry, control_dir=folder)
    with pytest.raises(hi.CheckSetError):
        hi.regions_for("Toy_13_10", [], registry=registry)
