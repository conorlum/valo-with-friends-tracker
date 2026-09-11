"""The buy-disruption econ model wired through build_impact_rows_for_match.

Implementation plan sections 3-4 and plan-review finding P1: inputs must be
validated and routed BEFORE the legacy economy-differential, swing and
combat-decoration loops, because those loops index killer IDs and stats
before any econ helper runs. Pure calculator tests cannot prove that, so
every case here goes through the real build path, and the persistence cases
through compute_impact_for_match.

Hand-derived values used below (ECON_SCALE = 1007.9209, C = 1):
    a 3900 enemy kill against an ABSORBING team      credit 0.02  -> +20
    its victim's debit at 30%                        0.006        -> -6
Team B broke next round (every next loadout and bank 0), two 3900 first
losses (L = 7800, D = G = 19500, severity pool = 7800):
    each enemy kill credit  0.02 + 7800/19500 * 3900/7800 = 0.22  -> +222
    each victim debit at 80%  0.8 * 0.22 = 0.176                   -> -177
"""
import pytest

from app.models import ImpactScore
from app.scoring import econ_buy_disruption as bd
from app.scoring.econ_component import ECON_SCALE
from app.scoring.impact import (
    FormulaWeights,
    ImpactInputError,
    _PERSISTED_FIELDS,
    build_impact_rows_for_match,
    compute_impact_for_match,
)
from app.scoring.impact_config import ImpactScoringConfig
from tests.buy_disruption_fixtures import build_match, round_id, rows_for_round, session, stat

B_BROKE_NEXT = {8: {f"B{i}": 0 for i in range(1, 6)}}
CANDIDATE = dict(use_realized_swing=True, enable_econ_component=True, econ_model=bd.MODEL_V2_30_80)
COMBAT_FIELDS = ("damage", "leverage_component", "time_impact", "kill_impact", "death_impact",
                 "kill_order_bonus", "clutch_kill", "clutch_death", "traded_teammate",
                 "traded_by_teammate", "trade_detail")


def _environmental_match(db, external_id="env"):
    return build_match(
        db, external_id,
        kills={7: [("A1", "B1", 10.0), (None, "B2", 20.0), ("B3", "A3", 30.0)]},
        loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT,
    )


def _collect(db, match, **kwargs):
    kills, econ = [], []
    rows = build_impact_rows_for_match(
        db, match.id, kill_observer=lambda **kw: kills.append(kw),
        econ_observer=lambda **kw: econ.append(kw), **kwargs)
    return rows, kills, econ


# ---- configuration identity ----------------------------------------------------------

def test_default_econ_model_is_still_the_separate_legacy_allocator():
    db = session()
    match, _, _ = build_match(db, "dflt", kills={7: [("A1", "B1", 10.0)]}, loadouts=B_BROKE_NEXT)
    implicit = build_impact_rows_for_match(db, match.id, enable_econ_component=True)
    explicit = build_impact_rows_for_match(db, match.id, enable_econ_component=True,
                                           econ_model=bd.MODEL_SEPARATE_ECON_LEGACY)
    assert implicit == explicit
    assert build_impact_rows_for_match(db, match.id) == build_impact_rows_for_match(
        db, match.id, enable_econ_component=False)


@pytest.mark.parametrize("kwargs", [
    dict(econ_model=bd.MODEL_V2_30_80),                                    # model without the structure
    dict(enable_econ_component=True, econ_model="buy_disruption_v9"),      # unknown model
    dict(enable_econ_component=True, econ_model=bd.MODEL_V2_30_80, neutralize_econ_terms=True),
])
def test_invalid_model_and_flag_combinations_are_rejected(kwargs):
    db = session()
    match, _, _ = build_match(db, "bad", kills={7: [("A1", "B1", 10.0)]})
    with pytest.raises(ValueError):
        build_impact_rows_for_match(db, match.id, **kwargs)


# ---- scoring through the build path ---------------------------------------------------

