"""Part 4's V(a, d, t) table: extraction population, the pooling ladder, the
moving-average smoother, the analytic terminal endpoint, and the rule that an
unsupported cell reports UNSUPPORTED rather than the number 1.0.

Spec: docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 4 ("Estimating V without overfitting", "The estimator contract",
"The no-attackers-left state", "Testing: Part 4").

In-memory sqlite, per test_impact_kill_order_bonus_net.py's pattern.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.postplant_value_table import (
    BAND_EDGES,
    MIN_OBSERVATIONS,
    RUNG_ANALYTIC,
    RUNG_BAND,
    RUNG_D_POOLED,
    RUNG_EXACT,
    PostPlantRoundSecond,
    ValueTable,
    band_of,
    build_value_table,
    extract_postplant_round_seconds,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


_ids = {}


def _player(db, name):
    if name not in _ids:
        p = Player(display_name=name)
        db.add(p)
        db.flush()
        _ids[name] = p.id
    return _ids[name]


def _match(db, external_id="pp1"):
    match = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"{external_id}A{i}"),
                         agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"{external_id}B{i}"),
                         agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()
    return match, players


def _round(db, match, players, *, number=1, outcome="Team A Detonate Win",
           plant_time=30.0, exploded=True, defused=False, defuse_time=None, kills=()):
    rnd = Round(match_id=match.id, round_number=number, outcome=outcome, planted=True,
                plant_time=plant_time, exploded=exploded, defused=defused, defuse_time=defuse_time)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    for killer, victim, t in kills:
        db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players[killer].id,
                          death_match_player_id=players[victim].id, weapon="Classic",
                          event_time_seconds=t))
    db.commit()
    return rnd


# --------------------------------------------------------------------------
# Extraction population
# --------------------------------------------------------------------------

def test_one_observation_per_whole_second_occupied_post_plant():
    """Round 1 attacks as TEAM_1. Plant at t=30, detonation horizon 45s, no
    kills at all, so every second 0..44 sits at 5v5."""
    _ids.clear()
    db = _session()
    match, players = _match(db)
    _round(db, match, players, plant_time=30.0)

    rows = extract_postplant_round_seconds(db)

    assert len(rows) == 45
    assert {r.t for r in rows} == set(range(45))
    assert all(r.attackers_alive == 5 and r.defenders_alive == 5 for r in rows)
    assert all(r.atk_won is True for r in rows)  # Team A detonated, TEAM_1 attacks round 1


def test_horizon_stops_at_the_defuse_not_the_spike_timer():
    """A defuse at plant+10 means only seconds 0..9 were ever occupied
    pre-resolution -- a kill after that is in a decided round."""
    _ids.clear()
    db = _session()
    match, players = _match(db)
    _round(db, match, players, outcome="Team B Defuse Win", plant_time=30.0,
           exploded=False, defused=True, defuse_time=40.0)

    rows = extract_postplant_round_seconds(db)

    assert {r.t for r in rows} == set(range(10))
    assert all(r.atk_won is False for r in rows)


def test_kill_after_a_completed_defuse_is_excluded():
    """Spec, Testing Part 4: fixtures must include a kill after a completed
    defuse and assert it is excluded. The defuse lands at plant+10; the kill
    at plant+20 must not move any observed state, because no second past 9
    is emitted at all."""
    _ids.clear()
    db = _session()
    match, players = _match(db)
    _round(db, match, players, outcome="Team B Defuse Win", plant_time=30.0,
           exploded=False, defused=True, defuse_time=40.0,
           kills=(("A1", "B1", 50.0),))

    rows = extract_postplant_round_seconds(db)

    assert max(r.t for r in rows) == 9
    # The post-defuse kill never reduces defenders_alive in the emitted rows.
    assert all(r.defenders_alive == 5 for r in rows)


def test_kill_after_detonation_is_excluded():
    """The spike horizon is 45s; a kill at plant+50 is post-resolution."""
    _ids.clear()
    db = _session()
    match, players = _match(db)
    _round(db, match, players, plant_time=30.0, kills=(("A1", "B1", 80.0),))

    rows = extract_postplant_round_seconds(db)

    assert max(r.t for r in rows) == 44
    assert all(r.defenders_alive == 5 for r in rows)


def test_alive_counts_track_post_plant_kills():
    """Two defenders die at plant+5 and plant+6, so seconds 0-4 are 5v5,
    second 5 is 5v4, and seconds 6+ are 5v3."""
    _ids.clear()
    db = _session()
    match, players = _match(db)
    _round(db, match, players, plant_time=30.0,
           kills=(("A1", "B1", 35.0), ("A1", "B2", 36.0)))

    rows = {r.t: (r.attackers_alive, r.defenders_alive) for r in extract_postplant_round_seconds(db)}

    assert rows[4] == (5, 5)
    assert rows[5] == (5, 4)
    assert rows[6] == (5, 3)
    assert rows[44] == (5, 3)


def test_surrendered_and_phantom_rounds_excluded():
    _ids.clear()
    db = _session()
    match, players = _match(db)
    _round(db, match, players, number=1, outcome="Team A Surrendered", plant_time=30.0)
    _round(db, match, players, number=2, outcome="Team A Time Win", plant_time=101.0)

    assert extract_postplant_round_seconds(db) == []


# --------------------------------------------------------------------------
# Bands -- half-open [0, 38), [38, 41.5), [41.5, 45)
# --------------------------------------------------------------------------

def test_band_edges_are_the_two_defuse_deadlines():
    assert BAND_EDGES == (0.0, 38.0, 41.5, 45.0)


def test_whole_second_41_is_in_the_middle_band_and_42_in_the_last():
    """The estimator contract's stated convention: the 41.5 deadline falls
    inside a second, and the table is indexed by floor(t)."""
    assert band_of(37) == band_of(0) == 0
    assert band_of(38) == band_of(41) == 1
    assert band_of(42) == band_of(44) == 2


# --------------------------------------------------------------------------
# The table: support, the analytic endpoint, and the ladder
# --------------------------------------------------------------------------

def _obs(a, d, t, atk_won, n, *, match_id=0, round_offset=0):
    return [
        PostPlantRoundSecond(match_id=match_id + i, round_id=round_offset + i, t=t,
                             attackers_alive=a, defenders_alive=d, atk_won=atk_won)
        for i in range(n)
    ]


def test_cell_below_the_floor_is_unsupported_not_one_point_zero():
    """Spec: 'An unsupported cell returns UNSUPPORTED, not the number 1.0...
    1.0 as a win probability is certainty.'"""
    table = build_value_table(_obs(4, 3, 10, True, MIN_OBSERVATIONS - 1))

    lookup = table.value(4, 3, 10)

    assert lookup.supported is False
    assert lookup.value is None
    assert lookup.rung == "unsupported"


