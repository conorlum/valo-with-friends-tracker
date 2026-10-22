"""Timing gaps' storage (timing-gaps spec, section 7): a round's run row and gap rows are replaced together.
SQLite, no engine."""

import json
import sys
import threading
from dataclasses import replace as dc_replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from test_control_store import put_row, feature_free_source  # noqa: E402
from test_replay_store import TABLES, add_match, condensed, pg  # noqa: E402,F401  (fixtures)

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.gaps.rows import to_rows  # noqa: E402
from app.models.replay import Replay, ReplayGap, ReplayRoundControl, ReplayRoundGapRun, ReplayRoundMoments, ReplayRoundUtil  # noqa: E402
from app.replays import choke_assets  # noqa: E402
from app.replays import control_format as cf  # noqa: E402
from app.replays import store  # noqa: E402
from app.services import replay_control as rc  # noqa: E402
from app.services import replay_gaps  # noqa: E402
from app.services import replay_gaps_store as gaps_store  # noqa: E402
from app.services.replay_gaps_store import store_gaps  # noqa: E402


@pytest.fixture
def session_factory(monkeypatch):
    feature_free_source(monkeypatch)
    monkeypatch.setattr(settings, "demo_mode", False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES + [ReplayRoundGapRun.__table__, ReplayGap.__table__,
                                                   ReplayRoundMoments.__table__, ReplayRoundUtil.__table__])
    return sessionmaker(bind=engine)


@pytest.fixture
def stored_round(session_factory, condensed):
    db = session_factory()
    add_match(db)
    replay_id = store.store_replay(db, condensed, source="local").replay_id
    db.close()
    return replay_id, 1


MOMENTS = {"status": "ok", "v": 1, "sight": True, "fights": [], "error": None}
RUN = {"status": "ok", "fingerprint": "f" * 16, "gaps_revision": 1, "chokes_hash": None, "notes": {}, "error": None}
ROW = {"seq": 0, "kind": "predicted", "map": "Toy", "victim_slot": 0, "victim_side": None, "t_open": 1.0,
       "t_last_exposed": 2.0, "t_close": 7.0, "spot_cell": 5, "victim_cell": 6, "distance_m": 3.0,
       "angle_deg": 170.0, "qualified_s": 2.0, "flicker": False, "cause": "open_timing", "cause_detail": {},
       "choke_seq": [], "route": [[[1.0, 1.0, 1.0]]], "candidate_slots": [5], "candidate_distances": {"5": 3.0},
       "checked_at": None, "stood_at": None, "stood_by": None, "shot_at": None, "shot_by": None,
       "killed_at": None, "killed_by": None, "victim_won_at": None, "context": {}, "linked_seq": None}


def test_store_replaces_a_rounds_gaps_in_one_go(session_factory, stored_round):
    replay_id, n = stored_round
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW, {**ROW, "seq": 1}], moments=MOMENTS) == "stored"
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=MOMENTS) == "stored"
    s = session_factory()
    assert s.query(ReplayGap).filter_by(replay_id=replay_id, round_number=n).count() == 1
    assert s.get(ReplayRoundGapRun, (replay_id, n)).gap_count == 1


def test_a_failed_run_stores_no_rows(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=MOMENTS)
    assert store_gaps(session_factory, replay_id, n, {**RUN, "status": "failed", "error": "boom"}, [], moments=MOMENTS) == "stored"
    s = session_factory()
    assert s.get(ReplayRoundGapRun, (replay_id, n)).status == "failed"
    assert s.query(ReplayGap).count() == 0