def test_rows_carry_the_calculators_signed_net_rounded_once():
    db = session()
    match, players, _ = build_match(db, "net", kills={7: [("A1", "B1", 10.0), ("A2", "B2", 20.0)]},
                                    loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT)
    rows, _, econ = _collect(db, match, **CANDIDATE)
    r7 = rows_for_round(rows, round_id(db, match, 7))
    assert r7[players["A1"].id].econ_component == 222
    assert r7[players["B1"].id].econ_component == -177
    assert r7[players["B3"].id].econ_component == 0
    result = next(kw["result"] for kw in econ if kw["round_number"] == 7)
    assert result.teams[players["B1"].team].penalty_rate == 0.80
    for pid, row in r7.items():
        assert row.econ_component == round(ECON_SCALE * result.raw_net_by_player()[pid])
        assert row.impact == row.damage + row.leverage_component + row.econ_component


def test_c_is_applied_exactly_once_and_rounding_happens_once():
    db = session()
    match, players, _ = build_match(db, "c2", kills={7: [("A1", "B1", 10.0), ("A2", "B2", 20.0)]},
                                    loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT)
    rows = build_impact_rows_for_match(db, match.id, weights=FormulaWeights(econ=2.0), **CANDIDATE)
    victim = rows_for_round(rows, round_id(db, match, 7))[players["B1"].id]
    # -0.176 * 1007.9209 * 2 = -354.79 -> -355. Rounding before C gives -354;
    # applying C twice gives -710.
    assert victim.econ_component == -355


def test_observer_does_not_change_scores_and_ex_ante_is_exactly_zero():
    db = session()
    match, _, _ = _environmental_match(db)
    without = build_impact_rows_for_match(db, match.id, **CANDIDATE)
    with_observers, kills, econ = _collect(db, match, **CANDIDATE)
    assert kills and econ
    assert with_observers == without
    ex_ante = build_impact_rows_for_match(db, match.id, **{**CANDIDATE, "use_realized_swing": False})
    assert all(row.econ_component == 0 for row in ex_ante)


def test_combat_terms_are_identical_to_the_separate_econ_structure():
    db = session()
    match, _, _ = build_match(db, "cmb", kills={5: [("A1", "B1", 10.0), ("B2", "A2", 12.0)],
                                               7: [("A1", "B1", 10.0), ("A2", "B2", 20.0)]},
                              loadouts=B_BROKE_NEXT, scores={7: {"A1": 250, "B2": 90}})
    legacy = build_impact_rows_for_match(db, match.id, enable_econ_component=True)
    candidate = build_impact_rows_for_match(db, match.id, **CANDIDATE)
    assert len(legacy) == len(candidate)
    for old, new in zip(legacy, candidate):
        for field in COMBAT_FIELDS:
            assert getattr(old, field) == getattr(new, field), field


# ---- routing of non-enemy and incomplete events ----------------------------------------

def test_environmental_death_debits_victim_without_crash_or_invented_credit():
    db = session()
    match, players, ids = _environmental_match(db)
    rows, kills, econ = _collect(db, match, **CANDIDATE)
    r7 = rows_for_round(rows, round_id(db, match, 7))

    result = next(kw["result"] for kw in econ if kw["round_number"] == 7)
    assert [e.event_id for e in result.events] == ids[7]
    assert [e.kind for e in result.events] == ["enemy", "unknown_killer", "enemy"]
    assert result.events[1].credit == 0

    # B1 and B2 each lose a 3900 first kit against a broke next buy.
    assert r7[players["A1"].id].econ_component == 222
    assert r7[players["B1"].id].econ_component == -177
    assert r7[players["B2"].id].econ_component == -177
    # A's next buy is funded, so A3's death is absorbed; B3's kill is small.
    assert r7[players["A3"].id].econ_component == -6
    assert r7[players["B3"].id].econ_component == 20
    assert sum(r7[players[n].id].econ_component for n in ("A2", "A4", "A5", "B4", "B5")) == 0


