"""Aim aggregation (app/services/aim_stats.py): the on-target rule, summaries with n and IQR, censored medians, and
the reads that pick a player's events. SQLite, rows inserted through the W3 store."""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from test_gaps_store import MOMENTS, RUN, session_factory, stored_round  # noqa: E402,F401  (fixtures)
from test_replay_store import condensed, pg  # noqa: E402,F401  (fixtures)

from app.models.replay import ReplayPlayer  # noqa: E402
from app.services import aim_stats, replay_aim, replay_gaps  # noqa: E402
from app.services.replay_gaps_store import store_gaps  # noqa: E402

FP = "f" * 16


def ev(cls="ok", err=1.0, d_m=10.0, on=None, shot=None, dmg=None, match_id=1, played_at=None):
    return SimpleNamespace(cls=cls, err=err, d_m=d_m, on_times=on, shot=shot, dmg=dmg, match_id=match_id,
                           played_at=played_at)


# ---------------------------------------------------------------------------------------------- the on rule

@pytest.mark.parametrize("d, want", [(2, 10), (5, 4), (10, 2), (20, 1), (60, 0.5)])
def test_on_threshold_body(d, want):
    assert aim_stats.on_threshold(d) == want


def test_on_threshold_other_rules():
    assert aim_stats.on_threshold(20, "five") == 5
    assert aim_stats.on_threshold(2, "five") == 10          # the ladder-fitted body (10) is above 5
    assert aim_stats.on_threshold(60, "fixed5") == 5


# ---------------------------------------------------------------------------------------------- summary

def test_twelve_ok_events_give_exact_median_and_iqr():
    events = [ev(err=float(i)) for i in range(1, 13)]
    p = aim_stats.summary(events)["placement"]
    assert p["n"] == 12
    assert p["median"] == 6.5 and p["q1"] == 3.75 and p["q3"] == 9.25
    # d_m 10 -> on at <= 2 deg; near <= CROSSHAIR_NEAR_DEG (20); the rest off
    assert p["on"] == pytest.approx(2 / 12) and p["near"] == pytest.approx(10 / 12) and p["off"] == 0
    assert p["on"] + p["near"] + p["off"] == pytest.approx(1)


def test_shares_split_on_near_off():
    events = [ev(err=1), ev(err=10), ev(err=30), ev(err=45)]
    events += [ev(err=1)] * 6
    p = aim_stats.summary(events)["placement"]
    assert p["n"] == 10
    assert (p["on"], p["near"], p["off"]) == (pytest.approx(.7), pytest.approx(.1), pytest.approx(.2))


def test_nine_ok_events_are_too_few():
    s = aim_stats.summary([ev(err=float(i)) for i in range(9)])
    assert s["placement"] == {"too_few": True, "n": 9}


def test_other_classes_are_counted_not_used():
    events = [ev(err=float(i)) for i in range(1, 11)] + [ev("behind", err=170), ev("behind", err=160), ev("no_view", err=None)]
    s = aim_stats.summary(events)
    assert s["classes"] == {"ok": 10, "behind": 2, "no_view": 1}
    assert s["placement"]["n"] == 10 and s["placement"]["median"] == 5.5


def test_censored_times_count_as_never():
    # 10 events in the close band, time to shot: 4 of 10 censored -> median is the 5th/6th value
    shots = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, None, None, None, None]
    s = aim_stats.summary([ev(d_m=5, shot=t) for t in shots])
    cell = s["bands"]["close"]["to_shot"]
    assert cell["n"] == 10 and cell["not_reached"] is False
    assert cell["median"] == pytest.approx(0.55) and cell["reached"] == pytest.approx(.6)


def test_three_of_five_censored_is_not_reached_two_of_five_is_the_third_value():
    def cell(shots):
        events = [ev(d_m=5, shot=t) for t in shots] * 2        # 10 events
        return aim_stats.summary(events)["bands"]["close"]["to_shot"]

    c = cell([None, None, None, 0.1, 0.2])
    assert c["not_reached"] is True and c["median"] is None and c["reached"] == pytest.approx(.4)
    c = cell([None, None, 0.1, 0.2, 0.3])
    assert c["not_reached"] is False and c["median"] == pytest.approx(0.3)


def test_time_to_on_target_reads_the_ladder_slot_of_the_rule():
    # d_m 10 -> threshold 2 deg -> ladder index 3
    on = [None, None, None, 0.7] + [0.1] * 10
    s = aim_stats.summary([ev(d_m=10, on=on, err=1)] * 10)
    assert s["bands"]["mid"]["to_on"]["median"] == pytest.approx(0.7)
    # the same events at 5 m (threshold 4 deg, index 6): 0.1
    s = aim_stats.summary([ev(d_m=5, on=on, err=1)] * 10)
    assert s["bands"]["close"]["to_on"]["median"] == pytest.approx(0.1)


def test_a_band_under_ten_events_is_too_few():
    events = [ev(d_m=5)] * 10 + [ev(d_m=30)] * 3
    b = aim_stats.summary(events)["bands"]
    assert b["long"] == {"too_few": True, "n": 3}
    assert b["close"]["n"] == 10


