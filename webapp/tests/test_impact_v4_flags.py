"""Impact v4's two flags (plan 2026-09-21-impact-v4, sections 2.2 and 2.3).

  enable_decided_only_time      T = 1 for every kill and death, 0 once the
                                round is decided (declaration 12, arm N)
  remove_post_decided_assists   an assist on a kill made after the round was
                                decided leaves the assists component (arm N+A)

Both default OFF. Scored round 5 is regulation, so TEAM_1 ("A") attacks.
"""
import dataclasses

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring import impact
from app.scoring.impact import _time_factor, build_impact_rows_for_match
from app.scoring.impact_config import ImpactScoringConfig
from app.scoring.impact_manifest import COMPARATORS, RC3

SCORED_ROUND = 5
D = 100.0


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    return sessionmaker(bind=engine)()


def _build(kills, round_kw=None, assists=None, names=None):
    """kills: (killer, victim, seconds, assistants or None); killer None =
    environmental. round_kw: SCORED_ROUND's plant/defuse/outcome. assists:
    {player: SCORED_ROUND assist count}. names: {player: display_name}."""
    db = _session()
    match = Match(external_id="v4", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    players = {}
    for side, team in (("A", Team.TEAM_1), ("B", Team.TEAM_2)):
        for i in range(1, 6):
            key = f"{side}{i}"
            player = Player(display_name=(names or {}).get(key, f"{side}{i}#tag"))
            db.add(player)
            db.flush()
            mp = MatchPlayer(match_id=match.id, player_id=player.id, agent="Jett", team=team)
            db.add(mp)
            players[key] = mp
    db.flush()
    scored = None
    for number in range(1, 9):
        kw = dict(outcome="Team A Elimination Win", planted=False, plant_time=None,
                  exploded=False, defused=False, defuse_time=None)
        if number == SCORED_ROUND:
            kw.update(round_kw or {})
        rnd = Round(match_id=match.id, round_number=number, **kw)
        db.add(rnd)
        db.flush()
        for name, mp in players.items():
            n = (assists or {}).get(name, 0) if number == SCORED_ROUND else 0
            db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mp.id, score=300, kills=1,
                                   deaths=1, assists=n, loadout=3900, remaining=1000))
        if number == SCORED_ROUND:
            scored = rnd
            for killer, victim, seconds, assistants in kills:
                db.add(KillEvent(
                    round_id=rnd.id,
                    killer_match_player_id=players[killer].id if killer else None,
                    death_match_player_id=players[victim].id,
                    weapon="Vandal", event_time_seconds=seconds,
                    source_meta=None if assistants is None else {"assistants": assistants},
                ))
    db.commit()
    return db, match, players, scored


def _rc3(**flags):
    kw = COMPARATORS[RC3].build_kwargs()
    kw.update(flags)
    return kw


def _score(kills, round_kw=None, assists=None, names=None, **flags):
    """({player: SCORED_ROUND row}, [(kill, context)] for SCORED_ROUND)."""
    db, match, players, scored = _build(kills, round_kw, assists, names)
    seen = []

    def observer(round_number, kill_index, kill, context):
        if round_number == SCORED_ROUND:
            seen.append((dict(kill), dict(context)))

    rows = build_impact_rows_for_match(db, match.id, kill_observer=observer, **_rc3(**flags))
    by_mp = {mp.id: name for name, mp in players.items()}
    return {by_mp[r.match_player_id]: r for r in rows if r.round_id == scored.id}, seen


PLANTED = dict(planted=True, plant_time=30.0, exploded=True, outcome="Team A Detonate Win")
DEFUSED = dict(planted=True, plant_time=30.0, defused=True, defuse_time=60.0,
               outcome="Team B Defuse Win")


# -- 2.2: the configuration ------------------------------------------------------------

def test_both_flags_default_off_and_pass_through_build_kwargs():
    config = ImpactScoringConfig("x")
    assert config.enable_decided_only_time is False
    assert config.remove_post_decided_assists is False
    on = ImpactScoringConfig("y", enable_decided_only_time=True, remove_post_decided_assists=True)
    assert on.build_kwargs()["enable_decided_only_time"] is True
    assert on.build_kwargs()["remove_post_decided_assists"] is True