def test_exact_cell_clears_the_floor_and_reports_its_own_rate():
    obs = _obs(4, 3, 10, True, 40) + _obs(4, 3, 10, False, 40, match_id=1000, round_offset=1000)

    table = build_value_table(obs, w=0)
    lookup = table.value(4, 3, 10)

    assert lookup.supported is True
    assert lookup.rung == RUNG_EXACT
    assert lookup.value == pytest.approx(0.5)


def test_terminal_endpoint_with_no_defenders_is_exactly_one_analytically():
    """V(a, 0, t) = 1.0 exactly -- no estimate, no floor, no smoothing, and
    it holds with an empty table."""
    table = build_value_table([])

    for a in (1, 3, 5):
        for t in (0, 20, 44):
            lookup = table.value(a, 0, t)
            assert lookup.value == 1.0
            assert lookup.supported is True
            assert lookup.rung == RUNG_ANALYTIC


def test_no_attackers_left_is_estimated_not_pinned():
    """V(0, d, t) is a table cell like any other -- attackers still win when
    the defuse fails. A measured 0 is legal; what must not happen is a pin."""
    obs = _obs(0, 2, 40, True, 20) + _obs(0, 2, 40, False, 60, match_id=1000, round_offset=1000)

    table = build_value_table(obs, w=0)
    lookup = table.value(0, 2, 40)

    assert lookup.supported is True
    assert lookup.value == pytest.approx(0.25)  # 20 of 80, reconstructs from raw counts
    assert 0.0 <= lookup.value <= 1.0


