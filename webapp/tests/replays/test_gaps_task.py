"""Gaps in the control task and the local command (timing-gaps spec, section 7, "Freshness" and "Writer";
revisions R1, R5, R6, R17-R20 of the engine-and-detector plan)."""

import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from control_toys import blob  # noqa: E402
from test_control_store import db, factory, linked, put_row  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed  # noqa: E402,F401  (fixtures)

import compute_control  # noqa: E402
from app.config import settings  # noqa: E402
from app.control.task import _plain, compute_task  # noqa: E402
from app.models.replay import ReplayGap, ReplayRoundControl, ReplayRoundGapRun  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_gaps  # noqa: E402
from app.services.replay_gaps_store import store_gaps  # noqa: E402

# Two walkable Ascent points about 99 m apart (checked: geo.walk[104, 36] and geo.walk[16, 44]; the brief's
# (150, 200) is cell [25, 18], which is not walkable).
# 40 s gives the stationary pair a few predicted gaps (each enemy's unknown reaches the other's sight late).
A_SPOT, B_SPOT = (292, 836), (356, 132)


def _task(tmp_path, monkeypatch, **extra):
    from app.gaps import cache
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path)
    data = blob({0: ("A", [(0.0, *A_SPOT, 180)]), 5: ("B", [(0.0, *B_SPOT, 180)])}, t_end=40.0)
    raw = gzip.compress(json.dumps(data).encode("utf-8"))
    return {"key": 0, "map": "Ascent", "blob": raw, "link": {"sides": {}, "db_deaths": []},
            "gaps": {"replay_id": 1, "round": 1, "fingerprint": "c" * 16}, **extra}


def _engine_calls(monkeypatch) -> list:
    import app.control.engine as ce
    real, calls = ce.compute_round, []

    def counted(*a, **k):
        calls.append(k)
        return real(*a, **k)

    monkeypatch.setattr(ce, "compute_round", counted)
    return calls


# ---------------------------------------------------------------- the task


