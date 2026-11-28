"""Branch-review fixes R1.1: the ability key, the source key's inputs, KAY/O's ability name, the failure guard,
back-shot time rounding and the D12 threshold pin."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import KillEvent, MatchPlayer, Player, Round
from app.models.match import Team
from app.models.replay import ReplayGap, ReplayPlayer, ReplayRoundUtil
from app.models.replay_search import ReplayMomentIndex, ReplayMomentIndexState
from app.search import index
from app.search.filters import parse
from app.search.match import ROW_KEYS, Context, matches, summary
from app.services import replay_search_index as store
from tests.search.test_search_index import _inputs
from tests.search.test_search_match import ctx, names, row
from tests.search.test_search_store import (OFFSET, factory, indexed, pending_ids, replay_id, state_of,  # noqa: F401
                                            stored)

# ---------------------------------------------------------------------------------------------- F1: ability key

KEY_CASES = [
    ("Grenadier_Q_SemtexBasic", "Q"),
    ("BountyHunter_4_WolfHound", "4"),
    ("BountyHunter_X_WaveForm", "X"),
    ("BountyHunter_Q_Tether_SphereExpansion", "Q"),
    ("Cable_X_FishingHook_Cage", "X"),
    ("Rift_E_SmokeZone", "E"),
    ("Q_Molotov", "Q"),
    ("4_Smoke", "4"),
    ("Nox_C_Thing", "C"),
    ("AcidMolotov_NewMolotov", None),
    ("StealthingTrap_Flash_2", None),
    ("Pandemic_Smoke", None),
    ("Ultimate_X", None),
    ("Sarge_4_Smoke_ProductionNEW", "4"),
    ("", None),
    (None, None),
]


@pytest.mark.parametrize("name,expected", KEY_CASES)
def test_key_of_is_the_archetype_letter(name, expected):
    assert index._key_of(name) == expected


@pytest.mark.parametrize("name,expected", KEY_CASES)
def test_build_rows_stores_the_letter_not_a_prefix(name, expected):
    inputs = _inputs()
    inputs["rounds"][1]["casts"] = [{"by": 2, "agent": "Agent2", "t": 10.0, "t_end": 12.0, "names": [name],
                                     "kinds": [], "type": "ability", "ability_id": "Agent2/Thing"}]
    cast = next(r for r in index.build_rows(inputs) if r["kind"] == "cast")
    assert cast["ability_key"] == expected


def test_first_name_with_a_letter_decides_and_none_when_no_name_has_one():
    inputs = _inputs()
    inputs["rounds"][1]["casts"] = [
        {"by": 2, "agent": "A", "t": 1.0, "names": ["AcidMolotov_NewMolotov", "Acid_E_Molotov"], "type": "ability",
         "ability_id": "A/M"},
        {"by": 2, "agent": "A", "t": 2.0, "names": ["AcidMolotov_NewMolotov"], "type": "ability", "ability_id": "A/M"},
        {"by": 2, "agent": "A", "t": 3.0, "names": None, "type": "ability", "ability_id": "A/M"},
    ]
    casts = [r for r in index.build_rows(inputs) if r["kind"] == "cast"]
    assert [c["ability_key"] for c in casts] == ["E", None, None]


def test_the_column_is_wide_enough_in_the_model_and_the_migration():
    assert ReplayMomentIndex.__table__.c.ability_key.type.length == 16
    source = (Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0039_replay_moment_index.py"
              ).read_text(encoding="utf-8")
    assert 'sa.Column("ability_key", sa.String(length=16)' in source


def test_a_rebuild_with_codename_prefixed_casts_stores_letters(factory):
    rid = replay_id(factory)
    names_in = ["Grenadier_Q_SemtexBasic", "BountyHunter_4_WolfHound", "BountyHunter_X_WaveForm",
                "Cable_X_FishingHook_Cage", "AcidMolotov_NewMolotov", "Q_Molotov"]
    with factory() as db:
        util = db.execute(select(ReplayRoundUtil).where(ReplayRoundUtil.round_number == 1)).scalar_one()
        util.data = {"casts": [{"t": 10.0 + i, "t_end": 12.0 + i, "by": 1, "agent": "Agent1",
                                "ability_id": f"Agent1/A{i}", "names": [nm], "type": "basic", "kinds": []}
                               for i, nm in enumerate(names_in)]}
        db.commit()
    assert store.rebuild(factory, rid).startswith("rebuilt: ")
    casts = [r for r in stored(factory, rid) if r["kind"] == "cast"]
    assert [c["ability_key"] for c in casts] == ["Q", "4", "X", "X", None, "Q"]
    assert state_of(factory, rid).error is None


# ------------------------------------------------------------------------------------------- F3: source key inputs


def _set_player_id(db):
    new = Player(display_name="renamed")
    db.add(new)
    db.flush()
    mp = db.execute(select(MatchPlayer).where(MatchPlayer.agent == "Agent0")).scalar_one()
    mp.player_id = new.id


def _set_team(db):
    mp = db.execute(select(MatchPlayer).where(MatchPlayer.agent == "Agent0")).scalar_one()
    mp.team = Team.TEAM_2


def _set_replay_agent(db):
    rp = db.execute(select(ReplayPlayer).where(ReplayPlayer.slot == 3)).scalar_one()
    rp.agent = "SomeoneElse"


def _kill(db, n=0):
    return db.execute(select(KillEvent).order_by(KillEvent.id)).scalars().all()[n]


def _set_killer(db):
    mp = db.execute(select(MatchPlayer).where(MatchPlayer.agent == "Agent3")).scalar_one()
    _kill(db).killer_match_player_id = mp.id


def _set_victim(db):
    mp = db.execute(select(MatchPlayer).where(MatchPlayer.agent == "Agent9")).scalar_one()
    _kill(db).death_match_player_id = mp.id


def _set_weapon(db):
    _kill(db).weapon = "Operator"


def _set_kill_time(db):
    _kill(db).event_time_seconds = _kill(db).event_time_seconds + 1.0


def _set_kill_round(db):
    rnd = db.execute(select(Round).where(Round.round_number == 2)).scalar_one()
    _kill(db).round_id = rnd.id


def _round(db, n):
    return db.execute(select(Round).where(Round.round_number == n)).scalar_one()


def _set(n, **kw):
    def go(db):
        for k, v in kw.items():
            setattr(_round(db, n), k, v)
    return go


INPUT_CHANGES = {
    "match_player.player_id": _set_player_id,
    "match_player.team": _set_team,
    "replay_player.agent": _set_replay_agent,
    "kill.killer": _set_killer,
    "kill.victim": _set_victim,
    "kill.weapon": _set_weapon,
    "kill.time": _set_kill_time,
    "kill.round": _set_kill_round,
    "round.outcome": _set(1, outcome="Team B Time Win"),
    "round.planted": _set(1, planted=True),
    "round.exploded": _set(2, exploded=True),
    "round.defused": _set(2, defused=True),
    "round.plant_time": _set(2, plant_time=141.0),
    "round.defuse_time": _set(2, defuse_time=170.0),
}


@pytest.mark.parametrize("name", sorted(INPUT_CHANGES))
def test_each_input_change_makes_the_replay_pending(factory, name):
    rid = indexed(factory)
    with factory() as db:
        INPUT_CHANGES[name](db)
        db.commit()
    assert pending_ids(factory) == [rid]


def test_replay_player_match_player_is_still_in_the_key(factory):
    rid = indexed(factory)
    with factory() as db:
        a = db.execute(select(ReplayPlayer).where(ReplayPlayer.slot == 0)).scalar_one()
        b = db.execute(select(ReplayPlayer).where(ReplayPlayer.slot == 1)).scalar_one()
        a.match_player_id, b.match_player_id = b.match_player_id, a.match_player_id
        db.commit()
    assert pending_ids(factory) == [rid]


def test_key_parts_stay_json_and_grouped(factory):
    import json

    rid = replay_id(factory)
    with factory() as db:
        parts = store.key_parts(db, [rid])[rid]
    json.dumps(parts)
    assert index.source_key(parts)


# ------------------------------------------------------------------------------------- F5: KAY/O's ability name


def kayo(**over):
    base = dict(kind="cast", slot=1, agent="KAY/O", ability="KAY/O/NULL/cmd", ult=True, alive1=None, alive2=None,
                post_plant=False, plant_t=None, t=10.0)
    base.update(over)
    return row(**base)


def test_kayo_ultimate_reads_null_cmd():
    s = summary(kayo(), names({}), "team-1")
    assert "O/NULL" not in s.replace("KAY/O/NULL", "")
    assert s.startswith("the enemy KAY/O used their ultimate, NULL/cmd")
    plain = summary(kayo(ult=False, ability="KAY/O/FLASH/drive"), names({}), "team-1")
    assert plain.startswith("the enemy KAY/O cast FLASH/drive")


def test_ability_substring_does_not_see_the_agents_slash():
    c = ctx()
    assert not matches(parse(["ability=o/"]), c, kayo())
    assert not matches(parse(["ability=kay"]), c, kayo())
    assert matches(parse(["ability=null"]), c, kayo())
    assert matches(parse(["ability=null/cmd"]), c, kayo())
    assert matches(parse(["ability=NULL"]), c, kayo())


def test_other_agents_and_odd_ids_still_split_at_the_first_slash():
    sova = row(kind="cast", slot=1, agent="Sova", ability="Sova/Hunter's Fury", ult=True, alive1=None, alive2=None,
               post_plant=False, plant_t=None)
    assert summary(sova, names({}), "team-1").startswith("the enemy Sova used their ultimate, Hunter's Fury")
    assert matches(parse(["ability=hunter"]), ctx(), sova)
    assert not matches(parse(["ability=sova"]), ctx(), sova)
    no_agent = row(kind="cast", slot=1, agent=None, ability="KAY/O/NULL/cmd", ult=True, alive1=None, alive2=None,
                   post_plant=False, plant_t=None)
    assert "NULL/cmd" in summary(no_agent, names({}), None) or "O/NULL/cmd" in summary(no_agent, names({}), None)
    mismatch = row(kind="cast", slot=1, agent="Jett", ability="Sova/Hunter's Fury", alive1=None, alive2=None,
                   post_plant=False, plant_t=None)
    assert matches(parse(["ability=hunter"]), ctx(), mismatch)
    assert not matches(parse(["ability=sova"]), ctx(), mismatch)


# ------------------------------------------------------------------------------------------- F7: failure guard


def _raise(_inputs):
    raise ValueError("kaboom")


def test_a_failed_build_leaves_a_row_that_already_holds_the_key_alone(factory, monkeypatch):
    rid = indexed(factory)
    before = state_of(factory, rid)
    key, built_at = before.key, before.built_at
    monkeypatch.setattr(index, "build_rows", _raise)
    assert store.rebuild(factory, rid) == "skipped: built elsewhere"
    after = state_of(factory, rid)
    assert after.error is None and after.key == key and after.built_at == built_at
    assert pending_ids(factory) == []


def test_a_failed_build_still_writes_the_error_when_the_revision_is_old(factory, monkeypatch):
    rid = indexed(factory)
    with factory() as db:
        db.get(ReplayMomentIndexState, rid).revision = 0
        db.commit()
    monkeypatch.setattr(index, "build_rows", _raise)
    store.rebuild(factory, rid)
    assert "kaboom" in state_of(factory, rid).error


def test_a_failed_build_writes_the_error_when_the_key_differs(factory, monkeypatch):
    rid = indexed(factory)
    with factory() as db:
        db.execute(ReplayMomentIndexState.__table__.update().values(key="stale"))
        db.commit()
    monkeypatch.setattr(index, "build_rows", _raise)
    store.rebuild(factory, rid)
    failed = state_of(factory, rid)
    assert "kaboom" in failed.error and failed.key == "stale"


def test_a_failure_before_the_key_exists_still_records_the_error(factory, monkeypatch):
    rid = indexed(factory)

    def boom(_db, _ids):
        raise RuntimeError("no key")

    monkeypatch.setattr(store, "key_parts", boom)
    store.rebuild(factory, rid)
    assert "no key" in state_of(factory, rid).error


# ----------------------------------------------------------------------------------------- F8: back-shot times


def test_back_shot_time_is_rounded_to_three_decimals(factory):
    rid = replay_id(factory)
    with factory() as db:
        db.execute(ReplayGap.__table__.update().values(t_open=41.470001220703125))
        db.commit()
        inputs = store.load_inputs(db, rid)
    assert inputs["rounds"][1]["backshots"][0]["t"] == 41.47
    store.rebuild(factory, rid)
    shot = next(r for r in stored(factory, rid) if r["kind"] == "backshot")
    assert shot["t"] == 41.47


# ------------------------------------------------------------------------------------------ F10: D12 thresholds


def test_buy_thresholds_match_impact_scoring():
    from app.scoring import impact

    assert index.ECO_BELOW == impact.FORCE_THRESHOLD
    assert index.FORCE_BELOW == impact.FULL_BUY_THRESHOLD
    assert impact.econ_tier_name(index.SAVE_BELOW) == "ECO" and impact.econ_tier_name(index.SAVE_BELOW - 1) == "SAVE"
    tier = {"save": "SAVE", "eco": "ECO", "force": "FORCE", "full": "FULL_BUY"}
    for loadout in (0, 999, 1000, 3249, 3250, 4199, 4200, 9000):
        assert tier[index.buy_of(2, [loadout])] == impact.econ_tier_name(loadout), loadout
    assert tuple(index.PISTOL_ROUNDS) == (1, 13)
