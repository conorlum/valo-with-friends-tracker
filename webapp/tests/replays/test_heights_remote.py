"""The web app's height-rebuild dispatcher (app/services/replay_heights_remote.py;
docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 3 and 4), on SQLite, against the real
worker handler over HTTP with stub children: when a rebuild is due, which rounds wait for it, how it resumes
after a restart on either side, what each failure costs, and that nothing goes live without proving itself."""

import base64
import json
import sys
import threading
import uuid
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WEBAPP = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))

from replay_worker import server  # noqa: E402
from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixture)
from test_replay_worker_control import STUB  # noqa: E402

from app.config import settings  # noqa: E402
from app.control import heights as hc  # noqa: E402
from app.models.replay import ControlHeight, Replay, ReplayRound  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import height_inputs as hi  # noqa: E402
from app.services import control_heights as ch  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402
from app.services import replay_heights_remote as hr  # noqa: E402

GRID = 128

TEST_CHECK_BYTES = b'{"lines": []}'
TEST_CHECK_SHA = __import__("hashlib").sha256(TEST_CHECK_BYTES).hexdigest()[:12]

# The build child: it answers what `mode.json` says, with the real manifest digest and a real asset's bytes.
BUILD_STUB = """
import base64, json, os, sys, time
sys.path.insert(0, {webapp!r})
from app.replays import height_inputs as hi
task = json.loads(sys.stdin.buffer.read())
mode = json.load(open({mode!r}))
time.sleep(mode.get("sleep", 0))
files = [f for d, _, fs in os.walk(task["dir"]) for f in fs]
out = {{"status": "ok", "key": task["key"], "map": task["map"], "inputs_sha": hi.digest(task["manifest"]),
       "rounds": len(files), "digest": mode["digest"], "asset": base64.b64encode(open(mode["asset"], "rb").read()).decode(),
       "report": mode["report"], "rules": mode["rules"], "seconds": 3.0, "peak": 1}}
out.update(mode.get("change", {{}}))
for gone in mode.get("drop", []):
    out.pop(gone, None)
print(mode["raw"] if "raw" in mode else json.dumps(out))
"""


def real_asset(path: Path, supported: int = 7, shift: int = 0) -> hc.HeightAsset:
    """A height asset with `supported` supported cells in one row, saved at `path`."""
    floors = np.full((GRID, GRID, hc.MAX_FLOORS), -1, np.int16)
    floors[40, 40:40 + supported, 0] = shift
    has = floors[..., 0] >= 0
    asset = hc.HeightAsset(floors, np.zeros_like(floors), has, np.zeros((GRID, GRID), bool), np.zeros((0, 5), np.int32),
                           {"origin_z": 100})
    hc.save_asset(path, asset)
    return asset


def good_report(supported: int = 7, walkable: int = 10, **changes) -> dict:
    out = {"walkable_cells": walkable, "supported_cells": supported, "supported": round(supported / walkable, 4),
           "ready": True, "not_ready": [],
           "kill_lines": {"qualifying": 400, "blocked": 2, "share": 0.005, "passes": True},
           "must_block": {"set": TEST_CHECK_SHA, "lines": 0, "checked": 0, "unchecked": 0, "blocked": 0,
                          "passes": True}}
    for key, value in changes.items():
        out[key] = {**out[key], **value} if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