def test_environmental_death_is_kept_for_round_state_and_death_leverage():
    db = session()
    match, players, ids = _environmental_match(db)
    rows, kills, _ = _collect(db, match, **CANDIDATE)
    r7 = rows_for_round(rows, round_id(db, match, 7))
    calls = [kw for kw in kills if kw["round_number"] == 7]
    assert [kw["kill"]["id"] for kw in calls] == ids[7]
    env = calls[1]
    assert env["kill"]["killer_match_player_id"] is None
    assert env["context"]["self_kill"] is True
    # The kill AFTER the environmental death sees Team B down two players.
    assert (calls[2]["context"]["killer_team_alive"], calls[2]["context"]["victim_team_alive"]) == (3, 5)
    victim = r7[players["B2"].id]
    assert victim.death_impact > 0          # the death still costs leverage
    assert victim.kill_impact == victim.damage  # no kill is credited to anyone for it


def test_environmental_death_does_not_crash_the_live_legacy_formula():
    db = session()
    match, players, _ = _environmental_match(db)
    rows = build_impact_rows_for_match(db, match.id, use_realized_swing=True)
    r7 = rows_for_round(rows, round_id(db, match, 7))
    assert r7[players["B2"].id].death_impact > 0
    assert r7[players["B2"].id].kill_impact == r7[players["B2"].id].damage


def test_unknown_victim_is_a_structured_failure_before_persistence():
    db = session()
    match, players, ids = build_match(db, "unk", kills={7: [("A1", None, 10.0)]})
    for kwargs in (CANDIDATE, {}):
        with pytest.raises(ImpactInputError) as caught:
            build_impact_rows_for_match(db, match.id, **kwargs)
        issue = caught.value.issues[0]
        assert caught.value.match_id == match.id
        assert (issue.kind, issue.round_number, issue.event_id) == ("unknown_victim", 7, ids[7][0])
    with pytest.raises(ImpactInputError):
        compute_impact_for_match(db, match.id, config=ImpactScoringConfig("t", **CANDIDATE))
    db.rollback()
    assert db.query(ImpactScore).count() == 0


def test_missing_stats_for_an_event_participant_is_a_structured_failure():
    db = session()
    match, players, ids = build_match(db, "mis", kills={7: [("A1", "B1", 10.0)]})
    db.delete(stat(db, match, players, 7, "A1"))
    db.commit()
    with pytest.raises(ImpactInputError) as caught:
        build_impact_rows_for_match(db, match.id, **CANDIDATE)
    issue = caught.value.issues[0]
    assert (issue.kind, issue.round_number, issue.match_player_id, issue.event_id) == (
        "killer_missing_round_stats", 7, players["A1"].id, ids[7][0])


@pytest.mark.parametrize("missing_round", [7, 8])
def test_missing_stats_for_a_bystander_abstains_econ_but_keeps_combat(missing_round):
    db = session()
    match, players, _ = build_match(db, "bys", kills={7: [("A1", "B1", 10.0)]},
                                    loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT)
    db.delete(stat(db, match, players, missing_round, "B5"))
    db.commit()
    rows, _, econ = _collect(db, match, **CANDIDATE)
    result = next(kw["result"] for kw in econ if kw["round_number"] == 7)
    assert result.abstention == "incomplete_stats"
    r7 = rows_for_round(rows, round_id(db, match, 7))
    assert all(row.econ_component == 0 for row in r7.values())
    assert r7[players["A1"].id].time_impact > 0


# round_player_stats.loadout/remaining are NOT NULL in the schema, so a null
# cannot reach the scorer from the database; the pure calculator tests cover
# None. A negative value can, and must not be read as poverty.
@pytest.mark.parametrize("field", ["remaining", "loadout"])
def test_invalid_next_round_economy_abstains_without_moving_combat(field):
    db = session()
    match, players, _ = build_match(db, "inv", kills={7: [("A1", "B1", 10.0)]},
                                    loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT)
    valid = build_impact_rows_for_match(db, match.id, **CANDIDATE)
    assert any(row.econ_component for row in valid)
    setattr(stat(db, match, players, 8, "B4"), field, -5)
    db.commit()
    rows, _, econ = _collect(db, match, **CANDIDATE)
    result = next(kw["result"] for kw in econ if kw["round_number"] == 7)
    assert result.abstention == "invalid_economy_data"
    assert all(row.econ_component == 0 for row in rows_for_round(rows, round_id(db, match, 7)).values())
    for old, new in zip(valid, rows):
        for field in COMBAT_FIELDS:
            assert getattr(old, field) == getattr(new, field)


