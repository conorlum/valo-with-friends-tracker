"""Layout selection survives planning, remote dispatch, storage and freshness reads."""

import base64
import gzip

from test_control_remote import FakeWorker
from test_control_store import db, factory, put_row
from test_layout_selection import layout_assets

from app.models.replay import Replay, ReplayRound
from app.replays import format as replay_format
from app.services import replay_control as rc
from app.services import replay_control_remote as remote
from tests.replays import layout_golden as golden


class LayoutWorker(FakeWorker):
    def ok(self, task):
        answer = super().ok(task)
        answer["result"]["data"] = base64.b64encode(gzip.compress(task["map"].encode())).decode()
        return answer


def add_replay(db, patch):
    replay = Replay(
        match_uuid=f"00000000-0000-4000-8000-{patch:012d}",
        map_name="Toy", round_count=1, format_version=replay_format.FORMAT_VERSION,
        recipe=golden.FIXED_RECIPE, game_branch=f"++Ares-Core+release-13.{patch:02d}",
        source_sha256=golden.FIXED_SHA, source="local", link_status="unlinked", link_inputs={},
    )
    db.add(replay)
    db.flush()
    db.add(ReplayRound(replay_id=replay.id, round_number=1, data=b"synthetic"))
    db.commit()
    return replay


def test_layouts_plan_dispatch_store_and_keep_pending_control(factory, db, layout_assets, monkeypatch):
    _, _, registry = layout_assets
    monkeypatch.setattr(remote, "IN_FLIGHT", 2)
    monkeypatch.setattr(remote, "MAX_MATCHES", 2)
    base, newer = add_replay(db, 8), add_replay(db, 10)
    planned = {item.replay_id: item for item in rc.plan(db)}
    assert set(planned) == {base.id, newer.id}
    assert [(planned[r.id].reason, planned[r.id].computable, planned[r.id].asset_key)
            for r in (base, newer)] == [("missing", True, "Toy"), ("missing", True, "Toy_13_10")]
    assert planned[base.id].fingerprint != planned[newer.id].fingerprint

    later_layout = registry["maps"]["Toy"].pop()
    try:
        assert rc.round_fingerprint(base, {}, 1) == planned[base.id].fingerprint
    finally:
        registry["maps"]["Toy"].append(later_layout)

    worker = LayoutWorker(layouts=["Toy", "Toy_13_10"])
    state = remote.State()
    assert remote.cycle(factory, worker, state, now=0)["sent"] == 2
    assert {task["key"]: task["map"] for task in worker.tasks.values()} == {
        remote.task_key(base.id, 1, planned[base.id].fingerprint): "Toy",
        remote.task_key(newer.id, 1, planned[newer.id].fingerprint): "Toy_13_10",
    }
    collected = remote.cycle(factory, worker, state, now=1)
    assert (collected["stored"], collected["sent"]) == (2, 0)
    db.expire_all()
    for replay, key in ((base, "Toy"), (newer, "Toy_13_10")):
        answer = rc.round_control(db, replay, 1)
        assert (answer.status, answer.stale, answer.layout) == ("ok", False, None)
        assert answer.row.fingerprint == planned[replay.id].fingerprint
        assert gzip.decompress(answer.row.data).decode() == key
    assert rc.plan(db) == []

    pending = add_replay(db, 12)
    put_row(db, pending, 1)
    before = rc.round_control(db, pending, 1)
    assert (before.status, before.stale, before.layout) == ("ok", False, None)
    stored = (before.row.fingerprint, before.row.data, before.row.summary, before.row.computed_at)
    registry["maps"]["Toy"].append({
        "id": "Toy@13.12", "key": "Toy_13_12", "start": "13.12",
        "drawn_from": "13.12", "regions": None,
    })
    [blocked] = rc.plan(db)
    assert (blocked.replay_id, blocked.reason, blocked.asset_key) == (
        pending.id, "layout_pending", "Toy_13_12")
    assert not blocked.computable
    assert blocked.inputs is None
    assert rc.round_fingerprint(pending, {}, 1) is None
    idle = remote.cycle(factory, worker, state, now=1 + remote.PLAN_IDLE_S)
    assert (idle["sent"], idle["stored"]) == (0, 0)
    assert len(worker.tasks) == 2
    assert not state.in_flight
    db.expire_all()
    after = rc.round_control(db, pending, 1)
    assert (after.status, after.stale, after.layout) == ("ok", True, "layout_pending")
    assert (after.row.fingerprint, after.row.data, after.row.summary, after.row.computed_at) == stored
    for replay in (base, newer):
        assert not rc.round_control(db, replay, 1).stale