def test_flags_off_build_kwargs_are_exactly_the_pre_v4_kwargs():
    # Emitted only when True, like the manifest (plan R5.2): an rc3 config's
    # kwargs -- which the K-chain compares against explicit kwargs -- are the
    # same dict they were before the flags existed.
    kw = COMPARATORS[RC3].build_kwargs()
    assert set(kw) == {"use_realized_swing", "enable_econ_component", "econ_model",
                       "weights", "enable_trade_credit"}
    only_n = ImpactScoringConfig("n", enable_decided_only_time=True).build_kwargs()
    assert "remove_post_decided_assists" not in only_n


@pytest.mark.parametrize("legacy", ["enable_preplant_empirical", "enable_postplant_leverage"])
def test_decided_only_time_with_a_legacy_timing_flag_raises(legacy):
    with pytest.raises(ValueError, match="enable_decided_only_time"):
        ImpactScoringConfig("x", enable_decided_only_time=True, **{legacy: True})


def test_remove_post_decided_assists_is_allowed_on_its_own():
    kw = ImpactScoringConfig("x", remove_post_decided_assists=True).build_kwargs()
    assert kw["remove_post_decided_assists"] is True
    assert "enable_decided_only_time" not in kw


# -- 2.3: the time factor -----------------------------------------------------------

@pytest.mark.parametrize("for_death", [False, True])
@pytest.mark.parametrize("t, legacy", [(20.0, 1), (40.0, 1 + 10 / 53), (69.0, None)])
def test_decided_only_time_is_1_while_live(for_death, t, legacy):
    r = Round(round_number=5, **PLANTED)
    if legacy is None:   # plant+39: the override window
        legacy = 0.5 if for_death else 1.75
    assert _time_factor(r, t, for_death=for_death) == pytest.approx(legacy)
    assert _time_factor(r, t, for_death=for_death, decided_only=True) == 1.0


@pytest.mark.parametrize("for_death", [False, True])
def test_decided_only_time_is_0_after_a_defuse_where_rc3_paid_half(for_death):
    r = Round(round_number=5, **DEFUSED)
    assert _time_factor(r, 61.0, for_death=for_death) == 0.5
    assert _time_factor(r, 61.0, for_death=for_death, decided_only=True) == 0.0
    assert _time_factor(r, 59.0, for_death=for_death, decided_only=True) == 1.0


def test_decided_only_time_is_0_at_exactly_plant_plus_45_without_an_exploded_flag():
    # rc3's override window includes plant+45, so rc3 paid 1.75 here.
    r = Round(round_number=5, planted=True, plant_time=30.0, exploded=False,
              outcome="Team A Elimination Win")
    assert _time_factor(r, 75.0) == 1.75
    assert _time_factor(r, 75.0, decided_only=True) == 0.0


def test_decided_only_time_wins_over_the_legacy_exploded_branch():
    r = Round(round_number=5, **PLANTED)
    assert _time_factor(r, 80.0) == 0.5
    assert _time_factor(r, 80.0, decided_only=True) == 0.0


@pytest.mark.parametrize("t", [20.0, 40.0, 69.0])
def test_scored_kill_and_death_carry_k_times_1_while_live(t):
    _, seen = _score([("A1", "B1", t, None)], PLANTED, enable_decided_only_time=True)
    (kill, context), = seen
    k = context["kill_order_bonus_raw"]
    assert kill["kill_order_bonus_x_time"] == k * 1.0
    assert kill["death_order_bonus_x_time"] == kill["death_order_bonus"] * 1.0


def test_scored_kill_and_death_carry_nothing_after_a_defuse():
    rows, seen = _score([("A1", "B1", 61.0, None)], DEFUSED, enable_decided_only_time=True)
    (kill, _), = seen
    assert kill["kill_order_bonus_x_time"] == 0.0
    assert kill["death_order_bonus_x_time"] == 0.0
    assert rows["A1"].time_impact == 0 and rows["B1"].time_impact == 0
    assert rows["A1"].post_plant_kill == 0


def test_a_decided_trade_kill_credits_nothing():
    # B1 kills A1 at 58 (live); A2 trades B1 at 61, after the defuse at 60.
    kills = [("B1", "A1", 58.0, None), ("A2", "B1", 61.0, None)]
    on, _ = _score(kills, DEFUSED, enable_decided_only_time=True)
    off, _ = _score(kills, DEFUSED)
    assert off["A1"].trade_credit > 0          # rc3 credited half the trade kill
    assert on["A1"].trade_credit == 0
    # and a trade while live still credits
    live, _ = _score([("B1", "A1", 50.0, None), ("A2", "B1", 51.0, None)], DEFUSED,
                     enable_decided_only_time=True)
    assert live["A1"].trade_credit > 0