def _gap(**kw):
    base = dict(kind="predicted", victim=3, team="A", choke_seq=(2, 4), t_open=1.0, spot=7, victim_node=8,
                distance_m=4.0, angle_deg=150.0, route=[[[1.0, 2.0, 3.0]]], cause="open_timing", cause_detail={},
                candidates={5: 4.0, 2: 6.5}, t_last_exposed=2.0, t_close=7.0, qualified_s=2.00049, flicker=False,
                checked_at=[], stood_at=None, stood_by=None, shot_at=None, shot_by=None, killed_at=None,
                killed_by=None, victim_won_at=None, context={}, linked=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_to_rows_assigns_seq_links_and_json_ready_fields(session_factory, stored_round):
    replay_id, n = stored_round
    back = _gap(kind="backshot", qualified_s=0.0, choke_seq=None)
    pred = _gap(linked=back)
    back.linked = pred
    rnd = SimpleNamespace(group_side={"A": "attack"})
    geo = SimpleNamespace(name="Toy", heights=None, node_cell=np.arange(20))
    rows = to_rows([pred, back], rnd, geo)
    assert [r["seq"] for r in rows] == [0, 1]
    assert rows[0]["linked_seq"] == 1 and rows[1]["linked_seq"] == 0
    assert rows[0]["victim_side"] == "attack" and rows[0]["map"] == "Toy"
    assert rows[0]["qualified_s"] == 2.0 and rows[1]["qualified_s"] is None
    assert rows[0]["choke_seq"] == [2, 4] and rows[1]["choke_seq"] is None
    assert rows[0]["candidate_slots"] == [2, 5] and rows[0]["candidate_distances"] == {"5": 4.0, "2": 6.5}
    assert rows[0]["checked_at"] is None and rows[0]["spot_cell"] == 7
    assert store_gaps(session_factory, replay_id, n, RUN, rows, moments=MOMENTS) == "stored"
    s = session_factory()
    assert s.query(ReplayGap).count() == 2


# ---------------------------------------------------------------- the guarded writer (the replay worker's results)
# (docs/superpowers/plans/2026-10-07-worker-gaps-and-kill-one-impl.md, Task 3)


def _current(session, replay_id, n, status="ok", **control):
    """Puts round n's control row under its current fingerprint and returns that fingerprint and a gap run the
    site would accept for it."""
    replay = session.get(Replay, replay_id)
    put_row(session, replay, n, status=status, **control)
    fingerprint = rc.round_fingerprint(replay, rc.side_groups(session, replay), n)
    run = {**RUN, "fingerprint": replay_gaps.gap_fingerprint(fingerprint, replay.map_name),
           "gaps_revision": replay_gaps.GAPS_REVISION, "chokes_hash": choke_assets.asset_hash(replay.map_name)}
    return fingerprint, run


def _stored(session_factory, replay_id, n):
    s = session_factory()
    try:
        run = s.get(ReplayRoundGapRun, (replay_id, n))
        rows = s.query(ReplayGap).filter_by(replay_id=replay_id, round_number=n).count()
        return (None if run is None else (run.status, run.fingerprint)), rows
    finally:
        s.close()


def test_a_guarded_write_stores_rows_that_came_through_json(session_factory, stored_round):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    s.close()
    rows = json.loads(json.dumps([dict(ROW, cause_detail={1: 2.5}, context={3: [1, 2]}), {**ROW, "seq": 1}]))
    run = json.loads(json.dumps({**run, "notes": {7: 1}}))
    assert store_gaps(session_factory, replay_id, n, run, rows, moments=MOMENTS, expected_control_fingerprint=fingerprint) == "stored"
    assert _stored(session_factory, replay_id, n) == (("ok", run["fingerprint"]), 2)


def test_a_guarded_write_stores_the_rounds_own_gap_failure(session_factory, stored_round):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    s.close()
    failed = {**run, "status": "failed", "error": "ValueError: boom"}
    assert store_gaps(session_factory, replay_id, n, failed, [], moments=MOMENTS, expected_control_fingerprint=fingerprint) == "stored"
    assert _stored(session_factory, replay_id, n) == (("failed", run["fingerprint"]), 0)


def test_a_result_whose_control_moved_is_skipped_and_newer_gaps_survive(session_factory, stored_round):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    # the link moves after the result was validated: new deaths, so a new control fingerprint
    s.get(Replay, replay_id).db_deaths = {str(n): [{"slot": 0, "t_db": 30.0}]}
    s.commit()
    newer, newer_run = _current(s, replay_id, n)
    s.close()
    assert newer != fingerprint
    assert store_gaps(session_factory, replay_id, n, newer_run, [ROW, {**ROW, "seq": 1}], moments=MOMENTS) == "stored"   # the local command
    outcome = store_gaps(session_factory, replay_id, n, run, [ROW], moments=MOMENTS, expected_control_fingerprint=fingerprint)
    assert outcome.startswith("skipped") and "changed" in outcome
    assert _stored(session_factory, replay_id, n) == (("ok", newer_run["fingerprint"]), 2)


def test_a_result_for_a_replay_that_is_gone_is_skipped(session_factory, stored_round):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    store._delete(s, s.get(Replay, replay_id))                  # a re-ingest or a deletion took the replay
    s.commit()
    s.close()
    outcome = store_gaps(session_factory, replay_id, n, run, [ROW], moments=MOMENTS, expected_control_fingerprint=fingerprint)
    assert outcome.startswith("skipped") and _stored(session_factory, replay_id, n) == (None, 0)


@pytest.mark.parametrize("control", ["missing", "failed", "old_data_version", "old_fingerprint"])
def test_a_guarded_write_needs_current_ok_control(session_factory, stored_round, control):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    row = s.get(ReplayRoundControl, (replay_id, n))
    if control == "missing":
        s.delete(row)
    elif control == "failed":
        row.status, row.data, row.summary, row.data_version = "failed", None, None, None
    elif control == "old_data_version":
        row.data_version = cf.DATA_VERSION - 1
    else:
        row.fingerprint = "0" * 16
    s.commit()
    s.close()
    outcome = store_gaps(session_factory, replay_id, n, run, [ROW], moments=MOMENTS, expected_control_fingerprint=fingerprint)
    assert outcome.startswith("skipped") and _stored(session_factory, replay_id, n) == (None, 0)


@pytest.mark.parametrize("field, value", [("fingerprint", "0" * 16), ("gaps_revision", -1), ("chokes_hash", "other")])
def test_a_guarded_write_checks_the_runs_own_keys(session_factory, stored_round, field, value):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    s.close()
    outcome = store_gaps(session_factory, replay_id, n, {**run, field: value}, [ROW],
                         moments=MOMENTS, expected_control_fingerprint=fingerprint)
    assert outcome.startswith("skipped") and _stored(session_factory, replay_id, n) == (None, 0)


@pytest.mark.parametrize("status", ["ok", "failed"])
def test_a_guarded_write_never_replaces_a_run_already_current(session_factory, stored_round, status):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    s.close()
    first = {**run, "status": status, "error": None if status == "ok" else "ValueError: boom"}
    assert store_gaps(session_factory, replay_id, n, first, [ROW] if status == "ok" else [], moments=MOMENTS) == "stored"
    outcome = store_gaps(session_factory, replay_id, n, run, [ROW, {**ROW, "seq": 1}],
                         moments=MOMENTS, expected_control_fingerprint=fingerprint)
    assert outcome == "skipped: already stored"
    assert _stored(session_factory, replay_id, n) == ((status, run["fingerprint"]), 1 if status == "ok" else 0)


def test_a_failed_guarded_write_leaves_the_old_rows(session_factory, stored_round):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    s.close()
    stale = {**run, "fingerprint": "1" * 16}
    assert store_gaps(session_factory, replay_id, n, stale, [ROW], moments=MOMENTS) == "stored"          # an older run's rows
    with pytest.raises(TypeError):                                                      # a row the model refuses
        store_gaps(session_factory, replay_id, n, run, [{**ROW, "no_such_column": 1}],
                   moments=MOMENTS, expected_control_fingerprint=fingerprint)
    assert _stored(session_factory, replay_id, n) == (("ok", "1" * 16), 1), "the delete rolled back with the rest"


# ---- Critical Util (migration 0029)


def _util_row(session_factory, replay_id, n):
    s = session_factory()
    try:
        row = s.get(ReplayRoundUtil, (replay_id, n))
        return None if row is None else (row.status, row.fingerprint, row.gaps_revision, row.data, row.error)
    finally:
        s.close()


def test_a_store_writes_the_rounds_util_with_the_run(session_factory, stored_round):
    replay_id, n = stored_round
    util = {"status": "ok", "data": {"v": 1, "casts": [{"i": 0}]}, "error": None}
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=MOMENTS, util=util) == "stored"
    assert _util_row(session_factory, replay_id, n) == ("ok", RUN["fingerprint"], RUN["gaps_revision"],
                                                        {"v": 1, "casts": [{"i": 0}]}, None)


