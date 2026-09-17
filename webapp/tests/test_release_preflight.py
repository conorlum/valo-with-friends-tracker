"""A release step checks which database it is on before it does anything.

The production and rehearsal URLs differ only by the database name, so the
preflight compares names and states exactly and treats anything it was not told
to expect as unchecked rather than as a match.
"""

from sqlalchemy import text

from scripts import release_preflight as preflight
from tests._postgres import postgres_session_or_skip

FACTS = {"database": "valo_rc3_rehearsal", "alembic": "0010", "matches": 3125, "max_match_id": 3133,
         "gate": "closed", "long_transactions": []}


def test_every_expectation_holding_is_no_mismatch():
    assert preflight.mismatches(FACTS, {"database": "valo_rc3_rehearsal", "alembic": "0010",
                                        "matches": 3125, "max_match_id": 3133, "gate": "closed"}) == []


def test_the_wrong_database_is_a_mismatch_even_when_everything_else_holds():
    assert preflight.mismatches(FACTS, {"database": "valo_prod", "alembic": "0010", "matches": 3125}) == [
        "database: expected valo_prod, found valo_rc3_rehearsal"]


def test_each_expectation_is_checked():
    found = preflight.mismatches(FACTS, {"database": "valo_rc3_rehearsal", "alembic": "0007",
                                         "matches": 3126, "max_match_id": 3134, "gate": "open"})
    assert found == ["alembic: expected 0007, found 0010", "matches: expected 3126, found 3125",
                     "max_match_id: expected 3134, found 3133", "gate: expected open, found closed"]


def test_an_expectation_left_out_is_not_checked():
    assert preflight.mismatches(FACTS, {"database": "valo_rc3_rehearsal", "alembic": None}) == []


def test_a_long_running_transaction_is_a_mismatch():
    facts = dict(FACTS, long_transactions=["pid 42 (psql, idle in transaction) open 900s"])
    assert preflight.mismatches(facts, {"database": "valo_rc3_rehearsal"}) == [
        "long-running transaction: pid 42 (psql, idle in transaction) open 900s"]


def test_the_facts_read_from_a_real_database_satisfy_themselves():
    db = postgres_session_or_skip()
    try:
        facts = preflight.read_target(db, max_transaction_seconds=3600)
        assert facts["database"] == db.execute(text("SELECT current_database()")).scalar()
        assert facts["gate"] in ("closed", "open", "absent")
        expected = {name: facts[name] for name in ("database", "alembic", "matches", "max_match_id", "gate")}
        assert preflight.mismatches(facts, expected) == []
    finally:
        db.rollback()
        db.close()
