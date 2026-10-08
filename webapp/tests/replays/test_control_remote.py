"""The web app's map-control dispatcher for the replay worker (app/services/replay_control_remote.py;
docs/map-control-worker-plan.md, step 4), on SQLite with a fake worker client."""

import base64
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from test_control_store import db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed, pg  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
from app.models.match import Match  # noqa: E402
from app.models.replay import Replay, ReplayGap, ReplayRoundControl, ReplayRoundGapRun  # noqa: E402
from app.replays import choke_assets  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402
from app.services import replay_gaps  # noqa: E402


GAP_ROW = {"seq": 0, "kind": "predicted", "map": "Toy", "victim_slot": 0, "victim_side": None, "t_open": 1.0,
           "t_last_exposed": 2.0, "t_close": 7.0, "spot_cell": 5, "victim_cell": 6, "distance_m": 3.0,
           "angle_deg": 170.0, "qualified_s": 2.0, "flicker": False, "cause": "open_timing", "cause_detail": {1: 2.5},
           "choke_seq": [], "route": [[[1.0, 1.0, 1.0]]], "candidate_slots": [5], "candidate_distances": {5: 3.0},
           "checked_at": None, "stood_at": None, "stood_by": None, "shot_at": None, "shot_by": None,
           "killed_at": None, "killed_by": None, "victim_won_at": None, "context": {}, "linked_seq": None}


class FakeWorker:
    """Accepts tasks and answers jobs; `answer(task)` makes a job's final state (None: still running).
    `gaps` is the `control.gaps_protocol` its health names: None is a worker image from before timing gaps
    (the default, so every older test here is also the old-worker regression), 1 a capable one."""

    def __init__(self, answer=None, busy_after=None, gaps=None):
        self.tasks, self.jobs, self.busy_after = {}, {}, busy_after
        self.answer = answer or self.ok
        self.forget = set()
        self.gaps, self.control_on, self.health_error, self.health_reads = gaps, True, None, 0

    def health(self):
        self.health_reads += 1
        if self.health_error is not None:
            raise self.health_error
        control = {"enabled": self.control_on, "queued": 0, "running": 0, "warm": [], "preempted": 0}
        if self.gaps is not None:
            control["gaps_protocol"] = self.gaps
        return {"ok": True, "queued": 0, "control": control}

    def ok(self, task):
        result = {"status": "ok", "data": base64.b64encode(gzip.compress(b"d")).decode(),
                  "summary": base64.b64encode(cf.pack_summary({})).decode(),
                  "revision": cf.CONTROL_REVISION, "data_version": cf.DATA_VERSION,
                  "geometry": rc.geometry_inputs(task["map"])}
        if self.gaps is not None:
            result["figures"] = cf.figures_hash()
        if "gaps" in task:
            assert self.gaps is not None, "an older image fails the whole task on a gaps block"
            result["gaps"] = self.gap_result(task)
        return {"status": "done", "result": json.loads(json.dumps(result))}     # as it comes over HTTP

    @staticmethod
    def gap_result(task, status="ok"):
        run = {"status": status, "fingerprint": task["gaps"]["gap_fingerprint"],
               "gaps_revision": replay_gaps.GAPS_REVISION, "chokes_hash": choke_assets.asset_hash(task["map"]),
               "notes": {7: 1} if status == "ok" else {}, "error": None if status == "ok" else "ValueError: boom"}
        return {"run": run, "rows": [GAP_ROW, {**GAP_ROW, "seq": 1}] if status == "ok" else []}

    def submit(self, task):
        if self.busy_after is not None and len(self.tasks) >= self.busy_after:
            raise remote.WorkerBusy("503")
        job_id = f"j{len(self.tasks)}"
        self.tasks[job_id] = task
        return {"id": job_id, "status": "queued"}

    def job(self, job_id):
        if job_id in self.forget:
            raise remote.WorkerGone("404")
        return self.answer(self.tasks[job_id]) or {"status": "running"}


def rows(db, replay):
    db.expire_all()
    return {r.round_number: r for r in db.query(ReplayRoundControl).filter(ReplayRoundControl.replay_id == replay.id)}


def test_missing_rounds_are_sent_then_stored(factory, db, linked):
    worker, state = FakeWorker(), remote.State()
    first = remote.cycle(factory, worker, state, now=0)
    assert first["sent"] == remote.IN_FLIGHT and len(state.in_flight) == remote.IN_FLIGHT
    sent = sorted(t["link"] and int(t["key"].split(":")[1]) for t in worker.tasks.values())
    assert sent == list(range(1, remote.IN_FLIGHT + 1))
    task = next(iter(worker.tasks.values()))
    assert set(task) == {"key", "map", "blob", "link"} and set(task["link"]) == {"sides", "db_deaths"}
    second = remote.cycle(factory, worker, state, now=1)
    assert second["stored"] == remote.IN_FLIGHT
    stored = rows(db, linked)
    assert sorted(stored) == list(range(1, remote.IN_FLIGHT + 1))
    [planned] = rc.plan(db, rounds={1}, force=True)
    assert stored[1].status == "ok" and stored[1].fingerprint == planned.fingerprint
    # later cycles finish the replay, and then there is nothing left to send
    for n in range(2, 10):
        remote.cycle(factory, worker, state, now=n)
    assert len(rows(db, linked)) == linked.round_count
    assert remote.cycle(factory, worker, state, now=20)["sent"] == 0