# ---------------------------------------------------------------- heights per layout, end to end

import json  # noqa: E402

import pytest  # noqa: E402

from app.control import geometry as cg  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.replays import control_format, height_inputs as hi, layouts  # noqa: E402
from app.services import control_heights  # noqa: E402
from app.services import replay_heights_remote as hr  # noqa: E402
from test_heights_remote import TEST_CHECK_SHA, good_report  # noqa: E402


@pytest.fixture
def toy_heights(tmp_path, monkeypatch):
    import numpy as np

    walk = np.ones((8, 8), dtype=bool)
    keys = ("Toy", "Toy_13_10")
    for key in keys:
        cg.write_mask_png(tmp_path / f"{key}.walk.png", walk)
        cg.write_mask_png(tmp_path / f"{key}.sight.png", ~walk)
    (tmp_path / "regions").mkdir()
    changed = np.zeros_like(walk)
    changed[2:4, 2:4] = True
    cg.write_mask_png(tmp_path / "regions/Toy_13_10.png", changed)
    (tmp_path / "index.json").write_text(json.dumps({"maps": {
        key: {"sight_sha": key + "-sight", "walk_sha": key + "-walk"} for key in keys}}), encoding="utf-8")
    (tmp_path / "tags.json").write_text(json.dumps({"maps": {"Toy": {}, "Toy_13_10": {}}}), encoding="utf-8")
    (tmp_path / "maps.json").write_text(json.dumps({"Toy": {"xMultiplier": 7e-5}}), encoding="utf-8")
    (tmp_path / "must_block.json").write_text('{"lines": []}', encoding="utf-8")
    registry = {"maps": {"Toy": [
        {"id": "Toy@base", "key": "Toy", "start": None, "drawn_from": "13.08", "regions": None},
        {"id": "Toy@13.10", "key": "Toy_13_10", "start": "13.10", "drawn_from": "13.10",
         "regions": "regions/Toy_13_10.png"},
    ]}}
    monkeypatch.setattr(layouts, "load_registry", lambda: registry)
    original = layouts.layout_for
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(layouts, "layout_for", lambda name, branch, registry=registry, index=index: original(
        name, branch, registry=registry, index=index))
    monkeypatch.setattr(hi, "CONTROL_DIR", tmp_path)
    monkeypatch.setattr(hi, "MAPS_JSON", tmp_path / "maps.json")
    monkeypatch.setattr(hi, "MUST_BLOCK", tmp_path / "must_block.json")
    monkeypatch.setattr(rc, "CONTROL_DIR", tmp_path)
    monkeypatch.setattr(rc, "MAPS_JSON", tmp_path / "maps.json")
    rc._assets.cache_clear()
    yield tmp_path, registry
    rc._assets.cache_clear()


class FinishedBuildClient:
    """A worker that answers every opened build as finished, with a result for the key it was asked for."""

    def __init__(self, digests):
        self.digests = digests
        self.opened = []

    def health(self):
        return {"idle": True, "heights": {"protocol": 1}}

    def open_build(self, key, map_name, rounds, manifest, **options):
        self.opened.append(key)
        return {"id": f"job-{key}", "status": "done", "result": {
            "key": hi.key(manifest), "map": map_name, "inputs_sha": hi.digest(manifest),
            "rounds": hi.rounds_expected(manifest), "digest": self.digests[map_name],
            "asset": base64.b64encode(b"built").decode(), "report": good_report(),
            "rules": hc.rules(), "seconds": 1.0, "peak": 1}}

    def cancel_build(self, job_id):
        pass


