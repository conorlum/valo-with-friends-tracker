"""Height inputs track each layout without changing single-layout identities."""

import hashlib
import json
from pathlib import Path
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
from app.control import height_build as hb, height_job, height_motion as hm
from app.replays import format as fmt
from tests.replays.control_toys import height_blob, standing
from tests.replays.test_control_heights import write_blobs


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


@pytest.mark.parametrize("target,borrowed", [("Toy_13_10", "Toy"), ("Toy", "Toy_13_10")])
def test_blob_dir_masks_pool_tracks_and_kills_only(height_layouts, monkeypatch, target, borrowed):
    folder, _ = height_layouts
    own, pool = str(UUID(int=101)), str(UUID(int=102))
    crossing = ("A", [(0.0, 1, 3, 20, 1.0), (4.0, 5, 3, 80, 2.0)])
    blob = height_blob({0: crossing, 1: standing(3, 3, 1.0),
                        5: standing(5, 3, 1.0, "B")}, t_end=4.0)
    u, v = round(5 / cg.PX * 10000), round(3 / cg.PX * 10000)
    blob["kills"] = [
        {"i": 0, "t": 2.0, "killer": 1, "victim": 5, "u": u, "v": v},
        {"i": 1, "t": 2.0, "killer": 5, "victim": 1,
         "u": round(3 / cg.PX * 10000), "v": v},
        {"i": 2, "t": 4.0, "killer": 0, "victim": 5, "u": u, "v": v},
    ]
    blobs = write_blobs(folder / "blobs", [(own, 1, blob), (pool, 1, blob)], name="Toy")
    # Preserve decoder identity to check that own rounds pass through as the same objects.
    monkeypatch.setattr(fmt, "decode_blob", lambda raw: blob)
    reads = []
    reader = cg.read_mask_png
    monkeypatch.setattr(cg, "read_mask_png", lambda path: (reads.append(path), reader(path))[1])
    source = height_job.BlobDir(blobs, target, pool=[(pool, borrowed)])
    rows = list(source)
    assert rows[0][2] is blob
    masked = rows[1][2]
    assert masked is not blob and len(blob["tracks"]["0"]) == 1
    original = next(hm.tracks(blob))
    pieces = [row for row in hm.tracks(masked) if row[0] == 0]
    assert len(pieces) == 2
    keep = ~((original[2] >= 2) & (original[2] < 4))
    for column in range(1, 5):
        np.testing.assert_allclose(np.concatenate([p[column] for p in pieces]), original[column][keep])
    original_yaw = np.cumsum(blob["tracks"]["0"][0]["yaw"]) % 360
    masked_yaw = np.concatenate([np.cumsum(piece["yaw"]) % 360 for piece in masked["tracks"]["0"]])
    np.testing.assert_array_equal(masked_yaw, original_yaw[keep])
    assert pieces[0][1][-1] < pieces[1][1][0] - 1 / blob["hz"]
    assert list(masked["tracks"]["1"]) == []
    assert masked["tracks"]["5"] == blob["tracks"]["5"]
    assert masked["kills"] == [blob["kills"][2]]
    assert len(blob["kills"]) == 3
    geo = cg.geometry_from_masks("Toy", np.zeros((cg.PX, cg.PX), bool),
                                 np.ones((cg.PX, cg.PX), bool), 7e-5)
    stands, _, _, counts = hb.all_ground([(pool, 1, masked)], geo)
    assert 1 not in {stand.slot for stand in stands}
    assert 5 in {stand.slot for stand in stands}
    assert counts["rounds"] == 1
    assert source.per_match() == {own: 1, pool: 1}
    list(source)
    assert len(reads) == 1, "the union is reused on later passes"


def test_base_blob_dir_preserves_decoded_objects(tmp_path, monkeypatch):
    blob = height_blob({0: standing(3, 3, 1.0)}, t_end=2.0)
    blob["map"] = "Ascent"
    folder = write_blobs(tmp_path / "blobs", [("synthetic", 1, blob)])
    monkeypatch.setattr(fmt, "decode_blob", lambda raw: blob)
    assert list(height_job.BlobDir(folder, "Ascent"))[0][2] is blob


