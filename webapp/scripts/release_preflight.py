"""Assert which database a release step is about to touch, before it touches it.

The production and rehearsal URLs differ only in the database name, so the name
is asserted, never eyeballed (docs/superpowers/plans/2026-09-16-rc3-ship-plan-v2.md,
section 1). Every runbook step runs this first with the values the runbook
expects at that point, and a mismatch stops the step before its first write:

    DATABASE_URL=... python scripts/release_preflight.py --expect-database valo_rc3_rehearsal \
        --expect-alembic 0010 --expect-matches 3125 --expect-max-match-id 3133 \
        --expect-gate closed --max-transaction-seconds 60

It prints the server's clock at the moment it passed, which is the restore point
to note before the first production write (Stage 7.2).

Exit codes: 0 every expectation holds; 3 at least one does not.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.scoring.write_gate import read_gate

EXIT_MISMATCH = 3


def read_target(db, *, max_transaction_seconds: int | None = None) -> dict:
    """What the connected database actually is. Read-only."""
    def scalar(sql, **params):
        return db.execute(text(sql), params).scalar()

    has_alembic = scalar("SELECT to_regclass('public.alembic_version')") is not None
    has_matches = scalar("SELECT to_regclass('public.matches')") is not None
    gate = read_gate(db)
    facts = {
        "database": scalar("SELECT current_database()"),
        "alembic": scalar("SELECT version_num FROM alembic_version") if has_alembic else None,
        "matches": scalar("SELECT count(*) FROM matches") if has_matches else None,
        "max_match_id": scalar("SELECT max(id) FROM matches") if has_matches else None,
        "gate": gate.state if gate else "absent",
        "server_time": scalar("SELECT now()::text"),
        "long_transactions": [],
    }
    if max_transaction_seconds is not None:
        # Only who, how long and in what state -- never the query text.
        facts["long_transactions"] = [
            f"pid {pid} ({application or 'no application name'}, {state}) open {int(age)}s"
            for pid, application, state, age in db.execute(text(
                "SELECT pid, application_name, state, EXTRACT(EPOCH FROM now() - xact_start) "
                "FROM pg_stat_activity WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() AND xact_start IS NOT NULL "
                "AND now() - xact_start > make_interval(secs => :seconds) ORDER BY xact_start"),
                {"seconds": max_transaction_seconds}).all()
        ]
    return facts


def mismatches(facts: dict, expected: dict) -> list[str]:
    """Every expectation that does not hold. An expectation of None is not checked."""
    found = [f"{name}: expected {want}, found {facts.get(name)}"
             for name, want in expected.items()
             if want is not None and str(facts.get(name)) != str(want)]
    found += [f"long-running transaction: {line}" for line in facts.get("long_transactions") or []]
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect-database", required=True)
    parser.add_argument("--expect-alembic")
    parser.add_argument("--expect-matches", type=int)
    parser.add_argument("--expect-max-match-id", type=int)
    parser.add_argument("--expect-gate", choices=("closed", "open", "absent"))
    parser.add_argument("--max-transaction-seconds", type=int,
                        help="refuse while another session has a transaction open longer than this")
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        facts = read_target(db, max_transaction_seconds=args.max_transaction_seconds)
        db.rollback()
    finally:
        db.close()

    problems = mismatches(facts, {
        "database": args.expect_database,
        "alembic": args.expect_alembic,
        "matches": args.expect_matches,
        "max_match_id": args.expect_max_match_id,
        "gate": args.expect_gate,
    })
    print(f"database {facts['database']}: alembic {facts['alembic']}, {facts['matches']} matches "
          f"(max id {facts['max_match_id']}), gate {facts['gate']}, server time {facts['server_time']}")
    if problems:
        for problem in problems:
            print(f"  MISMATCH {problem}")
        print("PREFLIGHT FAILED: stop here")
        return EXIT_MISMATCH
    print("preflight: every expectation holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