class Rig:
    """A real worker (handler, control runner, height builds) on localhost with stub children, and the real client."""

    def __init__(self, tmp_path: Path, **overrides):
        self.tmp = tmp_path
        (tmp_path / "events").mkdir(exist_ok=True)
        control_stub = tmp_path / "stub_control.py"
        control_stub.write_text(STUB.format(log=str(tmp_path / "events")), encoding="utf-8")
        self.mode_path = tmp_path / "mode.json"
        build_stub = tmp_path / "stub_build.py"
        build_stub.write_text(BUILD_STUB.format(webapp=str(WEBAPP), mode=str(self.mode_path)), encoding="utf-8")
        self.asset = real_asset(tmp_path / "asset.npz")
        self.mode(digest=self.asset.digest, asset=str(tmp_path / "asset.npz"), report=good_report(), rules=hc.rules())
        self.settings = server.Settings(temp_root=tmp_path / "jobs", control_cache_dir=tmp_path / "cache",
                                        control_cmd=[sys.executable, str(control_stub)],
                                        height_cmd=[sys.executable, str(build_stub)], **overrides)
        (tmp_path / "jobs").mkdir(exist_ok=True)
        self.control = server.ControlRunner(self.settings)
        self.builds = server.HeightBuilds(self.settings, self.control)
        self.worker = server.Worker(self.settings)
        self.httpd = server.make_server(self.worker, control=self.control, heights=self.builds)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = remote.ControlClient(f"http://127.0.0.1:{self.httpd.server_address[1]}")
        self.state = remote.State()
        self.t = 0.0

    def mode(self, **values):
        current = json.loads(self.mode_path.read_text(encoding="utf-8")) if self.mode_path.exists() else {}
        self.mode_path.write_text(json.dumps({**current, **values}), encoding="utf-8")

    def cycle(self, factory, step_s: float = 400.0) -> dict:
        self.t += step_s
        return remote.cycle(factory, self.client, self.state, now=self.t)

    def until(self, factory, done, cycles: int = 40, step_s: float = 400.0) -> list:
        """Cycles until `done(counts so far)` (the children are real processes: a build takes a moment)."""
        import time

        seen = []
        for _ in range(cycles):
            seen.append(self.cycle(factory, step_s))
            if done(seen):
                return seen
            time.sleep(0.15)
        raise AssertionError(f"not reached in {cycles} cycles: {[{k: v for k, v in c.items() if v} for c in seen]}")

    def restart_dispatcher(self):
        self.state = remote.State()

    def restart_worker_while_collecting(self):
        assert not self.control.running  # no child is being abandoned by this test helper
        self.httpd.shutdown()
        self.httpd.server_close()
        self.control.idle = lambda: False
        with self.builds.lock:
            self.builds.builds.clear()  # retire the old sweeper's entries before a new spool is created
        self.control = server.ControlRunner(self.settings)
        self.builds = server.HeightBuilds(self.settings, self.control)
        self.httpd = server.make_server(self.worker, control=self.control, heights=self.builds)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = remote.ControlClient(f"http://127.0.0.1:{self.httpd.server_address[1]}")
        assert not self.control.jobs and not self.control.by_key and not self.builds.builds


def total(seen: list, key: str) -> int:
    return sum(c.get(key, 0) for c in seen)


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    made.httpd.shutdown()


@pytest.fixture(autouse=True)
def on(monkeypatch, tmp_path):
    from app.control import geometry as cg

    directory = tmp_path / "verification-assets"
    directory.mkdir()
    walk_px = np.zeros((cg.PX, cg.PX), bool)
    walk_px[40 * cg.CELL:41 * cg.CELL, 40 * cg.CELL:50 * cg.CELL] = True
    for name in ("Ascent", "Bind"):
        cg.write_mask_png(directory / f"{name}.walk.png", walk_px)
        cg.write_mask_png(directory / f"{name}.sight.png", walk_px)
    (directory / "tags.json").write_text('{"maps": {}}', encoding="utf-8")
    (directory / "index.json").write_text(json.dumps({"maps": {
        name: {"sight_sha": "toy-sight", "walk_sha": "toy-walk"} for name in ("Ascent", "Bind")}}), encoding="utf-8")
    checks = tmp_path / "verification-checks.json"
    checks.write_bytes(TEST_CHECK_BYTES)
    monkeypatch.setattr(hi, "CONTROL_DIR", directory)
    monkeypatch.setattr(hi, "MUST_BLOCK", checks)
    monkeypatch.setattr(settings, "replay_heights_auto", True)
    monkeypatch.setattr(settings, "replay_control_remote", True)
    monkeypatch.setattr(settings, "replay_worker_url", "http://worker")
    monkeypatch.setattr(hi, "MIN_CONDENSE_REVISION", 0)       # the synthetic replay's recipe is an old revision


