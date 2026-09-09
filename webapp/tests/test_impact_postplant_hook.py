"""Part 4 wired into impact.py behind an off-by-default flag.

Spec: docs/superpowers/specs/2026-09-03-plant-window-and-time-factor-design.md,
Part 4 ("Testing: Part 4"). Same in-memory sqlite pattern as
test_impact_kill_order_bonus_net.py.

Dormancy, as in Part 3: enable_postplant_leverage defaults to False and
compute_impact_for_match never passes it, so IMPACT_CALCULATION_VERSION stays
at 1 and every stored score is unchanged.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.impact import _time_factor, build_impact_rows_for_match


class _Round:
    """Minimal stand-in for a Round row, for direct _time_factor tests."""

    def __init__(self, plant_time=30.0, planted=True, exploded=True,
                 defused=False, defuse_time=None, outcome=None):
        self.plant_time = plant_time
        self.planted = planted
        self.exploded = exploded
        self.defused = defused
        self.defuse_time = defuse_time
        # plant_window.is_phantom_plant reads this. None is a genuine plant;
        # pass a "... Time Win" string for a phantom one.
        self.outcome = outcome


class _StubFactorTable:
    """Returns a declared factor, so the wiring can be tested without
    standing up a whole V table."""

    def __init__(self, value=1.4):
        self.value = value
        self.calls = []

    def factor(self, a, d, t, victim_is_attacker):
        self.calls.append((a, d, t, victim_is_attacker))
        return self.value


def test_post_plant_ramp_is_unchanged_when_the_flag_is_off():
    round_row = _Round(plant_time=30.0)

    assert _time_factor(round_row, 40.0) == pytest.approx(1 + 10 / 53)


def test_the_flat_denial_window_override_is_gone_when_the_flag_is_on():
    """The plant+38..45 flat 1.75 override paid both sides the same at the
    moment their stakes are furthest apart. With Part 4 on, a kill at
    plant+39 and one at plant+44 in the same state get the factor table's
    values, not 1.75."""
    round_row = _Round(plant_time=30.0)
    table = _StubFactorTable(value=1.4)

    at_39 = _time_factor(round_row, 69.0, is_attacker=True, alive_counts=(2, 1),
                         postplant_factor_table=table, enable_postplant_leverage=True)

    assert at_39 == pytest.approx(1.4)
    assert at_39 != pytest.approx(1.75)


def test_flag_on_still_returns_half_after_the_round_resolves():
    """Post-resolution stays 0.5, unchanged and unmeasured."""
    round_row = _Round(plant_time=30.0, exploded=True)
    table = _StubFactorTable(value=1.4)

    # plant + 45 = 75.0; a kill at 80 is post-detonation.
    assert _time_factor(round_row, 80.0, is_attacker=True, alive_counts=(2, 1),
                        postplant_factor_table=table,
                        enable_postplant_leverage=True) == 0.5


def test_use_realized_false_changes_nothing_post_plant():
    """The mirror of Part 3's leakage gate: post-plant state is known at kill
    time, so nothing is stripped in ex-ante mode. Must be exact."""
    round_row = _Round(plant_time=30.0)
    table = _StubFactorTable(value=1.4)

    realized = _time_factor(round_row, 40.0, is_attacker=True, alive_counts=(3, 2),
                            postplant_factor_table=table,
                            enable_postplant_leverage=True, use_realized=True)
    ex_ante = _time_factor(round_row, 40.0, is_attacker=True, alive_counts=(3, 2),
                           postplant_factor_table=table,
                           enable_postplant_leverage=True, use_realized=False)

    assert realized == ex_ante == pytest.approx(1.4)


def test_the_victim_side_reaching_the_table_is_the_victims_own_side():
    """D is keyed on which side the VICTIM was on -- that split is the entire
    reason the factor is side-dependent."""
    round_row = _Round(plant_time=30.0)
    table = _StubFactorTable()

    _time_factor(round_row, 40.0, is_attacker=True, alive_counts=(3, 2),
                 postplant_factor_table=table, enable_postplant_leverage=True)
    # is_attacker here describes the KILLER, so the victim is a defender.
    assert table.calls[-1] == (3, 2, 10, False)

    _time_factor(round_row, 40.0, is_attacker=False, alive_counts=(3, 2),
                 postplant_factor_table=table, enable_postplant_leverage=True)
    assert table.calls[-1] == (3, 2, 10, True)


def test_timestamp_is_floored_to_a_whole_second():
    """The table is indexed by floor(t)."""
    round_row = _Round(plant_time=30.0)
    table = _StubFactorTable()

    _time_factor(round_row, 41.9, is_attacker=True, alive_counts=(3, 2),
                 postplant_factor_table=table, enable_postplant_leverage=True)

    assert table.calls[-1][2] == 11


# --------------------------------------------------------------------------
# End to end through build_impact_rows_for_match
# --------------------------------------------------------------------------

_ids = {}


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


def _match_with_postplant_kill(db):
    match = Match(external_id="pph1", source=MatchSource.SCRAPED, map_name="Bind")
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
                planted=True, plant_time=30.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["B1"].id, weapon="Classic",
                      event_time_seconds=40.0))
    db.commit()
    return match, players


def test_flag_defaults_off_end_to_end():
    _ids.clear()
    db = _session()
    match, _ = _match_with_postplant_kill(db)

    default_rows = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id)}
    off_rows = {
        r.match_player_id: r
        for r in build_impact_rows_for_match(db, match.id, enable_postplant_leverage=False)
    }

    for pid, row in default_rows.items():
        assert row.time_impact == off_rows[pid].time_impact


def test_flag_on_changes_the_post_plant_kill_and_its_mirrored_death():
    """Kill and death use the SAME factor for the same event -- the transfer
    is one number, credited to one player and debited from the other."""
    _ids.clear()
    db = _session()
    match, players = _match_with_postplant_kill(db)
    table = _StubFactorTable(value=0.5)

    off = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id)}
    on = {
        r.match_player_id: r
        for r in build_impact_rows_for_match(
            db, match.id, enable_postplant_leverage=True, postplant_factor_table=table,
        )
    }

    killer, victim = players["A1"].id, players["B1"].id
    assert on[killer].time_impact != off[killer].time_impact
    assert on[victim].time_impact != off[victim].time_impact
    # One transfer: the killer's credit is the victim's debit.
    assert on[killer].time_impact == -on[victim].time_impact


# --------------------------------------------------------------------------
# Review finding 5 -- phantom plants must not reach the enabled timing path
# --------------------------------------------------------------------------


def test_a_phantom_plant_does_not_reach_the_part_4_table():
    """A planted round that ended in a Time Win never armed for real.
    extract_postplant_kills drops those rounds from the fit, so the factor
    table has nothing to say about them and the centring constant does not
    cover them. The event falls back to the legacy ramp -- the same value
    arm 0 gives it -- rather than being scored by a curve trained on real
    plants."""
    phantom = _Round(plant_time=30.0, outcome="Team A Time Win")
    table = _StubFactorTable(value=1.4)

    factor = _time_factor(phantom, 40.0, is_attacker=True, alive_counts=(3, 2),
                          postplant_factor_table=table,
                          enable_postplant_leverage=True)

    assert table.calls == []
    assert factor == pytest.approx(1 + 10 / 53)


def test_a_genuine_plant_still_reaches_the_table_unchanged():
    """The guard above must be exactly the phantom exclusion and nothing
    wider: a real planted round is unaffected."""
    genuine = _Round(plant_time=30.0, outcome="Team A Detonate Win")
    table = _StubFactorTable(value=1.4)

    factor = _time_factor(genuine, 40.0, is_attacker=True, alive_counts=(3, 2),
                          postplant_factor_table=table,
                          enable_postplant_leverage=True)

    assert table.calls == [(3, 2, 10, False)]
    assert factor == pytest.approx(1.4)


def test_a_phantom_plant_does_not_reach_the_pre_plant_empirical_curve():
    """The same exclusion on the pre-plant half: seconds-to-plant against a
    plant that never armed is not a distance the curve was fitted over. The
    curve's own population is 'non-self pre-plant kills in a NON-PHANTOM
    round'. Falls back to the flat legacy 1.0."""
    phantom = _Round(plant_time=30.0, outcome="Team B Time Win")

    factor = _time_factor(phantom, 20.0, is_attacker=True,
                          enable_preplant_empirical=True)

    assert factor == 1