@pytest.mark.parametrize("environmental", [False, True])
@pytest.mark.parametrize("t, decided", [(59.0, False), (61.0, True)])
def test_self_kill_and_environmental_death_follow_the_timing_rule(environmental, t, decided):
    killer = None if environmental else "A1"
    rows, seen = _score([(killer, "A1", t, None)], DEFUSED, enable_decided_only_time=True)
    (kill, context), = seen
    assert context["self_kill"] is True
    # The KILL credit of a self-kill stays zero, as the caller already does.
    assert kill["kill_order_bonus_x_time"] == 0
    # The DEATH costs K * traded(=1 for a self-kill) * T.
    expected = 0.0 if decided else kill["death_order_bonus"] * 1.0
    assert kill["death_order_bonus_x_time"] == expected
    assert kill["death_order_bonus"] == context["kill_order_bonus_raw"]


def test_decided_only_time_in_the_scorer_with_a_legacy_timing_flag_raises():
    db, match, _, _ = _build([("A1", "B1", 20.0, None)])
    with pytest.raises(ValueError, match="enable_decided_only_time"):
        build_impact_rows_for_match(db, match.id, enable_decided_only_time=True,
                                    enable_preplant_empirical=True)
    with pytest.raises(ValueError, match="enable_decided_only_time"):
        build_impact_rows_for_match(db, match.id, enable_decided_only_time=True,
                                    enable_postplant_leverage=True, postplant_factor_table=object())


# -- flags off: bit-identical ----------------------------------------------------------

FIXTURES = [
    ([("A1", "B1", 20.0, ["A2#tag"]), ("B2", "A1", 40.0, None)], PLANTED, {"A2": 1}),
    ([("B1", "A1", 58.0, ["B2#tag"]), ("A2", "B1", 61.0, ["A3#tag"]),
      (None, "B3", 62.0, None)], DEFUSED, {"A3": 1, "B2": 1}),
    ([("A1", "B1", 101.0, ["A2#tag"])], dict(outcome="Team B Time Win"), {"A2": 1}),
]


@pytest.mark.parametrize("kills, round_kw, assists", FIXTURES)
def test_flags_off_rows_are_bit_identical(kills, round_kw, assists):
    db, match, _, _ = _build(kills, round_kw, assists)
    base = build_impact_rows_for_match(db, match.id, **COMPARATORS[RC3].build_kwargs())
    explicit = build_impact_rows_for_match(db, match.id, **_rc3(
        enable_decided_only_time=False, remove_post_decided_assists=False))
    assert base == explicit
    # and the legacy formula too
    assert (build_impact_rows_for_match(db, match.id)
            == build_impact_rows_for_match(db, match.id, enable_decided_only_time=False,
                                           remove_post_decided_assists=False))


@pytest.mark.parametrize("kills, round_kw, assists", FIXTURES)
def test_flags_off_observer_context_carries_no_new_keys(kills, round_kw, assists):
    _, seen = _score(kills, round_kw, assists)
    for kill, context in seen:
        assert "post_decided_assists" not in context


# -- 2.3: assists -----------------------------------------------------------------------

def test_an_assist_on_a_decided_kill_is_removed_from_the_assists_component_only():
    kills = [("A1", "B1", 61.0, ["A2#tag"])]
    on, seen = _score(kills, DEFUSED, {"A2": 2}, remove_post_decided_assists=True)
    off, _ = _score(kills, DEFUSED, {"A2": 2})
    assert off["A2"].assists_component == round(D * 2)
    assert on["A2"].assists_component == round(D * 1)
    assert on["A2"].impact == off["A2"].impact - round(D)
    assert on["A2"].kill_impact == off["A2"].kill_impact - round(D)
    assert on["A2"].damage == off["A2"].damage          # damage keeps its combat-score assist points
    (_, context), = seen
    assert context["post_decided_assists"] == {
        "decided": True, "removed": [on["A2"].match_player_id], "clamped": [],
        "unmapped": [], "ambiguous": []}


def test_an_assist_on_a_live_kill_stays():
    kills = [("A1", "B1", 59.0, ["A2#tag"])]
    on, seen = _score(kills, DEFUSED, {"A2": 1}, remove_post_decided_assists=True)
    assert on["A2"].assists_component == round(D)
    (_, context), = seen
    assert context["post_decided_assists"]["decided"] is False
    assert context["post_decided_assists"]["removed"] == []