def clone(db, replay, count):
    """`count` more valid replays of the same map: copies of `replay` under new match ids and source hashes."""
    made = []
    for _ in range(count):
        copy = Replay(**{c.name: getattr(replay, c.name) for c in Replay.__table__.columns
                         if c.name not in ("id", "match_uuid", "match_id", "source_sha256")},
                      match_uuid=str(uuid.uuid4()), match_id=None, source_sha256=uuid.uuid4().hex * 2)
        copy.link_status = "unlinked"
        db.add(copy)
        db.flush()
        for row in db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id):
            db.add(ReplayRound(replay_id=copy.id, round_number=row.round_number, data=row.data))
        made.append(copy)
    db.commit()
    return made


def delete(db, replay):
    db.query(ReplayRound).filter(ReplayRound.replay_id == replay.id).delete()
    db.query(Replay).filter(Replay.id == replay.id).delete()
    db.commit()


def one_round_batches(db, monkeypatch):
    """Batches that hold one or two rounds, one batch a cycle: a build that takes several cycles to send."""
    monkeypatch.setattr(hr, "BATCHES_PER_CYCLE", 1)
    monkeypatch.setattr(hr, "BATCH_BYTES", max(len(base64.b64encode(r.data)) for r in db.query(ReplayRound)))


def whole_batches(monkeypatch):
    monkeypatch.setattr(hr, "BATCHES_PER_CYCLE", 8)
    monkeypatch.setattr(hr, "BATCH_BYTES", 2_000_000)


def stored(db, name, digest="a" * 12, report=None, manifest=None, rules=None):
    manifest = manifest or hr.current_manifests(db)[name][0]
    return ch.store_build(db, map_name=name, digest=digest, asset=b"old", report=report or good_report(),
                          inputs=manifest, rules=rules or hc.rules())


# ---------------------------------------------------------------- when a rebuild is due


def test_nothing_is_due_with_one_match_and_the_first_build_is_due_at_two(db, linked):
    assert hr.plan_maps(db).due == []
    clone(db, linked, 1)
    [due] = hr.plan_maps(db).due
    assert due.map_name == linked.map_name and due.why == "first build" and len(due.replays) == 2
    assert hi.rounds_expected(due.manifest) == 2 * linked.round_count


def test_after_a_build_the_next_is_due_five_new_matches_later_whatever_its_status(db, linked):
    clone(db, linked, 1)
    stored(db, linked.map_name, report=good_report(ready=False, not_ready=["thin"]))        # rejected
    clone(db, linked, hr.HEIGHT_REBUILD_EVERY - 1)
    assert hr.plan_maps(db).due == [], "four new matches: a failing map can't loop"
    clone(db, linked, 1)
    assert [d.why for d in hr.plan_maps(db).due] == ["5 new matches"]


@pytest.mark.parametrize("name", ["rule", "format", "mask", "check set"])
def test_a_changed_rule_mask_or_check_set_makes_the_rebuild_due_at_once(db, linked, monkeypatch, name):
    clone(db, linked, 1)
    stored(db, linked.map_name)
    assert hr.plan_maps(db).due == []
    if name == "rule":
        monkeypatch.setattr(cf, "HEIGHT_RULES_REVISION", cf.HEIGHT_RULES_REVISION + 1)
    elif name == "format":
        monkeypatch.setattr(cf, "HEIGHT_VERSION", cf.HEIGHT_VERSION + 1)
    elif name == "mask":
        real = hi.geometry_identity
        monkeypatch.setattr(hi, "geometry_identity", lambda m, *a, **k: {**real(m), "walk": "x" * 12})
    else:
        monkeypatch.setattr(hi, "must_block_sha", lambda *a, **k: "z" * 12)
    assert [d.why for d in hr.plan_maps(db).due] == ["its inputs changed"], name


def test_deleted_or_recondensed_evidence_is_due_at_once_and_too_little_left_turns_heights_off(db, linked):
    others = clone(db, linked, 2)
    stored(db, linked.map_name)
    others[0].recipe = others[0].recipe + ".x"                 # the same match, other blobs
    db.commit()
    assert [d.why for d in hr.plan_maps(db).due] == ["its evidence changed"]
    stored(db, linked.map_name, digest="b" * 12)
    delete(db, others[0])
    assert [d.why for d in hr.plan_maps(db).due] == ["its evidence changed"], "not after five more matches: now"
    delete(db, others[1])                                      # one match left: no floor can have two matches
    plan = hr.plan_maps(db)
    assert plan.due == [] and plan.off == [linked.map_name], "heights built from deleted matches don't stay"
    delete(db, linked)
    assert hr.plan_maps(db).off == [linked.map_name]


