"""app/replays/store.py and app/replays/db.py: the dedupe rule, the link write, the backfill and the
demo refusal on a throwaway sqlite session; the trigger, the lock and rollback on PostgreSQL
(`VALO_TEST_DATABASE_URL`, a `*_test` database; skipped without one: tests/_postgres.py)."""

import copy
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))

from replay_synthetic import AGENT_NAMES, MATCH_UUID, SyntheticMatch, subject, team_of  # noqa: E402
from test_ingest_replay import LINK_KILLS, OFFSET, ROUNDS, WINNERS  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import KillEvent, Match, MatchPlayer, Player, Round  # noqa: E402
from app.models.match import MatchSource, Team  # noqa: E402
from app.models.replay import Replay, ReplayDeletion, ReplayPlayer, ReplayRound, ReplayRoundControl  # noqa: E402
from app.replays import condense as cd  # noqa: E402
from app.replays import db as replay_db  # noqa: E402
from app.replays import format as fmt  # noqa: E402
from app.replays import store  # noqa: E402

TABLES = [Player.__table__, Match.__table__, MatchPlayer.__table__, Round.__table__, KillEvent.__table__,
          Replay.__table__, ReplayRound.__table__, ReplayRoundControl.__table__, ReplayPlayer.__table__,
          ReplayDeletion.__table__]


@pytest.fixture(scope="module")
def condensed(tmp_path_factory):
    match = SyntheticMatch(rounds=ROUNDS, kills=copy.deepcopy(LINK_KILLS), winners=dict(WINNERS))
    return cd.condense_export_dir(match.write(tmp_path_factory.mktemp("m") / "e"), source_sha256=match.source_sha256)


def add_match(db, external_id=MATCH_UUID):
    match = Match(external_id=external_id, source=MatchSource.SCRAPED, map_name="Ascent",
                  team1_rounds_won=sum(w == "Red" for w in WINNERS.values()),
                  team2_rounds_won=sum(w == "Blue" for w in WINNERS.values()))
    db.add(match)
    db.flush()
    mps, players = [], []
    for slot in range(10):
        player = Player(display_name=f"synthetic-{slot}")
        db.add(player)
        db.flush()
        mp = MatchPlayer(match_id=match.id, player_id=player.id, agent=AGENT_NAMES[slot],
                         team=Team.TEAM_1 if team_of(slot) == 0 else Team.TEAM_2)
        db.add(mp)
        db.flush()
        mps.append(mp.id)
        players.append(player.id)
    for n, script in LINK_KILLS.items():
        rnd = Round(match_id=match.id, round_number=n,
                    outcome=f"Team {'A' if WINNERS[n] == 'Red' else 'B'} Elimination Win")
        db.add(rnd)
        db.flush()
        for t, killer, victim in script:
            db.add(KillEvent(round_id=rnd.id, killer_match_player_id=mps[killer], death_match_player_id=mps[victim],
                             weapon="Vandal", event_time_seconds=round(t + OFFSET, 3)))
    db.commit()
    return match, players


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def with_riot_subject(db):
    db.execute(text("ALTER TABLE players ADD COLUMN riot_subject VARCHAR(36)"))
    db.commit()


def test_a_store_links_and_writes_the_mapping(db, condensed):
    match, _ = add_match(db)
    result = store.store_replay(db, condensed, source="local")
    assert (result.action, result.link_status) == ("stored", "linked")
    row = db.get(Replay, result.replay_id)
    assert row.match_id == match.id and row.clock_offset == pytest.approx(OFFSET, abs=1e-3)
    assert row.link_inputs["eligibility"]["eligible"] and row.link_inputs["dropped_final_round"] is False
    assert row.kill_impact is None and replay_db.is_valid(db, row) and replay_db.is_linked(row)
    assert db.query(ReplayPlayer).filter(ReplayPlayer.match_player_id.isnot(None)).count() == 10
    blobs = {r.round_number: r.data for r in db.query(ReplayRound)}
    assert blobs == condensed.encoded_rounds()
    assert "synthetic-" not in str(row.link_report)


@pytest.mark.parametrize("source,replace", [("upload", False), ("local", False), ("local", True)])
def test_a_match_deleted_on_request_is_refused_by_every_store(db, condensed, source, replace):
    db.add(ReplayDeletion(match_uuid=condensed.match_uuid.lower(), reason="asked"))
    db.commit()
    with pytest.raises(store.StoreRefused, match="deleted on request"):
        store.store_replay(db, condensed, source=source, replace=replace)
    assert db.query(Replay).count() == 0


