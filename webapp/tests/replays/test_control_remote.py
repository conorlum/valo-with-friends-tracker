"""The web app's map-control dispatcher for the replay worker (app/services/replay_control_remote.py;
docs/map-control-worker-plan.md, step 4), on SQLite with a fake worker client."""

import base64
import gzip
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
from app.models.replay import ReplayRoundControl  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_control_remote as remote  # noqa: E402


class FakeWorker:
    """Accepts tasks and answers jobs; `answer(task)` makes a job's final state (None: still running)."""

    def __init__(self, answer=None, busy_after=None):
        self.tasks, self.jobs, self.busy_after = {}, {}, busy_after
        self.answer = answer or self.ok
        self.forget = set()

    def ok(self, task):
        return {"status": "done", "result": {"status": "ok", "data": base64.b64encode(gzip.compress(b"d")).decode(),
                                             "summary": base64.b64encode(cf.pack_summary({})).decode(),
                                             "revision": cf.CONTROL_REVISION, "data_version": cf.DATA_VERSION,
                                             "geometry": rc.geometry_inputs(task["map"])}}

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