def test_a_failed_util_keeps_no_data_and_no_util_leaves_the_row(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=MOMENTS,
               util={"status": "failed", "data": {"x": 1}, "error": "ValueError: no"})
    assert _util_row(session_factory, replay_id, n)[0::3] == ("failed", None)
    store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=MOMENTS)
    assert _util_row(session_factory, replay_id, n)[0] == "failed", "a caller with no util leaves the row"


# ---- critical moments (migration 0026)


def _moments_row(session_factory, replay_id, n):
    s = session_factory()                                       # a fresh session, not the writer's
    try:
        row = s.get(ReplayRoundMoments, (replay_id, n))
        return None if row is None else (row.status, row.fingerprint, row.gaps_revision, row.fights, row.error,
                                         row.computed_at)
    finally:
        s.close()


def test_a_store_writes_the_rounds_moments(session_factory, stored_round):
    replay_id, n = stored_round
    moments = {**MOMENTS, "v": 3, "sight": False, "fights": [{"t": 1.5}]}
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=moments) == "stored"
    status, fingerprint, revision, fights, error, computed_at = _moments_row(session_factory, replay_id, n)
    assert (status, fingerprint, revision, error) == ("ok", RUN["fingerprint"], RUN["gaps_revision"], None)
    assert fights == {"v": 3, "sight": False, "fights": [{"t": 1.5}]}
    assert computed_at is not None


