"""How many players each team has alive before every kill.

The count drives two things: Valorant's combat-score kill bonus, which the
scorer backs out of ACS to leave damage (150 at 5 alive, -20 per player down),
and the kill-order state the leverage term is keyed on. When the count is too
high the backed-out bonus is too big and damage goes negative; over the corpus
that was 40 player-rounds, and every one of them is one of the cases below
(owner, 2026-09-14: "i dont want to see negative damage numbers").

  1. A player who never took part in the round (disconnected or AFK: score,
     kills, deaths, assists and loadout all zero, not in the kill feed) is not
     alive. Valorant counts only the players actually in the round.
  2. A dead player who lands a kill LATER with lingering utility (Showstopper,
     Orbital Strike, a turret) is still dead. Only a later death, or a later
     kill with a gun or an ability that needs its user alive, proves a revive.
  3. A revived player is dead from their death until they next appear in the
     kill feed -- the earliest moment the feed proves them alive again.
  4. A kill logged at the same instant as its killer's death is a trade, not
     a revive.
  5. A team kill removes a player from the VICTIM's team.

Checked against the raw captures' per-kill player positions: this rule
reproduces the true alive count on 1,286 of 1,289 kills (the old rule 1,283).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import build_impact_rows_for_match

SCORED_ROUND = 5


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    return sessionmaker(bind=engine)()


def _build(kills, absent=()):
    """kills: (killer, victim, seconds, weapon); killer None = environmental.
    absent: players whose SCORED_ROUND stats are all zero."""
    db = _session()
    match = Match(external_id="alive", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for side, team in (("A", Team.TEAM_1), ("B", Team.TEAM_2)):
        for i in range(1, 6):
            player = Player(display_name=f"{side}{i}")
            db.add(player)
            db.flush()
            mp = MatchPlayer(match_id=match.id, player_id=player.id, agent="Jett", team=team)
            db.add(mp)
            players[f"{side}{i}"] = mp
    db.flush()
    # Rounds 1..8 contiguous: _rounds_since_last_win walks back from the scored
    # round and raises on a gap.
    for number in range(1, 9):
        rnd = Round(match_id=match.id, round_number=number, outcome="Team A Elimination Win",
                    planted=False, plant_time=None, exploded=False, defused=False)
        db.add(rnd)
        db.flush()
        for name, mp in players.items():
            if number == SCORED_ROUND and name in absent:
                stats = dict(score=0, kills=0, deaths=0, assists=0, loadout=0)
            else:
                stats = dict(score=300, kills=1, deaths=1, assists=0, loadout=3900)
            db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, remaining=1000, **stats))
        if number == SCORED_ROUND:
            for killer, victim, seconds, weapon in kills:
                db.add(KillEvent(
                    round_id=rnd.id,
                    killer_match_player_id=players[killer].id if killer else None,
                    death_match_player_id=players[victim].id,
                    weapon=weapon, event_time_seconds=seconds,
                ))
    db.commit()
    return db, match, players


def _victim_alive_and_bonus(kills, absent=()):
    """[(victim_team_alive, acs_bonus)] per kill of SCORED_ROUND, as scored."""
    db, match, _ = _build(kills, absent)
    seen = []

    def observer(round_number, kill_index, kill, context):
        if round_number == SCORED_ROUND:
            seen.append((context["victim_team_alive"], kill["acs_bonus"]))

    build_impact_rows_for_match(db, match.id, kill_observer=observer)
    return seen


def test_absent_players_are_not_alive():
    # B4 and B5 never played the round: A1's kill is on a team of 3, not 5.
    seen = _victim_alive_and_bonus([("A1", "B1", 10.0, "Vandal")], absent=("B4", "B5"))
    assert seen == [(3, 110)]


def test_a_player_in_the_kill_feed_is_present_even_with_zero_stats():
    # B5's row is all zeros, but B5 is killed: they were in the round.
    seen = _victim_alive_and_bonus([("A1", "B5", 10.0, "Vandal")], absent=("B5",))
    assert seen == [(5, 150)]


def test_a_utility_kill_after_death_is_not_a_revive():
    seen = _victim_alive_and_bonus([
        ("A1", "B1", 10.0, "Vandal"),
        ("B1", "A2", 12.0, "Showstopper"),   # B1's rocket, fired before dying
        ("A3", "B2", 20.0, "Vandal"),        # B1 is still dead: TEAM_2 has 4
    ])
    assert seen[2] == (4, 130)


def test_a_revived_player_is_dead_until_they_next_appear():
    seen = _victim_alive_and_bonus([
        ("A1", "B1", 10.0, "Vandal"),
        ("A2", "B2", 15.0, "Vandal"),        # B1 not yet revived: TEAM_2 has 4
        ("A3", "B1", 30.0, "Vandal"),        # B1 back up, killed again: 5 - 2 + 1
    ])
    assert seen == [(5, 150), (4, 130), (4, 130)]


def test_a_gun_kill_after_death_proves_a_revive():
    seen = _victim_alive_and_bonus([
        ("A1", "B1", 10.0, "Vandal"),
        ("A2", "B2", 15.0, "Vandal"),        # B1 still dead: TEAM_2 has 4
        ("B1", "A3", 30.0, "Vandal"),        # a revived B1 shoots A3
        ("A4", "B3", 40.0, "Vandal"),        # B1 back, B2 dead: 4 alive (3 if B1 stayed dead)
    ])
    assert seen[1] == (4, 130)
    assert seen[3] == (4, 130)


def test_a_kill_at_the_instant_of_the_killers_death_is_a_trade_not_a_revive():
    seen = _victim_alive_and_bonus([
        ("A1", "B1", 10.0, "Vandal"),
        ("B1", "A2", 10.0, "Vandal"),        # same instant: B1 traded, stays dead
        ("A3", "B2", 20.0, "Vandal"),
    ])
    assert seen[2] == (4, 130)


def test_a_team_kill_removes_a_player_from_the_victims_team():
    seen = _victim_alive_and_bonus([
        ("A1", "A2", 5.0, "Vandal"),         # team kill: TEAM_1 is down to 4
        ("B1", "A3", 10.0, "Vandal"),
    ])
    assert seen[1] == (4, 130)


def test_no_negative_damage_when_the_killer_barely_damaged_a_short_team():
    # The shape of match 2974 round 10: an enemy team of 2 in the round, and a
    # kill finishing someone a teammate had already hurt. Score 120 = the 90
    # bonus + 30 damage. With the team counted at 5 the scorer backed out 150.
    db, match, players = _build([("A1", "B1", 10.0, "Vandal")], absent=("B3", "B4", "B5"))
    stat = db.query(RoundPlayerStat).join(Round).filter(
        Round.round_number == SCORED_ROUND, RoundPlayerStat.match_player_id == players["A1"].id).one()
    stat.score = 120
    db.commit()
    rows = build_impact_rows_for_match(db, match.id)
    a1 = next(r for r in rows if r.match_player_id == players["A1"].id
              and r.round_id == stat.round_id)
    assert a1.damage == round(30 * 1.25)
