"""Control selects replay layouts without changing the base identities."""

import copy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from test_control_store import db, factory, linked, put_row
from test_replay_store import condensed, pg
from test_replay_routes import request
from app.control import geometry as cg
from app.db import Base
from app.models.replay import ControlFeatureArtifact, ControlHeight, Replay, ReplayRound
from app.replays import layouts
from app.routers import replays as routes
from app.services import control_feature_artifacts as artifacts
from app.services import replay_control as rc
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
