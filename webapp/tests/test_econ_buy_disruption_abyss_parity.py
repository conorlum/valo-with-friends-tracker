"""Frozen Abyss 3104 parity: the PRODUCTION calculator against the saved reference.

The artifacts in docs/superpowers/abyss-buy-disruption-review/ are immutable
regression expectations (implementation plan, section 4):

  * calculations.json          -- V2 kill credit + the historical wealth debit
                                  (MODEL_V2_WEALTH), the Abyss -100..-928 column
  * death-penalty-30-80.json   -- identical V2 credit + the owner's 30%/80% debit
                                  (MODEL_V2_30_80)

Acceptance: unrounded values agree within numerical tolerance, and every
rounded player-round net agrees EXACTLY. This is an implementation parity
check, not independent validation of the model -- Abyss helped develop V2.

Two routes are checked: the pure calculator fed from source.json, and the
real build path (build_impact_rows_for_match) fed from the same snapshot
loaded into sqlite. Neither imports the reference implementation in docs/.
"""
import functools
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring import econ_buy_disruption as bd
from app.scoring import econ_component
from app.scoring.agent_economy import free_ability_credits
from app.scoring.impact import build_impact_rows_for_match

REVIEW = Path(__file__).resolve().parents[2] / "docs/superpowers/abyss-buy-disruption-review"

# SHA-256 over LF-normalized bytes, so the pin survives a CRLF checkout.
FROZEN_LF_SHA256 = {
    "source.json": "74271ea043a623646fd9202648779a31c05d011655b05a548427763e3a5011a4",
    "calculations.json": "cd685798389849dbbfd12d857a7cb487fe78a5de8f77c55e4feac6edfb98816c",
    "death-penalty-30-80.json": "6274386bd538a3acd6f22edb9599cdfdfbcdc0d689e1b65c5cc6cf72e63153a1",
}
TOL = dict(rel=1e-12, abs=1e-9)
SCALE = 1007.9209

EXPECTED_30_80_TOTALS = {
    "Osmin#NA1": 708, "DoubleBl1nd#BEEF": 277, "VorteXx#Val": 209, "Helpless#qiqi": 196,
    "ZETA 3y5#213": 194, "ternstyle#GIGI": 148, "NPrightdolphin#NA1": 111, "Najumi#NPC": 49,
    "Mokalover67#ILLIT": 4, "1xgoofy#56719": -101,
}
EXPECTED_30_80_TEAMS = {"TEAM_1": 502, "TEAM_2": 1293}
EXPECTED_V2_TOTALS = {
    "Osmin#NA1": -100, "DoubleBl1nd#BEEF": -525, "VorteXx#Val": -727, "Helpless#qiqi": -761,
    "ZETA 3y5#213": -718, "ternstyle#GIGI": -689, "NPrightdolphin#NA1": -541, "Najumi#NPC": -682,
    "Mokalover67#ILLIT": -923, "1xgoofy#56719": -928,
}