def test_event_ids_are_preserved_in_time_then_id_order():
    db = session()
    match, _, ids = build_match(db, "ord", kills={7: [("A2", "B2", 10.0), ("A1", "B1", 10.0),
                                                    ("A3", "B3", 5.0)]})
    _, kills, econ = _collect(db, match, **CANDIDATE)
    expected = [ids[7][2], ids[7][0], ids[7][1]]
    assert [kw["kill"]["id"] for kw in kills if kw["round_number"] == 7] == expected
    result = next(kw["result"] for kw in econ if kw["round_number"] == 7)
    assert [e.event_id for e in result.events] == expected


# ---- persistence, aggregation, display, isolation ------------------------------------------

def test_negative_econ_persists_aggregates_and_displays_signed():
    from app.services.matches import get_match_summary

    db = session(all_tables=True)
    match, players, _ = build_match(db, "per", kills={7: [("A1", "B1", 10.0), ("A2", "B2", 20.0)]},
                                    loadouts=B_BROKE_NEXT, remainings=B_BROKE_NEXT)
    config = ImpactScoringConfig("test-candidate", **CANDIDATE)
    expected = build_impact_rows_for_match(db, match.id, **config.build_kwargs())
    compute_impact_for_match(db, match.id, config=config)

    stored = {(s.round_id, s.match_player_id): s for s in db.query(ImpactScore).all()}
    assert len(stored) == len(expected)
    for row in expected:
        for field in _PERSISTED_FIELDS:
            assert getattr(stored[(row.round_id, row.match_player_id)], field) == getattr(row, field)
    b1_rows = [s for s in stored.values() if s.match_player_id == players["B1"].id]
    assert sum(s.econ_component for s in b1_rows) == -177

    summary = {p.match_player_id: p for p in get_match_summary(db, match).players}
    for mp_id, player in summary.items():
        impacts = [s.impact for s in stored.values() if s.match_player_id == mp_id]
        assert player.average_impact == sum(impacts) / len(impacts)

    # Make one player's round far more negative: nobody else's displayed
    # score may move, and that player's moves by exactly the change.
    stored[(round_id(db, match, 7), players["B1"].id)].impact -= 1000
    db.commit()
    after = {p.match_player_id: p for p in get_match_summary(db, match).players}
    for mp_id, player in after.items():
        if mp_id == players["B1"].id:
            assert player.average_impact == pytest.approx(summary[mp_id].average_impact - 1000 / 8)
        else:
            assert player.average_impact == summary[mp_id].average_impact


def test_default_persistence_path_is_unchanged_legacy():
    db = session()
    match, _, _ = build_match(db, "leg", kills={7: [("A1", "B1", 10.0)]}, loadouts=B_BROKE_NEXT)
    expected = build_impact_rows_for_match(db, match.id, use_realized_swing=True)
    compute_impact_for_match(db, match.id)
    stored = {(s.round_id, s.match_player_id): s for s in db.query(ImpactScore).all()}
    for row in expected:
        assert stored[(row.round_id, row.match_player_id)].impact == row.impact
        assert stored[(row.round_id, row.match_player_id)].econ_component == 0


def test_a_matchs_scores_do_not_depend_on_other_matches():
    db = session()
    match, _, _ = build_match(db, "iso", kills={7: [("A1", "B1", 10.0), ("A2", "B2", 20.0)]},
                              loadouts=B_BROKE_NEXT)
    before = build_impact_rows_for_match(db, match.id, **CANDIDATE)
    build_match(db, "other", kills={3: [("B1", "A1", 1.0)], 7: [(None, "A2", 2.0)]},
                loadouts={4: {f"A{i}": 0 for i in range(1, 6)}}, remainings={4: {"A1": 0}})
    assert build_impact_rows_for_match(db, match.id, **CANDIDATE) == before