def test_a_detector_failure_leaves_control_ok(tmp_path, monkeypatch):
    from app.gaps import detect
    monkeypatch.setattr(detect.GapDetector, "finish", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    result = compute_task(_task(tmp_path, monkeypatch))
    assert result["status"] == "ok" and result["data"]
    assert result["gaps"]["run"]["status"] == "failed" and "boom" in result["gaps"]["run"]["error"]
    assert result["gaps"]["rows"] == []


def test_gaps_only_uses_the_cache_written_by_a_full_run(tmp_path, monkeypatch):
    first = compute_task(_task(tmp_path, monkeypatch))
    assert first["gaps"]["run"]["status"] == "ok" and first["gaps"]["rows"], "the toy has gaps"
    assert json.dumps(first["gaps"]), "rows are JSON-ready"
    assert list(tmp_path.glob("*.ticks.pkl.gz"))
    import app.control.engine as ce
    monkeypatch.setattr(ce, "compute_round", lambda *a, **k: (_ for _ in ()).throw(AssertionError("engine ran")))
    again = compute_task(_task(tmp_path, monkeypatch, gaps_only=True))
    assert again["gaps"]["run"]["status"] == "ok"
    assert again["gaps"]["rows"] == first["gaps"]["rows"] and "data" not in again
    assert again["gaps"]["run"] == first["gaps"]["run"]


def test_gaps_only_without_a_cache_runs_the_engine_and_matches_the_full_run(tmp_path, monkeypatch):
    # R20: the live path's notes (the engine's missing inputs) equal the cache path's (stored with the file)
    first = compute_task(_task(tmp_path, monkeypatch))
    for path in tmp_path.glob("*.ticks.pkl.gz"):
        path.unlink()
    calls = _engine_calls(monkeypatch)
    live = compute_task(_task(tmp_path, monkeypatch, gaps_only=True))
    assert len(calls) == 1 and calls[0]["knowledge"] is False
    assert live["gaps"]["run"]["status"] == "ok" and "data" not in live
    assert live["gaps"]["rows"] == first["gaps"]["rows"]
    assert live["gaps"]["run"]["notes"] == first["gaps"]["run"]["notes"]
    assert list(tmp_path.glob("*.ticks.pkl.gz")), "the engine run writes the cache for next time"
    cached = compute_task(_task(tmp_path, monkeypatch, gaps_only=True))
    assert len(calls) == 1 and cached["gaps"]["run"]["notes"] == live["gaps"]["run"]["notes"]


def test_the_notes_carry_the_compute_time_missing_inputs(tmp_path, monkeypatch):
    from app.gaps import cache
    first = compute_task(_task(tmp_path, monkeypatch))
    [path] = tmp_path.glob("*.ticks.pkl.gz")
    assert cache.read_missing(path) == first["missing"]
    assert first["missing"].items() <= first["gaps"]["run"]["notes"].items()


def test_a_cache_in_another_format_is_a_miss(tmp_path, monkeypatch):
    from app.gaps import cache
    compute_task(_task(tmp_path, monkeypatch))
    monkeypatch.setattr(cache, "FORMAT", cache.FORMAT + 1)        # the file on disk is now the old format
    calls = _engine_calls(monkeypatch)
    again = compute_task(_task(tmp_path, monkeypatch, gaps_only=True))
    assert again["gaps"]["run"]["status"] == "ok" and len(calls) == 1


def test_an_observer_exception_leaves_control_ok_and_the_gap_run_failed(tmp_path, monkeypatch):
    # R18: the writer stops, its partial file is deleted, and the failure is the gap run's
    from app.gaps import cache
    seen = []

    def broken(self, rec, unknown):
        seen.append(rec.t)
        if len(seen) == 2:
            raise OSError("disk full")
        self.ticks.append(None)

    monkeypatch.setattr(cache.Writer, "__call__", broken)
    result = compute_task(_task(tmp_path, monkeypatch))
    assert result["status"] == "ok" and result["data"]
    assert result["gaps"]["run"]["status"] == "failed" and "disk full" in result["gaps"]["run"]["error"]
    assert len(seen) == 2, "no further cache writes after the failure"
    assert not list(tmp_path.glob("*")), "no file, partial or whole"


def test_a_close_exception_leaves_control_ok_and_the_gap_run_failed(tmp_path, monkeypatch):
    from app.gaps import cache
    real_close = cache.Writer.close

    def broken(self, missing=None):
        real_close(self, missing)
        raise OSError("rename failed")

    monkeypatch.setattr(cache.Writer, "close", broken)
    result = compute_task(_task(tmp_path, monkeypatch))
    assert result["status"] == "ok" and result["data"]
    assert result["gaps"]["run"]["status"] == "failed" and "rename failed" in result["gaps"]["run"]["error"]
    assert not list(tmp_path.glob("*")), "the file it wrote is deleted"


def test_a_task_without_gaps_is_unchanged(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch)
    del task["gaps"]
    result = compute_task(task)
    assert result["status"] == "ok" and "gaps" not in result
    assert not list(tmp_path.glob("*")), "no tick cache without a gap job"


def test_a_choke_edit_misses_the_old_cache(tmp_path, monkeypatch):
    # R5: the cache is keyed by the engine key, so a gaps-only run after a choke edit goes through the engine
    from app.replays import choke_assets
    first = compute_task(_task(tmp_path, monkeypatch))
    [old] = tmp_path.glob("*.ticks.pkl.gz")
    real = choke_assets.asset_hash
    monkeypatch.setattr(choke_assets, "asset_hash", lambda name, *a, **k: "e" * 16 if name == "Ascent" else real(name))
    calls = _engine_calls(monkeypatch)
    again = compute_task(_task(tmp_path, monkeypatch, gaps_only=True))
    assert len(calls) == 1, "the old file was not read"
    assert again["gaps"]["run"]["fingerprint"] != first["gaps"]["run"]["fingerprint"]
    assert again["gaps"]["run"]["chokes_hash"] == "e" * 16
    assert len(list(tmp_path.glob("*.ticks.pkl.gz"))) == 2 and old.exists()


def test_cache_keys_differ_by_round_and_replay(tmp_path):
    from app.gaps import cache
    key = replay_gaps.engine_key("c" * 16, "Ascent")
    paths = {cache.cache_path(r, n, key, tmp_path) for r, n in ((1, 1), (1, 2), (2, 1))}
    assert len(paths) == 3


def test_rows_and_notes_are_plain_python():
    value = {"a": np.float32(1.5), np.int64(3): [np.int32(2), (np.float64(0.25), None)], "m": np.array([1, 2])}
    out = _plain(value)
    assert out == {"a": 1.5, 3: [2, [0.25, None]], "m": [1, 2]}
    assert json.dumps({str(k): v for k, v in out.items()})
    assert all(type(x) is int for x in out["m"]) and type(out["a"]) is float


# ---------------------------------------------------------------- fingerprints


def test_the_gaps_revision_is_one_number():
    from app.gaps import detect
    assert detect.GAPS_REVISION == replay_gaps.GAPS_REVISION


def test_the_gap_fingerprint_moves_with_each_input(monkeypatch, tmp_path):
    from app.replays import choke_assets
    base = replay_gaps.gap_fingerprint("c" * 16, "Ascent")
    assert len(base) == 16 and base == replay_gaps.gap_fingerprint("c" * 16, "Ascent")
    assert replay_gaps.gap_fingerprint("d" * 16, "Ascent") != base
    monkeypatch.setattr(replay_gaps, "GAPS_REVISION", replay_gaps.GAPS_REVISION + 1)
    assert replay_gaps.gap_fingerprint("c" * 16, "Ascent") != base
    monkeypatch.undo()
    monkeypatch.setattr(choke_assets, "asset_hash", lambda name, *a, **k: "f" * 16)
    assert replay_gaps.gap_fingerprint("c" * 16, "Ascent") != base


def test_a_hearing_only_edit_changes_the_gap_fingerprint_and_the_engine_key(monkeypatch, tmp_path):
    # R6: the hearing table is read as a file; an edit with no revision change still makes the round stale
    fp, key = replay_gaps.gap_fingerprint("c" * 16, "Ascent"), replay_gaps.engine_key("c" * 16, "Ascent")
    assert replay_gaps.hearing_hash() == __import__("hashlib").sha256(
        replay_gaps.HEARING_FILE.read_bytes()).hexdigest()[:16]
    edited = tmp_path / "hearing.json"
    edited.write_bytes(replay_gaps.HEARING_FILE.read_bytes() + b"\n")
    monkeypatch.setattr(replay_gaps, "HEARING_FILE", edited)
    assert replay_gaps.gap_fingerprint("c" * 16, "Ascent") != fp
    assert replay_gaps.engine_key("c" * 16, "Ascent") != key


# ---------------------------------------------------------------- the plan (R17)


def _gap_run(factory, replay, n, status="ok", fingerprint=None):
    session = factory()
    try:
        [p] = rc.plan(session, rounds={n}, force=True)
    finally:
        session.close()
    run = {"status": status, "fingerprint": fingerprint or replay_gaps.gap_fingerprint(p.fingerprint, p.map_name),
           "gaps_revision": replay_gaps.GAPS_REVISION, "chokes_hash": None, "notes": {},
           "error": None if status == "ok" else "RuntimeError: boom"}
    assert store_gaps(factory, replay.id, n, run, []) == "stored"


def _plan_gaps(db, retry_failed=False):
    return replay_gaps.plan_gaps(db, rc.plan(db, retry_failed=retry_failed), rc.plan(db, force=True),
                                 retry_failed=retry_failed)


def test_the_plan_takes_fresh_ok_control_without_current_gaps(factory, db, linked):
    n_rounds = linked.round_count
    assert _plan_gaps(db) == [], "no control yet: control's own plan computes the gaps with it"
    for n in range(1, n_rounds + 1):
        put_row(db, linked, n)
    planned = _plan_gaps(db)
    assert [(p.round_number, p.reason) for p in planned] == [(n, "gaps") for n in range(1, n_rounds + 1)]
    assert planned[0].fingerprint == rc.plan(db, rounds={1}, force=True)[0].fingerprint
    assert planned[0].link == rc.plan(db, rounds={1}, force=True)[0].link
    _gap_run(factory, linked, 1)
    _gap_run(factory, linked, 2, fingerprint="0" * 16)                 # stale: new rules, chokes or hearing
    assert [p.round_number for p in _plan_gaps(db)] == list(range(2, n_rounds + 1))


def test_a_round_whose_control_is_stale_or_failed_is_not_gaps_only(factory, db, linked):
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    put_row(db, linked, 2, fingerprint="0" * 16)          # stale control: control's plan takes it, with gaps
    put_row(db, linked, 3, status="failed")               # current failed control: no gaps from it
    rounds = {p.round_number for p in _plan_gaps(db)}
    assert 2 not in rounds and 3 not in rounds and 1 in rounds
    assert 3 not in {p.round_number for p in _plan_gaps(db, retry_failed=True)}, "control retries it, with gaps"


def test_a_current_failed_gap_run_waits_for_retry_failed(factory, db, linked):
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    _gap_run(factory, linked, 1, status="failed")
    assert 1 not in {p.round_number for p in _plan_gaps(db)}
    assert 1 in {p.round_number for p in _plan_gaps(db, retry_failed=True)}
    _gap_run(factory, linked, 1, status="failed", fingerprint="0" * 16)
    assert 1 in {p.round_number for p in _plan_gaps(db)}, "a failure with old inputs is retried"


def test_dry_run_lists_gaps_rounds(factory, db, linked, capsys):
    for n in range(1, linked.round_count + 1):
        put_row(db, linked, n)
    assert compute_control.main(["--dry-run", "--round", "1"], session_factory=factory) == 0
    out = capsys.readouterr().out
    assert out.startswith("1 round(s) to compute (1 gaps)") and " r1 " in out and ": gaps" in out


# ---------------------------------------------------------------- the command (R19)


class _Done:
    def __init__(self, value):
        self.value = value

    def ready(self):
        return True

    def get(self):
        return self.value


class _Pool:
    def __init__(self, processes):
        pass

    def apply_async(self, fn, args):
        return _Done(fn(*args))

    def close(self):
        pass

    def join(self):
        pass

    def terminate(self):
        pass


def _command(monkeypatch, gaps_result):
    """compute_control.run with a synchronous pool and a fake task (no engine)."""
    import multiprocessing

    from app.control import geometry

    monkeypatch.setattr(multiprocessing, "get_context", lambda kind: SimpleNamespace(Pool=_Pool))
    monkeypatch.setattr(geometry, "load_geometry", lambda name: name)
    monkeypatch.setattr(geometry, "visibility", lambda geo: SimpleNamespace(visibility_source="fake"))
    monkeypatch.setattr(compute_control, "POLL_S", 0)
    tasks = []

    def fake(task):
        tasks.append(task)
        job = task["gaps"]
        run = {"fingerprint": replay_gaps.gap_fingerprint(job["fingerprint"], task["map"]),
               "gaps_revision": replay_gaps.GAPS_REVISION, "chokes_hash": None, **gaps_result}
        result = {"status": "ok", "geometry": {}, "gaps": {"run": run, "rows": []}, "key": task["key"],
                  "seconds": 0.0, "peak": None}
        if not task["gaps_only"]:
            result.update({"data": b"d", "summary": b"s", "missing": {}})
        return result

    monkeypatch.setattr(compute_control, "compute_task", fake)
    return tasks


def test_a_failed_gap_run_makes_the_command_fail(factory, db, linked, monkeypatch, capsys):
    put_row(db, linked, 1)
    tasks = _command(monkeypatch, {"status": "failed", "notes": {}, "error": "RuntimeError: boom"})
    assert compute_control.main(["--round", "1"], session_factory=factory) == 1
    assert [t["gaps_only"] for t in tasks] == [True]
    out = capsys.readouterr().out
    assert "gaps FAILED: RuntimeError: boom" in out and "1 failed" in out
    db.expire_all()
    assert db.get(ReplayRoundGapRun, (linked.id, 1)).status == "failed"
    assert db.get(ReplayRoundControl, (linked.id, 1)).status == "ok", "control untouched"
    assert compute_control.main(["--round", "1"], session_factory=factory) == 0, "skipped until --retry-failed"
    assert len(tasks) == 1


def test_an_ok_gap_run_is_stored_without_rewriting_control(factory, db, linked, monkeypatch, capsys):
    put_row(db, linked, 1, data=b"control bytes")
    before = db.get(ReplayRoundControl, (linked.id, 1))
    computed_at, data = before.computed_at, before.data
    _command(monkeypatch, {"status": "ok", "notes": {"x": 1}, "error": None})
    assert compute_control.main(["--round", "1"], session_factory=factory) == 0
    assert "gaps 0" in capsys.readouterr().out
    db.expire_all()
    run = db.get(ReplayRoundGapRun, (linked.id, 1))
    assert run.status == "ok" and run.gap_count == 0 and run.notes == {"x": 1}
    after = db.get(ReplayRoundControl, (linked.id, 1))
    assert after.data == data and after.computed_at == computed_at
    assert db.query(ReplayGap).count() == 0
    assert _plan_gaps(db) == [p for p in _plan_gaps(db) if p.round_number != 1]


def test_an_unstored_gap_run_makes_the_command_fail(factory, db, linked, monkeypatch, capsys):
    import app.services.replay_gaps_store as gs
    put_row(db, linked, 1)
    _command(monkeypatch, {"status": "ok", "notes": {}, "error": None})
    monkeypatch.setattr(gs, "store_gaps", lambda *a, **k: "skipped: the replay changed while computing")
    assert compute_control.main(["--round", "1"], session_factory=factory) == 1
    assert "GAPS NOT STORED" in capsys.readouterr().out


def test_the_demo_database_gets_no_gaps(factory, db, linked, monkeypatch):
    put_row(db, linked, 1)
    tasks = _command(monkeypatch, {"status": "ok", "notes": {}, "error": None})
    monkeypatch.setattr(settings, "demo_mode", True)
    assert compute_control.main(["--round", "1"], session_factory=factory) == 3
    assert tasks == [] and db.query(ReplayRoundGapRun).count() == 0


def test_a_full_round_carries_its_gap_job_and_stores_both(factory, db, linked, monkeypatch, capsys):
    tasks = _command(monkeypatch, {"status": "ok", "notes": {}, "error": None})
    [p] = rc.plan(db, rounds={1})
    assert p.reason == "missing"
    assert compute_control.main(["--round", "1"], session_factory=factory) == 0
    [task] = tasks
    assert task["gaps_only"] is False
    assert task["gaps"] == {"replay_id": linked.id, "round": 1, "fingerprint": p.fingerprint}
    assert "gaps 0" in capsys.readouterr().out
    db.expire_all()
    assert db.get(ReplayRoundControl, (linked.id, 1)).status == "ok"
    assert db.get(ReplayRoundGapRun, (linked.id, 1)).status == "ok"
    assert _plan_gaps(db) == [] or 1 not in {q.round_number for q in _plan_gaps(db)}