def test_failed_moments_store_no_fights_and_the_error(session_factory, stored_round):
    replay_id, n = stored_round
    moments = {"status": "failed", "v": 1, "sight": True, "fights": [], "error": "ValueError: boom"}
    assert store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=moments) == "stored"
    status, _, _, fights, error, _ = _moments_row(session_factory, replay_id, n)
    assert (status, fights, error) == ("failed", None, "ValueError: boom")


def test_a_second_store_replaces_the_moments_row(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, RUN, [ROW], moments=MOMENTS)
    second = {**MOMENTS, "fights": [{"t": 9.0}]}
    store_gaps(session_factory, replay_id, n, {**RUN, "fingerprint": "e" * 16}, [ROW], moments=second)
    s = session_factory()
    rows = s.query(ReplayRoundMoments).filter(ReplayRoundMoments.replay_id == replay_id).all()
    assert len(rows) == 1 and rows[0].fingerprint == "e" * 16 and rows[0].fights["fights"] == [{"t": 9.0}]
    s.close()


def test_a_skipped_store_leaves_the_moments_alone(session_factory, stored_round):
    replay_id, n = stored_round
    s = session_factory()
    fingerprint, run = _current(s, replay_id, n)
    s.close()
    skipped = store_gaps(session_factory, replay_id, n, {**run, "fingerprint": "0" * 16}, [ROW],
                         moments=MOMENTS, expected_control_fingerprint=fingerprint)
    assert skipped.startswith("skipped") and _moments_row(session_factory, replay_id, n) is None
    assert store_gaps(session_factory, replay_id, n, run, [ROW], moments=MOMENTS) == "stored"
    before = _moments_row(session_factory, replay_id, n)
    again = store_gaps(session_factory, replay_id, n, run, [ROW], moments={**MOMENTS, "fights": [{"t": 2.0}]},
                       expected_control_fingerprint=fingerprint)
    assert again == "skipped: already stored" and _moments_row(session_factory, replay_id, n) == before


def test_moments_are_required():
    with pytest.raises(TypeError):
        store_gaps(None, 1, 1, RUN, [])


