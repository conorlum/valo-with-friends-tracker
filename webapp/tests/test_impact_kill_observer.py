"""build_impact_rows_for_match's optional per-kill observer.

It exists so a trace/review tool can report what the SCORER computed instead
of re-deriving it. Re-derivation is how preplant_fit_support and ten
diagnostics silently drifted from impact.py on self-kills and resurrections,
misfiling 6.42% of pre-plant kills with no test catching it.

Two properties matter: passing an observer must not change a single scored
number, and what it reports must be the scorer's own values -- the mutated
kill dict, and the alive counts as they stood when kill_order_bonus was
keyed on them.

Same in-memory sqlite pattern as test_impact_postplant_hook.py.
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
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _player(db, name):
    if name not in _ids:
        p = Player(display_name=name)
        db.add(p)
        db.flush()
        _ids[name] = p.id
    return _ids[name]


def _match(db):
    """Two rounds, one planted with a post-plant kill and one pre-plant only,
    so the observer is exercised across both regimes and across rounds."""
    match = Match(external_id="obs1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"A{i}"),
                         agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        mp = MatchPlayer(match_id=match.id, player_id=_player(db, f"B{i}"),
                         agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()

    r1 = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
               planted=True, plant_time=30.0, exploded=True, defused=False)
    r2 = Round(match_id=match.id, round_number=2, outcome="Team B Elimination Win",
               planted=False, plant_time=None, exploded=False, defused=False)
    db.add_all([r1, r2])
    db.flush()
    for rnd in (r1, r2):
        for mp in players.values():
            db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0,
                                   deaths=0, assists=0, score=0, loadout=3900,
                                   remaining=1000))
    db.add(KillEvent(round_id=r1.id, killer_match_player_id=players["A1"].id,
                     death_match_player_id=players["B1"].id, weapon="Vandal",
                     event_time_seconds=20.0))
    db.add(KillEvent(round_id=r1.id, killer_match_player_id=players["A2"].id,
                     death_match_player_id=players["B2"].id, weapon="Vandal",
                     event_time_seconds=40.0))
    db.add(KillEvent(round_id=r2.id, killer_match_player_id=players["B1"].id,
                     death_match_player_id=players["A1"].id, weapon="Phantom",
                     event_time_seconds=15.0))
    db.commit()
    return match, players


def _collect(db, match_id, **kwargs):
    seen = []
    rows = build_impact_rows_for_match(
        db, match_id, kill_observer=lambda **kw: seen.append(kw), **kwargs
    )
    return rows, seen


def test_observer_does_not_change_any_scored_value():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    without = build_impact_rows_for_match(db, match.id)
    with_observer, seen = _collect(db, match.id)

    assert seen, "fixture produced no kills, so this would prove nothing"
    assert with_observer == without


def test_observer_is_called_once_per_kill():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    _, seen = _collect(db, match.id)
    assert len(seen) == 3


def test_observer_reports_the_scorers_own_mutated_kill_dict():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    _, seen = _collect(db, match.id)

    # These keys are set by the scorer during the loop; their presence proves
    # the observer fires AFTER the kill has been scored, not before.
    for call in seen:
        for key in ("kill_order_bonus", "kill_order_bonus_x_time",
                    "death_order_bonus", "is_post_plant"):
            assert key in call["kill"], key


def test_observer_reports_alive_counts_before_the_decrement():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    _, seen = _collect(db, match.id)

    by_round: dict[int, list] = {}
    for call in seen:
        by_round.setdefault(call["round_number"], []).append(call)

    # Round 1's two kills are both Team A killing Team B, so the victim side
    # drops 5 -> 4 while the killer side holds at 5. Pre-decrement counts are
    # what kill_order_bonus is keyed on, so that is what must be reported.
    first, second = by_round[1]
    assert (first["context"]["killer_team_alive"],
            first["context"]["victim_team_alive"]) == (5, 5)
    assert (second["context"]["killer_team_alive"],
            second["context"]["victim_team_alive"]) == (5, 4)


def test_observer_reports_the_post_plant_regime_and_seconds_to_plant():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    _, seen = _collect(db, match.id)

    by_round: dict[int, list] = {}
    for call in seen:
        by_round.setdefault(call["round_number"], []).append(call)

    pre, post = by_round[1]
    # plant_time is 30.0; the kills are at 20.0 and 40.0.
    assert pre["context"]["seconds_to_plant"] == pytest.approx(10.0)
    assert pre["kill"]["is_post_plant"] is False
    assert post["context"]["seconds_to_plant"] == pytest.approx(-10.0)
    assert post["kill"]["is_post_plant"] is True
    # An unplanted round has no plant to measure against.
    assert by_round[2][0]["context"]["seconds_to_plant"] is None


def test_observer_reports_which_side_the_killer_was_on():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    _, seen = _collect(db, match.id)
    # Keyed by round, never by position: round_kills is a dict and the kill
    # loop does not promise round order.
    by_round: dict[int, list] = {}
    for call in seen:
        by_round.setdefault(call["round_number"], []).append(call)
    # TEAM_1 attacks rounds 1-12, and Team A is TEAM_1.
    assert by_round[1][0]["context"]["killer_is_attacker"] is True
    # Round 2's killer is Team B, so still the defending side.
    assert by_round[2][0]["context"]["killer_is_attacker"] is False


def test_default_is_none_so_existing_callers_are_untouched():
    _ids.clear()
    db = _session()
    match, _ = _match(db)
    assert (build_impact_rows_for_match(db, match.id)
            == build_impact_rows_for_match(db, match.id, kill_observer=None))