def test_per_match_gives_date_n_and_median():
    from datetime import datetime
    d1, d2 = datetime(2026, 1, 1), datetime(2026, 1, 5)
    events = [ev(err=1, match_id=1, played_at=d1), ev(err=3, match_id=1, played_at=d1), ev(err=5, match_id=2, played_at=d2)]
    out = aim_stats.per_match(events)
    assert out[1] == {"date": d1, "n": 2, "median": 2.0}
    assert out[2] == {"date": d2, "n": 1, "median": 5.0}


def test_infinite_never_leaks_into_a_median():
    assert not math.isinf(aim_stats.summary([ev(d_m=5, shot=0.1)] * 10)["bands"]["close"]["to_shot"]["median"])


# ---------------------------------------------------------------------------------------------- events_for

def _event(slot, i=0, **kw):
    base = dict(slot=slot, i=i, opponent=7, t=12.0, cls="ok", err=1.25, err_125=None, err_250=None, d_m=14.2,
                fine=True, hold=None, opener=None, gun_class="rifle", hp=100, enemy_hp=100, outcome="won",
                t_end=13.0, on=[0.1] * 14, shot=0.2, dmg=0.3)
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def fresh_is_fp(monkeypatch):
    monkeypatch.setattr(replay_gaps.Freshness, "expected", lambda self, replay, n: FP)


def _store(session_factory, replay_id, n, events, **run):
    run = {**RUN, "fingerprint": FP, "gaps_revision": replay_gaps.GAPS_REVISION, **run}
    store_gaps(session_factory, replay_id, n, run, [], moments={**MOMENTS, "aim": events})


def _player_ids(db, replay_id):
    from app.models.match import MatchPlayer
    rps = {r.slot: r for r in db.query(ReplayPlayer).filter_by(replay_id=replay_id)}
    return {s: db.get(MatchPlayer, r.match_player_id) for s, r in rps.items() if r.match_player_id}


def test_events_for_picks_the_players_events(session_factory, stored_round):
    replay_id, n = stored_round
    _store(session_factory, replay_id, n, [_event(0), _event(1, 1), _event(1, 2)])
    db = session_factory()
    mps = _player_ids(db, replay_id)
    assert mps, "the fixture replay should have linked players"
    out = aim_stats.events_for(db, [mps[1].player_id])
    assert len(out) == 2
    assert {e.player_id for e in out} == {mps[1].player_id}
    assert {e.match_id for e in out} == {mps[1].match_id}
    assert out[0].agent == mps[1].agent
    assert len(aim_stats.events_for(db, None)) == 3


def test_events_for_honours_agents(session_factory, stored_round):
    replay_id, n = stored_round
    _store(session_factory, replay_id, n, [_event(0), _event(1, 1)])
    db = session_factory()
    mps = _player_ids(db, replay_id)
    both = [mps[0].player_id, mps[1].player_id]
    assert len(aim_stats.events_for(db, both)) == 2
    only = aim_stats.events_for(db, both, agents=[mps[1].agent])
    assert [e.agent for e in only] == [mps[1].agent]
    assert aim_stats.events_for(db, both, agents=["Nobody"]) == []


def test_one_per_match_keeps_the_lowest_player_id(session_factory, stored_round):
    replay_id, n = stored_round
    _store(session_factory, replay_id, n, [_event(0), _event(1, 1)])
    db = session_factory()
    mps = _player_ids(db, replay_id)
    both = [mps[0].player_id, mps[1].player_id]
    assert len(aim_stats.events_for(db, both)) == 2
    one = aim_stats.events_for(db, both, one_per_match=True)
    assert {e.player_id for e in one} == {min(both)}


def test_only_the_players_replays_are_read(session_factory, stored_round, monkeypatch):
    replay_id, n = stored_round
    _store(session_factory, replay_id, n, [_event(0)])
    db = session_factory()
    mps = _player_ids(db, replay_id)
    seen = []
    real = replay_aim.current_events
    monkeypatch.setattr(replay_aim, "current_events", lambda d, ids=None: seen.append(ids) or real(d, ids))
    aim_stats.events_for(db, [mps[0].player_id])
    assert [sorted(i) for i in seen] == [[replay_id]]
    seen.clear()
    aim_stats.events_for(db, [10 ** 6])
    assert seen in ([[]], [])           # a player in no replay reads nothing
    seen.clear()
    aim_stats.events_for(db, None)
    assert seen == [None]


def test_stale_rows_are_excluded(session_factory, stored_round):
    replay_id, n = stored_round
    _store(session_factory, replay_id, n, [_event(0)], gaps_revision=replay_gaps.GAPS_REVISION - 1)
    db = session_factory()
    mps = _player_ids(db, replay_id)
    assert aim_stats.events_for(db, [mps[0].player_id]) == []


def test_everyone_summary_is_cached(session_factory, stored_round, monkeypatch):
    replay_id, n = stored_round
    _store(session_factory, replay_id, n, [_event(0)])
    aim_stats._EVERYONE.clear()
    calls = []
    real = aim_stats.events_for
    monkeypatch.setattr(aim_stats, "events_for", lambda *a, **k: calls.append(1) or real(*a, **k))
    db = session_factory()
    a = aim_stats.everyone_summary(db)
    b = aim_stats.everyone_summary(db)
    assert a == b and len(calls) == 1
    aim_stats._EVERYONE.clear()