def sent_rounds(worker):
    return sorted(int(t["key"].split(":")[1]) for t in worker.tasks.values())


def test_unlinked_replays_are_sent_too(factory, db, linked):
    linked.link_status = "unlinked"
    linked.match_id = None
    db.commit()
    worker = FakeWorker()
    assert remote.cycle(factory, worker, remote.State(), now=0)["sent"] == remote.IN_FLIGHT


def test_stale_rounds_are_sent_and_a_current_failure_is_not(factory, db, linked):
    put_row(db, linked, 1, fingerprint="0" * 16)                    # stale: an old fingerprint
    put_row(db, linked, 2, status="failed")                         # failed under the current inputs
    put_row(db, linked, 3, status="failed", fingerprint="1" * 16)   # failed under old inputs: stale
    for n in range(4, linked.round_count + 1):
        put_row(db, linked, n)
    worker = FakeWorker()
    remote.cycle(factory, worker, remote.State(), now=0)
    assert sent_rounds(worker) == [1, 3]


def test_rank_puts_linked_by_played_at_before_the_rest_by_upload_time():
    class P:
        def __init__(self, rid, n):
            self.replay_id, self.round_number = rid, n
    played = {1: datetime(2026, 9, 1, tzinfo=timezone.utc), 2: datetime(2026, 10, 1, tzinfo=timezone.utc)}
    created = {3: datetime(2026, 10, 4, tzinfo=timezone.utc), 4: datetime(2026, 10, 3, tzinfo=timezone.utc)}
    ranked = remote._rank([P(1, 1), P(3, 2), P(2, 2), P(2, 1), P(4, 1), P(3, 1)], played, created)
    assert [(p.replay_id, p.round_number) for p in ranked] == [(2, 1), (2, 2), (1, 1), (3, 1), (3, 2), (4, 1)]


@pytest.mark.parametrize("unlink", [False, True])
def test_order_reads_played_at_through_the_link_or_falls_back_to_upload_time(db, linked, monkeypatch, unlink):
    db.get(Match, linked.match_id).played_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    if unlink:
        linked.link_status, linked.match_id = "unlinked", None
    db.commit()
    seen = {}
    monkeypatch.setattr(remote, "_rank", lambda todo, played, created: seen.update(played=played, created=created) or todo)
    remote._order(db, rc.plan(db))
    if unlink:
        assert seen["played"] == {} and set(seen["created"]) == {linked.id}
    else:
        assert set(seen["played"]) == {linked.id} and seen["created"] == {}


def test_planning_is_throttled_while_nothing_can_be_sent(factory, db, linked, monkeypatch):
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    calls = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: calls.append(1) or real(session, **kw))
    state = remote.State()
    remote.cycle(factory, FakeWorker(), state, now=0)
    remote.cycle(factory, FakeWorker(), state, now=remote.CYCLE_S)
    assert len(calls) == 1
    remote.cycle(factory, FakeWorker(), state, now=remote.PLAN_IDLE_S + 1)
    assert len(calls) == 2


def test_rounds_that_can_never_be_sent_dont_defeat_the_throttle(factory, db, linked, monkeypatch):
    monkeypatch.setattr(rc, "map_layer", lambda name: None)      # every round is no_map, and has no row
    calls = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: calls.append(1) or real(session, **kw))
    state = remote.State()
    for t in range(0, 100, remote.CYCLE_S):
        remote.cycle(factory, FakeWorker(), state, now=t)
    assert len(calls) == 1


def test_exhausted_retries_dont_defeat_the_throttle(factory, db, linked):
    state = remote.State()
    for p in rc.plan(db):
        for _ in range(remote.MAX_TRIES):
            state.failed(remote.task_key(p.replay_id, p.round_number, p.fingerprint), 0)
    worker = FakeWorker()
    remote.cycle(factory, worker, state, now=1)
    assert not worker.tasks and state.last_found is False


def test_a_job_queued_through_a_long_parse_batch_is_still_collected(factory, db, linked):
    phase = {"queued": True}
    worker = FakeWorker(answer=lambda task: {"status": "queued"} if phase["queued"] else FakeWorker.ok(worker, task))
    state = remote.State()
    remote.cycle(factory, worker, state, now=0)
    sent = len(worker.tasks)
    t = 0
    for _ in range(4):                               # four STALE_JOB_S windows queued on the worker
        t += remote.STALE_JOB_S + 1
        remote.cycle(factory, worker, state, now=t)
    assert len(state.in_flight) == sent and not state.tries, "queued time never counts (D8)"
    phase["queued"] = False
    counts = remote.cycle(factory, worker, state, now=t + 1)
    assert counts["stored"] == sent


