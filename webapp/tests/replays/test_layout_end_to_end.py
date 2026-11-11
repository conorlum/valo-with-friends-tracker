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