def test_a_map_with_a_published_feature_generation_is_not_rebuilt(db, linked, monkeypatch):
    clone(db, linked, 1)
    real = rc.geometry_inputs
    monkeypatch.setattr(rc, "geometry_inputs", lambda m, heights=None: {**real(m, heights), "features": "feat" * 4})
    plan = hr.plan_maps(db)
    assert plan.due == [] and linked.map_name in plan.skipped and "feature generation" in plan.skipped[linked.map_name]


# ---------------------------------------------------------------- the whole cycle, over the real handler


def test_a_due_maps_rounds_wait_and_its_build_goes_live(rig, factory, db, linked):
    clone(db, linked, 1)
    first = rig.cycle(factory)
    assert first["sent"] == 0, "the map's rounds are held while its rebuild is due or running"
    seen = [first, *rig.until(factory, lambda s: total(s, "heights_live") == 1)]
    row = db.query(ControlHeight).one()
    assert (row.status, row.digest, bytes(row.asset)) == ("active", rig.asset.digest, (rig.tmp / "asset.npz").read_bytes())
    assert row.inputs_sha == hi.digest(row.inputs) and len(row.match_uuids) == 2
    assert row.report["seconds"] == 3.0, "what the build cost is kept with it"
    assert total(seen, "heights_sent") == 2 * linked.round_count, "every round sent exactly once"
    assert rig.state.heights.build is None and hr.plan_maps(db).due == []
    after = rig.until(factory, lambda s: total(s, "sent") > 0)
    # The cycle that stores the build releases the map, and its own submit already sends (and pushes) there.
    assert total([*seen, *after], "pushed") == 1, "the rounds name the new heights, and the worker is given them"
    assert (rig.tmp / "cache" / "heights" / f"{linked.map_name}.{rig.asset.digest}.height.npz").is_file()


def test_a_build_below_the_bar_is_rejected_the_old_heights_stay_and_the_rounds_flow(rig, factory, db, linked):
    clone(db, linked, 1)
    stored(db, linked.map_name)                                # active, built from these two matches
    clone(db, linked, hr.HEIGHT_REBUILD_EVERY)                 # five more: due, with nothing gone
    rig.mode(report=good_report(supported=4, ready=False, not_ready=["supported 40.0% is under 60%"]),
             **{"asset": str(rig.tmp / "four.npz"), "digest": real_asset(rig.tmp / "four.npz", supported=4).digest})
    seen = rig.until(factory, lambda s: total(s, "heights_rejected") == 1)
    assert total(seen, "heights_failed") == 0
    assert ch.active_digests(db) == {linked.map_name: "a" * 12}, "keeping the old heights is the fallback"
    assert [r.status for r in ch.rows(db, linked.map_name)] == ["rejected", "active"]
    assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0
    assert hr.plan_maps(db).due == [], "not built again until its inputs change"


def test_a_restarted_dispatcher_adopts_a_running_build_and_never_resends_to_it(rig, factory, db, linked):
    clone(db, linked, 1)
    rig.mode(sleep=1.5)
    rig.until(factory, lambda s: rig.builds.status()["running"] or rig.builds.status()["queued"])
    [build] = rig.builds.builds.values()
    rig.restart_dispatcher()                                   # the web app restarted: nothing in memory
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert total(seen, "heights_sent") == 0, "it asked the worker what state the build was in, and waited"
    assert total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1
    assert [b["id"] for b in rig.builds.builds.values()] == [build["id"]]
    assert db.query(ControlHeight).count() == 1