def test_an_unknown_match_is_stored_unlinked_and_links_later(db, condensed):
    result = store.store_replay(db, condensed, source="upload")
    assert (result.action, result.link_status) == ("stored", "unlinked")
    add_match(db)
    row = db.get(Replay, result.replay_id)
    replay_db.advisory_lock(db, row.match_uuid)
    assert replay_db.link_replay(db, row) == "linked"
    db.commit()
    assert db.get(Replay, result.replay_id).link_status == "linked"


def test_the_same_file_and_recipe_is_a_no_op(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="local")
    again = store.store_replay(db, condensed, source="local")
    assert again.action == "unchanged" and again.replay_id == first.replay_id


def test_a_new_recipe_of_the_same_file_replaces_it(db, condensed):
    add_match(db)
    store.store_replay(db, condensed, source="local")
    newer = replace(condensed, recipe=condensed.recipe + "x")
    result = store.store_replay(db, newer, source="local")
    assert result.action == "replaced" and db.query(Replay).one().recipe.endswith("x")


def test_another_recording_never_replaces_a_linked_replay(db, condensed):
    add_match(db)
    first = store.store_replay(db, condensed, source="local")
    other = replace(condensed, source_sha256="f" * 64)
    result = store.store_replay(db, other, source="upload")
    assert result.action == "kept_existing" and db.query(Replay).one().id == first.replay_id
    assert db.query(Replay).one().source_sha256 == condensed.source_sha256


def test_another_recording_replaces_an_unlinked_one_only_if_it_links(db, condensed):
    first = store.store_replay(db, condensed, source="upload")  # no match yet: unlinked
    other = replace(condensed, source_sha256="f" * 64)
    kept = store.store_replay(db, other, source="upload")  # still no match: doesn't link, so doesn't replace
    assert kept.action == "kept_existing" and db.query(Replay).one().source_sha256 == condensed.source_sha256
    add_match(db)
    result = store.store_replay(db, other, source="upload")
    assert (result.action, result.link_status) == ("replaced", "linked")
    assert db.query(Replay).one().source_sha256 == "f" * 64
    assert db.query(ReplayRound).count() == ROUNDS and db.query(ReplayPlayer).count() == 10
    assert first.replay_id is not None


def test_replace_overrides_the_dedupe_rule(db, condensed):
    add_match(db)
    store.store_replay(db, condensed, source="local")
    other = replace(condensed, source_sha256="f" * 64)
    result = store.store_replay(db, other, source="local", replace=True)
    assert result.action == "replaced" and db.query(Replay).one().source_sha256 == "f" * 64


