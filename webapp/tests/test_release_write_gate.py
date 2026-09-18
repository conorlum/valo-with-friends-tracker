"""The release write gate refuses writers, not row values.

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md, after
an external review showed that `impact_scores.scoring_version` cannot police
who writes: a checkout whose model does not know the column can UPDATE a row
that already says 3, leave the value untouched, and satisfy any CHECK on it.
test_a_writer_that_never_touches_the_version_is_still_refused is that exact
case, and it is the reason the gate is a trigger.

Needs a disposable PostgreSQL database (see tests/_postgres.py); skipped
otherwise, because triggers cannot be exercised on sqlite.
"""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import InternalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from app.models import ImpactScore, Match, MatchPlayer, Player, Round
from app.models.match import MatchSource, Team
from app.scoring.write_gate import claim_write_identity, install_write_identity, read_gate
from scripts.install_release_write_gate import INSTALL_LOCK_TIMEOUT, install
from tests._postgres import postgres_session_or_skip, postgres_url_or_skip

REFUSED = (InternalError, ProgrammingError)
GATED_TABLES = ("impact_scores", "matches", "match_players", "rounds",
                "round_player_stats", "round_player_spend", "kill_events")


@pytest.fixture
def db():
    session = postgres_session_or_skip()
    if read_gate(session) is None:
        pytest.skip("the write gate is not installed on the test database")
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _gate(db, state, release_id="impact-rc3", admin_id="rc3-runbook"):
    """Set the gate inside the test's own transaction, so it rolls back."""
    db.execute(text("UPDATE scoring_gate SET state = :s, release_id = :r, admin_id = :a WHERE id"),
               {"s": state, "r": release_id, "a": admin_id})


def _a_match(db):
    match = Match(external_id=f"gate-{uuid.uuid4()}", source=MatchSource.SCRAPED, map_name="Bind")
    db.add(match)
    db.flush()
    return match


def _a_scored_row(db, *, scoring_version):
    """A whole scored player-round, written as the runbook."""
    match = _a_match(db)
    player = Player(display_name=f"gate-{uuid.uuid4()}")
    db.add(player)
    db.flush()
    match_player = MatchPlayer(match_id=match.id, player_id=player.id, agent="Jett",
                               team=Team.TEAM_1)
    round_row = Round(match_id=match.id, round_number=1, outcome="Team A Elimination Win",
                      planted=False, exploded=False, defused=False)
    db.add_all([match_player, round_row])
    db.flush()
    score = ImpactScore(round_id=round_row.id, match_player_id=match_player.id, kill_impact=0,
                        death_impact=0, impact=100, damage=100, scoring_version=scoring_version)
    db.add(score)
    db.flush()
    return score


def test_every_declared_table_is_gated_for_every_write(db):
    rows = db.execute(text(
        "SELECT c.relname, count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE NOT t.tgisinternal AND t.tgname LIKE 'scoring_gate_%' GROUP BY c.relname")).all()
    gated = dict(rows)
    assert set(gated) == set(GATED_TABLES)
    assert set(gated.values()) == {4}, "insert, update, delete and truncate each need a trigger"


def test_installing_bounds_how_long_it_can_stall_the_site(db):
    """Re-installing drops and recreates the triggers, which takes ACCESS
    EXCLUSIVE on every gated table: readers queue behind it. Bounded, it gives
    up instead, having changed nothing."""
    install(db, state=None, release_id=None, admin_id=None, note="lock timeout test")
    assert db.execute(text("SHOW lock_timeout")).scalar() == INSTALL_LOCK_TIMEOUT
    db.rollback()


def test_a_write_with_no_identity_is_refused(db):
    _gate(db, "open")
    with pytest.raises(REFUSED) as caught:
        _a_match(db)
    assert "release write gate refused" in str(caught.value)


def test_an_empty_identity_is_no_identity_even_against_a_blank_gate_row(db):
    """An identity that was set and then rolled back reads back as '' rather
    than NULL. It must never match a gate row whose ids were left blank."""
    _gate(db, "open", release_id="", admin_id="")
    claim_write_identity(db, "")
    with pytest.raises(REFUSED) as caught:
        _a_match(db)
    assert "connection identity (none)" in str(caught.value)


def test_the_open_release_may_write(db):
    _gate(db, "open")
    install_write_identity(db, "impact-rc3")
    assert _a_match(db).id is not None


def test_a_different_release_is_refused(db):
    _gate(db, "open", release_id="impact-rc4")
    install_write_identity(db, "impact-rc3")
    with pytest.raises(REFUSED):
        _a_match(db)


