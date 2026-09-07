"""Part 3: pre-plant observation extraction, the fixed-knot shape basis, and
the exact-state logistic fit. No live Postgres required for extraction tests
-- in-memory sqlite, per test_impact_kill_order_bonus_net.py's pattern.

dt throughout is seconds_to_plant (app.scoring.plant_window): POSITIVE
before the plant, decreasing to 0 at the plant instant.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.preplant_time_model import extract_preplant_observations


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _player(db, name):
    p = Player(display_name=name)
    db.add(p)
    db.flush()
    return p.id


def _match_with_one_planted_round(db):
    """Round 1 (TEAM_1 attacks), planted at t=40. A1 kills B1 at t=20
    (pre-plant, dt = 40-20 = +20, killer's exact_state is 5v5, adv=0).
    Team A wins by Detonate."""
    match = Match(external_id="m1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"), agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"), agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()

    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
                planted=True, plant_time=40.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["B1"].id, event_time_seconds=20.0,
                      weapon="Vandal"))
    db.commit()
    return match, rnd, players


def test_extracts_one_row_for_the_only_qualifying_kill():
    db = _session()
    match, rnd, _ = _match_with_one_planted_round(db)

    obs = extract_preplant_observations(db)

    assert len(obs) == 1
    row = obs[0]
    assert row.match_id == match.id
    assert row.round_id == rnd.id
    assert row.dt == 20.0
    assert row.adv == 0
    assert row.is_attacker is True  # round 1 -> TEAM_1 attacks, killer is A1/TEAM_1
    assert row.exact_state == "5v5"
    assert row.round_won_by_killer_team is True  # Team A won, killer is on TEAM_1


def test_self_kill_excluded():
    db = _session()
    match, rnd, players = _match_with_one_planted_round(db)
    a2 = players["A2"]
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a2.id,
                      death_match_player_id=a2.id, event_time_seconds=10.0, weapon="Ghost"))
    db.commit()

    obs = extract_preplant_observations(db)

    assert len(obs) == 1  # still just the one cross-team kill


def test_post_plant_kill_excluded():
    db = _session()
    match, rnd, players = _match_with_one_planted_round(db)
    a2, b2 = players["A2"], players["B2"]
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a2.id,
                      death_match_player_id=b2.id, event_time_seconds=42.0, weapon="Vandal"))
    db.commit()

    obs = extract_preplant_observations(db)

    assert len(obs) == 1  # the post-plant kill at t=42 (dt would be negative) is excluded


def test_phantom_plant_round_excluded():
    """Time Win with plant_time > 100s -- not a real plant (plant_window's
    is_phantom_plant), so this round contributes nothing at all, including
    its early kills."""
    db = _session()
    match = Match(external_id="m2", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "PA1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "PB1"), agent="Sova", team=Team.TEAM_2)
    db.add_all([a1, b1])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Time Win",
                planted=True, plant_time=101.0, exploded=False, defused=False)
    db.add(rnd)
    db.flush()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a1.id,
                      death_match_player_id=b1.id, event_time_seconds=10.0, weapon="Vandal"))
    db.commit()

    obs = extract_preplant_observations(db)

    assert obs == []


def test_surrendered_round_excluded():
    db = _session()
    match = Match(external_id="m3", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "SA1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "SB1"), agent="Sova", team=Team.TEAM_2)
    db.add_all([a1, b1])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Surrendered",
                planted=False, plant_time=None)
    db.add(rnd)
    db.flush()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a1.id,
                      death_match_player_id=b1.id, event_time_seconds=10.0, weapon="Vandal"))
    db.commit()

    obs = extract_preplant_observations(db)

    assert obs == []


def test_never_planted_round_excluded():
    db = _session()
    match = Match(external_id="m4", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    a1 = MatchPlayer(match_id=match.id, player_id=_player(db, "NA1"), agent="Jett", team=Team.TEAM_1)
    b1 = MatchPlayer(match_id=match.id, player_id=_player(db, "NB1"), agent="Sova", team=Team.TEAM_2)
    db.add_all([a1, b1])
    db.flush()
    rnd = Round(match_id=match.id, round_number=1, outcome="Team B Eliminated",
                planted=False, plant_time=None)
    db.add(rnd)
    db.flush()
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=a1.id,
                      death_match_player_id=b1.id, event_time_seconds=10.0, weapon="Vandal"))
    db.commit()

    obs = extract_preplant_observations(db)

    assert obs == []


# -- shape_basis --------------------------------------------------------
#
# dt = seconds_to_plant, POSITIVE before the plant. Knots at 30, 20, 10, 5, 0.

from app.scoring.preplant_time_model import shape_basis  # noqa: E402


def test_shape_basis_at_or_beyond_far_knot_is_zero():
    assert shape_basis(30.0) == (0.0, 0.0)
    assert shape_basis(45.0) == (0.0, 0.0)  # beyond 30: still anchored at 0


def test_shape_basis_at_middle_knot_is_pure_theta1():
    w1, w2 = shape_basis(20.0)
    assert w1 == 1.0
    assert w2 == 0.0


def test_shape_basis_at_near_knot_is_pure_theta2():
    w1, w2 = shape_basis(10.0)
    assert w1 == 0.0
    assert w2 == 1.0


def test_shape_basis_plateaus_from_ten_seconds_to_the_plant():
    # dt=10 down to dt=0 must all reproduce EXACTLY theta2 -- the imposed
    # plateau, not a separately fitted value.
    for dt in (10.0, 7.5, 5.0, 2.0, 0.0):
        w1, w2 = shape_basis(dt)
        assert w1 == 0.0
        assert w2 == 1.0


def test_shape_basis_interpolates_linearly_between_free_knots():
    w1, w2 = shape_basis(25.0)  # halfway between 30 (0) and 20 (theta1)
    assert w1 == pytest.approx(0.5)
    assert w2 == 0.0
    w1, w2 = shape_basis(15.0)  # halfway between 20 (theta1) and 10 (theta2)
    assert w1 == pytest.approx(0.5)
    assert w2 == pytest.approx(0.5)


def test_shape_of_composed_scalar_matches_hand_computation():
    theta1, theta2 = 0.6, 1.0
    for dt, expected in ((30.0, 0.0), (25.0, 0.3), (20.0, 0.6),
                         (15.0, 0.8), (10.0, 1.0), (5.0, 1.0), (0.0, 1.0)):
        w1, w2 = shape_basis(dt)
        assert w1 * theta1 + w2 * theta2 == pytest.approx(expected)
