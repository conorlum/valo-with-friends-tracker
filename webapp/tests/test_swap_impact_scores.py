"""Building a replacement scores table and renaming it in, then back out.

The forward path and the rollback are the same operation in opposite
directions, and both must be all-or-nothing: at no point may a reader see a
half-replaced table. Needs a disposable PostgreSQL database (tests/_postgres.py).
"""

import types
import uuid

import pytest
from sqlalchemy import text

from app.models import Match, MatchPlayer, Player, Round, RoundPlayerStat
from app.models.match import MatchSource, Team
from app.scoring.write_gate import install_write_identity, read_gate
from scripts import swap_impact_scores as swap_tool
from scripts.export_impact_artifact import load_columns, write_load_artifact
from tests._postgres import postgres_session_or_skip

ADMIN = "rc3-runbook"


@pytest.fixture
def db():
    session = postgres_session_or_skip()
    if read_gate(session) is None:
        pytest.skip("the write gate is not installed on the test database")
    install_write_identity(session, ADMIN)
    try:
        yield session
    finally:
        session.rollback()
        for leftover in (swap_tool.BUILT, swap_tool.PREVIOUS, swap_tool.ROLLED_BACK):
            if swap_tool._table_exists(session, leftover):
                session.execute(text(f"DROP TABLE {leftover}"))
        session.execute(text("DELETE FROM impact_scores"))
        session.execute(text("DELETE FROM round_player_stats"))
        session.execute(text("DELETE FROM kill_events"))
        session.execute(text("DELETE FROM rounds"))
        session.execute(text("DELETE FROM match_players"))
        session.execute(text("DELETE FROM matches"))
        session.execute(text("UPDATE scoring_gate SET state = 'closed' WHERE id"))
        session.commit()
        session.close()


