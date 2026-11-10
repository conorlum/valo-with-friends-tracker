"""Control selects replay layouts without changing the base identities."""

import base64
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from test_control_store import db, factory, linked, put_row
from test_replay_store import condensed, pg
from test_replay_routes import request
from test_control_remote import FakeWorker
from app.control import geometry as cg
from app.db import Base
from app.models.replay import ControlFeatureArtifact, ControlHeight, Replay, ReplayRound, ReplayRoundGapRun
from app.replays import choke_assets, layouts
from app.routers import replays as routes
from app.services import control_feature_artifacts as artifacts
from app.services import replay_control as rc
from app.services import replay_control_remote as remote
from app.services import replay_gaps
from app.services import replay_gaps_store, replay_progress
from tests.replays import layout_golden as golden


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def layout_assets(tmp_path, monkeypatch):
    from tests.replays.control_toys import toy_geometry

    geo = toy_geometry("LayoutToy", [(160, 480, 240, 272)], cache_dir=tmp_path / "visibility")
    for key, walls in (("Toy", False), ("Toy_13_10", True)):
        sight, walk = geo.sight.copy(), geo.walk_px.copy()
        if walls:
            sight[240:248, 240:248] = True
            walk[240:248, 240:248] = False
        cg.write_mask_png(tmp_path / f"{key}.sight.png", sight)
        cg.write_mask_png(tmp_path / f"{key}.walk.png", walk)
    index = {"maps": {key: {"walk_sha": key + "-walk", "sight_sha": key + "-sight",
                            "kill_lines": {"passes": True}} for key in ("Toy", "Toy_13_10")}}
    write_json(tmp_path / "index.json", index)
    write_json(tmp_path / "tags.json", {"maps": {"Toy": {}, "Toy_13_10": {}}})
    write_json(tmp_path / "maps.json", {"Toy": {"xMultiplier": 7e-5}})
    registry = {"maps": {"Toy": [
        {"id": "Toy@base", "key": "Toy", "start": None, "drawn_from": "13.08", "regions": None},
        {"id": "Toy@13.10", "key": "Toy_13_10", "start": "13.10", "drawn_from": "13.10", "regions": None},
    ]}}
    original = layouts.layout_for
    monkeypatch.setattr(layouts, "layout_for", lambda name, branch: original(
        name, branch, registry=registry, index=json.loads((tmp_path / "index.json").read_text())))
    monkeypatch.setattr(rc, "CONTROL_DIR", tmp_path)
    monkeypatch.setattr(rc, "MAPS_JSON", tmp_path / "maps.json")
    rc._assets.cache_clear()
    yield tmp_path, index, registry
    rc._assets.cache_clear()


def test_13_08_replay_keeps_golden_fingerprint(monkeypatch):
    registry = layouts.load_registry()
    registry["maps"]["Ascent"].append({"id": "Ascent@13.10", "key": "Ascent_13_10",
        "start": "13.10", "drawn_from": "13.10", "regions": None})
    original = layouts.layout_for
    monkeypatch.setattr(layouts, "layout_for", lambda name, branch: original(
        name, branch, registry=registry, index={"maps": {"Ascent": {}}}))
    replay = SimpleNamespace(map_name="Ascent", game_branch="release-13.08",
                             recipe=golden.FIXED_RECIPE, source_sha256=golden.FIXED_SHA)
    monkeypatch.setattr(rc, "round_link", lambda *args: golden.FIXED_LINK)
    expected = json.loads(golden.GOLDEN_PATH.read_text())["maps"]["Ascent"]["control_fingerprint"]
    assert rc.round_fingerprint(replay, {}, 1) == expected
    assert rc.layout_of(replay).layout.asset_key == "Ascent"
    figures = json.loads(golden.GOLDEN_PATH.read_text())["maps"]["Ascent"]
    assert replay_gaps.gap_fingerprint(expected, "Ascent") == figures["gap_fingerprint"]
    assert replay_gaps.engine_key(golden.FIXED_CONTROL_FP, "Ascent") == figures["engine_key"]