def test_a_restarted_dispatcher_finishes_a_half_sent_build_without_double_counting(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    one_round_batches(db, monkeypatch)
    rig.cycle(factory)
    rig.cycle(factory)
    [build] = rig.builds.builds.values()
    assert 0 < len(build["received"]) < 2 * linked.round_count
    spooled = rig.builds.status()["spool_bytes"]
    rig.restart_dispatcher()
    whole_batches(monkeypatch)
    rig.cycle(factory)                                         # sends everything again: the worker keeps what it had
    [again] = rig.builds.builds.values()
    assert again["id"] == build["id"] and len(again["received"]) == 2 * linked.round_count
    assert rig.builds.status()["spool_bytes"] > spooled, "the rounds it lacked were added; the rest cost nothing"
    assert sum(size for _, size in again["received"].values()) == rig.builds.status()["spool_bytes"]
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1


def test_a_lost_start_response_is_recovered_by_asking(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    real = rig.client.start_build
    lost = {"n": 0}

    def start_then_lose_the_answer(build_id, previous):
        real(build_id, previous)                               # the worker did start it
        if lost["n"] == 0:
            lost["n"] += 1
            raise remote.Unreachable("the answer never arrived")

    monkeypatch.setattr(rig.client, "start_build", start_then_lose_the_answer)
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert lost["n"] == 1 and total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1
    assert total(seen, "heights_sent") == 2 * linked.round_count, "nothing was sent to the started build"


def test_a_real_worker_restart_resends_from_the_first_round(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    one_round_batches(db, monkeypatch)
    first = rig.cycle(factory, step_s=10)
    assert first["heights_sent"] > 0 and rig.state.heights.build.todo
    rig.restart_worker_while_collecting()
    whole_batches(monkeypatch)
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1, step_s=10)
    assert total(seen, "heights_sent") == 2 * linked.round_count
    assert total(seen, "heights_failed") == 0


@pytest.mark.parametrize("where", ["open", "known build"])
def test_repeated_404s_exhaust_the_budget_and_release_the_map(rig, factory, db, linked, monkeypatch, where):
    clone(db, linked, 1)
    if where == "known build":
        one_round_batches(db, monkeypatch)
        rig.cycle(factory, step_s=10)
        assert rig.state.heights.build.job_id is not None

    def forgotten(*args, **kwargs):
        raise remote.WorkerGone("404: height builds unavailable")

    monkeypatch.setattr(rig.client, "open_build", forgotten)
    monkeypatch.setattr(rig.client, "build", forgotten)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60, step_s=400)
    assert not ch.active_digests(db) and db.query(ControlHeight).count() == 0
    assert rig.state.heights.build is None and all(n == hr.MAX_TRIES for n, _ in rig.state.heights.tries.values())
    assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0


@pytest.mark.parametrize("answer", [[], {}, {"idle": "yes"}, {"idle": 1}, {"idle": None}])
def test_malformed_health_before_open_spends_the_selected_manifests_budget(rig, factory, db, linked,
                                                                        monkeypatch, answer):
    clone(db, linked, 1)
    manifest = hr.current_manifests(db)[linked.map_name][0]
    monkeypatch.setattr(rig.client, "health", lambda: answer)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert rig.state.heights.tries[hi.key(manifest)][0] == hr.MAX_TRIES
    assert not rig.builds.builds and rig.state.heights.build is None and db.query(ControlHeight).count() == 0
    assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0


# ---------------------------------------------------------------- a result that isn't what it says


@pytest.mark.parametrize("name, mode", [
    ("not JSON", {"raw": "not json"}),
    ("no asset", {"drop": ["asset"]}),
    ("an asset that isn't base64", {"change": {"asset": "***"}}),
    ("bytes that aren't an asset", {"change": {"asset": base64.b64encode(b"not a zip").decode()}}),
    ("another build's key", {"change": {"key": "heights:Other:0000000000000000"}}),
    ("another map", {"change": {"map": "Bind"}}),
    ("other inputs", {"change": {"inputs_sha": "0" * 16}}),
    ("fewer rounds than the manifest", {"change": {"rounds": 1}}),
    ("a digest the bytes don't have", {"change": {"digest": "0" * 12}}),
    ("a report that isn't one", {"change": {"report": "ready"}}),
    ("a report whose counts aren't the asset's", {"change": {"report": good_report(supported=9)}}),
    ("no must-block evidence", {"change": {"report": {k: v for k, v in good_report().items() if k != "must_block"}}}),
    ("a check run against another list", {"change": {"report": good_report(must_block={"set": "o" * 12})}}),
    ("rules that aren't an object", {"change": {"rules": "v2"}}),
    ("rules of another format", {"change": {"rules": {"version": cf.HEIGHT_VERSION + 1,
                                                       "revision": cf.HEIGHT_RULES_REVISION, "constants": "x"}}}),
    ("rules of another revision", {"change": {"rules": {"version": cf.HEIGHT_VERSION,
                                                         "revision": cf.HEIGHT_RULES_REVISION + 1, "constants": "x"}}}),
])
def test_a_result_that_cant_be_trusted_is_never_stored_and_spends_the_budget(rig, factory, db, linked, name, mode):
    clone(db, linked, 1)
    rig.mode(**mode)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert db.query(ControlHeight).count() == 0, name
    assert total(seen, "heights_live") == 0 and total(seen, "heights_rejected") == 0
    more = rig.until(factory, lambda s: total(s, "sent") > 0)
    assert total(more, "heights_failed") == 0, "the budget is spent: the map's rounds flow, with no more tries"
    assert all("height" not in json.loads(job.task or b"{}") for job in rig.control.jobs.values() if job.kind == "control")


def test_a_build_that_fails_on_the_worker_spends_the_budget_and_other_maps_are_never_stopped(rig, factory, db, linked):
    clone(db, linked, 1)
    other = clone(db, linked, 1)[0]
    other.map_name = "Haven"                                   # a second map with one match: nothing due for it
    # (Haven, not Bind: the committed index gives Bind no control layer yet, so its rounds would never be sent)
    db.commit()
    rig.mode(raw="not json")
    first = rig.cycle(factory)
    assert first["sent"] > 0, "the other map's rounds go out while this map's are held"
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert total(seen, "heights_live") == 0


# ---------------------------------------------------------------- failures that must not hold a map forever


def test_a_refused_request_is_a_failed_try_not_an_unreachable_worker(tmp_path, factory, db, linked):
    clone(db, linked, 1)
    rig = Rig(tmp_path, height_spool_bytes=10)                 # the worker answers 413 to the first batch
    try:
        seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
        assert total(seen, "unreachable") == 0 and not rig.builds.builds, "and the half-opened build was cancelled"
        assert total(rig.until(factory, lambda s: total(s, "sent") > 0), "sent") > 0
    finally:
        rig.httpd.shutdown()


def test_a_round_too_large_to_send_fails_the_build_instead_of_being_sent(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    monkeypatch.setattr(hr, "BATCH_BYTES", 8)
    seen = rig.until(factory, lambda s: total(s, "heights_failed") == hr.MAX_TRIES, cycles=60)
    assert total(seen, "heights_sent") == 0 and not rig.builds.builds


def test_a_build_nobody_can_finish_sending_is_given_up_on(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    answers = iter([True])                                     # idle once, to open it; then a parse that never ends
    monkeypatch.setattr(rig.client, "health", lambda: {"idle": next(answers, False)})
    rig.cycle(factory, step_s=10)
    assert rig.state.heights.build is not None
    seen = [rig.cycle(factory, step_s=hr.COLLECT_STALE_S + 1)]
    assert total(seen, "heights_failed") == 1 and rig.state.heights.build is None and not rig.builds.builds


def test_queued_behind_a_parse_is_never_a_failure_and_queued_on_an_idle_worker_is(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    rig.control.idle = lambda: False                           # the runner starts nothing
    rig.until(factory, lambda s: rig.builds.status()["queued"], step_s=10)
    monkeypatch.setattr(rig.client, "health", lambda: {"idle": False})      # because the worker is parsing
    for _ in range(4):
        assert rig.cycle(factory, step_s=hr.QUEUED_STALE_S)["heights_failed"] == 0
    monkeypatch.setattr(rig.client, "health", lambda: {"idle": True})       # idle, and still not started
    rig.cycle(factory, step_s=10)
    assert rig.cycle(factory, step_s=hr.QUEUED_STALE_S + 1)["heights_failed"] == 1


def test_an_error_inside_the_height_step_never_stops_round_dispatch(rig, factory, db, linked, monkeypatch):
    def boom(session):
        raise RuntimeError("unforeseen")

    monkeypatch.setattr(hr, "plan_maps", boom)
    counts = rig.cycle(factory)
    assert counts["heights_error"] == 1 and counts["sent"] == remote.IN_FLIGHT


def test_an_answer_that_isnt_shaped_as_the_protocol_says_spends_a_try_and_is_not_met_again(rig, factory, db, linked,
                                                                                         monkeypatch):
    clone(db, linked, 1)
    rig.mode(sleep=1.5)
    rig.until(factory, lambda s: rig.builds.status()["running"] or rig.builds.status()["queued"])
    real = rig.client.build
    monkeypatch.setattr(rig.client, "build", lambda build_id: ["not", "a", "build"])
    counts = rig.cycle(factory)
    assert counts["heights_failed"] == 1 and counts.get("heights_error", 0) == 0
    assert rig.state.heights.build is None, "dropped here: left to the cycle's catch-all it would repeat every cycle"
    monkeypatch.setattr(rig.client, "build", real)
    seen = rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert total(seen, "heights_failed") == 0 and len(rig.builds.builds) == 1, "the next try adopts the worker's build"


def test_the_height_step_never_commits_or_rolls_back_the_dispatchers_own_session(rig, factory, db, linked):
    # That session holds the cycle's transaction-scoped lock on PostgreSQL: ending its transaction in the middle
    # of a cycle would let a second dispatcher in. Turning heights off and storing a build both write, and both
    # must do it through a session of their own.
    import time

    from sqlalchemy import event

    def watched():
        session, ended = factory(), []
        event.listen(session, "after_commit", lambda s: ended.append("commit"))
        event.listen(session, "after_rollback", lambda s: ended.append("rollback"))
        return session, ended

    others = clone(db, linked, 1)
    stored(db, linked.map_name)
    delete(db, others[0])                                      # one match left under active heights: off
    session, ended = watched()
    counts = {}
    hr.step(factory, session, rig.client, hr.HeightState(), 1.0, counts)
    assert counts["heights_off"] == 1 and ended == []
    session.close()
    check = factory()
    assert ch.active_digests(check) == {}
    check.close()

    clone(db, linked, 1)                                       # two matches again: a build, stored active
    hstate, t = hr.HeightState(), 1.0
    for _ in range(40):
        session, ended = watched()
        counts, t = {}, t + 400.0
        hr.step(factory, session, rig.client, hstate, t, counts)
        assert ended == [], "nor while a build is sent, waited for, verified and stored"
        session.close()
        if counts["heights_live"]:
            break
        time.sleep(0.15)
    assert counts["heights_live"] == 1


def test_generation_published_during_collection_keeps_the_pending_result_from_being_stored(rig, factory, db,
                                                                                         linked, monkeypatch):
    clone(db, linked, 1)
    manifest = hr.current_manifests(db)[linked.map_name][0]
    build = hr.Build(linked.map_name, hi.key(manifest), manifest, [], opened_at=0)
    result = {"key": build.key, "map": build.map_name, "inputs_sha": hi.digest(manifest),
              "rounds": hi.rounds_expected(manifest), "digest": rig.asset.digest, "report": good_report(),
              "rules": hc.rules(), "asset": base64.b64encode((rig.tmp / "asset.npz").read_bytes()).decode()}
    stored(db, linked.map_name)
    before = ch.active_digests(db)
    real = rc.geometry_inputs
    monkeypatch.setattr(rc, "geometry_inputs", lambda m, heights=None: {**(real(m, heights) or {}), "features": "new-generation"})
    counts = dict.fromkeys(hr.COUNTS, 0)
    with pytest.raises(hr._Stale):
        hr._finish(factory, db, build, {"status": "done", "result": result}, counts)
    assert ch.active_digests(db) == before and db.query(ControlHeight).count() == 1
    assert counts["heights_live"] == counts["heights_rejected"] == counts["heights_failed"] == 0
    assert linked.map_name in hr.plan_maps(db).skipped


# ---------------------------------------------------------------- inputs that move under a build


def test_a_match_deleted_while_its_rounds_are_being_sent_drops_the_build(rig, factory, db, linked, monkeypatch):
    others = clone(db, linked, 2)
    one_round_batches(db, monkeypatch)
    rig.cycle(factory)
    first_key = rig.state.heights.build.key
    delete(db, others[0])
    counts = rig.cycle(factory)
    assert counts["heights_stale"] == 1 and counts["heights_failed"] == 0
    whole_batches(monkeypatch)
    rig.until(factory, lambda s: total(s, "heights_live") == 1)
    row = db.query(ControlHeight).one()
    assert hi.key(row.inputs) != first_key and len(row.match_uuids) == 2, "built from the matches that are left"


def test_a_match_deleted_after_the_build_started_keeps_its_result_from_going_live(rig, factory, db, linked):
    others = clone(db, linked, 2)
    rig.mode(sleep=1.5)
    rig.until(factory, lambda s: rig.builds.status()["running"] or rig.builds.status()["queued"])
    delete(db, others[0])
    seen = rig.until(factory, lambda s: total(s, "heights_stale") >= 1)
    assert total(seen, "heights_live") == 0, "a result for inputs that are gone is never activated"
    assert db.query(ControlHeight).count() == 0
    rig.mode(sleep=0)
    rig.until(factory, lambda s: total(s, "heights_live") == 1)
    assert len(db.query(ControlHeight).one().match_uuids) == 2


def test_a_good_result_for_inputs_that_changed_meanwhile_is_not_stored(rig, factory, db, linked):
    # Between the last plan and the result arriving, a match was deleted: the result is whole and honest, and
    # still must not go live.
    others = clone(db, linked, 2)
    manifest, replays = hr.current_manifests(db)[linked.map_name]
    build = hr.Build(linked.map_name, hi.key(manifest), manifest, [], opened_at=0.0)
    result = {"key": build.key, "map": build.map_name, "inputs_sha": hi.digest(manifest),
              "rounds": hi.rounds_expected(manifest), "digest": rig.asset.digest, "report": good_report(),
              "rules": hc.rules(), "asset": base64.b64encode((rig.tmp / "asset.npz").read_bytes()).decode()}
    delete(db, others[0])
    counts = dict.fromkeys(hr.COUNTS, 0)
    with pytest.raises(hr._Stale):
        hr._finish(factory, db, build, {"status": "done", "result": result}, counts)
    assert db.query(ControlHeight).count() == 0 and counts["heights_live"] == 0


def test_evidence_deleted_and_the_rebuild_rejected_turns_the_maps_heights_off(rig, factory, db, linked):
    others = clone(db, linked, 2)
    stored(db, linked.map_name)
    delete(db, others[0])
    rig.mode(report=good_report(supported=4, ready=False, not_ready=["supported 40.0% is under 60%"]),
             **{"asset": str(rig.tmp / "four.npz"), "digest": real_asset(rig.tmp / "four.npz", supported=4).digest})
    seen = rig.until(factory, lambda s: total(s, "heights_rejected") == 1)
    assert total(seen, "heights_off") == 1 and ch.active_digests(db) == {}
    assert [r.status for r in ch.rows(db, linked.map_name)] == ["rejected", "superseded"]


def test_fewer_than_two_matches_left_turns_heights_off_without_a_build(rig, factory, db, linked):
    others = clone(db, linked, 1)
    stored(db, linked.map_name)
    delete(db, others[0])
    counts = rig.cycle(factory)
    assert counts["heights_off"] == 1 and ch.active_digests(db) == {} and not rig.builds.builds
    assert counts["sent"] > 0, "and its rounds are recomputed flat"


def test_it_is_off_by_default_and_in_demo_mode(rig, factory, db, linked, monkeypatch):
    clone(db, linked, 1)
    monkeypatch.setattr(settings, "replay_heights_auto", False)
    assert rig.cycle(factory)["sent"] == remote.IN_FLIGHT and not rig.builds.builds
    monkeypatch.setattr(settings, "replay_heights_auto", True)
    monkeypatch.setattr(settings, "demo_mode", True)
    assert hr.enabled() is False