def test_a_genuine_plant_still_reaches_the_pre_plant_empirical_curve():
    genuine = _Round(plant_time=30.0, outcome="Team A Detonate Win")

    factor = _time_factor(genuine, 20.0, is_attacker=True,
                          enable_preplant_empirical=True)

    assert factor != 1


# --------------------------------------------------------------------------
# Review finding 6 -- a post-plant self-death must not take the opposite
# side's curve
# --------------------------------------------------------------------------


def test_a_post_plant_self_death_does_not_reach_the_part_4_table():
    """On a self-kill the killer and the victim are the SAME player, so
    Part 4's `victim_is_attacker = not is_attacker` names the wrong side.
    extract_postplant_kills excludes self-kills from the fit for the same
    reason the centring constant excludes them, so the honest answer is the
    legacy factor, not a mis-sided table lookup."""
    round_row = _Round(plant_time=30.0, outcome="Team A Detonate Win")
    table = _StubFactorTable(value=1.4)

    factor = _time_factor(round_row, 40.0, for_death=True, is_attacker=True,
                          alive_counts=(3, 2), postplant_factor_table=table,
                          enable_postplant_leverage=True, self_kill=True)

    assert table.calls == []
    assert factor == pytest.approx(1 + 10 / 53)


def test_a_self_death_never_asks_the_table_for_the_opposite_side():
    """The specific defect: were the self-kill routed to the table at all, it
    would arrive keyed on victim_is_attacker=False for an ATTACKER who killed
    themselves. No lookup at all is the fix; this asserts the wrong lookup in
    particular never happens, for either side."""
    round_row = _Round(plant_time=30.0, outcome="Team A Detonate Win")
    table = _StubFactorTable(value=1.4)

    for is_attacker in (True, False):
        _time_factor(round_row, 40.0, for_death=True, is_attacker=is_attacker,
                     alive_counts=(3, 2), postplant_factor_table=table,
                     enable_postplant_leverage=True, self_kill=True)

    assert table.calls == []


