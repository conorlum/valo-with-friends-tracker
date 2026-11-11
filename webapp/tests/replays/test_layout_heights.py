"""Height inputs track each layout without changing single-layout identities."""

import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest

from app.control import geometry as cg
from app.replays import height_inputs as hi, layouts
from tests.replays import layout_golden as golden
from tests.replays.test_control_store import db, factory  # noqa: F401 (fixtures)
from app.models.replay import ControlHeight, Replay, ReplayRound
from app.replays import control_format as cf
from app.services import control_heights as ch
from app.services import replay_control_remote as remote, replay_heights_remote as hr


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


@pytest.fixture
def height_layouts(layout_assets, monkeypatch):
    folder, registry = layout_assets
    monkeypatch.setattr(layouts, "load_registry", lambda: registry)
    monkeypatch.setattr(hi, "MAPS_JSON", folder / "maps.json")
    monkeypatch.setattr(hi, "MUST_BLOCK", folder / "must_block.json")
    (folder / "must_block.json").write_text('{"lines": []}', encoding="utf-8")
    (folder / "must_block.Toy_13_10.json").write_text(
        '{"lines": [], "layout": "later"}', encoding="utf-8")
    return folder, registry


def add_replay(db, branch="release-13.08", **changes):
    number = db.query(Replay).count() + 1
    replay = Replay(match_uuid=str(UUID(int=number)), map_name="Toy", round_count=1,
                    format_version=1, recipe="p.c11.f1.a1", game_branch=branch,
                    source_sha256="a" * 64, source="local", link_status="unlinked", link_inputs={})
    for key, value in changes.items():
        setattr(replay, key, value)
    db.add(replay)
    db.flush()
    db.add(ReplayRound(replay_id=replay.id, round_number=1, data=b"synthetic"))
    db.commit()
    return replay


def remember_build(db, key, manifest, digest="a" * 12):
    row = ControlHeight(map_name=key, digest=digest, asset=b"synthetic", report={},
                        match_uuids=hi.matches(manifest), rules={"version": cf.HEIGHT_VERSION},
                        inputs=manifest, inputs_sha=hi.digest(manifest), status="active")
    db.add(row)
    db.commit()
    return row


def test_current_manifests_pool_both_layouts_and_use_their_own_checks(db, height_layouts):
    base = add_replay(db)
    later = add_replay(db, "release-13.10")
    manifests = hr.current_manifests(db)
    assert set(manifests) == {"Toy", "Toy_13_10"}
    for key, own, other, from_key in (("Toy", base, later, "Toy_13_10"),
                                      ("Toy_13_10", later, base, "Toy")):
        manifest, rows = manifests[key]
        assert rows == [(own.id, own.match_uuid, 1), (other.id, other.match_uuid, 1)]
        assert hi.matches(manifest) == sorted([base.match_uuid, later.match_uuid])
        assert manifest["regions"]["pool"] == [[other.match_uuid, from_key]]
        assert manifest["must_block"] == hi.must_block_sha(key=key)
        assert manifest["geometry"] == hi.geometry_identity(key)
    assert len(hr.plan_maps(db).due) == 2, "first-build minimum includes borrowed replays"


def test_single_layout_is_unchanged_and_adding_a_layout_rebuilds_base(db, height_layouts):
    _, registry = height_layouts
    later_layout = registry["maps"]["Toy"].pop()
    base = add_replay(db)
    add_replay(db)
    before, rows = hr.current_manifests(db)["Toy"]
    expected = hi.manifest("Toy", [[uuid, base.recipe, base.source_sha256, count]
                                   for _, uuid, count in rows],
                           hi.geometry_identity("Toy"), hi.must_block_sha())
    assert "regions" not in before
    assert hi.digest(before) == hi.digest(expected)
    remember_build(db, "Toy", before)
    assert hr.plan_maps(db).due == []
    registry["maps"]["Toy"].append(later_layout)
    after = hr.current_manifests(db)
    assert set(after) == {"Toy"}, "an unplayed layout does not get a build"
    assert hi.digest(after["Toy"][0]) != hi.digest(before)
    assert [(d.map_name, d.why) for d in hr.plan_maps(db).due] == [("Toy", "its inputs changed")]