@pytest.fixture
def height_child(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from replay_worker import height_job as child

    return child


@pytest.mark.parametrize("key", ["Toy", "Toy_13_10"])
def test_child_recomputes_regions_and_uses_layout_checks(height_layouts, monkeypatch, height_child, key):
    folder, _ = height_layouts
    uuid = str(UUID(int=103))
    blob = height_blob({0: standing(5, 3, 1.0)}, t_end=2.0)
    directory = write_blobs(folder / "blobs", [(uuid, 1, blob)], name="Toy")
    other = "Toy" if key != "Toy" else "Toy_13_10"
    manifest = hi.manifest(key, [[uuid, "p.c11.f1.a1", "a" * 64, 1]],
                           hi.geometry_identity(key), hi.must_block_sha(key=key),
                           regions=hi.regions_for(key, [(uuid, other)]))
    seen = []

    def build(name, source, **options):
        seen.append((name, list(source), options))
        return {"status": "ok", "asset": b"synthetic"}

    monkeypatch.setattr(height_job, "run", build)
    answer = height_child.run({"key": hi.key(manifest), "map": key, "manifest": manifest, "dir": str(directory)})
    assert answer["status"] == "ok", answer
    assert answer["inputs_sha"] == hi.digest(manifest) and answer["rounds"] == 1
    assert seen[0][0] == key and seen[0][1][0][2]["map"] == "Toy"
    assert seen[0][2]["must_block"] == hi.must_block_path(key)


@pytest.mark.parametrize("missing", ["target", "mask", "map", "pool"])
def test_child_refuses_unavailable_regions_as_unknown_layout(height_layouts, monkeypatch, height_child, missing):
    folder, registry = height_layouts
    key = "Toy_13_10"
    manifest = hi.manifest(key, [], hi.geometry_identity(key), hi.must_block_sha(key=key),
                           regions=hi.regions_for(key, []))
    if missing == "target":
        registry["maps"]["Toy"].pop()
    elif missing == "map":
        registry["maps"].pop("Toy")
    elif missing == "pool":
        manifest["regions"]["pool"] = [[str(UUID(int=104)), "Toy_13_12"]]
    else:
        (folder / "regions/Toy_13_10.png").unlink()
    monkeypatch.setattr(height_job, "run", lambda *a, **k: pytest.fail("an incompatible build ran"))
    answer = height_child.run({"key": hi.key(manifest), "map": key, "manifest": manifest, "dir": str(folder)})
    assert (answer["status"], answer["error_kind"], answer.get("error_code")) == (
        "failed", "compat", "unknown_layout")


@pytest.fixture
def height_client(monkeypatch):
    client = SimpleNamespace(health=lambda: {"idle": True, "heights": {}}, opened=[])

    def open_build(*args, **options):
        client.opened.append(args)
        return {"id": "synthetic", "status": "running"}

    client.open_build = open_build
    monkeypatch.setattr(ch, "active_digests", lambda session: {})
    return client


@pytest.mark.parametrize("regions,protocol,opens", [(True, None, False), (True, 0, False),
    (True, 1, True), (True, 2, True), (False, None, True)])
def test_site_requires_heights_protocol_for_regions(monkeypatch, height_client, regions, protocol, opens):
    manifest = hi.manifest("Toy", [], {}, None)
    if regions:
        manifest["regions"] = {"masks": [["Toy", None]], "pool": []}
    due = hr.Due("Toy", manifest, [], "synthetic")
    monkeypatch.setattr(hr, "plan_maps", lambda session: hr.Plan(due=[due]))
    health = {"idle": True, "heights": {} if protocol is None else {"protocol": protocol}}
    height_client.health = lambda: health
    state, counts = hr.HeightState(), {}
    assert hr.step(None, None, height_client, state, 10.0, counts) == {"Toy"}
    assert bool(height_client.opened) is opens
    assert state.tries == {} and counts["heights_failed"] == 0


def test_unknown_layout_height_failure_waits_at_no_cost(monkeypatch, height_client):
    manifest = hi.manifest("Toy", [], {}, None)
    due = hr.Due("Toy", manifest, [], "synthetic")
    key = hi.key(manifest)
    monkeypatch.setattr(hr, "plan_maps", lambda session: hr.Plan(due=[due]))
    build = hr.Build("Toy", key, manifest, [], 0.0, job_id="synthetic")
    state = hr.HeightState(builds={key: build})
    height_client.build = lambda job: {"status": "failed", "error_kind": "compat",
                                     "error_code": "unknown_layout"}
    height_client.cancel_build = lambda job: None
    counts = {}
    assert hr.step(None, None, height_client, state, 10.0, counts) == {"Toy"}
    assert not state.builds and state.tries == {} and counts["heights_failed"] == 0
    assert hr.step(None, None, height_client, state, 10.0 + hr.BACKOFF_S - 1, counts) == {"Toy"}
    assert height_client.opened == []
    assert hr.step(None, None, height_client, state, 10.0 + hr.BACKOFF_S, counts) == {"Toy"}
    assert len(height_client.opened) == 1 and state.tries == {}


def test_regions_remain_held_when_worker_has_no_heights_health(monkeypatch, height_client):
    from app.services import replay_upload

    manifest = hi.manifest("Toy", [], {}, None, regions={"masks": [], "pool": []})
    monkeypatch.setattr(hr, "plan_maps", lambda session: hr.Plan(due=[hr.Due("Toy", manifest, [], "synthetic")]))
    height_client.health = lambda: {"idle": True, "recipe": replay_upload.site_recipe()}
    state = hr.HeightState()
    assert hr.step(None, None, height_client, state, 10.0, {}) == {"Toy"}
    assert not height_client.opened and not state.tries


def test_unopened_regions_build_waits_if_worker_protocol_changes(monkeypatch, height_client):
    manifest = hi.manifest("Toy", [], {}, None, regions={"masks": [], "pool": []})
    due = hr.Due("Toy", manifest, [], "synthetic")
    key = hi.key(manifest)
    monkeypatch.setattr(hr, "plan_maps", lambda session: hr.Plan(due=[due]))
    state = hr.HeightState(builds={key: hr.Build("Toy", key, manifest, [], 0.0)})
    assert hr.step(None, None, height_client, state, 10.0, {}) == {"Toy"}
    assert not height_client.opened and not state.tries


@pytest.mark.parametrize("health", [None, {"heights": {}}, {"idle": True, "heights": []}])
def test_unconfirmed_regions_protocol_spends_no_try(monkeypatch, height_client, health):
    manifest = hi.manifest("Toy", [], {}, None, regions={"masks": [], "pool": []})
    monkeypatch.setattr(hr, "plan_maps", lambda session: hr.Plan(due=[hr.Due("Toy", manifest, [], "synthetic")]))
    height_client.health = lambda: health
    state = hr.HeightState()
    assert hr.step(None, None, height_client, state, 10.0, {}) == {"Toy"}
    assert not height_client.opened and not state.tries


def test_every_layout_of_a_map_shares_one_heights_lock():
    """F7/S12 (2026-11-12): heights writers lock by asset key, the round stores by the map name; one lock."""
    assert ch.lock_name("Ascent_13_10") == ch.lock_name("Ascent") == "control-heights:Ascent"
    assert ch.lock_name("Lotus_13_10") != ch.lock_name("Ascent_13_10")