@pytest.fixture
def layout_chokes(layout_assets, monkeypatch):
    folder, _, _ = layout_assets
    for key, cells in (("Toy", [1, 2]), ("Toy_13_10", [3, 4])):
        choke_assets.save(key, [choke_assets.Choke(1, "1", cells)], 2, asset_dir=folder)
    original = choke_assets.asset_hash
    monkeypatch.setattr(choke_assets, "asset_hash", lambda key: original(key, asset_dir=folder))
    assert choke_assets.asset_hash("Toy") != choke_assets.asset_hash("Toy_13_10")


def test_gap_freshness_caches_each_layout_separately(db, linked, layout_chokes):
    linked.map_name = "Toy"
    freshness = replay_gaps.Freshness(db)
    for branch, key in (("release-13.08", "Toy"), ("release-13.10", "Toy_13_10")):
        linked.game_branch = branch
        control = rc.round_fingerprint(linked, rc.side_groups(db, linked), 1)
        expected = replay_gaps.gap_fingerprint(control, key)
        assert freshness.expected(linked, 1) == expected
        if key != "Toy":
            assert expected != replay_gaps.gap_fingerprint(control, "Toy")
            assert replay_gaps.engine_key(control, key) != replay_gaps.engine_key(control, "Toy")
    assert set(freshness.maps) == {"Toy", "Toy_13_10"}


@pytest.mark.parametrize("stamp", ["Toy_13_10", "Toy"])
def test_guarded_gap_store_checks_layout_chokes(factory, db, linked, layout_chokes, stamp):
    linked.map_name, linked.game_branch = "Toy", "release-13.10"
    put_row(db, linked, 1)
    control = rc.round_fingerprint(linked, rc.side_groups(db, linked), 1)
    run = {"status": "ok", "fingerprint": replay_gaps.gap_fingerprint(control, "Toy_13_10"),
           "gaps_revision": replay_gaps.GAPS_REVISION, "chokes_hash": choke_assets.asset_hash(stamp),
           "notes": {}, "error": None}
    result = replay_gaps_store.store_gaps(factory, linked.id, 1, run, [],
        moments={"status": "ok", "v": 1, "sight": True, "fights": [], "error": None},
        expected_control_fingerprint=control)
    assert result == (replay_gaps_store.STORED if stamp == "Toy_13_10" else
                      "skipped: computed under other gap rules or assets")


def test_gap_reader_planner_and_progress_use_layout_key(db, linked, layout_chokes):
    linked.map_name, linked.game_branch = "Toy", "release-13.10"
    put_row(db, linked, 1)
    control = rc.round_fingerprint(linked, rc.side_groups(db, linked), 1)
    run = ReplayRoundGapRun(replay_id=linked.id, round_number=1, status="ok",
        fingerprint=replay_gaps.gap_fingerprint(control, "Toy_13_10"),
        gaps_revision=replay_gaps.GAPS_REVISION, chokes_hash=choke_assets.asset_hash("Toy_13_10"), gap_count=0)
    db.add(run)
    db.commit()
    assert replay_gaps.round_gaps(db, linked, 1) == ("ok", [])
    assert replay_gaps.plan_gaps(db, [], rc.plan(db, force=True, rounds={1})) == []
    assert replay_progress.round_states(db)["states"][linked.id, 1] == "done"
    run.status = "failed"
    db.commit()
    assert replay_progress.round_states(db)["states"][linked.id, 1] == "gaps_failed"


@pytest.mark.parametrize("branch,state", [("release-13.10", "layout_pending"), ("b", "layout_unknown")])
def test_gap_progress_counts_unavailable_layouts(db, linked, layout_assets, monkeypatch, branch, state):
    folder, index, _ = layout_assets
    linked.map_name, linked.game_branch = "Toy", branch
    index["maps"].pop("Toy_13_10")
    write_json(folder / "index.json", index)
    db.commit()
    def no_geometry(*args, **kwargs):
        pytest.fail("unavailable layouts must not resolve geometry")
    monkeypatch.setattr(rc, "resolve_current_geometry", no_geometry)
    assert replay_gaps.Freshness(db).expected(linked, 1) is None
    body = replay_progress.compute(db)
    assert {step["key"]: step["rounds"] for step in body["steps"]} == {state: linked.round_count}
    assert body["needs_a_person"] == linked.round_count
    assert body["worker_will_do"] == body["done"] == 0