@pytest.mark.parametrize("key,own_branch,pool_branch", [
    ("Toy_13_10", "release-13.10", "release-13.08"),
    ("Toy", "release-13.08", "release-13.10"),
])
def test_every_five_counts_only_own_uploads(db, height_layouts, key, own_branch, pool_branch):
    add_replay(db, own_branch)
    add_replay(db, pool_branch)
    before = hr.current_manifests(db)[key][0]
    remember_build(db, key, before)
    for _ in range(5):
        add_replay(db, pool_branch)
    after = hr.current_manifests(db)[key][0]
    moved = hi.changes(before, after)
    assert len(moved["added"]) == 5 and not moved["other"]
    assert key not in {d.map_name for d in hr.plan_maps(db).due}
    for _ in range(4):
        add_replay(db, own_branch)
    assert key not in {d.map_name for d in hr.plan_maps(db).due}
    add_replay(db, own_branch)
    assert [d.why for d in hr.plan_maps(db).due if d.map_name == key] == ["5 new matches"]


def test_unknown_pending_invalid_and_old_replays_are_not_pooled(db, height_layouts):
    folder, _ = height_layouts
    base = add_replay(db)
    add_replay(db, "")  # stored game_branch is non-null; empty means unknown
    add_replay(db, "release-13.10", recipe="p.c10.f1.a1")
    add_replay(db, "release-13.10", round_count=2)  # incomplete round set
    assert set(hr.current_manifests(db)) == {"Toy"}
    assert hi.matches(hr.current_manifests(db)["Toy"][0]) == [base.match_uuid]
    add_replay(db, "release-13.10")
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    del index["maps"]["Toy_13_10"]
    (folder / "index.json").write_text(json.dumps(index), encoding="utf-8")
    assert hi.matches(hr.current_manifests(db)["Toy"][0]) == [base.match_uuid]


def test_pool_membership_is_not_other_but_masks_are(height_layouts):
    _, registry = height_layouts
    old = hi.manifest("Toy", [], hi.geometry_identity("Toy"), hi.must_block_sha(),
                      regions=hi.regions_for("Toy", [], registry=registry))
    new = json.loads(json.dumps(old))
    new["regions"]["pool"] = [[str(UUID(int=1)), "Toy_13_10"]]
    assert hi.digest(old) != hi.digest(new)
    assert hi.changes(old, new) == {"added": [], "gone": [], "other": False}
    new["regions"]["masks"][1][1] = "changed"
    assert hi.changes(old, new)["other"]


@pytest.mark.parametrize("held_key,sent_key", [("Toy_13_10", "Toy"), ("Toy", "Toy_13_10")])
@pytest.mark.parametrize("kind", ["control", "gaps"])
def test_held_rounds_follow_asset_key(monkeypatch, held_key, sent_key, kind):
    planned = [SimpleNamespace(map_name="Toy", asset_key=key, computable=True, reason="missing")
               for key in ("Toy", "Toy_13_10")]
    monkeypatch.setattr(remote.replay_control, "plan", lambda *a, **k: planned)
    monkeypatch.setattr(remote.replay_gaps, "plan_gaps", lambda *a: planned)
    monkeypatch.setattr(remote, "supports_gaps", lambda health: True)
    monkeypatch.setattr(remote, "_sendable", lambda state, now, rounds, **kw: rounds)
    sent = {}

    def send(session, client, state, now, counts, todo, *, kind, **kw):
        sent[kind] = [p.asset_key for p in todo]
        return kind == "control" and kind == target_kind

    target_kind = kind
    monkeypatch.setattr(remote, "_send", send)
    remote._submit(None, SimpleNamespace(health=lambda: {}), remote.State(), 0, {"held": 0},
                   held_maps=frozenset({held_key}), session_factory=None)
    assert sent[kind] == [sent_key]


def test_active_height_rows_are_separate_by_asset_key(db):
    manifest = hi.manifest("Toy", [], {"sight": "s", "walk": "w", "scale": 1}, None)
    remember_build(db, "Toy", manifest)
    assert ch.select_height(db, "Toy_13_10", None) == hi.HeightSelection("flat")
    remember_build(db, "Toy_13_10", {**manifest, "map": "Toy_13_10"}, digest="b" * 12)
    assert ch.active_digests(db) == {"Toy": "a" * 12, "Toy_13_10": "b" * 12}
    assert ch.select_height(db, "Toy_13_10", None) == hi.HeightSelection("asset", "b" * 12)
    assert ch.select_height(db, "Toy", None) == hi.HeightSelection("asset", "a" * 12)