def _load(name):
    return json.loads((REVIEW / name).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def artifacts():
    return _load("source.json"), _load("calculations.json"), _load("death-penalty-30-80.json")


def _names():
    return {p["id"]: p["display_name"] for p in artifacts()[0]["players"]}


def _inputs_by_round(source):
    players = sorted(source["players"], key=lambda p: p["id"])
    rounds = {r["round_number"]: r for r in source["rounds"]}
    stats, events = defaultdict(dict), defaultdict(list)
    for s in source["stats"]:
        stats[s["round_number"]][s["match_player_id"]] = s
    for e in source["events"]:
        events[e["round_number"]].append(e)
    out = {}
    for rn, rnd in rounds.items():
        nxt = stats.get(rn + 1, {})
        out[rn] = bd.RoundEconInputs(
            round_number=rn, last_round_number=max(rounds), team_a="TEAM_1", team_b="TEAM_2",
            players=tuple(
                bd.PlayerEconomy(
                    match_player_id=p["id"], team=p["team"], free_ability_credits=p["free_ability_value"],
                    loadout=stats[rn].get(p["id"], {}).get("loadout"),
                    next_loadout=nxt.get(p["id"], {}).get("loadout"),
                    next_remaining=nxt.get(p["id"], {}).get("remaining"),
                    has_current_stats=p["id"] in stats[rn], has_next_stats=p["id"] in nxt,
                )
                for p in players
            ),
            events=tuple(bd.EconEvent(event_id=e["id"], time_seconds=e["time"],
                                      killer_id=e["killer"], victim_id=e["victim"]) for e in events[rn]),
            outcome=rnd["outcome"], next_outcome=rounds.get(rn + 1, {}).get("outcome"),
            pistol_outcome=rounds.get(bd.pistol_round_for(rn), {}).get("outcome"),
            has_next_round=rn + 1 in rounds,
        )
    return out


@functools.lru_cache(maxsize=None)
def results(model):
    return {rn: bd.score_round(inputs, model) for rn, inputs in _inputs_by_round(artifacts()[0]).items()}


def _eligible_v2_rounds():
    return [r for r in artifacts()[1]["rounds"] if not r["abstention"]]


# ---- identity of the frozen inputs ------------------------------------------------

def test_frozen_reference_artifacts_are_unchanged():
    for name, digest in FROZEN_LF_SHA256.items():
        data = (REVIEW / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == digest, f"{name} was modified"


def test_scale_and_agent_allowances_are_the_frozen_ones():
    source, _, thirty = artifacts()
    assert econ_component.ECON_SCALE == thirty["constants"]["scale"] == SCALE
    assert thirty["constants"]["absorbed"] == bd.ABSORBED_RATE
    assert thirty["constants"]["disrupted"] == bd.DISRUPTED_RATE
    for player in source["players"]:
        assert free_ability_credits(player["agent"]) == player["free_ability_value"]


# ---- V2 with the historical wealth debit ------------------------------------------

def test_abstentions_match_the_reference():
    ours = results(bd.MODEL_V2_WEALTH)
    for rnd in artifacts()[1]["rounds"]:
        assert ours[rnd["round"]].abstention == rnd["abstention"], rnd["round"]
    assert sum(1 for r in ours.values() if r.abstention is None) == 20


def test_v2_every_team_budget_target_and_count():
    ours = results(bd.MODEL_V2_WEALTH)
    checked = 0
    for rnd in _eligible_v2_rounds():
        for team, info in rnd["teams"].items():
            audit = ours[rnd["round"]].teams[team]
            for key, value in info["budget"].items():
                assert getattr(audit.budget, key) == pytest.approx(value, **TOL), (rnd["round"], team, key)
            assert list(audit.targets) == info["targets"]
            assert audit.deaths == info["deaths"]
            assert audit.first_loss_players == info["first_loss_events"]
            assert audit.next_below_raw_4200 == info["next_below_raw4200"]
            assert audit.pistol_winner == info["pistol_winner"]
            assert audit.round_winner == info["round_winner"]
            checked += 1
    assert checked == 40


def test_v2_every_player_round_raw_parts_and_rounded_net():
    ours = results(bd.MODEL_V2_WEALTH)
    checked = 0
    for rnd in _eligible_v2_rounds():
        for info in rnd["teams"].values():
            for ref in info["players"]:
                ledger = ours[rnd["round"]].players[ref["id"]]
                parts = ref["raw_parts"]
                assert ledger.background_credit == pytest.approx(parts["background_credit"], **TOL)
                assert ledger.disruption_credit == pytest.approx(parts["disruption_credit"], **TOL)
                assert ledger.background_debit == pytest.approx(parts["background_debit"], **TOL)
                assert ledger.scarcity_debit == pytest.approx(parts["scarcity_debit"], **TOL)
                assert ledger.raw_net == pytest.approx(ref["raw_net"], **TOL)
                assert (ledger.lost, ledger.target, ledger.current_paid, ledger.next_paid, ledger.next_bank) == (
                    ref["lost"], ref["target"], ref["current_paid"], ref["next_paid"], ref["next_bank"])
                assert round(SCALE * ledger.raw_net) == ref["econ_points"], (rnd["round"], ref["name"])
                checked += 1
    assert checked == 200


def test_v2_every_event_keeps_its_source_id_and_values():
    ours = results(bd.MODEL_V2_WEALTH)
    checked = 0
    for rnd in _eligible_v2_rounds():
        mine = ours[rnd["round"]].events
        assert [e.event_id for e in mine] == [e["id"] for e in rnd["events"]]
        for event, ref in zip(mine, rnd["events"]):
            assert event.exposure == ref["exposure"]
            assert (event.kind == "enemy") == ref["enemy"]
            assert SCALE * event.background_credit == pytest.approx(ref["background_points"], **TOL)
            assert SCALE * event.disruption_credit == pytest.approx(ref["disruption_points"], **TOL)
            assert SCALE * event.credit == pytest.approx(ref["econ_credit_points"], **TOL)
            assert SCALE * event.victim_debit == pytest.approx(ref["victim_debit_points"], **TOL)
            checked += 1
    assert checked == 152


def test_v2_match_totals_reproduce_the_previous_column():
    ours = results(bd.MODEL_V2_WEALTH)
    names = _names()
    totals = defaultdict(int)
    for res in ours.values():
        for pid, net in res.raw_net_by_player().items():
            totals[names[pid]] += round(SCALE * net)
    assert dict(totals) == EXPECTED_V2_TOTALS
    for pid, ref in artifacts()[1]["totals"].items():
        assert totals[ref["name"]] == ref["econ_points"]


# ---- V2 with the 30%/80% debit ----------------------------------------------------

def test_30_80_every_player_round():
    ours = results(bd.MODEL_V2_30_80)
    names = _names()
    by_key = {(rn, names[pid]): ledger for rn, res in ours.items() if res.abstention is None
              for pid, ledger in res.players.items()}
    rows = artifacts()[2]["player_rounds"]
    assert len(rows) == 200
    assert {(r["round"], r["player"]) for r in rows} == set(by_key)
    for ref in rows:
        ledger = by_key[(ref["round"], ref["player"])]
        assert SCALE * ledger.credit == pytest.approx(ref["credit"], **TOL)
        assert SCALE * ledger.debit == pytest.approx(ref["debit"], **TOL)
        assert ledger.penalty_rate == ref["rate"]
        assert ledger.lost == ref["loss"]
        assert round(SCALE * ledger.raw_net) == ref["net"], (ref["round"], ref["player"])


def test_30_80_every_event_with_the_source_ids():
    ours = results(bd.MODEL_V2_30_80)
    names = _names()
    flat = [(rn, e) for rn in sorted(ours) if ours[rn].abstention is None for e in ours[rn].events]
    refs = artifacts()[2]["events"]
    v2_events = [e for rnd in _eligible_v2_rounds() for e in rnd["events"]]
    assert len(flat) == len(refs) == len(v2_events) == 152
    for (rn, event), ref, v2 in zip(flat, refs, v2_events):
        assert rn == ref["round"]
        assert event.time_seconds == ref["time"]
        assert names.get(event.killer_id, "unknown/environment") == ref["killer"]
        assert names[event.victim_id] == ref["victim"]
        assert SCALE * event.credit == pytest.approx(ref["credit"], **TOL)
        assert SCALE * event.victim_debit == pytest.approx(ref["debit"], **TOL)
        assert event.penalty_rate == ref["rate"]
        assert event.event_id == v2["id"]


def test_30_80_player_and_team_totals():
    ours = results(bd.MODEL_V2_30_80)
    names = _names()
    team_of = {p["display_name"]: p["team"] for p in artifacts()[0]["players"]}
    totals = defaultdict(int)
    for res in ours.values():
        for pid, net in res.raw_net_by_player().items():
            totals[names[pid]] += round(SCALE * net)
    assert dict(totals) == EXPECTED_30_80_TOTALS
    teams = defaultdict(int)
    for name, net in totals.items():
        teams[team_of[name]] += net
    assert dict(teams) == EXPECTED_30_80_TEAMS
    thirty = artifacts()[2]
    assert {p["name"]: p["net"] for p in thirty["players"].values()} == EXPECTED_30_80_TOTALS
    assert {t: v["net"] for t, v in thirty["teams"].items()} == EXPECTED_30_80_TEAMS


def test_gross_credits_are_identical_between_the_two_debit_models():
    wealth, thirty = results(bd.MODEL_V2_WEALTH), results(bd.MODEL_V2_30_80)
    for rn in wealth:
        assert wealth[rn].abstention == thirty[rn].abstention
        for pid, ledger in wealth[rn].players.items():
            assert ledger.background_credit == thirty[rn].players[pid].background_credit
            assert ledger.disruption_credit == thirty[rn].players[pid].disruption_credit
        assert [e.credit for e in wealth[rn].events] == [e.credit for e in thirty[rn].events]


# ---- the same snapshot through the real build path ---------------------------------

def _abyss_session():
    source = artifacts()[0]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Player.__table__, Match.__table__, MatchPlayer.__table__,
        Round.__table__, RoundPlayerStat.__table__, KillEvent.__table__,
    ])
    db = sessionmaker(bind=engine)()
    m = source["match"]
    db.add(Match(id=m["id"], external_id=m["external_id"], source=MatchSource.SCRAPED, map_name=m["map_name"]))
    for p in source["players"]:
        player = Player(display_name=p["display_name"])
        db.add(player)
        db.flush()
        db.add(MatchPlayer(id=p["id"], match_id=m["id"], player_id=player.id, agent=p["agent"],
                           team=Team[p["team"]]))
    round_ids = {}
    for r in source["rounds"]:
        db.add(Round(id=r["id"], match_id=m["id"], round_number=r["round_number"], outcome=r["outcome"],
                     planted=r["planted"], plant_time=r["plant_time"], exploded=False, defused=False))
        round_ids[r["round_number"]] = r["id"]
    for s in source["stats"]:
        db.add(RoundPlayerStat(round_id=round_ids[s["round_number"]], match_player_id=s["match_player_id"],
                               kills=s["kills"], deaths=s["deaths"], assists=0, score=0,
                               loadout=s["loadout"], remaining=s["remaining"]))
    for e in source["events"]:
        db.add(KillEvent(id=e["id"], round_id=round_ids[e["round_number"]], killer_match_player_id=e["killer"],
                         death_match_player_id=e["victim"], weapon=e["weapon"], event_time_seconds=e["time"]))
    db.commit()
    return db, {rid: rn for rn, rid in round_ids.items()}