def test_no_attackers_left_raw_rate_may_legitimately_be_zero():
    """Every qualifying round defused -- a directly observed rate of 0 is a
    legal value, not evidence the estimator was skipped."""
    table = build_value_table(_obs(0, 3, 42, False, 80), w=0)

    assert table.value(0, 3, 42).value == 0.0


def test_ladder_pools_d_upward_when_the_exact_cell_is_thin():
    """Rung (ii): d in {3, 4, 5} pool together. Neither d=4 nor d=5 clears
    the floor alone at t=10; pooled they do."""
    obs = (
        _obs(5, 4, 10, True, 40)
        + _obs(5, 5, 10, False, 40, match_id=1000, round_offset=1000)
    )

    table = build_value_table(obs, w=0)
    lookup = table.value(5, 4, 10)

    assert lookup.rung == RUNG_D_POOLED
    assert lookup.value == pytest.approx(0.5)  # 40 of the pooled 80


def test_ladder_widens_to_the_deadline_band_when_pooling_is_still_thin():
    """Rung (iii): no single second clears the floor even d-pooled, but the
    band [0, 38) does."""
    obs = []
    for t in range(20):
        obs += _obs(2, 1, t, True, 5, match_id=t * 100, round_offset=t * 100)

    table = build_value_table(obs, w=0)
    lookup = table.value(2, 1, 10)

    assert lookup.rung == RUNG_BAND
    assert lookup.value == pytest.approx(1.0)


def test_unsupported_when_even_the_band_is_thin():
    obs = _obs(2, 1, 10, True, 5)

    table = build_value_table(obs, w=0)

    assert table.value(2, 1, 10).supported is False


# --------------------------------------------------------------------------
# The smoother
# --------------------------------------------------------------------------

def test_w_zero_is_no_moving_average():
    """W=0 leaves the support floor, ladder, endpoint rule and fallback
    active but applies no smoothing -- the cell reports its own rate."""
    obs = _obs(4, 2, 10, True, 60) + _obs(4, 2, 11, False, 60, match_id=1000, round_offset=1000)

    table = build_value_table(obs, w=0)

    assert table.value(4, 2, 10).value == pytest.approx(1.0)
    assert table.value(4, 2, 11).value == pytest.approx(0.0)


def test_moving_average_is_weighted_by_observation_count():
    """t=10 has 60 obs all won; t=11 has 120 obs all lost. Centred W=1 at
    t=10 sees only t=10 and t=11 (t=9 is empty, weight zero), so the
    smoothed value is 60/(60+120) = 1/3 -- count-weighted, not the 0.5 a
    naive mean of rates would give."""
    obs = _obs(4, 2, 10, True, 60) + _obs(4, 2, 11, False, 120, match_id=1000, round_offset=1000)

    table = build_value_table(obs, w=1)

    assert table.value(4, 2, 10).value == pytest.approx(1.0 / 3.0)


def test_window_is_one_sided_at_the_ends_and_never_widened_to_compensate():
    """At t=0 the window is [0, W]; empty neighbours contribute weight zero
    rather than pulling in further seconds."""
    obs = _obs(4, 2, 0, True, 60) + _obs(4, 2, 1, False, 60, match_id=1000, round_offset=1000)

    table = build_value_table(obs, w=1)

    assert table.value(4, 2, 0).value == pytest.approx(0.5)


def test_support_is_decided_before_smoothing_not_on_the_summed_window():
    """The 60-observation floor is never applied to a summed moving window
    (declaration 3.2). Two adjacent 40-observation seconds sum to 80 but
    neither clears the floor on its own, so the exact rung must not fire."""
    obs = _obs(4, 2, 10, True, 40) + _obs(4, 2, 11, True, 40, match_id=1000, round_offset=1000)

    table = build_value_table(obs, w=2)

    assert table.value(4, 2, 10).rung != RUNG_EXACT