def test_a_job_running_past_the_limit_counts_as_a_failure(factory, db, linked):
    worker = FakeWorker(answer=lambda task: {"status": "running"})
    state = remote.State()
    remote.cycle(factory, worker, state, now=0)
    remote.cycle(factory, worker, state, now=1)                      # first seen running
    counts = remote.cycle(factory, worker, state, now=2 + remote.STALE_JOB_S)
    assert counts["timed_out"] == remote.IN_FLIGHT and all(c == 1 for c, _ in state.tries.values())


def test_a_preempted_job_starts_its_running_clock_again(factory, db, linked):
    phase = {"status": "running"}
    worker = FakeWorker(answer=lambda task: {"status": phase["status"]})
    state = remote.State()
    remote.cycle(factory, worker, state, now=0)
    remote.cycle(factory, worker, state, now=1)                       # seen running
    phase["status"] = "queued"
    remote.cycle(factory, worker, state, now=2)                       # preempted: back in the worker's queue
    phase["status"] = "running"
    counts = remote.cycle(factory, worker, state, now=3 + remote.STALE_JOB_S)
    assert counts["timed_out"] == 0 and not state.tries


@pytest.mark.parametrize("change, counted", [
    (lambda r: r.update(revision=cf.CONTROL_REVISION + 1), "dropped_revision"),
    (lambda r: r.update(geometry={**r["geometry"], "sight": "other"}), "dropped_geometry"),
    # a result computed with a feature generation this deploy doesn't have (map features, M5)
    (lambda r: r.update(geometry={**r["geometry"], "features": "0123456789abcdef"}), "dropped_geometry"),
])
def test_a_result_from_another_deploy_is_dropped_and_asked_again(factory, db, linked, change, counted):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        change(job["result"])
        return job

    worker, state = FakeWorker(answer), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts[counted] == remote.IN_FLIGHT and not rows(db, linked)
    assert all(state.tries[k][0] == 1 for k in state.tries)
    # backoff: not asked again at once, then again after BACKOFF_S
    before = len(worker.tasks)
    remote.cycle(factory, worker, state, now=2)
    assert len(worker.tasks) == before
    remote.cycle(factory, worker, state, now=2 + remote.BACKOFF_S)
    assert len(worker.tasks) > before


def test_the_rounds_own_failure_is_stored_and_the_machines_is_retried_then_left(factory, db, linked):
    def answer(task):
        n = int(task["key"].split(":")[1])
        return {"status": "failed", "error_kind": "engine" if n == 1 else "infra", "error": "ControlError: no side"}

    worker, state = FakeWorker(answer), remote.State()
    t = 0
    for _ in range(2 * remote.MAX_TRIES + 2):
        remote.cycle(factory, worker, state, now=t)
        t += remote.BACKOFF_S + 1
    stored = rows(db, linked)
    assert list(stored) == [1] and stored[1].status == "failed" and stored[1].error.startswith("ControlError")
    round_two = [k for k in state.tries if k.split(":")[1] == "2"]
    assert round_two and state.tries[round_two[0]][0] == remote.MAX_TRIES
    asked = sum(1 for task in worker.tasks.values() if task["key"].split(":")[1] == "2")
    assert asked == remote.MAX_TRIES


def test_a_forgotten_job_is_asked_again_and_a_busy_worker_stops_the_cycle(factory, db, linked):
    worker, state = FakeWorker(answer=lambda task: None, busy_after=3), remote.State()
    counts = remote.cycle(factory, worker, state, now=0)
    assert counts["sent"] == 3 and counts["busy"] == 1
    worker.forget = set(worker.tasks)
    worker.busy_after = None
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["forgotten"] == 3 and counts["sent"] == remote.IN_FLIGHT


def test_pg_one_instance_dispatches_at_a_time(pg, condensed):
    from sqlalchemy import text

    from test_replay_store import add_match

    from app.replays import store

    session = pg()
    add_match(session)
    store.store_replay(session, condensed, source="local")
    session.commit()
    holder = pg()
    holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": remote.LOCK_ID})   # another instance's cycle
    worker = FakeWorker()
    assert remote.cycle(pg, worker, remote.State(), now=0).get("locked_out") == 1 and not worker.tasks
    holder.rollback()
    assert remote.cycle(pg, worker, remote.State(), now=1)["sent"] == remote.IN_FLIGHT
    holder.close()
    session.close()


def test_it_is_off_by_default_without_a_worker_and_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "replay_worker_url", "worker:8080")
    monkeypatch.setattr(settings, "replay_control_remote", False)
    monkeypatch.setattr(settings, "demo_mode", False)
    assert not remote.enabled() and remote.start(None) is None
    monkeypatch.setattr(settings, "replay_control_remote", True)
    assert remote.enabled()
    monkeypatch.setattr(settings, "demo_mode", True)
    assert not remote.enabled()
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "replay_worker_url", None)
    assert not remote.enabled()


def test_the_client_maps_the_workers_answers():
    import io
    import urllib.error

    client = remote.ControlClient("worker:8080")
    assert client.base == "http://worker:8080"

    def fail(code):
        def call(request, timeout):
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b"{}"))
        return call

    for code, error in ((404, remote.WorkerGone), (503, remote.WorkerBusy), (500, remote.Unreachable)):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(remote.urllib.request, "urlopen", fail(code))
            with pytest.raises(error):
                client.job("j1")