def test_a_later_layouts_heights_are_built_stored_activated_and_fingerprinted_under_its_key(
        factory, db, toy_heights, monkeypatch):
    from tests.replays.test_layout_heights import add_replay as add_toy_replay

    _, registry = toy_heights
    base, later = add_toy_replay(db), add_toy_replay(db, "release-13.10")
    # The checks pass against this deploy's own check sets; the asset bytes are a stand-in.
    monkeypatch.setattr(hi, "must_block_sha", lambda path=None, key=None: TEST_CHECK_SHA)
    monkeypatch.setattr(control_heights, "verify_asset", lambda data, map_name, **kw: {
        "digest": "c" * 12 if map_name == "Toy_13_10" else "b" * 12, "version": control_format.HEIGHT_VERSION,
        "supported_cells": 7, "walkable_cells": 10, "ready": True, "not_ready": [],
        "must_block": good_report()["must_block"]})

    manifests = hr.current_manifests(db)
    base_manifest, later_manifest = manifests["Toy"][0], manifests["Toy_13_10"][0]
    assert "regions" in later_manifest
    # "Toy" already has its heights, built from the same pool: only the later layout is due.
    assert control_heights.store_build(
        db, map_name="Toy", digest="b" * 12, asset=b"old", report=good_report(),
        inputs=base_manifest, rules=hc.rules())[0] == control_heights.ACTIVE
    toy_row = control_heights.active_rows(db)["Toy"].id
    assert [d.map_name for d in hr.plan_maps(db).due] == ["Toy_13_10"]

    client = FinishedBuildClient({"Toy": "b" * 12, "Toy_13_10": "c" * 12})
    counts = {}
    held = hr.step(factory, db, client, hr.HeightState(), 0.0, counts)
    assert client.opened == [hi.key(later_manifest)] and counts["heights_live"] == 1
    assert held == set(), "the build is stored, so nothing is held"
    db.expire_all()
    active = control_heights.active_digests(db)
    assert active == {"Toy": "b" * 12, "Toy_13_10": "c" * 12}
    assert control_heights.active_rows(db)["Toy"].id == toy_row, "the base layout's row is untouched"
    assert control_heights.active_rows(db)["Toy_13_10"].inputs_sha == hi.digest(later_manifest)

    flat = rc.round_fingerprint(later, {}, 1, None)
    built = rc.round_fingerprint(later, {}, 1, active)
    assert built != flat
    assert built == rc.round_fingerprint(later, {}, 1, {"Toy_13_10": "c" * 12})
    assert built != rc.round_fingerprint(later, {}, 1, {"Toy": "c" * 12}), "keyed by the layout's own key"
    base_flat = rc.round_fingerprint(base, {}, 1, None)
    assert rc.round_fingerprint(base, {}, 1, active) == rc.round_fingerprint(base, {}, 1, {"Toy": "b" * 12})
    assert rc.round_fingerprint(base, {}, 1, {"Toy_13_10": "c" * 12}) == base_flat, \
        "a later layout's heights never touch a base replay"

    # With one layout the base map's manifest has no regions and its digest is what it was before a layout was added.
    later_layout = registry["maps"]["Toy"].pop()
    try:
        single = hr.current_manifests(db)
        assert set(single) == {"Toy"} and "regions" not in single["Toy"][0]
        expected = hi.manifest("Toy", [[u, base.recipe, base.source_sha256, n] for _, u, n in single["Toy"][1]],
                               hi.geometry_identity("Toy"), TEST_CHECK_SHA)
        assert hi.digest(single["Toy"][0]) == hi.digest(expected)
        assert hi.digest(single["Toy"][0]) != hi.digest(base_manifest)
    finally:
        registry["maps"]["Toy"].append(later_layout)
