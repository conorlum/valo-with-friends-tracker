"""Install or re-state the release write gate (scripts/sql/release_write_gate.sql).

Idempotent: re-running reinstalls the triggers and leaves the gate's state as it
is unless --state is given. Installs CLOSED by default, which is what Stage 7 of
docs/superpowers/plans/2026-09-16-rc3-ship-plan-v2.md wants: from the moment the
migrations land until the observation hold ends, nothing but the runbook writes.

    # install, closed, before merging PR #67
    python scripts/install_release_write_gate.py --admin-id "rc3-runbook" \
        --release-id "impact-rc3" --state closed --note "installed with 0010"

    # after the 48-hour hold, let rc3 ingestion back in
    python scripts/install_release_write_gate.py --state open --note "hold over"

    # after a rollback
    python scripts/install_release_write_gate.py --state closed --note "R1"

Reads DATABASE_URL like every other script here; it prints the database name it
is about to change and refuses nothing -- check the name before you run it.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring.write_gate import read_gate

SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "release_write_gate.sql")


def install(db, *, state: str | None, release_id: str | None, admin_id: str | None,
            note: str | None) -> None:
    db.execute(text(open(SQL_PATH, encoding="utf-8").read()))
    existing = db.execute(text("SELECT count(*) FROM scoring_gate WHERE id")).scalar()
    if not existing:
        db.execute(text(
            "INSERT INTO scoring_gate (id, state, release_id, admin_id, note) "
            "VALUES (true, :state, :release_id, :admin_id, :note)"),
            {"state": state or "closed", "release_id": release_id or "impact-rc3",
             "admin_id": admin_id or "rc3-runbook", "note": note})
        return
    sets, params = ["updated_at = now()"], {}
    for column, value in (("state", state), ("release_id", release_id),
                          ("admin_id", admin_id), ("note", note)):
        if value is not None:
            sets.append(f"{column} = :{column}")
            params[column] = value
    db.execute(text(f"UPDATE scoring_gate SET {', '.join(sets)} WHERE id"), params)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", choices=("closed", "open"))
    parser.add_argument("--release-id")
    parser.add_argument("--admin-id")
    parser.add_argument("--note")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        database = db.execute(text("SELECT current_database()")).scalar()
        before = read_gate(db)
        print(f"database {database}: gate before = {before}")
        install(db, state=args.state, release_id=args.release_id, admin_id=args.admin_id,
                note=args.note)
        db.commit()
        print(f"database {database}: gate after  = {read_gate(db)}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
