"""Replay search storage (W3): the loader, the source key, the per-replay rebuild, pending, drop_unlinked,
rows_for_replay and status, on one toy linked replay of two rounds (SQLite in memory, foreign keys on)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import KillEvent, Match, MatchPlayer, Player, Round, RoundPlant, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.models.replay import (Replay, ReplayGap, ReplayPlayer, ReplayRound, ReplayRoundMoments,
                               ReplayRoundUtil)
from app.models.replay_search import ReplayMomentIndex, ReplayMomentIndexState
from app.search import index
from app.search.match import ROW_KEYS
from app.services import replay_search_index as store

OFFSET = 100.0
UUID = "11111111-1111-4111-8111-111111111111"
TABLES = [Player.__table__, Match.__table__, MatchPlayer.__table__, Round.__table__, RoundPlayerStat.__table__,
          KillEvent.__table__, RoundPlant.__table__, Replay.__table__, ReplayRound.__table__,
          ReplayPlayer.__table__, ReplayRoundMoments.__table__, ReplayRoundUtil.__table__, ReplayGap.__table__,
          ReplayMomentIndex.__table__, ReplayMomentIndexState.__table__]


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def pragma(dbapi, _record):
        dbapi.execute("PRAGMA foreign_keys=ON")

    for table in TABLES:
        table.create(engine)
    maker = sessionmaker(bind=engine)
    with maker() as db:
        seed(db)
    yield maker
    engine.dispose()


def seed(db):
    match = Match(external_id="toy-match", source=MatchSource.SCRAPED, map_name="Ascent", team1_rounds_won=1,
                  team2_rounds_won=1, played_at=datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc))
    db.add(match)
    db.flush()
    mps = []
    for slot in range(10):
        player = Player(display_name=f"synthetic-{slot}")
        db.add(player)
        db.flush()
        mp = MatchPlayer(match_id=match.id, player_id=player.id, agent=f"Agent{slot}",
                         team=Team.TEAM_1 if slot < 5 else Team.TEAM_2)
        db.add(mp)
        db.flush()
        mps.append(mp.id)
    replay = Replay(match_uuid=UUID, match_id=match.id, map_name="Ascent", round_count=2, format_version=1,
                    recipe="r", game_branch="release-13.08", source_sha256="0" * 64, source="upload",
                    link_status="linked", link_inputs={}, clock_offset=OFFSET)
    db.add(replay)
    db.flush()
    for slot in range(10):
        db.add(ReplayPlayer(replay_id=replay.id, slot=slot, agent=f"Agent{slot}", match_player_id=mps[slot]))
    rounds = {}
    for n, outcome, plant in ((1, "Team A Time Win", None), (2, "Team B Time Win", 140.0)):
        rnd = Round(match_id=match.id, round_number=n, outcome=outcome, planted=plant is not None,
                    plant_time=plant)
        db.add(rnd)
        db.flush()
        rounds[n] = rnd
        db.add(ReplayRound(replay_id=replay.id, round_number=n, data=b""))
        for slot in range(10):
            db.add(RoundPlayerStat(round_id=rnd.id, match_player_id=mps[slot], loadout=4500 if slot < 5 else 2000))
    # round 1: a kill whose time has a 4th decimal, then a second kill; round 2: one kill
    for rnd, killer, victim, when in ((1, 0, 5, OFFSET + 20.1234), (1, 1, 6, OFFSET + 30.0),
                                      (2, 5, 0, OFFSET + 25.0)):
        db.add(KillEvent(round_id=rounds[rnd].id, killer_match_player_id=mps[killer],
                         death_match_player_id=mps[victim], weapon="Vandal", event_time_seconds=when))
    db.add(ReplayRoundMoments(replay_id=replay.id, round_number=1, status="ok", fingerprint="fp1",
                              gaps_revision=5,
                              fights={"v": 1, "sight": None, "fights": [
                                  {"t0": 19.0, "t1": 21.0, "slot": 0, "first": "player", "tags": ["won"]}]}))
    db.add(ReplayRoundUtil(replay_id=replay.id, round_number=1, status="ok", fingerprint="fp1", gaps_revision=5,
                           data={"casts": [{"t": 10.0, "t_end": 12.0, "by": 1, "agent": "Agent1",
                                            "ability_id": "Agent1/Smoke", "names": ["C_Smoke"],
                                            "type": "basic", "kinds": ["smoke"]}]}))
    db.add(ReplayGap(replay_id=replay.id, round_number=1, seq=0, kind="backshot", map="Ascent", victim_slot=6,
                     t_open=25.0, spot_cell=1, victim_cell=2, distance_m=5.0, angle_deg=10.0, cause="flank",
                     route=[], candidate_slots=[], flicker=False))
    db.commit()


def replay_id(factory):
    with factory() as db:
        return db.execute(select(Replay.id)).scalar_one()


def stored(factory, rid):
    with factory() as db:
        out = []
        for row in db.execute(select(ReplayMomentIndex).where(ReplayMomentIndex.replay_id == rid)
                              .order_by(ReplayMomentIndex.round, ReplayMomentIndex.seq)).scalars():
            out.append({k: getattr(row, k) for k in ROW_KEYS})
        return out


def state_of(factory, rid):
    with factory() as db:
        return db.get(ReplayMomentIndexState, rid)


def pending_ids(factory, **kw):
    with factory() as db:
        return store.pending(db, kw.get("limit", 50), kw.get("skip", ()))


def test_loader_inputs_shape(factory):
    rid = replay_id(factory)
    with factory() as db:
        inputs = store.load_inputs(db, rid)
    assert inputs["replay"]["played_at"] == "2026-11-01T20:30:00+00:00"
    assert inputs["players"][0]["team"] == "team-1" and inputs["players"][7]["team"] == "team-2"
    r1 = inputs["rounds"][1]
    assert r1["won_team"] == "team-1" and r1["attack_team"] == "team-1"
    assert [k["t"] for k in r1["kills"]] == [20.123, 30.0]
    assert r1["kills"][0]["killer_slot"] == 0 and r1["kills"][0]["victim_slot"] == 5
    assert r1["states"][0]["t"] == 0.0 and r1["states"][1]["t"] == 20.123
    assert r1["fights"][0]["slot"] == 0 and r1["casts"][0]["by"] == 1
    assert r1["backshots"] == [{"t": 25.0, "victim_slot": 6, "cause": "flank"}]
    assert r1["loadouts"] == {"team-1": [4500] * 5, "team-2": [2000] * 5}
    assert inputs["rounds"][2]["plant_t"] == 40.0 and inputs["rounds"][2]["won_team"] == "team-2"
    assert inputs["rounds"][2]["attack_team"] == "team-1"
    assert inputs["rounds"][2]["fights"] is None and inputs["rounds"][2]["casts"] is None


def test_load_inputs_none_when_not_linked(factory):
    rid = replay_id(factory)
    with factory() as db:
        db.execute(text("UPDATE replays SET link_status='unlinked'"))
        db.commit()
        assert store.load_inputs(db, rid) is None
        assert store.build_for_replay(db, rid) == []
        assert store.source_keys(db) == {}


def test_rebuild_writes_the_built_rows_and_is_idempotent(factory):
    rid = replay_id(factory)
    with factory() as db:
        built = store.build_for_replay(db, rid)
    assert built and {r["kind"] for r in built} >= {"state", "kill", "death", "cast", "fight", "backshot", "plant"}
    assert pending_ids(factory) == [rid]
    assert store.rebuild(factory, rid) == f"rebuilt: {len(built)} rows"
    assert stored(factory, rid) == built       # key for key, played_at ISO string, float times
    first = state_of(factory, rid)
    assert (first.revision, first.row_count, first.error) == (index.INDEX_REVISION, len(built), None)
    with factory() as db:
        assert first.key == store.source_keys(db)[rid]
    assert pending_ids(factory) == []
    store.rebuild(factory, rid)
    assert stored(factory, rid) == built and state_of(factory, rid).key == first.key
    assert pending_ids(factory) == []
    assert pending_ids(factory, skip=[rid]) == []


def indexed(factory):
    rid = replay_id(factory)
    store.rebuild(factory, rid)
    assert pending_ids(factory) == []
    return rid


def test_fingerprint_change_makes_it_pending(factory):
    rid = indexed(factory)
    with factory() as db:
        db.execute(text("UPDATE replay_round_moments SET fingerprint='fp2'"))
        db.commit()
    assert pending_ids(factory) == [rid]


def test_kill_event_change_makes_it_pending(factory):
    rid = indexed(factory)
    with factory() as db:
        rnd = db.execute(select(Round).where(Round.round_number == 2)).scalar_one()
        mp = db.execute(select(MatchPlayer).where(MatchPlayer.agent == "Agent6")).scalar_one()
        db.add(KillEvent(round_id=rnd.id, killer_match_player_id=mp.id, death_match_player_id=None,
                         weapon="Vandal", event_time_seconds=OFFSET + 50.0))
        db.commit()
    assert pending_ids(factory) == [rid]


def test_revision_bump_makes_it_pending(factory, monkeypatch):
    rid = indexed(factory)
    monkeypatch.setattr(index, "INDEX_REVISION", index.INDEX_REVISION + 1)
    assert pending_ids(factory) == [rid]


def test_round_plant_and_loadout_changes_make_it_pending(factory):
    rid = indexed(factory)
    with factory() as db:
        rnd = db.execute(select(Round).where(Round.round_number == 2)).scalar_one()
        db.add(RoundPlant(round_id=rnd.id, match_id=rnd.match_id, site="B", u=1, v=2, source="replay",
                          round_number=2, plant_time=140.0, defused=False))
        db.commit()
    assert pending_ids(factory) == [rid]
    store.rebuild(factory, rid)
    assert pending_ids(factory) == []
    assert {r["site"] for r in stored(factory, rid) if r["round"] == 2} == {"B"}
    with factory() as db:
        stat = db.execute(select(RoundPlayerStat).limit(1)).scalar_one()
        stat.loadout = stat.loadout + 50
        db.commit()
    assert pending_ids(factory) == [rid]


def test_clock_offset_change_makes_it_pending(factory):
    rid = indexed(factory)
    with factory() as db:
        db.execute(text("UPDATE replays SET clock_offset=101.0"))
        db.commit()
    assert pending_ids(factory) == [rid]


def test_a_failed_build_keeps_old_rows_key_and_revision(factory, monkeypatch):
    rid = indexed(factory)
    old_rows, old = stored(factory, rid), state_of(factory, rid)
    old_key, old_revision = old.key, old.revision
    with factory() as db:
        db.execute(text("UPDATE replay_round_moments SET fingerprint='fp2'"))
        db.commit()

    def boom(_inputs):
        raise ValueError("kaboom")

    monkeypatch.setattr(index, "build_rows", boom)
    result = store.rebuild(factory, rid)
    assert result.startswith("failed: ") and "kaboom" in result
    assert stored(factory, rid) == old_rows
    failed = state_of(factory, rid)
    assert (failed.key, failed.revision) == (old_key, old_revision)
    assert "kaboom" in failed.error and failed.built_at is not None
    assert pending_ids(factory) == [rid]
    # an INDEX_REVISION bump plus a failed rebuild leaves the stored revision at the old one
    monkeypatch.setattr(index, "INDEX_REVISION", index.INDEX_REVISION + 1)
    assert store.rebuild(factory, rid).startswith("failed: ")
    assert state_of(factory, rid).revision == old_revision
    assert pending_ids(factory) == [rid]


def test_a_first_build_that_fails_writes_an_empty_key_state(factory, monkeypatch):
    rid = replay_id(factory)
    monkeypatch.setattr(index, "build_rows", lambda _i: (_ for _ in ()).throw(RuntimeError("nope")))
    assert store.rebuild(factory, rid).startswith("failed: ")
    row = state_of(factory, rid)
    assert (row.key, row.revision, row.row_count) == ("", 0, 0) and "nope" in row.error
    assert stored(factory, rid) == [] and pending_ids(factory) == [rid]


def test_unlinking_and_drop_unlinked(factory):
    rid = indexed(factory)
    with factory() as db:
        db.execute(text("UPDATE replays SET link_status='unlinked'"))
        db.commit()
    assert pending_ids(factory) == []
    assert stored(factory, rid)
    with factory() as db:
        assert store.drop_unlinked(db) == 1
        db.commit()
    assert stored(factory, rid) == [] and state_of(factory, rid) is None
    with factory() as db:
        assert store.drop_unlinked(db) == 0


def test_deleting_the_replay_cascades(factory):
    rid = indexed(factory)
    with factory() as db:
        db.execute(text("DELETE FROM replays"))
        db.commit()
    assert stored(factory, rid) == [] and state_of(factory, rid) is None


def test_a_fourth_decimal_kill_time_uses_the_pre_kill_counts(factory):
    rid = indexed(factory)
    rows = [r for r in stored(factory, rid) if r["round"] == 1]
    kill = next(r for r in rows if r["kind"] == "kill" and r["t"] == 20.123)
    assert (kill["alive1"], kill["alive2"]) == (5, 5)
    state = next(r for r in rows if r["kind"] == "state" and r["t"] == 20.123)
    assert (state["alive1"], state["alive2"]) == (5, 4)
    assert 5 not in state["alive_slots"] and 0 in state["alive_slots"]


def test_rows_for_replay(factory):
    rid = replay_id(factory)
    with factory() as db:
        assert store.rows_for_replay(db, rid) == (False, {})
    store.rebuild(factory, rid)
    with factory() as db:
        current, by_round = store.rows_for_replay(db, rid)
    assert current is True and sorted(by_round) == [1, 2]
    assert [r["seq"] for r in by_round[1]] == sorted(r["seq"] for r in by_round[1])
    assert [r for rows in by_round.values() for r in rows] == stored(factory, rid)
    with factory() as db:
        db.execute(text("UPDATE replay_round_moments SET fingerprint='fp2'"))
        db.commit()
        assert store.rows_for_replay(db, rid)[0] is False


def test_status_counts(factory):
    with factory() as db:
        assert store.status(db) == {"linked": 1, "indexed": 0, "missing": 1, "stale": 0, "failed": 0}
    rid = indexed(factory)
    with factory() as db:
        assert store.status(db) == {"linked": 1, "indexed": 1, "missing": 0, "stale": 0, "failed": 0}
        db.execute(text("UPDATE replay_round_moments SET fingerprint='fp2'"))
        db.commit()
        assert store.status(db) == {"linked": 1, "indexed": 0, "missing": 0, "stale": 1, "failed": 0}
    with factory() as db:
        row = db.get(ReplayMomentIndexState, rid)
        row.error = "x"
        db.commit()
        assert store.status(db) == {"linked": 1, "indexed": 0, "missing": 0, "stale": 0, "failed": 1}


def test_key_parts_is_json_and_covers_the_listed_inputs(factory):
    rid = replay_id(factory)
    with factory() as db:
        parts = store.key_parts(db, [rid])[rid]
    for name in ("match_id", "clock_offset", "moments", "util", "backshots", "kills", "rounds", "sites",
                 "loadouts", "slots"):
        assert name in parts
    assert index.source_key(parts)
