"""Aim events stored with a round's moments (migration 0039) and read back by `replay_aim.current_events`."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from test_gaps_store import MOMENTS, RUN, session_factory, stored_round  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed, pg  # noqa: E402,F401  (fixtures)

from app.models.replay import ReplayAimEvent, ReplayRoundMoments  # noqa: E402
from app.services import replay_aim, replay_gaps  # noqa: E402
from app.services.replay_gaps_store import store_gaps  # noqa: E402

FP = "f" * 16


def _event(i=0, **kw):
    base = dict(slot=3, i=i, opponent=7, t=12.345, cls="ok", err=1.25, err_125=0.5, err_250=None, d_m=14.2,
                fine=True, hold=None, opener="peeker", gun_class="rifle", hp=100, enemy_hp=150, outcome="won",
                t_end=13.5, on=[0.1] * 14, shot=0.25, dmg=0.3)
    base.update(kw)
    return base


def _moments(*events, **kw):
    out = dict(MOMENTS)
    if events:
        out["aim"] = list(events)
    out.update(kw)
    return out


def _run(**kw):
    return {**RUN, "fingerprint": FP, "gaps_revision": replay_gaps.GAPS_REVISION, **kw}


@pytest.fixture(autouse=True)
def fresh_is_fp(monkeypatch):
    """The round counts as current when its fingerprint is FP (the control inputs are not under test here)."""
    monkeypatch.setattr(replay_gaps.Freshness, "expected", lambda self, replay, n: FP)


def test_two_events_make_two_rows_with_the_runs_keys(session_factory, stored_round):
    replay_id, n = stored_round
    assert store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments(_event(0), _event(1, slot=4))) == "stored"
    s = session_factory()
    rows = s.query(ReplayAimEvent).order_by(ReplayAimEvent.idx).all()
    assert [r.idx for r in rows] == [0, 1]
    assert [r.slot for r in rows] == [3, 4]
    assert all(r.fingerprint == FP and r.gaps_revision == replay_gaps.GAPS_REVISION for r in rows)
    first = rows[0]
    assert (first.replay_id, first.round_number, first.fight_i, first.opponent) == (replay_id, n, 0, 7)
    assert first.cls == "ok" and first.err == 1.25 and first.err_250 is None and first.hold is None
    assert first.on_times == [0.1] * 14 and first.t_end == 13.5 and first.outcome == "won"
    assert s.get(ReplayRoundMoments, (replay_id, n)).fights == {"v": 1, "sight": True, "fights": []}


def test_storing_again_replaces_the_rows(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments(_event(0), _event(1)))
    store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments(_event(0, slot=9)))
    s = session_factory()
    rows = s.query(ReplayAimEvent).all()
    assert len(rows) == 1 and rows[0].slot == 9


def test_failed_moments_or_no_aim_store_no_rows(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments(_event(0)))
    assert session_factory().query(ReplayAimEvent).count() == 1
    store_gaps(session_factory, replay_id, n, _run(), [], moments={"status": "failed", "error": "x"})
    assert session_factory().query(ReplayAimEvent).count() == 0
    store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments(_event(0)))
    store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments())       # an older worker: no "aim"
    assert session_factory().query(ReplayAimEvent).count() == 0


def test_freshness_is_decided_once_per_round_not_per_row(session_factory, stored_round, monkeypatch):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments(*[_event(i) for i in range(20)]))
    calls = []
    real = replay_gaps.Freshness.current

    def spy(self, *a, **k):
        calls.append(a[1:])
        return real(self, *a, **k)

    monkeypatch.setattr(replay_gaps.Freshness, "current", spy)
    assert len(replay_aim.current_events(session_factory())) == 20
    assert len(calls) == 1
    # a stale round: still checked once, every row of it dropped
    store_gaps(session_factory, replay_id, n, _run(fingerprint="a" * 16), [],
               moments=_moments(*[_event(i) for i in range(20)]))
    calls.clear()
    assert replay_aim.current_events(session_factory()) == [] and len(calls) == 1


def test_current_events_drops_older_revisions_and_stale_rounds(session_factory, stored_round):
    replay_id, n = stored_round
    store_gaps(session_factory, replay_id, n, _run(), [], moments=_moments(_event(0)))
    db = session_factory()
    assert len(replay_aim.current_events(db)) == 1
    assert len(replay_aim.current_events(db, [replay_id])) == 1
    assert replay_aim.current_events(db, [replay_id + 100]) == []
    assert replay_aim.current_events(db, []) == []
    # an older gaps revision
    store_gaps(session_factory, replay_id, n, _run(gaps_revision=replay_gaps.GAPS_REVISION - 1), [],
               moments=_moments(_event(0)))
    assert replay_aim.current_events(session_factory()) == []
    # a stale fingerprint
    store_gaps(session_factory, replay_id, n, _run(fingerprint="a" * 16), [], moments=_moments(_event(0)))
    assert replay_aim.current_events(session_factory()) == []