@pytest.mark.parametrize("failed_inputs", [False, True])
def test_gap_worker_collection_uses_layout_key(factory, db, linked, layout_chokes, monkeypatch, failed_inputs):
    linked.map_name, linked.game_branch = "Toy", "release-13.10"
    put_row(db, linked, 1)
    original_plan = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kwargs: original_plan(session, **{**kwargs, "rounds": {1}}))
    worker = FakeWorker(gaps=remote.GAPS_PROTOCOL, layouts=["Toy", "Toy_13_10"])
    if failed_inputs:
        worker.answer = lambda task: {"status": "failed", "error_kind": "inputs",
                                     "error_code": "source_unavailable", "error": "synthetic missing source"}
    state = remote.State()
    assert remote.cycle(factory, worker, state, now=0)["gaps_sent"] == 1
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["gaps_stored"] == 1, counts
    db.expire_all()
    run = db.get(ReplayRoundGapRun, (linked.id, 1))
    assert run.status == ("failed" if failed_inputs else "ok")
    assert run.chokes_hash == choke_assets.asset_hash("Toy_13_10")


def test_new_layout_changes_only_newer_replays(db, linked, layout_assets):
    folder, index, registry = layout_assets
    linked.map_name = "Toy"
    linked.game_branch = "release-13.08"
    newer = Replay(match_uuid="00000000-0000-4000-8000-000000000002", map_name="Toy", round_count=1,
                   format_version=linked.format_version, recipe=linked.recipe, game_branch="release-13.10",
                   source_sha256=linked.source_sha256, source="local", link_status="unlinked", link_inputs={})
    db.add(newer)
    db.flush()
    db.add(ReplayRound(replay_id=newer.id, round_number=1, data=b"synthetic"))
    # Before the layout is declared both patches use the base assets.
    future = registry["maps"]["Toy"].pop()
    put_row(db, linked, 1)
    put_row(db, newer, 1)
    old_fp = rc.round_fingerprint(linked, rc.side_groups(db, linked), 1)
    assert rc.plan(db, rounds={1}) == []
    registry["maps"]["Toy"].append(future)
    assert rc.layout_of(newer).layout.asset_key == "Toy_13_10"
    assert rc.round_fingerprint(linked, rc.side_groups(db, linked), 1) == old_fp
    assert rc.round_fingerprint(newer, {}, 1) != rc.round_fingerprint(newer, {}, 1,
        context=rc.resolve_current_geometry(db, "Toy"))
    [planned] = rc.plan(db, rounds={1})
    assert (planned.replay_id, planned.reason, planned.asset_key) == (newer.id, "stale", "Toy_13_10")
    assert planned.inputs.geometry["scale"] == 7e-5
    assert planned.inputs.geometry["walk"] == index["maps"]["Toy_13_10"]["walk_sha"]
    assert not rc.round_control(db, linked, 1).stale
    assert rc.round_control(db, newer, 1).stale


@pytest.mark.parametrize("state,branch", [("layout_pending", "release-13.10"), ("layout_unknown", "b")])
def test_unavailable_layout_keeps_stored_row_and_cannot_plan(db, linked, layout_assets, state, branch):
    folder, index, _ = layout_assets
    linked.map_name = "Toy"
    linked.game_branch = "release-13.08"
    put_row(db, linked, 1)
    stored = rc.round_control(db, linked, 1).row
    linked.game_branch = branch
    index["maps"].pop("Toy_13_10")
    write_json(folder / "index.json", index)
    answer = rc.round_control(db, linked, 1)
    assert (answer.status, answer.row, answer.stale, answer.layout) == ("ok", stored, True, state)
    assert rc.round_fingerprint(linked, rc.side_groups(db, linked), 1) is None
    [planned] = rc.plan(db, rounds={1}, force=True)
    assert planned.reason == state
    assert not planned.computable
    assert planned.inputs is None