def test_pg_deleting_the_round_deletes_its_moments(pg, condensed):
    from sqlalchemy import text

    replay_id, uuid, fingerprint, run = _pg_round(pg, condensed)
    assert store_gaps(pg, replay_id, 1, run, [ROW], moments=MOMENTS) == "stored"
    s = pg()
    s.execute(text("DELETE FROM replay_rounds WHERE replay_id = :r AND round_number = 1"), {"r": replay_id})
    s.commit()
    s.close()
    s = pg()
    assert s.get(ReplayRoundMoments, (replay_id, 1)) is None
    s.close()


# ---- PostgreSQL only: the replay lock serialises the check and the write with a replace or a relink


def _pg_round(pg, condensed):
    session = pg()
    add_match(session)
    replay_id = store.store_replay(session, condensed, source="local").replay_id
    fingerprint, run = _current(session, replay_id, 1)
    uuid = str(session.get(Replay, replay_id).match_uuid)
    session.close()
    return replay_id, uuid, fingerprint, run


def test_pg_a_gap_write_and_a_replace_of_the_replay_take_turns(pg, condensed, monkeypatch):
    replay_id, _, fingerprint, run = _pg_round(pg, condensed)
    holding, release, out = threading.Event(), threading.Event(), {}

    def pause(session):
        holding.set()
        assert release.wait(30)

    monkeypatch.setattr(gaps_store, "_after_lock", pause)
    writer = threading.Thread(target=lambda: out.update(
        gaps=store_gaps(pg, replay_id, 1, run, [ROW], moments=MOMENTS, expected_control_fingerprint=fingerprint)))
    writer.start()
    assert holding.wait(30), "the writer holds the replay's lock"

    def replace_it():
        session = pg()
        try:
            out["replace"] = store.store_replay(session, dc_replace(condensed, recipe=condensed.recipe + "x"),
                                                source="local").action
        finally:
            session.close()

    other = threading.Thread(target=replace_it)
    other.start()
    other.join(1.5)
    assert other.is_alive(), "the replace waits: the check and the write are one locked transaction"
    release.set()
    writer.join(30)
    other.join(30)
    assert out == {"gaps": "stored", "replace": "replaced"}
    assert _stored(pg, replay_id, 1) == (None, 0), "the replaced replay's gaps went with it"


@pytest.mark.parametrize("meanwhile", ["relinked", "already_written"])
def test_pg_a_writer_that_waited_for_the_lock_reads_everything_again(pg, condensed, meanwhile):
    from sqlalchemy import text

    replay_id, uuid, fingerprint, run = _pg_round(pg, condensed)
    holder = pg()
    holder.execute(text("SELECT pg_advisory_xact_lock(hashtext(:u))"), {"u": uuid.lower()})   # another writer
    out = {}
    writer = threading.Thread(target=lambda: out.update(
        gaps=store_gaps(pg, replay_id, 1, run, [ROW], moments=MOMENTS, expected_control_fingerprint=fingerprint)))
    writer.start()
    writer.join(1.5)
    assert writer.is_alive(), "it waits for the replay's lock"
    if meanwhile == "relinked":
        holder.get(Replay, replay_id).db_deaths = {"1": [{"slot": 0, "t_db": 30.0}]}
        holder.flush()
        _, kept = _current(holder, replay_id, 1)                  # control under the new link, and its gaps
    else:
        kept = run
    holder.add(ReplayRoundGapRun(replay_id=replay_id, round_number=1, status="ok", fingerprint=kept["fingerprint"],
                                 gaps_revision=kept["gaps_revision"], chokes_hash=kept["chokes_hash"], gap_count=2))
    holder.add_all([ReplayGap(replay_id=replay_id, round_number=1, **ROW),
                    ReplayGap(replay_id=replay_id, round_number=1, **{**ROW, "seq": 1})])
    holder.commit()                                               # the lock goes with the transaction
    holder.close()
    writer.join(30)
    assert out["gaps"].startswith("skipped")
    assert _stored(pg, replay_id, 1) == (("ok", kept["fingerprint"]), 2), "the late result replaced nothing"