def test_names_map_case_insensitively():
    on, _ = _score([("A1", "B1", 61.0, ["a2#TAG"])], DEFUSED, {"A2": 1},
                   remove_post_decided_assists=True)
    assert on["A2"].assists_component == 0


def test_the_clamp_never_takes_the_component_below_zero():
    # Two decided kills credit A2, but the scoreboard says one assist.
    kills = [("A1", "B1", 61.0, ["A2#tag"]), ("A3", "B2", 62.0, ["A2#tag"])]
    on, seen = _score(kills, DEFUSED, {"A2": 1}, remove_post_decided_assists=True)
    assert on["A2"].assists_component == 0
    mp = on["A2"].match_player_id
    assert seen[0][1]["post_decided_assists"]["removed"] == [mp]
    assert seen[1][1]["post_decided_assists"]["removed"] == []
    assert seen[1][1]["post_decided_assists"]["clamped"] == [mp]


def test_unmapped_names_are_reported_and_left_in():
    on, seen = _score([("A1", "B1", 61.0, ["Nobody#0000", "A2#tag"])], DEFUSED, {"A2": 1},
                      remove_post_decided_assists=True)
    (_, context), = seen
    assert context["post_decided_assists"]["unmapped"] == ["Nobody#0000"]
    assert on["A2"].assists_component == 0


def test_ambiguous_names_are_reported_and_left_in():
    names = {"A2": "Twin#1", "B2": "twin#1"}
    on, seen = _score([("A1", "B1", 61.0, ["TWIN#1"])], DEFUSED, {"A2": 1, "B2": 1},
                      names=names, remove_post_decided_assists=True)
    (_, context), = seen
    assert context["post_decided_assists"]["ambiguous"] == ["TWIN#1"]
    assert on["A2"].assists_component == round(D)
    assert on["B2"].assists_component == round(D)


def test_demo_pipeline_matches_have_no_assistants_and_lose_nothing():
    kills = [("A1", "B1", 61.0, None), ("A3", "B2", 62.0, None)]
    on, _ = _score(kills, DEFUSED, {"A2": 2}, remove_post_decided_assists=True)
    off, _ = _score(kills, DEFUSED, {"A2": 2})
    assert on == off


def test_assists_removal_leaves_the_time_factor_alone():
    kills = [("A1", "B1", 61.0, ["A2#tag"])]
    on, _ = _score(kills, DEFUSED, {"A2": 1}, remove_post_decided_assists=True)
    off, _ = _score(kills, DEFUSED, {"A2": 1})
    for name in on:
        if name != "A2":
            assert on[name] == off[name]
    assert dataclasses.replace(on["A2"], assists_component=0, impact=0, kill_impact=0) == \
        dataclasses.replace(off["A2"], assists_component=0, impact=0, kill_impact=0)


def test_both_flags_together_reconcile():
    kills = [("B1", "A1", 58.0, ["B2#tag"]), ("A2", "B1", 61.0, ["A3#tag"])]
    on, _ = _score(kills, DEFUSED, {"A3": 1, "B2": 1},
                   enable_decided_only_time=True, remove_post_decided_assists=True)
    for row in on.values():
        assert row.impact == row.damage + row.leverage_component + row.econ_component + row.assists_component
    assert on["A3"].assists_component == 0
    assert on["B2"].assists_component == round(D)


# -- the persisted semantics stay what the model docstring says ---------------------------

def test_time_impact_is_unweighted_net_k_times_t_including_trade_credit():
    kills = [("B1", "A1", 50.0, None), ("A2", "B1", 51.0, None)]
    on, seen = _score(kills, DEFUSED, enable_decided_only_time=True)
    # A1 dies at 50 and is traded 1.0s later: the 54% share of the trade kill.
    trade_kill_x_time = seen[1][0]["kill_order_bonus_x_time"]
    a1_death_x_time = seen[0][0]["death_order_bonus_x_time"]
    assert a1_death_x_time > 0 and trade_kill_x_time > 0
    assert on["A1"].time_impact == round(0.54 * trade_kill_x_time - a1_death_x_time)


def test_impact_version_is_unchanged_on_this_branch():
    assert impact.IMPACT_CALCULATION_VERSION == 3
