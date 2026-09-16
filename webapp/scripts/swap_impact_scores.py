"""Replace every impact_scores row by building a table and renaming it in.

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md; the
design is section 3 of docs/superpowers/plans/2026-09-16-rc3-ship-plan-v2.md.

WHY NOT TRUNCATE-AND-INSERT. A player page reads player_view_cache first and
impact_scores second (app/services/players.py, app/services/player_data.py). A
swap that truncated impact_scores first and the cache second would take those
locks in the opposite order, which is a deadlock, and it would hold ACCESS
EXCLUSIVE on the scores for as long as the insert took -- every reader waiting.
Building the replacement first and renaming it in holds the lock for
milliseconds, and the old table survives as the rollback source with its indexes
intact. It is migration 0006's pattern, which this database has been through
before.

The subcommands are separate on purpose: everything slow happens while the site
is up and nothing is committed to the live table until `swap`, which is one
transaction.

    build         create impact_scores_new and COPY the load projection in
    verify-build  count, key set, version, hash and approved rows -- read-only
    swap          one transaction: locks, renames, cache clear
    rollback      the same in reverse, and close the gate
    state         print what the database currently looks like

Every subcommand needs the gate's admin identity (scripts/sql/release_write_gate.sql).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.db import SessionLocal
from app.scoring.write_gate import install_write_identity, read_gate
from scripts.export_impact_artifact import canonical_json, load_columns, render_field

LIVE = "impact_scores"
BUILT = "impact_scores_new"
PREVIOUS = "impact_scores_v1"
ROLLED_BACK = "impact_scores_rc3_rolled_back"

#: A swap that cannot take its locks in this long changes nothing and is retried.
SWAP_LOCK_TIMEOUT = "5s"


def _identity(db, identity: str) -> None:
    install_write_identity(db, identity)


def _scalar(db, sql, **params):
    return db.execute(text(sql), params).scalar()


def _table_exists(db, name: str) -> bool:
    return bool(_scalar(db, "SELECT to_regclass(:n)", n=f"public.{name}"))


# ---- names -------------------------------------------------------------------------

def _rename_owned_objects(db, table: str, old_prefix: str, new_prefix: str) -> list[str]:
    """Rename a table's index-backed and foreign-key constraints, and its plain
    indexes, from one prefix to another.

    Index names are schema-scoped, so the old table must give up
    `impact_scores_pkey` before the new one can take it. NOT NULL constraint
    names are per-table and are deliberately left alone: two tables may both
    carry `impact_scores_damage_not_null`, and after a rollback rename the old
    table's names are already the canonical ones.
    """
    renamed = []
    constraints = db.execute(text(
        "SELECT conname, contype FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) "
        "AND contype IN ('p', 'u', 'f') ORDER BY conname"), {"t": table}).all()
    for name, _type in constraints:
        if not name.startswith(old_prefix):
            continue
        new_name = new_prefix + name[len(old_prefix):]
        db.execute(text(f'ALTER TABLE {table} RENAME CONSTRAINT "{name}" TO "{new_name}"'))
        renamed.append(f"{name} -> {new_name}")
    constraint_indexes = {row[0] for row in db.execute(text(
        "SELECT c.relname FROM pg_constraint k JOIN pg_class c ON c.oid = k.conindid "
        "WHERE k.conrelid = CAST(:t AS regclass)"), {"t": table}).all()}
    for (index_name,) in db.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = :t ORDER BY indexname"),
            {"t": table}).all():
        if index_name in constraint_indexes or not index_name.startswith("ix_" + old_prefix):
            continue
        new_name = "ix_" + new_prefix + index_name[len("ix_" + old_prefix):]
        db.execute(text(f'ALTER INDEX "{index_name}" RENAME TO "{new_name}"'))
        renamed.append(f"{index_name} -> {new_name}")
    return renamed


# ---- build -------------------------------------------------------------------------

def build(db, artifact_path: str) -> dict:
    started = time.time()
    if _table_exists(db, BUILT):
        db.execute(text(f"DROP TABLE {BUILT}"))
    db.execute(text(f"CREATE TABLE {BUILT} (LIKE {LIVE} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"))

    columns = load_columns()
    with open(artifact_path, "r", encoding="utf-8", newline="") as handle:
        header = tuple(next(csv.reader(handle)))
        if header != columns:
            raise SystemExit(f"load artifact header {header} is not the table's columns {columns}")
        handle.seek(0)
        raw = db.connection().connection.cursor()
        raw.copy_expert(
            f"COPY {BUILT} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true)", handle)

    db.execute(text(f"ALTER TABLE {BUILT} ADD CONSTRAINT {BUILT}_pkey "
                    "PRIMARY KEY (round_id, match_player_id)"))
    db.execute(text(f"CREATE INDEX ix_{BUILT}_match_player_id ON {BUILT} (match_player_id)"))
    db.execute(text(f"ALTER TABLE {BUILT} ADD CONSTRAINT {BUILT}_round_id_fkey "
                    "FOREIGN KEY (round_id) REFERENCES rounds(id)"))
    db.execute(text(f"ALTER TABLE {BUILT} ADD CONSTRAINT {BUILT}_match_player_id_fkey "
                    "FOREIGN KEY (match_player_id) REFERENCES match_players(id)"))
    # The replacement is gated exactly like the live table, so nothing can edit
    # it between verification and the swap.
    for operation in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        db.execute(text(
            f"CREATE TRIGGER scoring_gate_{operation.lower()} BEFORE {operation} ON {BUILT} "
            "FOR EACH STATEMENT EXECUTE FUNCTION scoring_gate_guard()"))
    db.execute(text(f"ANALYZE {BUILT}"))
    rows = _scalar(db, f"SELECT count(*) FROM {BUILT}")
    return {"rows": rows, "minutes": round((time.time() - started) / 60, 2)}


# ---- verify ------------------------------------------------------------------------

def _canonical_rows(db, table: str) -> str:
    """The built table read back and re-serialised by the exporter's own
    renderer -- not by PostgreSQL's JSON text form, which makes no promise to
    match Python's."""
    columns = load_columns()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    result = db.execute(text(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY round_id, match_player_id"))
    for row in result:
        writer.writerow([render_field(value) for value in row])
    return buffer.getvalue()


def verify_build(db, artifact_path: str, *, expect_scoring_version: int,
                 approved_path: str | None) -> dict:
    problems, facts = [], {}

    facts["rows"] = _scalar(db, f"SELECT count(*) FROM {BUILT}")
    facts["stat_rows"] = _scalar(db, "SELECT count(*) FROM round_player_stats")
    missing = _scalar(db, f"""
        SELECT count(*) FROM round_player_stats s
        LEFT JOIN {BUILT} b ON b.round_id = s.round_id AND b.match_player_id = s.match_player_id
        WHERE b.round_id IS NULL""")
    unexpected = _scalar(db, f"""
        SELECT count(*) FROM {BUILT} b
        LEFT JOIN round_player_stats s ON s.round_id = b.round_id
             AND s.match_player_id = b.match_player_id
        WHERE s.id IS NULL""")
    facts["stat_rows_without_a_score"] = missing
    facts["scores_without_a_stat_row"] = unexpected
    if missing or unexpected:
        problems.append(f"key set differs from round_player_stats: {missing} missing, "
                        f"{unexpected} unexpected")

    versions = [v for (v,) in db.execute(text(
        f"SELECT DISTINCT scoring_version FROM {BUILT} ORDER BY 1")).all()]
    facts["scoring_versions"] = versions
    if versions != [expect_scoring_version]:
        problems.append(f"scoring_version is {versions}, expected [{expect_scoring_version}]")

    with open(artifact_path, "r", encoding="utf-8", newline="") as handle:
        expected = handle.read()
    read_back = _canonical_rows(db, BUILT)
    facts["artifact_sha256"] = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    facts["read_back_sha256"] = hashlib.sha256(read_back.encode("utf-8")).hexdigest()
    if facts["artifact_sha256"] != facts["read_back_sha256"]:
        problems.append("the built table does not read back as the artifact that was loaded")

    if approved_path:
        approved = json.load(open(approved_path, encoding="utf-8"))
        fields = approved.get("fields") or []
        differing = []
        for match_id, match in approved.get("matches", {}).items():
            stored = {
                f"{r[0]}:{r[1]}": [render_field(v) for v in r[2:]]
                for r in db.execute(text(
                    f"SELECT b.round_id, b.match_player_id, {', '.join(fields)} FROM {BUILT} b "
                    "JOIN rounds r ON r.id = b.round_id WHERE r.match_id = :m"), {"m": int(match_id)})
            }
            for key, values in match.get("rows", {}).items():
                if stored.get(key) != [render_field(v) for v in values]:
                    differing.append(f"{match_id}:{key}")
        facts["approved_rows_checked"] = sum(len(m.get("rows", {}))
                                             for m in approved.get("matches", {}).values())
        if differing:
            problems.append(f"{len(differing)} approved rows differ (first {differing[0]})")

    facts["problems"] = problems
    return facts


# ---- swap and rollback ---------------------------------------------------------------

def swap(db) -> dict:
    """One transaction. Either every rename lands or none does."""
    started = time.time()
    db.execute(text(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'"))
    # Request order: pages read the cache, then the scores. Taking the locks the
    # other way round is the deadlock this design exists to avoid.
    db.execute(text("LOCK TABLE player_view_cache IN ACCESS EXCLUSIVE MODE"))
    db.execute(text(f"LOCK TABLE {LIVE}, {BUILT} IN ACCESS EXCLUSIVE MODE"))

    renamed = _rename_owned_objects(db, LIVE, LIVE, PREVIOUS)
    db.execute(text(f"ALTER TABLE {LIVE} RENAME TO {PREVIOUS}"))
    db.execute(text(f"ALTER TABLE {BUILT} RENAME TO {LIVE}"))
    renamed += _rename_owned_objects(db, LIVE, BUILT, LIVE)
    db.execute(text("TRUNCATE player_view_cache"))
    return {"renamed": renamed, "seconds": round(time.time() - started, 2)}


def rollback(db) -> dict:
    started = time.time()
    if not _table_exists(db, PREVIOUS):
        raise SystemExit(f"{PREVIOUS} does not exist: there is nothing to roll back to")
    db.execute(text(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'"))
    db.execute(text("LOCK TABLE player_view_cache IN ACCESS EXCLUSIVE MODE"))
    db.execute(text(f"LOCK TABLE {LIVE}, {PREVIOUS} IN ACCESS EXCLUSIVE MODE"))

    renamed = _rename_owned_objects(db, LIVE, LIVE, ROLLED_BACK)
    db.execute(text(f"ALTER TABLE {LIVE} RENAME TO {ROLLED_BACK}"))
    db.execute(text(f"ALTER TABLE {PREVIOUS} RENAME TO {LIVE}"))
    renamed += _rename_owned_objects(db, LIVE, PREVIOUS, LIVE)
    db.execute(text("TRUNCATE player_view_cache"))
    # Nothing writes scores again until the owner decides what should.
    db.execute(text("UPDATE scoring_gate SET state = 'closed', note = :n, updated_at = now() "
                    "WHERE id"), {"n": "closed by swap_impact_scores.py rollback"})
    return {"renamed": renamed, "seconds": round(time.time() - started, 2)}


def state(db) -> dict:
    tables = {name: _table_exists(db, name) for name in (LIVE, BUILT, PREVIOUS, ROLLED_BACK)}
    counts = {name: _scalar(db, f"SELECT count(*) FROM {name}") for name, exists in tables.items()
              if exists}
    return {
        "database": _scalar(db, "SELECT current_database()"),
        "tables": tables,
        "rows": counts,
        "gate": str(read_gate(db)),
        "player_view_cache": _scalar(db, "SELECT count(*) FROM player_view_cache"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("build", "verify-build", "swap", "rollback", "state"))
    parser.add_argument("--artifact", help="the load projection CSV (<label>.load.csv)")
    parser.add_argument("--approved", help="review-results.json to compare against")
    parser.add_argument("--expect-scoring-version", type=int, default=3)
    parser.add_argument("--identity", default="rc3-runbook", help="the gate's admin identity")
    parser.add_argument("--yes", action="store_true", help="required for swap and rollback")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        database = _scalar(db, "SELECT current_database()")
        print(f"database {database}")
        _identity(db, args.identity)

        if args.command == "state":
            print(canonical_json(state(db)))
            return 0

        if args.command == "build":
            if not args.artifact:
                raise SystemExit("build needs --artifact")
            result = build(db, args.artifact)
            db.commit()
            print(f"built {BUILT}: {result['rows']:,} rows in {result['minutes']} min")
            return 0

        if args.command == "verify-build":
            if not args.artifact:
                raise SystemExit("verify-build needs --artifact")
            facts = verify_build(db, args.artifact,
                                 expect_scoring_version=args.expect_scoring_version,
                                 approved_path=args.approved)
            db.rollback()
            print(canonical_json(facts))
            if facts["problems"]:
                print("VERIFY FAILED")
                return 2
            print("verify-build: clean")
            return 0

        if not args.yes:
            raise SystemExit(f"{args.command} changes the live table: pass --yes")
        result = swap(db) if args.command == "swap" else rollback(db)
        db.commit()
        print(f"{args.command} committed in {result['seconds']}s")
        for line in result["renamed"]:
            print(f"  renamed {line}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