def _corpus(db, rounds=2, players=2):
    """A handful of real stat rows, so key-set checks have something to match."""
    match = Match(external_id=f"swap-{uuid.uuid4()}", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    match_players = []
    for index in range(players):
        player = Player(display_name=f"swap-{uuid.uuid4()}")
        db.add(player)
        db.flush()
        match_player = MatchPlayer(match_id=match.id, player_id=player.id, agent="Jett",
                                   team=Team.TEAM_1 if index % 2 == 0 else Team.TEAM_2)
        db.add(match_player)
        match_players.append(match_player)
    db.flush()
    keys = []
    for number in range(1, rounds + 1):
        round_row = Round(match_id=match.id, round_number=number, outcome="Team A Elimination Win",
                          planted=False, exploded=False, defused=False)
        db.add(round_row)
        db.flush()
        for match_player in match_players:
            db.add(RoundPlayerStat(round_id=round_row.id, match_player_id=match_player.id,
                                   score=100, kills=1, deaths=0, assists=0, loadout=3900,
                                   remaining=1000))
            keys.append((round_row.id, match_player.id))
    db.flush()
    return keys


def _row(round_id, match_player_id, *, impact, scoring_version):
    values = {name: 0 for name in load_columns()}
    values.update(round_id=round_id, match_player_id=match_player_id, impact=impact,
                  trade_detail=None, scoring_version=scoring_version)
    return types.SimpleNamespace(**values)


def _install_v1(db, keys, *, impact=10):
    """The rows the live table starts with. Every column is written: they are
    NOT NULL with no default, exactly as production has them."""
    columns = list(load_columns())
    statement = text(f"INSERT INTO impact_scores ({', '.join(columns)}) "
                     f"VALUES ({', '.join(':' + name for name in columns)})")
    for round_id, match_player_id in keys:
        values = {name: 0 for name in columns}
        values.update(round_id=round_id, match_player_id=match_player_id, impact=impact,
                      trade_detail=None, scoring_version=1)
        db.execute(statement, values)
    db.flush()


def _artifact(tmp_path, keys, *, impact, scoring_version=3):
    rows = [_row(r, m, impact=impact, scoring_version=scoring_version) for r, m in keys]
    path = tmp_path / "K5.load.csv"
    written = write_load_artifact(rows, path)
    return str(path), written


def test_build_then_verify_then_swap_replaces_every_row(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    path, written = _artifact(tmp_path, keys, impact=77)

    assert swap_tool.build(db, path)["rows"] == written["rows"]
    db.commit()

    facts = swap_tool.verify_build(db, path, expect_scoring_version=3, approved_path=None)
    assert facts["problems"] == []
    assert facts["artifact_sha256"] == facts["read_back_sha256"]

    swap_tool.swap(db)
    db.commit()

    assert [v for (v,) in db.execute(text("SELECT DISTINCT impact FROM impact_scores"))] == [77]
    assert [v for (v,) in db.execute(text(
        f"SELECT DISTINCT impact FROM {swap_tool.PREVIOUS}"))] == [10]
    assert db.execute(text(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'impact_scores_pkey'")).scalar() == 1


def test_rollback_restores_the_previous_rows_and_closes_the_gate(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    path, _ = _artifact(tmp_path, keys, impact=77)
    swap_tool.build(db, path)
    db.commit()
    swap_tool.swap(db)
    db.commit()
    db.execute(text("UPDATE scoring_gate SET state = 'open' WHERE id"))
    db.commit()

    swap_tool.rollback(db)
    db.commit()

    assert [v for (v,) in db.execute(text("SELECT DISTINCT impact FROM impact_scores"))] == [10]
    assert read_gate(db).state == "closed", "a rollback must leave scoring frozen"
    assert swap_tool._table_exists(db, swap_tool.ROLLED_BACK), "keep the rc3 rows for inspection"


def test_verify_refuses_an_artifact_that_misses_a_player_round(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, _ = _artifact(tmp_path, keys[:-1], impact=77)  # one player-round short

    swap_tool.build(db, path)
    facts = swap_tool.verify_build(db, path, expect_scoring_version=3, approved_path=None)
    assert any("key set differs" in problem for problem in facts["problems"])


def test_verify_refuses_the_wrong_scoring_version(db, tmp_path):
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, _ = _artifact(tmp_path, keys, impact=77, scoring_version=2)

    swap_tool.build(db, path)
    facts = swap_tool.verify_build(db, path, expect_scoring_version=3, approved_path=None)
    assert any("scoring_version is [2]" in problem for problem in facts["problems"])


def test_verify_catches_a_single_edited_row(db, tmp_path):
    """The built table is compared against the artifact by reading it back, so a
    row changed after the load cannot pass."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, _ = _artifact(tmp_path, keys, impact=77)
    swap_tool.build(db, path)
    db.execute(text(f"UPDATE {swap_tool.BUILT} SET impact = 78 "
                    "WHERE round_id = :r AND match_player_id = :m"),
               {"r": keys[0][0], "m": keys[0][1]})

    facts = swap_tool.verify_build(db, path, expect_scoring_version=3, approved_path=None)
    assert any("does not read back" in problem for problem in facts["problems"])


def test_an_interrupted_swap_changes_nothing(db, tmp_path):
    """Everything the swap does is inside one transaction, so a process that
    dies mid-swap leaves the live table exactly as it was."""
    keys = _corpus(db)
    _install_v1(db, keys, impact=10)
    db.commit()
    path, _ = _artifact(tmp_path, keys, impact=77)
    swap_tool.build(db, path)
    db.commit()

    swap_tool.swap(db)
    db.rollback()  # the process dies before COMMIT

    assert [v for (v,) in db.execute(text("SELECT DISTINCT impact FROM impact_scores"))] == [10]
    assert not swap_tool._table_exists(db, swap_tool.PREVIOUS)
    assert swap_tool._table_exists(db, swap_tool.BUILT), "the built table survives for a retry"


def test_the_built_table_is_gated_too(db, tmp_path):
    """Nothing may edit the replacement between verification and the swap."""
    keys = _corpus(db)
    _install_v1(db, keys)
    db.commit()
    path, _ = _artifact(tmp_path, keys, impact=77)
    swap_tool.build(db, path)
    db.commit()

    other = postgres_session_or_skip()
    try:
        with pytest.raises(Exception) as caught:
            other.execute(text(f"UPDATE {swap_tool.BUILT} SET impact = 1"))
        assert "release write gate refused" in str(caught.value)
    finally:
        other.rollback()
        other.close()