def test_demo_mode_refuses_whatever_called_it(db, condensed, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    with pytest.raises(store.StoreRefused):
        store.store_replay(db, condensed, source="local")
    assert db.query(Replay).count() == 0


def test_a_failure_rolls_everything_back_and_the_old_rows_survive(db, condensed, monkeypatch):
    add_match(db)
    store.store_replay(db, condensed, source="local")
    newer = replace(condensed, recipe=condensed.recipe + "x")

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(replay_db, "link_replay", broken)
    with pytest.raises(RuntimeError):
        store.store_replay(db, newer, source="local")
    row = db.query(Replay).one()
    assert row.recipe == condensed.recipe and db.query(ReplayRound).count() == ROUNDS


def test_the_backfill_writes_subjects_and_refuses_a_conflict(db, condensed, tmp_path):
    with_riot_subject(db)
    add_match(db)
    result = store.store_replay(db, condensed, source="local")
    assert result.link_status == "linked"
    subjects = dict(db.execute(text("SELECT id, riot_subject FROM players")).all())
    assert sorted(s for s in subjects.values() if s) == sorted(p["subject"].lower() for p in condensed.players)
    # A second match whose player already holds a different Subject refuses its link.
    db.execute(text("UPDATE players SET riot_subject = :s WHERE id = (SELECT max(id) FROM players)"),
               {"s": subject(99)})
    db.commit()
    row = db.get(Replay, result.replay_id)
    replay_db.advisory_lock(db, row.match_uuid)
    status = replay_db.link_replay(db, row)
    assert status == "refused" and row.link_report["check"] in ("backfill", "assignment")
    assert row.match_id is None and row.clock_offset is None


def test_without_a_write_identity_the_link_skips_the_backfill(db, condensed, monkeypatch):
    # The release write gate refuses `players` writes from a connection with no verified identity
    # (the web process; a script that skipped the preflight): the link goes ahead without Subjects.
    with_riot_subject(db)
    add_match(db)
    monkeypatch.setattr(replay_db, "may_write_players", lambda session: False)
    result = store.store_replay(db, condensed, source="local")
    assert result.link_status == "linked"
    row = db.get(Replay, result.replay_id)
    assert row.link_report["backfill"] == "skipped: no release write identity"
    assert db.execute(text("SELECT count(*) FROM players WHERE riot_subject IS NOT NULL")).scalar() == 0


def test_no_gate_means_players_are_writable(db):
    assert replay_db.may_write_players(db)  # sqlite: no gate, as on a local database without one


def test_an_incomplete_round_set_is_invalid(db, condensed):
    add_match(db)
    result = store.store_replay(db, condensed, source="local")
    db.query(ReplayRound).filter(ReplayRound.round_number == ROUNDS).delete()
    db.commit()
    assert not replay_db.is_valid(db, db.get(Replay, result.replay_id))


def test_the_split_shows_only_while_its_fingerprint_is_current(db, condensed, monkeypatch):
    # impact_scores has a JSONB column sqlite can't create: the fingerprint itself is PostgreSQL's.
    add_match(db)
    result = store.store_replay(db, condensed, source="local")
    row = db.get(Replay, result.replay_id)
    row.kill_impact = {"fingerprint": "abc", "kills": {"1": [10.0, -5.0]}}
    db.commit()
    monkeypatch.setattr(replay_db, "impact_fingerprint", lambda session, match_id: "abc")
    assert replay_db.kill_impact_for_page(db, row) == {1: [10.0, -5.0]}
    monkeypatch.setattr(replay_db, "impact_fingerprint", lambda session, match_id: "rescored")
    assert replay_db.kill_impact_for_page(db, row) is None


# ---------------------------------------------------------------- PostgreSQL only


@pytest.fixture
def pg():
    from _postgres import postgres_url_or_skip

    url = postgres_url_or_skip(writes=True)
    engine = create_engine(url)
    with engine.begin() as conn:
        for table in ("replay_players", "replay_rounds", "replay_uploads", "replays", "impact_scores",
                      "kill_events", "round_player_stats", "rounds", "match_players", "matches", "friendships",
                      "players"):
            conn.execute(text(f"DELETE FROM {table}"))
    factory = sessionmaker(bind=engine)
    yield factory
    with engine.begin() as conn:
        for table in ("replay_players", "replay_rounds", "replay_uploads", "replays", "kill_events", "rounds",
                      "match_players", "matches", "players"):
            conn.execute(text(f"DELETE FROM {table}"))
    engine.dispose()


def test_pg_deleting_a_match_unlinks_its_replay_and_clears_the_link(pg, condensed):
    session = pg()
    match, _ = add_match(session)
    result = store.store_replay(session, condensed, source="local")
    session.execute(text("UPDATE replays SET kill_impact = '{\"kills\": {}}'::jsonb"))
    session.commit()
    inputs = session.get(Replay, result.replay_id).link_inputs
    session.execute(text("DELETE FROM kill_events"))
    session.execute(text("DELETE FROM rounds"))
    session.execute(text("DELETE FROM match_players"))
    session.execute(text("DELETE FROM matches WHERE id = :m"), {"m": match.id})
    session.commit()
    session.expire_all()
    row = session.get(Replay, result.replay_id)
    assert (row.link_status, row.match_id, row.clock_offset, row.kill_map, row.db_deaths, row.kill_impact,
            row.linked_at) == ("unlinked", None, None, None, None, None, None)
    assert row.link_report["unlinked"] == "match deleted" and row.link_inputs == inputs
    assert session.query(ReplayPlayer).filter(ReplayPlayer.match_player_id.isnot(None)).count() == 0
    assert session.query(Replay).filter(Replay.match_id.is_(None)).count() == 1
    session.close()


def test_pg_a_relink_write_is_not_undone_by_the_trigger(pg, condensed):
    session = pg()
    add_match(session)
    result = store.store_replay(session, condensed, source="local")
    row = session.get(Replay, result.replay_id)
    replay_db.clear_link(session, row, "refused", {"check": "test"})
    session.commit()
    session.expire_all()
    assert session.get(Replay, result.replay_id).link_status == "refused"
    session.close()


def test_pg_a_forced_failure_keeps_the_old_rows(pg, condensed, monkeypatch):
    session = pg()
    add_match(session)
    store.store_replay(session, condensed, source="local")
    monkeypatch.setattr(replay_db, "link_replay", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        store.store_replay(session, replace(condensed, recipe="other"), source="local")
    assert session.query(Replay).one().recipe == condensed.recipe
    session.close()


def test_pg_concurrent_stores_of_one_replay_serialise(pg, condensed):
    setup = pg()
    add_match(setup)
    setup.close()
    results, errors = [], []

    def run():
        session = pg()
        try:
            results.append(store.store_replay(session, condensed, source="local").action)
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        finally:
            session.close()

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and sorted(results) == ["stored", "unchanged", "unchanged"]


def test_pg_the_split_after_commit_fails_softly_and_the_link_stands(pg, condensed, monkeypatch):
    from app.services import replay_impact

    session = pg()
    add_match(session)
    result = store.store_replay(session, condensed, source="local")
    session.close()

    def broken(session, match_id):
        session.execute(text("SELECT * FROM no_such_table"))

    monkeypatch.setattr(replay_impact, "compute_split", broken)
    status = replay_impact.refresh_replay_impact(pg, result.replay_id)
    assert status.startswith("failed")
    check = pg()
    row = check.get(Replay, result.replay_id)
    assert row.link_status == "linked" and row.kill_impact is None
    check.close()
    assert fmt.FORMAT_VERSION == 1


# ---------------------------------------------------------------- link_replays.py and reingest_replays.py


@pytest.fixture
def factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=TABLES)
    return sessionmaker(bind=engine)


def test_the_link_later_pass_links_replays_whose_match_arrived(factory, condensed, monkeypatch):
    from app.services import replay_impact

    session = factory()
    store.store_replay(session, condensed, source="upload")
    session.close()
    monkeypatch.setattr(replay_impact, "refresh_replay_impact", lambda f, replay_id: "failed: no scorer in tests")
    assert replay_impact.link_pending_replays(factory) == {"still no match": 1}
    session = factory()
    add_match(session)
    session.close()
    counts = replay_impact.link_pending_replays(factory)
    assert counts == {"linked": 1, "per-kill failed": 1}
    session = factory()
    assert session.query(Replay).one().link_status == "linked"
    session.close()
    # Linked replays are not re-linked by a plain pass; --uuid relinks whatever the state.
    assert replay_impact.link_pending_replays(factory) == {"per-kill failed": 1}
    assert replay_impact.link_pending_replays(factory, [MATCH_UUID.upper()])["linked"] == 1


def test_the_link_later_pass_never_raises():
    from app.services import replay_impact

    assert "skipped" in replay_impact.link_pending_replays(lambda: _Broken())


class _Broken:
    def query(self, *a, **k):
        raise RuntimeError("no replays table")

    def rollback(self):
        pass

    def close(self):
        pass


def test_reingest_lists_stale_and_invalid_replays(factory, condensed, tmp_path):
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    import reingest_replays

    session = factory()
    add_match(session)
    store.store_replay(session, condensed, source="local")
    current = condensed.recipe
    assert reingest_replays.plan(session, current, tmp_path) == []
    [entry] = reingest_replays.plan(session, current + "-newer", tmp_path)
    assert entry["stale"] and entry["action"].startswith("refused: the archive has no .vrf")
    (tmp_path / f"{MATCH_UUID}.vrf").write_bytes(b"not the stored file")
    assert "differs" in reingest_replays.plan(session, current + "-newer", tmp_path)[0]["action"]
    row = session.query(Replay).one()
    row.source = "upload"
    session.query(ReplayRound).filter(ReplayRound.round_number == 1).delete()
    session.commit()
    [entry] = reingest_replays.plan(session, current, tmp_path)
    assert (entry["valid"], entry["action"]) == (False, "re-upload to refresh")
    session.close()