def test_a_map_with_a_feature_generation_sends_it_and_keeps_only_results_computed_with_it(factory, db, linked, monkeypatch):
    # map features (M5): no committed map has one, so this patches the index as a later build would write it
    index, tags, maps = rc._assets()
    patched = {name: {**row, "features_sha": "feat0000feat0000"} for name, row in index.items()}
    monkeypatch.setattr(rc, "_assets", lambda: (patched, tags, maps))
    worker, state = FakeWorker(), remote.State()
    remote.cycle(factory, worker, state, now=0)
    task = next(iter(worker.tasks.values()))
    assert task["features"] == "feat0000feat0000" and rc.geometry_inputs(task["map"])["features"] == "feat0000feat0000"
    assert remote.cycle(factory, worker, state, now=1)["stored"] == remote.IN_FLIGHT

    def stale(task):                     # a worker still on the previous generation (no features)
        job = FakeWorker.ok(worker2, task)
        job["result"]["geometry"] = {k: v for k, v in job["result"]["geometry"].items() if k != "features"}
        return job

    worker2, state2 = FakeWorker(stale), remote.State()
    remote.cycle(factory, worker2, state2, now=100)
    assert remote.cycle(factory, worker2, state2, now=101)["dropped_geometry"] > 0


# ---------------------------------------------------------------- timing gaps, only from a worker that can
# (docs/superpowers/plans/2026-10-07-worker-gaps-and-kill-one-impl.md, Task 3)


def gap_runs(db, replay):
    db.expire_all()
    return {r.round_number: r for r in db.query(ReplayRoundGapRun).filter(ReplayRoundGapRun.replay_id == replay.id)}


@pytest.mark.parametrize("health, capable", [
    ({"control": {"enabled": True, "gaps_protocol": 1}}, True),
    ({"control": {"enabled": True}}, False),                          # an image from before timing gaps
    ({"control": {"enabled": True, "gaps_protocol": 2}}, False),      # a protocol this deploy doesn't speak
    ({"control": {"enabled": True, "gaps_protocol": "1"}}, False),
    ({"control": {"enabled": True, "gaps_protocol": True}}, False),
    ({"control": {"enabled": False, "gaps_protocol": 1}}, False),     # control switched off on the worker
    ({"control": {"gaps_protocol": 1}}, False),
    ({"control": None}, False), ({}, False), (None, False), ([], False),
])
def test_only_a_worker_with_control_on_and_protocol_one_supports_gaps(health, capable):
    assert remote.supports_gaps(health) is capable


@pytest.mark.parametrize("worker_is", ["older", "other_protocol", "control_off", "unreachable_health"])
def test_a_worker_without_the_capability_gets_plain_control_and_no_gap_work(factory, db, linked, worker_is):
    worker, state = FakeWorker(), remote.State()
    if worker_is == "other_protocol":
        worker.gaps = 2
    elif worker_is == "control_off":
        worker.gaps, worker.control_on = 1, False
    elif worker_is == "unreachable_health":
        worker.gaps, worker.health_error = 1, remote.Unreachable("the replay worker is unreachable")
    first = remote.cycle(factory, worker, state, now=0)
    assert first["sent"] == remote.IN_FLIGHT == first["gaps_unavailable"] and worker.health_reads == 1
    assert all(set(t) == {"key", "map", "blob", "link"} for t in worker.tasks.values()), "the legacy task"
    assert all(len(t["key"].split(":")) == 3 for t in worker.tasks.values()), "and the legacy key"
    if worker_is == "unreachable_health":
        worker.gaps = None                          # its results carry no figures either
    second = remote.cycle(factory, worker, state, now=1)
    assert second["stored"] == remote.IN_FLIGHT and len(rows(db, linked)) == remote.IN_FLIGHT
    assert not gap_runs(db, linked) and not state.tries, "no gap run, and nothing counted against a retry"
    assert second["gaps_dropped"] == second["gaps_skipped"] == second["gaps_stored"] == 0


def test_a_capable_worker_gets_the_sites_gap_keys_and_both_results_are_stored(factory, db, linked):
    worker, state = FakeWorker(gaps=1), remote.State()
    first = remote.cycle(factory, worker, state, now=0)
    assert first["sent"] == remote.IN_FLIGHT and first["gaps_unavailable"] == 0 and worker.health_reads == 1
    assert sent_rounds(worker) == list(range(1, remote.IN_FLIGHT + 1))
    [planned] = rc.plan(db, rounds={1}, force=True)
    task = next(t for t in worker.tasks.values() if t["gaps"]["round"] == 1)
    assert set(task) == {"key", "map", "blob", "link", "gaps"}
    gap_print = replay_gaps.gap_fingerprint(planned.fingerprint, linked.map_name)
    assert task["gaps"] == {"replay_id": linked.id, "round": 1, "fingerprint": planned.fingerprint,
                            "gap_fingerprint": gap_print,
                            "engine_key": replay_gaps.engine_key(planned.fingerprint, linked.map_name)}
    assert task["key"] == f"{linked.id}:1:{planned.fingerprint}:g1:{gap_print}"
    assert task["key"] != remote.task_key(linked.id, 1, planned.fingerprint), "never a plain task's result"
    assert all(f.expect_gaps for f in state.in_flight.values())
    second = remote.cycle(factory, worker, state, now=1)
    assert second["stored"] == second["gaps_stored"] == remote.IN_FLIGHT and worker.health_reads == 2
    runs = gap_runs(db, linked)
    assert sorted(runs) == list(range(1, remote.IN_FLIGHT + 1))
    assert runs[1].status == "ok" and runs[1].fingerprint == gap_print and runs[1].gap_count == 2
    assert db.query(ReplayGap).filter_by(replay_id=linked.id, round_number=1).count() == 2, "rows through JSON"
    assert rows(db, linked)[1].status == "ok"


