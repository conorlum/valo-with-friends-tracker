"""build_impact_rows_for_match's optional econ-attribution observer.

econ_component is the largest single term in the new formula and the least
legible: a team-level magnitude divided among players by how much enemy
loadout value each removed. A review has to be able to show that division,
and re-deriving it outside the scorer is how earlier tooling drifted.

    credit(p) = econ_round(T)   * removed_by(p) / removed(T)
    debit(p)  = econ_round(opp) * lost_by(p)    / removed(opp)
    value(p)  = credit - debit,  scaled by ECON_SCALE

The property that makes it explainable is that credits sum to the team's own
econ_round -- the share is of the ALLOCATION, never of the magnitude.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import build_impact_rows_for_match

_ids: dict[str, int] = {}


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    return sessionmaker(bind=engine)()


def _player(db, name):
    if name not in _ids:
        p = Player(display_name=name)
        db.add(p)
        db.flush()
        _ids[name] = p.id
    return _ids[name]


def _match(db):
    match = Match(external_id="eco1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        players[f"A{i}"] = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"),
                                       agent="Jett", team=Team.TEAM_1)
        players[f"B{i}"] = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"),
                                       agent="Sova", team=Team.TEAM_2)
    for mp in players.values():
        db.add(mp)
    db.flush()

    rounds = {}
    for number in range(1, 9):
        rnd = Round(match_id=match.id, round_number=number,
                    outcome="Team A Detonate Win", planted=True, plant_time=30.0,
                    exploded=True, defused=False)
        db.add(rnd)
        db.flush()
        rounds[number] = rnd
        for name, mp in players.items():
            db.add(RoundPlayerStat(
                round_id=rnd.id, match_player_id=mp.id, kills=1, deaths=1,
                assists=0, score=200, loadout=3900 if name.startswith("A") else 2400,
                remaining=1500,
            ))
    # Two Team A killers with DIFFERENT kill counts, so the share is not a
    # uniform split and a broken denominator would be visible.
    for killer, victim in (("A1", "B1"), ("A1", "B2"), ("A2", "B3")):
        db.add(KillEvent(round_id=rounds[5].id,
                         killer_match_player_id=players[killer].id,
                         death_match_player_id=players[victim].id,
                         weapon="Vandal", event_time_seconds=20.0))
    db.commit()
    return match, players


def _collect(db, match_id):
    seen = []
    rows = build_impact_rows_for_match(
        db, match_id, use_realized_swing=True, enable_econ_component=True,
        econ_observer=lambda **kw: seen.append(kw),
    )
    return rows, seen


def test_observer_does_not_change_any_scored_value():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    without = build_impact_rows_for_match(
        db, match.id, use_realized_swing=True, enable_econ_component=True)
    with_observer, seen = _collect(db, match.id)
    assert seen, "fixture produced no econ rounds, so this proves nothing"
    assert with_observer == without


def test_observer_reports_the_team_magnitude_and_its_denominator():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    _, seen = _collect(db, match.id)
    detail = next(d for d in seen if d["round_number"] == 5)
    assert set(detail["econ_round_by_team"]) == {Team.TEAM_1, Team.TEAM_2}
    # Team A took three kills in round 5, so its removed(T) is the sum of the
    # three victims' committed values and is strictly positive.
    assert detail["removed_by_team"][Team.TEAM_1] > 0


def test_credits_sum_to_the_teams_own_econ_round():
    # The share is of the ALLOCATION, not of the magnitude -- this is the
    # property that makes the attribution explainable at all.
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    _, seen = _collect(db, match.id)
    detail = next(d for d in seen if d["round_number"] == 5)
    for team, magnitude in detail["econ_round_by_team"].items():
        if detail["removed_by_team"].get(team, 0.0) <= 0:
            continue  # the allocation abstains; credits are 0 by design
        credited = sum(p["credit"] for p in detail["players"].values()
                       if p["team"] == team)
        assert credited == pytest.approx(magnitude)


def test_a_players_share_is_proportional_to_the_value_it_removed():
    _ids.clear()
    db = _session()
    match, players = _match(db)
    _, seen = _collect(db, match.id)
    detail = next(d for d in seen if d["round_number"] == 5)
    a1 = detail["players"][players["A1"].id]
    a2 = detail["players"][players["A2"].id]
    assert a1["removed"] > a2["removed"] > 0, "fixture no longer has unequal removals"
    assert a1["credit"] / a2["credit"] == pytest.approx(a1["removed"] / a2["removed"])


def test_reported_value_matches_the_scored_econ_component():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    rows, seen = _collect(db, match.id)
    detail = next(d for d in seen if d["round_number"] == 5)
    round_id = next(r.round_id for r in rows
                    if r.econ_component and detail["players"][r.match_player_id])
    for row in rows:
        if row.round_id != round_id:
            continue
        assert row.econ_component == round(detail["players"][row.match_player_id]["scaled"])


def test_default_is_none_so_existing_callers_are_untouched():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    assert (build_impact_rows_for_match(db, match.id, enable_econ_component=True)
            == build_impact_rows_for_match(db, match.id, enable_econ_component=True,
                                           econ_observer=None))