@pytest.mark.parametrize("branch,state", [("release-13.10", "layout_pending"), ("b", "layout_unknown")])
def test_unavailable_layout_without_row_is_404(db, linked, layout_assets, branch, state):
    folder, index, _ = layout_assets
    linked.map_name, linked.game_branch = "Toy", branch
    index["maps"].pop("Toy_13_10")
    write_json(folder / "index.json", index)
    assert rc.round_control(db, linked, 1).status == state
    response = routes.replay_round_control(request(), str(linked.match_uuid), 1, db)
    assert response.status_code == 404
    assert json.loads(response.body) == {"status": state}
    _, response = routes._control_replay_or_404(db, str(linked.match_uuid))
    assert response.status_code == 404
    assert json.loads(response.body) == {"status": state}


def test_base_only_map_with_unparsed_branch_computes(db, linked):
    linked.game_branch = "b"
    [planned] = rc.plan(db, rounds={1})
    assert planned.computable
    assert planned.asset_key == linked.map_name
    assert rc.PlannedRound(1, "synthetic", "Ascent", 1, None, "missing").asset_key is None


@pytest.mark.parametrize("branch,asset_key", [
    ("release-13.08", "Toy"), ("release-13.10", "Toy_13_10"), ("release-13.08", None),
])
@pytest.mark.parametrize("kind", ["plain", "full", "gaps"])
def test_dispatch_task_uses_layout_asset_key(factory, db, linked, layout_assets, monkeypatch,
                                           branch, asset_key, kind):
    linked.map_name, linked.game_branch = "Toy", branch
    db.commit()
    if kind == "gaps":
        put_row(db, linked, 1)
    [planned] = rc.plan(db, rounds={1}, force=True)
    planned = replace(planned, asset_key=asset_key, reason="gaps" if kind == "gaps" else "missing")
    monkeypatch.setattr(replay_gaps.choke_assets, "asset_hash", lambda name: name + "-chokes")
    monkeypatch.setattr(rc, "plan", lambda session, **kwargs:
                        [planned] if kind != "gaps" or kwargs.get("force") else [])
    worker = FakeWorker(gaps=remote.GAPS_PROTOCOL if kind != "plain" else None, layouts=['Toy', 'Toy_13_10'])
    counts = remote.cycle(factory, worker, remote.State(), now=0)
    assert counts["gaps_sent" if kind == "gaps" else "sent"] == 1
    [task] = worker.tasks.values()
    key = asset_key or "Toy"
    expected = {
        "key": remote.task_key(linked.id, 1, planned.fingerprint),
        "map": key,
        "blob": base64.b64encode(db.get(ReplayRound, (linked.id, 1)).data).decode("ascii"),
        "link": {"sides": planned.link["sides"], "db_deaths": planned.link["db_deaths"]},
        "height_mode": planned.inputs.height_mode,
    }
    if kind != "plain":
        gap_fp = replay_gaps.gap_fingerprint(planned.fingerprint, key)
        expected["gaps"] = {
            "replay_id": linked.id, "round": 1, "fingerprint": planned.fingerprint,
            "gap_fingerprint": gap_fp, "engine_key": replay_gaps.engine_key(planned.fingerprint, key),
        }
        make_key = remote.gaps_key if kind == "gaps" else remote.full_key
        expected["key"] = make_key(linked.id, 1, planned.fingerprint, gap_fp)
    if kind == "gaps":
        expected["gaps_only"] = True
    assert task == expected


@pytest.mark.parametrize("branch,key", [("release-13.08", "Toy"), ("release-13.10", "Toy_13_10")])
@pytest.mark.parametrize("archived", [False, True])
def test_dispatch_height_push_uses_layout_asset_key(factory, db, linked, layout_assets, monkeypatch,
                                                  branch, key, archived):
    linked.map_name, linked.game_branch = "Toy", branch
    db.commit()
    [planned] = rc.plan(db, rounds={1})
    digest, features = "a" * 12, "f" * 64 if archived else None
    planned = replace(planned, inputs=rc.PinnedInputs(
        {**planned.inputs.geometry, "height": digest}, height_mode="asset", artifact_digest=features,
        envelope={} if archived else None))
    monkeypatch.setattr(rc, "plan", lambda *args, **kwargs: [planned])
    reads = []
    monkeypatch.setattr(rc, "exact_height_bytes", lambda session, name, sha:
                        reads.append((name, sha)) or b"synthetic height")
    monkeypatch.setattr(artifacts, "archived_height_bytes", lambda session, sha:
                        reads.append(sha) or b"synthetic height")
    worker = FakeWorker(features=remote.fa.protocol_identity(), layouts=['Toy', 'Toy_13_10'])
    counts = remote.cycle(factory, worker, remote.State(), now=0)
    assert counts["sent"] == counts["pushed"] == 1
    assert reads == ([features] if archived else [(key, digest)])
    assert worker.pushed == [(key, digest, b"synthetic height")]