def test_a_rounds_own_gap_failure_is_stored_and_control_stays_ok(factory, db, linked):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        job["result"]["gaps"] = json.loads(json.dumps(FakeWorker.gap_result(task, status="failed")))
        return job

    worker, state = FakeWorker(answer, gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["stored"] == counts["gaps_stored"] == remote.IN_FLIGHT and counts["stored_failed"] == 0
    run = gap_runs(db, linked)[1]
    assert run.status == "failed" and run.error.startswith("ValueError") and run.gap_count == 0
    assert rows(db, linked)[1].status == "ok" and not state.tries


@pytest.mark.parametrize("change", [
    lambda g: g["run"].update(gaps_revision=replay_gaps.GAPS_REVISION + 1),
    lambda g: g["run"].update(chokes_hash="another"),
    lambda g: g["run"].update(fingerprint="0" * 16),
    lambda g: g["run"].update(status="ok", fingerprint=""),              # failed before it had its keys
    lambda g: g.pop("rows"),
    lambda g: g.clear(),
])
def test_gaps_from_other_rules_or_assets_are_dropped_and_control_is_kept(factory, db, linked, change):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        change(job["result"]["gaps"])
        return job

    worker, state = FakeWorker(answer, gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["stored"] == counts["gaps_dropped"] == remote.IN_FLIGHT and counts["gaps_stored"] == 0
    assert len(rows(db, linked)) == remote.IN_FLIGHT and not gap_runs(db, linked) and not state.tries


@pytest.mark.parametrize("figures", [None, "0" * 16])
def test_a_capable_result_without_this_deploys_figures_stores_nothing(factory, db, linked, figures):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        if figures is None:
            del job["result"]["figures"]
        else:
            job["result"]["figures"] = figures
        return job

    worker, state = FakeWorker(answer, gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["dropped_figures"] == remote.IN_FLIGHT and counts["stored"] == counts["gaps_stored"] == 0
    assert not rows(db, linked) and not gap_runs(db, linked)
    assert all(count == 1 for count, _ in state.tries.values()), "a trust failure: asked again after the backoff"


def test_a_plain_result_naming_other_figures_is_dropped_too(factory, db, linked):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        job["result"]["figures"] = "0" * 16          # a newer worker on other figures, asked without gaps
        return job

    worker, state = FakeWorker(answer), remote.State()
    remote.cycle(factory, worker, state, now=0)
    assert remote.cycle(factory, worker, state, now=1)["dropped_figures"] == remote.IN_FLIGHT and not rows(db, linked)


def test_a_link_that_moves_between_the_two_stores_skips_the_gaps(factory, db, linked, monkeypatch):
    real = remote.store_round

    def store_then_relink(session_factory, replay_id, round_number, fingerprint, row, **kw):
        outcome = real(session_factory, replay_id, round_number, fingerprint, row, **kw)
        if round_number == 1:                        # a relink lands after control is stored, before the gaps
            session = session_factory()
            session.get(Replay, replay_id).db_deaths = {"1": [{"slot": 0, "t_db": 30.0}]}
            session.commit()
            session.close()
        return outcome

    monkeypatch.setattr(remote, "store_round", store_then_relink)
    worker, state = FakeWorker(gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["gaps_skipped"] == 1 and counts["gaps_stored"] == remote.IN_FLIGHT - 1
    assert 1 not in gap_runs(db, linked) and not state.tries, "skipped, not failed: nothing counted"


def test_a_full_task_that_lands_on_an_older_worker_is_asked_again_plain(factory, db, linked):
    # The worker went back to an older image between the health read and the submit: the gaps block fails the
    # whole task there (tests/replays/test_gaps_task.py pins that). The next pass reads health again.
    def old_image(task):
        if "gaps" in task:
            return {"status": "failed", "error_kind": "infra", "error": "ModuleNotFoundError: No module named 'app.gaps'"}
        return FakeWorker.ok(worker, task)

    worker, state = FakeWorker(old_image, gaps=1), remote.State()
    assert remote.cycle(factory, worker, state, now=0)["sent"] == remote.IN_FLIGHT
    worker.gaps = None                               # what its health says from now on
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["infra_failed"] == remote.IN_FLIGHT
    assert counts["sent"] == remote.IN_FLIGHT, "asked again at once, plain: the failed key was the full one"
    plain = [t for t in worker.tasks.values() if "gaps" not in t]
    assert sorted(int(t["key"].split(":")[1]) for t in plain) == list(range(1, remote.IN_FLIGHT + 1))
    worker.gaps = 1                                  # the worker updates: the rest go out with gaps, no restart
    before = len(worker.tasks)
    assert remote.cycle(factory, worker, state, now=2)["stored"] == remote.IN_FLIGHT
    assert all(len(key.split(":")) == 5 for key in state.tries), "only the full keys carry a failure"
    later = [t for job_id, t in worker.tasks.items() if int(job_id[1:]) >= before]
    full = [t for t in later if not t.get("gaps_only")]
    assert len(full) == linked.round_count - remote.IN_FLIGHT and all("gaps" in t for t in later)
    # and the rounds whose control was stored plain get their gaps alone, in what is left of the pool (Task 4)
    assert [int(t["key"].split(":")[1]) for t in later if t.get("gaps_only")] == [1]


def test_once_health_names_the_protocol_rounds_still_needing_control_go_out_with_gaps(factory, db, linked):
    worker, state = FakeWorker(), remote.State()
    remote.cycle(factory, worker, state, now=0)                      # plain, to an older worker
    worker.gaps = 1
    counts = remote.cycle(factory, worker, state, now=1)             # the same State: no restart
    rest = linked.round_count - remote.IN_FLIGHT
    assert counts["stored"] == remote.IN_FLIGHT and counts["sent"] == rest and counts["gaps_unavailable"] == 0
    newer = [t for job_id, t in worker.tasks.items() if int(job_id[1:]) >= remote.IN_FLIGHT and not t.get("gaps_only")]
    assert len(newer) == rest and all("gaps" in t for t in newer)


def test_a_round_in_flight_is_never_sent_again_when_the_capability_changes(factory, db, linked):
    worker, state = FakeWorker(answer=lambda task: {"status": "queued"}), remote.State()
    remote.cycle(factory, worker, state, now=0)                      # eight plain jobs, waiting on the worker
    worker.gaps = 1
    state.in_flight.pop(next(iter(state.in_flight)))                 # room for one more
    remote.cycle(factory, worker, state, now=1)
    assert len(worker.tasks) == remote.IN_FLIGHT + 1
    flying = [(f.replay_id, f.round_number) for f in state.in_flight.values()]
    assert len(flying) == len(set(flying)) == remote.IN_FLIGHT, "one job per round, whatever its kind"


def test_gap_rows_the_model_refuses_are_dropped_without_stopping_the_cycle(factory, db, linked):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        job["result"]["gaps"]["rows"][0]["no_such_column"] = 1
        return job

    worker, state = FakeWorker(answer, gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["stored"] == counts["gaps_dropped"] == remote.IN_FLIGHT and not gap_runs(db, linked)


def test_the_client_reads_health_through_the_same_error_mapping():
    import io
    import urllib.error

    client = remote.ControlClient("worker:8080")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(remote.urllib.request, "urlopen",
                   lambda request, timeout: io.BytesIO(b'{"control": {"enabled": true, "gaps_protocol": 1}}'))
        assert remote.supports_gaps(client.health())

    def fail(request, timeout):
        raise urllib.error.URLError("down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(remote.urllib.request, "urlopen", fail)
        with pytest.raises(remote.Unreachable):
            client.health()
        assert remote._gaps_capable(client) is False


# ---------------------------------------------------------------- gaps alone, for rounds whose control is current
# (docs/superpowers/plans/2026-10-07-worker-gaps-and-kill-one-impl.md, Task 4)


def put_gap_run(db, replay, n, status="ok", fingerprint=None):
    control = rc.round_fingerprint(replay, rc.side_groups(db, replay), n)
    db.merge(ReplayRoundGapRun(replay_id=replay.id, round_number=n, status=status,
                               fingerprint=fingerprint or replay_gaps.gap_fingerprint(control, replay.map_name),
                               gaps_revision=replay_gaps.GAPS_REVISION,
                               chokes_hash=choke_assets.asset_hash(replay.map_name), gap_count=0,
                               error=None if status == "ok" else "ValueError: boom"))
    db.commit()


def all_control(db, replay, but=()):
    for n in range(1, replay.round_count + 1):
        if n not in but:
            put_row(db, replay, n)


def gaps_only_rounds(worker):
    return sorted(int(t["key"].split(":")[1]) for t in worker.tasks.values() if t.get("gaps_only"))


def control_rounds(worker):
    return sorted(int(t["key"].split(":")[1]) for t in worker.tasks.values() if not t.get("gaps_only"))


def test_rounds_with_current_control_and_no_gaps_are_sent_for_gaps_alone(factory, db, linked):
    all_control(db, linked)
    before = {n: r.computed_at for n, r in rows(db, linked).items()}
    worker, state = FakeWorker(gaps=1), remote.State()
    first = remote.cycle(factory, worker, state, now=0)
    assert first["gaps_sent"] == remote.IN_FLIGHT and first["sent"] == 0
    assert gaps_only_rounds(worker) == list(range(1, remote.IN_FLIGHT + 1)) and not control_rounds(worker)
    [planned] = rc.plan(db, rounds={1}, force=True)
    task = next(t for t in worker.tasks.values() if t["gaps"]["round"] == 1)
    gap_print = replay_gaps.gap_fingerprint(planned.fingerprint, linked.map_name)
    assert set(task) == {"key", "map", "blob", "link", "gaps", "gaps_only"} and task["gaps_only"] is True
    assert task["key"] == f"{linked.id}:1:{planned.fingerprint}:go1:{gap_print}"
    assert task["gaps"]["gap_fingerprint"] == gap_print and task["gaps"]["engine_key"]
    assert all(f.gaps_only and f.expect_gaps for f in state.in_flight.values())
    second = remote.cycle(factory, worker, state, now=1)
    assert second["gaps_stored"] == remote.IN_FLIGHT and second["stored"] == 0
    assert sorted(gap_runs(db, linked)) == list(range(1, remote.IN_FLIGHT + 1))
    assert {n: r.computed_at for n, r in rows(db, linked).items()} == before, "control is not rewritten"
    for t in range(2, 6):
        remote.cycle(factory, worker, state, now=t)
    assert len(gap_runs(db, linked)) == linked.round_count
    assert remote.cycle(factory, worker, state, now=10)["gaps_sent"] == 0 and not state.tries


def test_gaps_under_old_rules_are_sent_again_and_a_current_gap_failure_is_left(factory, db, linked):
    all_control(db, linked)
    for n in range(1, linked.round_count + 1):
        put_gap_run(db, linked, n)
    put_gap_run(db, linked, 2, fingerprint="0" * 16)                 # other gap rules or assets: stale
    put_gap_run(db, linked, 3, status="failed")                      # failed under the current keys
    put_gap_run(db, linked, 4, status="failed", fingerprint="1" * 16)
    worker = FakeWorker(gaps=1)
    remote.cycle(factory, worker, remote.State(), now=0)
    assert gaps_only_rounds(worker) == [2, 4] and not control_rounds(worker)


def test_control_goes_first_and_gaps_fill_what_is_left_of_the_pool(factory, db, linked):
    all_control(db, linked, but=(1, 2, 3))
    worker, state = FakeWorker(gaps=1), remote.State()
    counts = remote.cycle(factory, worker, state, now=0)
    assert counts["sent"] == 3 and counts["gaps_sent"] == remote.IN_FLIGHT - 3
    assert control_rounds(worker) == [1, 2, 3] and gaps_only_rounds(worker) == list(range(4, 4 + remote.IN_FLIGHT - 3))
    order = [bool(t.get("gaps_only")) for t in worker.tasks.values()]
    assert order == sorted(order), "every control task was submitted before the first gaps-only one"
    assert state.last_found is True, "gaps work is left: plan again next cycle"


def test_a_pool_filled_by_control_sends_no_gaps_only_work(factory, db, linked):
    all_control(db, linked, but=range(1, remote.IN_FLIGHT + 2))      # more control work than the pool holds
    worker, state = FakeWorker(gaps=1), remote.State()
    counts = remote.cycle(factory, worker, state, now=0)
    assert counts["sent"] == remote.IN_FLIGHT and counts["gaps_sent"] == 0 and not gaps_only_rounds(worker)


def test_no_gaps_only_work_goes_to_a_worker_without_the_protocol_until_it_has_it(factory, db, linked, monkeypatch):
    all_control(db, linked)
    forced = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: forced.append(kw.get("force", False)) or real(session, **kw))
    worker, state = FakeWorker(), remote.State()
    counts = remote.cycle(factory, worker, state, now=0)
    assert not worker.tasks and counts["gaps_sent"] == 0 and not state.tries
    assert forced == [False], "without the capability the gaps plan is not even made"
    remote.cycle(factory, worker, state, now=remote.CYCLE_S)
    assert forced == [False], "and the idle throttle still holds"
    worker.gaps = 1                                                  # the same worker object, updated in place
    counts = remote.cycle(factory, worker, state, now=remote.PLAN_IDLE_S + 1)     # the same State: no restart
    assert counts["gaps_sent"] == remote.IN_FLIGHT and forced == [False, False, True]
    assert remote.cycle(factory, worker, state, now=remote.PLAN_IDLE_S + 2)["gaps_stored"] == remote.IN_FLIGHT


def test_planning_is_throttled_when_control_and_gaps_are_both_current(factory, db, linked, monkeypatch):
    all_control(db, linked)
    for n in range(1, linked.round_count + 1):
        put_gap_run(db, linked, n)
    calls = []
    real = rc.plan
    monkeypatch.setattr(rc, "plan", lambda session, **kw: calls.append(kw.get("force", False)) or real(session, **kw))
    worker, state = FakeWorker(gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    remote.cycle(factory, worker, state, now=remote.CYCLE_S)
    assert calls == [False, True] and not worker.tasks, "one control plan and one forced plan, then the throttle"
    remote.cycle(factory, worker, state, now=remote.PLAN_IDLE_S + 1)
    assert calls == [False, True, False, True] and worker.health_reads == 2


def test_a_gaps_only_result_that_arrives_after_control_moved_is_skipped(factory, db, linked):
    all_control(db, linked)
    worker, state = FakeWorker(gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    linked.db_deaths = {"1": [{"slot": 0, "t_db": 30.0}]}            # a relink while the worker computes
    db.commit()
    before = rows(db, linked)[1].fingerprint
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts["gaps_skipped"] == 1 and counts["gaps_stored"] == remote.IN_FLIGHT - 1
    assert 1 not in gap_runs(db, linked) and rows(db, linked)[1].fingerprint == before
    assert not state.tries, "a stale result is not a failure"


@pytest.mark.parametrize("change, counted", [
    (lambda r: r.update(revision=cf.CONTROL_REVISION + 1), "dropped_revision"),
    (lambda r: r.update(data_version=cf.DATA_VERSION + 1), "dropped_revision"),
    (lambda r: r.update(geometry={**r["geometry"], "sight": "other"}), "dropped_geometry"),
    (lambda r: r.update(figures="0" * 16), "dropped_figures"),
    (lambda r: r["gaps"]["run"].update(gaps_revision=replay_gaps.GAPS_REVISION + 1), "gaps_dropped"),
    (lambda r: r["gaps"]["run"].update(chokes_hash="another"), "gaps_dropped"),
    (lambda r: r["gaps"]["run"].update(fingerprint="0" * 16), "gaps_dropped"),
    (lambda r: r.pop("gaps"), "gaps_dropped"),
])
def test_a_gaps_only_result_from_another_deploy_is_dropped_and_asked_again(factory, db, linked, change, counted):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        change(job["result"])
        return job

    all_control(db, linked)
    worker, state = FakeWorker(answer, gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=1)
    assert counts[counted] == remote.IN_FLIGHT and counts["gaps_stored"] == 0 and not gap_runs(db, linked)
    assert len(state.tries) == remote.IN_FLIGHT and all(count == 1 for count, _ in state.tries.values())
    for t in range(2, 8):                            # the other rounds go out and are dropped the same way
        remote.cycle(factory, worker, state, now=t)
    assert len(worker.tasks) == linked.round_count, "each asked once: a dropped one waits out the backoff"
    remote.cycle(factory, worker, state, now=8 + remote.BACKOFF_S)
    assert len(worker.tasks) == linked.round_count + remote.IN_FLIGHT, "then it is asked again"


def test_a_detector_failure_on_a_gaps_only_task_is_stored_and_not_asked_again(factory, db, linked):
    def answer(task):
        job = FakeWorker.ok(worker, task)
        job["result"]["gaps"] = json.loads(json.dumps(FakeWorker.gap_result(task, status="failed")))
        return job

    all_control(db, linked)
    worker, state = FakeWorker(answer, gaps=1), remote.State()
    t = 0
    for _ in range(6):
        remote.cycle(factory, worker, state, now=t)
        t += remote.PLAN_IDLE_S + 1
    runs = gap_runs(db, linked)
    assert len(runs) == linked.round_count and all(r.status == "failed" for r in runs.values())
    assert len(worker.tasks) == linked.round_count and not state.tries, "each asked once; the failure is the round's"


def test_a_machine_failure_on_a_gaps_only_task_backs_off_and_stops_after_the_budget(factory, db, linked):
    def answer(task):
        return {"status": "failed", "error_kind": "infra", "error": "MemoryError"}

    all_control(db, linked, but=range(2, linked.round_count + 1))    # one round with control: one gaps-only task
    put_row(db, linked, 1)
    for n in range(2, linked.round_count + 1):
        put_row(db, linked, n, status="failed")                      # the rest failed control: no gaps for them
    worker, state = FakeWorker(answer, gaps=1), remote.State()
    t = 0
    for _ in range(2 * remote.MAX_TRIES + 2):
        remote.cycle(factory, worker, state, now=t)
        t += remote.BACKOFF_S + 1
    assert gaps_only_rounds(worker) == [1] * remote.MAX_TRIES and not gap_runs(db, linked)
    [(key, (count, _))] = state.tries.items()
    assert count == remote.MAX_TRIES and ":go1:" in key
    assert rows(db, linked)[1].status == "ok", "control is untouched"


def test_a_gaps_only_job_waits_and_times_out_like_any_other(factory, db, linked):
    phase = {"status": "queued"}
    all_control(db, linked)
    worker, state = FakeWorker(answer=lambda task: {"status": phase["status"]}, gaps=1), remote.State()
    remote.cycle(factory, worker, state, now=0)
    remote.cycle(factory, worker, state, now=5 * remote.STALE_JOB_S)
    assert len(state.in_flight) == remote.IN_FLIGHT and not state.tries, "queued behind parses never counts (D8)"
    phase["status"] = "running"
    remote.cycle(factory, worker, state, now=5 * remote.STALE_JOB_S + 1)
    counts = remote.cycle(factory, worker, state, now=6 * remote.STALE_JOB_S + 2)
    assert counts["timed_out"] == remote.IN_FLIGHT
