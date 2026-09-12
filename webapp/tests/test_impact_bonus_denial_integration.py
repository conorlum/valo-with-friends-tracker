"""The bonus-denial model through build_impact_rows_for_match (spec 2026-09-12 section 8).

Round 1: Team A wins the pistol. Round 2: B1 kills A1 (Jett, loadout 2600 -> kit
2050) and Team B wins, so A lost round 2 -> factor 1.0, reward into round 3 is 1900.
Survivors A2-A5: cash 1000 + 1900 = 2900; surplus (1000+3900) - (2900+3900) = -1900: no recovery.
    B1 credit 1.0 * 1.10 * 2050 / 19500 * 1007.9209 = 116.56 -> +117
    A1 debit  0.8 * that                            =  93.25 ->  -93
"""
from app.models import KillEvent
from app.scoring import econ_buy_disruption as bd
from app.scoring.impact import build_impact_rows_for_match
from tests.buy_disruption_fixtures import build_match, round_id, rows_for_round, session

NEW = dict(use_realized_swing=True, enable_econ_component=True, econ_model=bd.MODEL_V2_30_80_BONUS_DENIAL)
OLD = dict(use_realized_swing=True, enable_econ_component=True, econ_model=bd.MODEL_V2_30_80)


def _match(db, external_id="bonus"):
    return build_match(db, external_id, kills={2: [("B1", "A1", 10.0)]},
                       weapons={2: ["Spectre"]}, loadouts={2: {"A1": 2600}},
                       outcomes={2: "Team B Elimination Win"}, count_stats=True)


def test_bonus_denial_values_through_the_build_path():
    db = session()
    match, players, _ = _match(db)
    econ = []
    rows = build_impact_rows_for_match(db, match.id, econ_observer=lambda **kw: econ.append(kw), **NEW)
    r2 = rows_for_round(rows, round_id(db, match, 2))
    assert r2[players["B1"].id].econ_component == 117
    assert r2[players["A1"].id].econ_component == -93
    result = next(kw["result"] for kw in econ if kw["round_number"] == 2)
    assert result.audit_version == 2 and result.abstention is None
    assert result.teams[players["A1"].team].bonus.denied == {players["A1"].id: 2050}


def test_other_rounds_match_30_80_through_the_build_path():
    db = session()
    match, _, _ = _match(db, "parity")
    new = {(r.round_id, r.match_player_id): r.econ_component
           for r in build_impact_rows_for_match(db, match.id, **NEW)}
    old = {(r.round_id, r.match_player_id): r.econ_component
           for r in build_impact_rows_for_match(db, match.id, **OLD)}
    r2 = round_id(db, match, 2)
    assert {k: v for k, v in new.items() if k[0] != r2} == {k: v for k, v in old.items() if k[0] != r2}


def test_missing_kill_rows_abstain_rather_than_score_as_no_kills():
    db = session()
    match, _, _ = _match(db, "nokills")
    db.query(KillEvent).delete()
    db.commit()
    econ = []
    build_impact_rows_for_match(db, match.id, econ_observer=lambda **kw: econ.append(kw), **NEW)
    result = next(kw["result"] for kw in econ if kw["round_number"] == 2)
    assert result.abstention == "kill_feed_incomplete"