def test_discovery_prepares_distinct_available_layout_keys(factory, db, linked, layout_assets, monkeypatch):
    _, _, registry = layout_assets
    registry["maps"]["Toy"].append({"id": "Toy@13.12", "key": "Toy_13_12",
        "start": "13.12", "drawn_from": "13.12", "regions": None})
    linked.map_name, linked.game_branch = "Toy", "release-13.08"
    for number, branch in enumerate(("release-13.10", "release-13.10", "release-13.12", "b"), start=2):
        db.add(Replay(match_uuid=f"00000000-0000-4000-8000-{number:012d}", map_name="Toy", round_count=1,
                      format_version=linked.format_version, recipe=linked.recipe, game_branch=branch,
                      source_sha256=linked.source_sha256, source="local", link_status="unlinked", link_inputs={}))
    db.commit()
    prepared = []
    monkeypatch.setattr(rc, "prepare_discovered", lambda session_factory, keys, retry_after, now:
                        prepared.append(list(keys)))
    monkeypatch.setattr(remote, "_submit", lambda *args, **kwargs: None)
    remote.cycle(factory, FakeWorker(), remote.State(), now=0)
    [keys] = prepared
    assert sorted(keys) == ["Toy", "Toy_13_10"]


@pytest.mark.slow
def test_bundle_layout_resolves_and_prepares_artifact(tmp_path, monkeypatch):
    from app.control import feature_job
    from app.config import settings
    from app.replays.map_feature_sources import descriptor_digest, png_mask
    from app.replays.map_feature_artifacts import pack_mask

    key = "Ascent_13_10"
    index, tags, maps, _ = rc._current_snapshot()
    entry = copy.deepcopy(index["Ascent"])
    for kind in ("sight", "walk", "barrier", "cover"):
        source = rc.CONTROL_DIR / f"Ascent.{kind}.png"
        if source.exists():
            target = tmp_path / f"{key}.{kind}.png"
            target.write_bytes(source.read_bytes())
            if kind in ("sight", "walk"):
                mask = cg.read_mask_png(target)
                mask[0:8, 0:8] = ~mask[0:8, 0:8]
                cg.write_mask_png(target, mask)
                entry[kind + "_sha"] = descriptor_digest(pack_mask(png_mask(target.read_bytes()), [1024, 1024]))
                assert target.read_bytes() != source.read_bytes()
    entry.pop("height_sha", None)
    write_json(tmp_path / "index.json", {"maps": {key: entry}})
    write_json(tmp_path / "tags.json", {"maps": {key: tags["Ascent"]}})
    write_json(tmp_path / "maps.json", {"Ascent": maps["Ascent"]})
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(rc, "CONTROL_DIR", tmp_path)
    monkeypatch.setattr(rc, "MAPS_JSON", tmp_path / "maps.json")
    monkeypatch.setattr(feature_job, "_commit", lambda: "0" * 40)
    monkeypatch.setattr(artifacts, "run_feature_child", lambda mode, payload:
                        json.dumps(feature_job.run(json.loads(payload))).encode())
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[ControlHeight.__table__, ControlFeatureArtifact.__table__])
    try:
        with Session(engine) as session:
            context = rc.resolve_current_geometry(session, key)
            assert context.state == "features_pending"
            assert context.feature_input.key.map_name == key
            artifact = artifacts.prepare_artifact(session, context.feature_input, None)
            assert artifact.key.map_name == key
            ready = rc.resolve_current_geometry(session, key)
            assert ready.state == "ready"
            assert ready.pinned.geometry["features"] == artifact.digest
            assert ready.pinned.geometry["scale"] == maps["Ascent"]["xMultiplier"]
            assert artifacts.refresh_feature_report(session, key)["status"] != "error"
    finally:
        engine.dispose()
        rc._assets.cache_clear()