def _expected_nets(model):
    if model == bd.MODEL_V2_30_80:
        ids = {name: pid for pid, name in _names().items()}
        return {(r["round"], ids[r["player"]]): r["net"] for r in artifacts()[2]["player_rounds"]}
    return {(rnd["round"], p["id"]): p["econ_points"]
            for rnd in _eligible_v2_rounds() for info in rnd["teams"].values() for p in info["players"]}


@pytest.mark.parametrize("model", [bd.MODEL_V2_WEALTH, bd.MODEL_V2_30_80])
def test_build_path_reproduces_every_player_round_econ_component(model):
    db, round_number_of = _abyss_session()
    audits = []
    rows = build_impact_rows_for_match(
        db, 3104, use_realized_swing=True, enable_econ_component=True, econ_model=model,
        econ_observer=lambda **kw: audits.append(kw))
    expected = _expected_nets(model)
    assert len(rows) == 240
    for row in rows:
        key = (round_number_of[row.round_id], row.match_player_id)
        assert row.econ_component == expected.get(key, 0), key
        assert row.impact == row.damage + row.leverage_component + row.econ_component
    # The scorer's own audit IS the pure calculator's result, not a copy of it.
    by_round = {kw["round_number"]: kw["result"] for kw in audits}
    pure = results(model)
    assert set(by_round) == set(pure)
    for rn, res in by_round.items():
        assert res.abstention == pure[rn].abstention
        assert res.raw_net_by_player() == pure[rn].raw_net_by_player()
        assert [e.event_id for e in res.events] == [e.event_id for e in pure[rn].events]