def test_an_enemy_death_is_unaffected_by_the_self_kill_guard():
    """The surrounding convention is deliberate and must survive: a death
    references the same event and side the kill-side scalar used, which is the
    KILLER's side, so the victim is the other side."""
    round_row = _Round(plant_time=30.0, outcome="Team A Detonate Win")
    table = _StubFactorTable(value=1.4)

    factor = _time_factor(round_row, 40.0, for_death=True, is_attacker=True,
                          alive_counts=(3, 2), postplant_factor_table=table,
                          enable_postplant_leverage=True, self_kill=False)

    assert table.calls == [(3, 2, 10, False)]
    assert factor == pytest.approx(1.4)


def _match_with_postplant_self_kill(db):
    """Same shape as _match_with_postplant_kill, but A1 kills themselves
    after the plant."""
    match = Match(external_id="pph2", source=MatchSource.SCRAPED, map_name="Bind")
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
                planted=True, plant_time=30.0, exploded=True, defused=False)
    db.add(rnd)
    db.flush()
    for mp in players.values():
        db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, kills=0, deaths=0,
                                assists=0, score=0, loadout=800, remaining=0))
    db.add(KillEvent(round_id=rnd.id, killer_match_player_id=players["A1"].id,
                      death_match_player_id=players["A1"].id, weapon="Classic",
                      event_time_seconds=40.0))
    db.commit()
    return match, players


def test_a_post_plant_self_death_scores_the_same_with_the_flag_on_end_to_end():
    """End to end: turning Part 4 on must not move a self-death's time
    impact, because Part 4 has no estimate for one."""
    _ids.clear()
    db = _session()
    match, players = _match_with_postplant_self_kill(db)
    table = _StubFactorTable(value=0.5)

    off = {r.match_player_id: r for r in build_impact_rows_for_match(db, match.id)}
    on = {
        r.match_player_id: r
        for r in build_impact_rows_for_match(
            db, match.id, enable_postplant_leverage=True, postplant_factor_table=table,
        )
    }

    assert table.calls == []
    victim = players["A1"].id
    assert on[victim].time_impact == off[victim].time_impact
