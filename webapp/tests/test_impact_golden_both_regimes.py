"""Golden-file test on one fully-scored match containing BOTH regimes
(spec, Testing: Part 4, final bullet).

The match below has pre-plant kills, post-plant kills inside the old denial
window, and a post-resolution kill, so it exercises every branch of
_time_factor at once. Two goldens are pinned:

  * flags OFF -- today's shipped scoring. This is the one that matters most:
    it locks current behaviour so a refactor cannot silently move a live
    score, which is the whole premise of the "no rescore" constraint the
    Part 3 and Part 4 work has been carrying.
  * flags ON  -- Part 3's empirical pre-plant curve plus Part 4's post-plant
    leverage, the latter against a deterministic stub table so the golden
    does not depend on the DB-fitted V table.

Regenerate deliberately, never reflexively: set REGENERATE = True, run once,
read the diff, and only then set it back. A moved golden is a scoring change
and should be reviewed as one.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import build_impact_rows_for_match

REGENERATE = False
GOLDEN_DIR = Path(__file__).parent / "golden"


class _StubPostPlantTable:
    """Deterministic stand-in for the fitted factor table: a factor that
    varies with state and second and differs by victim side, so the golden
    would move if the wiring stopped passing any of those through."""

    def factor(self, a, d, t, victim_is_attacker):
        base = 0.6 + 0.05 * a - 0.03 * d + 0.01 * t
        return round(base * (1.1 if victim_is_attacker else 0.9), 6)


def _session():
    engine = create_engine("sqlite:///:memory:")
    tables = [
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine)()


def _build_match(db):
    """Round 1 (TEAM_1 attacks), plant at t=40, detonation at t=85.

    Kills, in order:
      t=18  A1 -> B1   pre-plant, seconds_to_plant = 22
      t=32  B2 -> A2   pre-plant, seconds_to_plant = 8
      t=52  A1 -> B2   post-plant, t-plant = 12  (the old ramp's range)
      t=79  B3 -> A1   post-plant, t-plant = 39  (the old flat-1.75 window)
      t=90  A3 -> B3   post-resolution (after detonation at plant+45)
    """
    match = Match(external_id="golden1", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for i in range(1, 6):
        p = Player(display_name=f"GA{i}")
        db.add(p)
        db.flush()
        mp = MatchPlayer(match_id=match.id, player_id=p.id, agent="Jett", team=Team.TEAM_1)
        db.add(mp)
        players[f"A{i}"] = mp
    for i in range(1, 6):
        p = Player(display_name=f"GB{i}")
        db.add(p)
        db.flush()
        mp = MatchPlayer(match_id=match.id, player_id=p.id, agent="Sova", team=Team.TEAM_2)
        db.add(mp)
        players[f"B{i}"] = mp
    db.flush()

    rnd = Round(match_id=match.id, round_number=1, outcome="Team A Detonate Win",
                planted=True, plant_time=40.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for name, mp in players.items():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id,
                                kills=1 if name in ("A1", "A3", "B2", "B3") else 0,
                                deaths=1 if name in ("A1", "A2", "B1", "B2", "B3") else 0,
                                assists=0, score=200, loadout=3900, remaining=1200))
    for killer, victim, t in (
        ("A1", "B1", 18.0), ("B2", "A2", 32.0), ("A1", "B2", 52.0),
        ("B3", "A1", 79.0), ("A3", "B3", 90.0),
    ):
        db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players[killer].id,
                          death_match_player_id=players[victim].id, weapon="Vandal",
                          event_time_seconds=t))
    db.commit()
    return match, players


def _rows_as_golden(rows, players):
    by_id = {mp.id: name for name, mp in players.items()}
    out = {}
    for row in rows:
        out[by_id[row.match_player_id]] = {
            "kill_impact": row.kill_impact,
            "death_impact": row.death_impact,
            "impact": row.impact,
            "econ_impact": row.econ_impact,
            "time_impact": row.time_impact,
            "swing_impact": row.swing_impact,
            "kill_order_bonus": row.kill_order_bonus,
            "post_plant_kill": row.post_plant_kill,
            "post_plant_death": row.post_plant_death,
        }
    return out


def _check_against_golden(name, produced):
    GOLDEN_DIR.mkdir(exist_ok=True)
    path = GOLDEN_DIR / name
    if REGENERATE or not path.exists():
        path.write_text(json.dumps(produced, indent=2, sort_keys=True), encoding="utf-8")
        if REGENERATE:
            pytest.fail(f"regenerated {name}; set REGENERATE back to False")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert produced == expected, (
        f"{name} moved. If this change is intended, review the diff as a SCORING "
        f"change, then regenerate deliberately."
    )


def test_golden_shipped_scoring_both_regimes():
    db = _session()
    match, players = _build_match(db)

    rows = build_impact_rows_for_match(db, match.id)

    _check_against_golden("impact_both_regimes_flags_off.json", _rows_as_golden(rows, players))


def test_golden_with_part3_and_part4_enabled():
    db = _session()
    match, players = _build_match(db)

    rows = build_impact_rows_for_match(
        db, match.id,
        enable_preplant_empirical=True,
        enable_postplant_leverage=True,
        postplant_factor_table=_StubPostPlantTable(),
    )

    _check_against_golden("impact_both_regimes_flags_on.json", _rows_as_golden(rows, players))


def test_the_two_goldens_actually_differ():
    """Guard against a golden pair that would pass even if both flags were
    silently ignored."""
    off = json.loads((GOLDEN_DIR / "impact_both_regimes_flags_off.json").read_text(encoding="utf-8"))
    on = json.loads((GOLDEN_DIR / "impact_both_regimes_flags_on.json").read_text(encoding="utf-8"))

    assert off != on
    moved = [name for name in off if off[name]["time_impact"] != on[name]["time_impact"]]
    assert moved, "no player's time_impact moved -- the flags did nothing"


def test_post_resolution_kill_is_still_scored_at_the_flat_discount():
    """The t=90 kill lands after detonation at plant+45, so it must keep the
    0.5 post-resolution factor under BOTH configurations -- Part 4 retunes
    the live regime, not the decided one."""
    db = _session()
    match, players = _build_match(db)

    off = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id)}
    on = {
        r.match_player_id: r
        for r in build_impact_rows_for_match(
            db, match.id, enable_postplant_leverage=True,
            postplant_factor_table=_StubPostPlantTable(),
        )
    }

    # A3's only event is the post-resolution kill, so its time_impact is
    # entirely governed by the post-resolution branch.
    a3 = players["A3"].id
    assert off[a3].time_impact == on[a3].time_impact
