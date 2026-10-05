"""The web app's map-control dispatcher for the replay worker (app/services/replay_control_remote.py;
docs/map-control-worker-plan.md, step 4), on SQLite with a fake worker client."""

import base64
import gzip
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from test_control_store import db, factory, linked  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed, pg  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
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


def test_unlinked_replays_wait_for_their_link(factory, db, linked):
    linked.link_status = "unlinked"
    db.commit()
    worker = FakeWorker()
    assert remote.cycle(factory, worker, remote.State(), now=0)["sent"] == 0 and not worker.tasks


def test_stale_rounds_stay_for_the_local_command(factory, db, linked):
    [planned] = rc.plan(db, rounds={1})
    db.add(ReplayRoundControl(replay_id=linked.id, round_number=1, status="ok", fingerprint="0" * 16,
                              data_version=cf.DATA_VERSION, data=gzip.compress(b"x"), summary=cf.pack_summary({})))
    db.commit()
    worker = FakeWorker()
    remote.cycle(factory, worker, remote.State(), now=0)
    assert all(int(t["key"].split(":")[1]) != 1 for t in worker.tasks.values())


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


def test_a_job_out_too_long_is_given_up_on(factory, db, linked):
    worker, state = FakeWorker(answer=lambda task: None), remote.State()
    remote.cycle(factory, worker, state, now=0)
    counts = remote.cycle(factory, worker, state, now=remote.STALE_JOB_S + 1)
    assert counts["timed_out"] == remote.IN_FLIGHT


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