def test_a_closed_gate_refuses_even_the_open_release(db):
    _gate(db, "closed")
    install_write_identity(db, "impact-rc3")
    with pytest.raises(REFUSED) as caught:
        _a_match(db)
    assert "closed" in str(caught.value)


def test_the_admin_identity_writes_through_a_closed_gate(db):
    """The runbook's swaps and rollbacks run while everything else is frozen."""
    _gate(db, "closed")
    install_write_identity(db, "rc3-runbook")
    assert _a_match(db).id is not None


def test_a_writer_that_never_touches_the_version_is_still_refused(db):
    """B1: the case a CHECK (scoring_version = 3) would have allowed.

    An old checkout updates the columns it knows about on a row that already
    says 3. Its new row value still says 3, so a row-value constraint passes
    it; the gate refuses it because the connection has no identity.
    """
    _gate(db, "closed")
    install_write_identity(db, "rc3-runbook")
    score = _a_scored_row(db, scoring_version=3)
    db.commit()

    fresh = postgres_session_or_skip()
    try:
        _gate(fresh, "open")  # rc3 ingestion is allowed; this writer is not rc3
        with pytest.raises(REFUSED) as caught:
            fresh.execute(text("UPDATE impact_scores SET damage = 999 WHERE round_id = :r"),
                          {"r": score.round_id})
            fresh.flush()
        assert "release write gate refused" in str(caught.value)
        fresh.rollback()
        still = fresh.execute(text(
            "SELECT damage, scoring_version FROM impact_scores WHERE round_id = :r"),
            {"r": score.round_id}).one()
        assert still == (100, 3), "the stale write must not have landed"
    finally:
        fresh.rollback()
        fresh.close()

    cleanup = postgres_session_or_skip()
    try:
        install_write_identity(cleanup, "rc3-runbook")
        cleanup.execute(text("DELETE FROM impact_scores WHERE round_id = :r"), {"r": score.round_id})
        cleanup.execute(text("DELETE FROM rounds WHERE id = :r"), {"r": score.round_id})
        match_id, player_id = cleanup.execute(text(
            "SELECT match_id, player_id FROM match_players WHERE id = :m"),
            {"m": score.match_player_id}).one()
        cleanup.execute(text("DELETE FROM match_players WHERE id = :m"),
                        {"m": score.match_player_id})
        cleanup.execute(text("DELETE FROM matches WHERE id = :m"), {"m": match_id})
        cleanup.execute(text("DELETE FROM players WHERE id = :p"), {"p": player_id})
        cleanup.commit()
    finally:
        cleanup.close()


def test_delete_is_refused(db):
    _gate(db, "open")
    install_write_identity(db, "not-the-release")
    with pytest.raises(REFUSED):
        db.execute(text("DELETE FROM impact_scores WHERE round_id = -1"))


def test_truncate_is_refused(db):
    _gate(db, "open")
    install_write_identity(db, "not-the-release")
    with pytest.raises(REFUSED):
        db.execute(text("TRUNCATE impact_scores"))


# ---- the identity must reach every connection the pool hands out ----------------------

def _pooled_engine():
    """A real QueuePool. The shared helper uses NullPool, which opens a fresh
    connection per checkout and so hides both cases below."""
    return create_engine(postgres_url_or_skip())


def test_a_connection_opened_before_the_install_still_carries_the_identity():
    """The pool can already hold connections; a connect-time hook never fires
    for those, and the writer that gets one would be refused mid-ingest."""
    engine = _pooled_engine()
    try:
        warmed = [engine.connect() for _ in range(3)]
        for connection in warmed:
            connection.close()  # checked back in, never identified
        installer = sessionmaker(bind=engine)()
        if read_gate(installer) is None:
            pytest.skip("the write gate is not installed on the test database")
        install_write_identity(installer, "rc3-runbook")
        installer.commit()
        installer.close()

        writer = sessionmaker(bind=engine)()
        try:
            assert _a_match(writer).id is not None
        finally:
            writer.rollback()
            writer.close()
    finally:
        engine.dispose()


def test_the_identity_outlives_a_transaction_that_rolled_back():
    """set_config is reverted with the transaction that made it, and the pool
    hands that same connection out again without opening it afresh."""
    engine = _pooled_engine()
    try:
        db = sessionmaker(bind=engine)()
        if read_gate(db) is None:
            pytest.skip("the write gate is not installed on the test database")
        install_write_identity(db, "rc3-runbook")
        db.rollback()  # the claim goes with it

        try:
            assert _a_match(db).id is not None
        finally:
            db.rollback()
            db.close()
    finally:
        engine.dispose()
