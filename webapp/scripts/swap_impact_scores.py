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
    verify-build  count, key set, version, hashes, the chain's own artifact
                  compared row by row, the approved rows, and every match's
                  input fingerprint against the export's sidecar -- read-only
    swap          one transaction: locks, renames, cache clear
    verify-live   verify-build's checks on the live table, after the swap -- read-only
    rollback      the same in reverse, and close the gate
    state         print what the database currently looks like, with the log

THE OPERATION-STATE RECORD. Every subcommand but `state` appends to
scoring_release_log (scripts/sql/release_write_gate.sql), and build, swap and
rollback write their entry inside the transaction that makes the change, so the
entry and the change commit together or not at all. After a lost connection,
`state` says whether a swap happened; nobody repeats one blind. The log also
binds the steps together: a swap refuses unless the newest build-or-verify entry
is a CLEAN verification of the very table it is about to swap in.

Every subcommand first asserts --expect-database, and runs under the gate's
admin identity.

Exit codes: 0 done; 2 verify-build found problems; 3 refused, nothing changed;
4 the swap or rollback could not take its locks in time, nothing changed -- retry.
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
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.scoring.impact_manifest import lf_sha256, load_manifest, match_source_fingerprint
from app.scoring.write_gate import WRITE_IDENTITY_SETTING, install_write_identity, read_gate
from scripts.export_impact_artifact import (
    COMPARISON_HEADER,
    canonical_json,
    load_columns,
    render_field,
)

LIVE = "impact_scores"
BUILT = "impact_scores_new"
PREVIOUS = "impact_scores_v1"
ROLLED_BACK = "impact_scores_rc3_rolled_back"
LOG = "scoring_release_log"

#: Per lock acquisition, not for the whole transaction: a swap takes two, so
#: this bounds each wait rather than the total. A swap that cannot take a lock
#: in this long changes nothing and is retried.
SWAP_LOCK_TIMEOUT = "5s"

#: A backstop for the statements themselves. Nothing here should take seconds.
SWAP_STATEMENT_TIMEOUT = "60s"

EXIT_VERIFY_FAILED = 2
EXIT_REFUSED = 3
EXIT_LOCK_TIMEOUT = 4

#: SQLSTATE lock_not_available, raised when lock_timeout expires.
LOCK_NOT_AVAILABLE = "55P03"

#: Everything the scorer reads. A verification says these rows produced the
#: staged scores, so a write to any of them makes it stale.
SOURCE_TABLES = ("matches", "match_players", "rounds", "round_player_stats", "kill_events")


class Refused(RuntimeError):
    """A precondition does not hold. Raised before anything is committed."""


def _identity(db, identity: str) -> None:
    install_write_identity(db, identity)


def _scalar(db, sql, **params):
    return db.execute(text(sql), params).scalar()


def _table_exists(db, name: str) -> bool:
    return bool(_scalar(db, "SELECT to_regclass(:n)", n=f"public.{name}"))


def _table_oid(db, name: str) -> int | None:
    return _scalar(db, "SELECT CAST(to_regclass(:n) AS oid)", n=f"public.{name}")


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- the operation-state record ---------------------------------------------------------

def record(db, operation: str, outcome: str, details: dict) -> int:
    """Append one entry, in the caller's transaction."""
    return db.execute(text(
        f"INSERT INTO {LOG} (operation, outcome, identity, details) "
        "VALUES (:operation, :outcome, current_setting(:setting, true), CAST(:details AS jsonb)) "
        "RETURNING id"),
        {"operation": operation, "outcome": outcome, "setting": WRITE_IDENTITY_SETTING,
         "details": canonical_json(details)}).scalar()


def _latest(db, *entries: tuple[str, str]):
    """The newest entry among these (operation, outcome) pairs.

    Matching outcomes, not just operations, matters: a refused second swap is
    logged as ("swap", "refused") and must not hide the swap that really
    happened from a later rollback."""
    clauses = " OR ".join(f"(operation = :operation{i} AND outcome = :outcome{i})"
                          for i in range(len(entries)))
    params = {}
    for i, (operation, outcome) in enumerate(entries):
        params[f"operation{i}"], params[f"outcome{i}"] = operation, outcome
    return db.execute(text(
        f"SELECT id, operation, outcome, details FROM {LOG} WHERE {clauses} ORDER BY id DESC LIMIT 1"),
        params).first()


def _write_counters(db, tables) -> dict:
    """Rows inserted, updated and deleted per table, cumulatively.

    A verification is a statement about the staged table AND the source rows it
    was scored from, and the admin identity may write both at any time (that is
    what it is for). Comparing these counters at the swap notices a deliberate
    correction that landed after the verification -- which neither the table's
    oid nor the highest match id would show (external review, C6). They can lag
    a moment behind a just-committed writer, so a spurious refusal means verify
    again, never force.
    """
    return {name: [inserted, updated, deleted] for name, inserted, updated, deleted in db.execute(text(
        "SELECT relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_all_tables "
        "WHERE schemaname = 'public' AND relname = ANY(:names)"), {"names": list(tables)}).all()}


def _require_gate_closed(db, operation: str) -> None:
    gate = read_gate(db)
    if gate is None:
        raise Refused("the release write gate is not installed")
    if gate.is_open:
        raise Refused(f"the gate is open for {gate.release_id}: {operation} runs only while it is "
                      "closed, because an open gate lets ingestion write while tables are renamed "
                      "(plan v2, D11)")


def _lock_gate_closed(db, operation: str) -> None:
    """The gate re-read under a row lock, after the table locks are held.

    The early check refuses fast; this one closes the gap behind it. Until this
    transaction ends the installer cannot open the gate, so no writer can be let
    in between this check and the renames."""
    gate_state = db.execute(text("SELECT state FROM scoring_gate WHERE id FOR UPDATE")).scalar()
    if gate_state != "closed":
        raise Refused(f"the gate is {gate_state or 'missing'} under lock: {operation} runs only while it is "
                      "closed (plan v2, D11)")


def _require_clean_verification(db) -> dict:
    """The newest build-or-verify entry must be a clean verification of the
    table about to be swapped in, and no match may have arrived since.

    Called with the locks held, so neither can change before the renames."""
    # A failed build rolls back whole, leaving any earlier build and its
    # verification exactly as they were, so it is not a reason to refuse.
    latest = _latest(db, ("build", "built"), ("verify-build", "clean"), ("verify-build", "failed"))
    if latest is None or latest.operation != "verify-build" or latest.outcome != "clean":
        found = "nothing" if latest is None else f"{latest.operation} {latest.outcome} (entry {latest.id})"
        raise Refused(f"the newest build-or-verify entry is {found}, not a clean verify-build: "
                      "verify the build before swapping it in")
    details = latest.details
    built_oid = _table_oid(db, BUILT)
    if details.get("built_oid") != built_oid:
        raise Refused(f"verify-build entry {latest.id} checked table oid {details.get('built_oid')}, "
                      f"but {BUILT} is now oid {built_oid}: it was rebuilt after verification")
    max_match_id = _scalar(db, "SELECT max(id) FROM matches")
    if details.get("max_match_id") != max_match_id:
        raise Refused(f"matches changed after verification (max id {details.get('max_match_id')} "
                      f"then, {max_match_id} now): verify again")
    counters = _write_counters(db, (BUILT, *SOURCE_TABLES))
    verified_counters = details.get("write_counters") or {}
    moved = sorted(name for name, counts in counters.items() if verified_counters.get(name) != counts)
    if moved:
        raise Refused(f"{', '.join(moved)} changed after verification (rows written since it ran): "
                      "verify again before swapping")
    return {"verify_entry": latest.id, "built_oid": built_oid, "max_match_id": max_match_id,
            "rows": details.get("rows"), "comparison_sha256": details.get("comparison_sha256"),
            "artifact_sha256": details.get("artifact_sha256")}


# ---- names -------------------------------------------------------------------------

def _clear_player_cache(db) -> int:
    """Empty player_view_cache by DELETE, never TRUNCATE.

    TRUNCATE needs ACCESS EXCLUSIVE, which a page load's cache read blocks --
    and that page load may itself be waiting on its own second connection
    writing the cache through, which would now be queued behind the TRUNCATE.
    Nothing in that cycle is a database deadlock, so nothing breaks it (external
    review, C1). DELETE takes row locks that no reader conflicts with, and the
    table holds a few thousand rows.
    """
    return db.execute(text("DELETE FROM player_view_cache")).rowcount


def _rename_owned_objects(db, table: str, old_prefix: str, new_prefix: str) -> list[str]:
    """Rename a table's index-backed and foreign-key constraints, and its plain
    indexes, from one prefix to another.

    Index names are schema-scoped, so the old table must give up
    `impact_scores_pkey` before the new one can take it. NOT NULL constraint
    names are per-table and are deliberately left alone: PostgreSQL 18 copies
    them with `CREATE TABLE ... LIKE` under their original names
    (`impact_scores_damage_not_null`), and a table rename does not change them,
    so after a swap or a rollback the live table carries the canonical names.
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
    result = {
        "rows": _scalar(db, f"SELECT count(*) FROM {BUILT}"),
        "minutes": round((time.time() - started) / 60, 2),
        "artifact": os.path.basename(artifact_path),
        "artifact_sha256": _file_sha256(artifact_path),
        "built_oid": _table_oid(db, BUILT),
    }
    record(db, "build", "built", result)
    return result


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


def _check_against_approved_scoring(db, table: str, comparison_path: str, facts: dict, problems: list, *,
                                    expect_comparison_sha256: str) -> None:
    """The chain's own artifact, compared against the rows about to go live.

    Everything else proves the table matches the file that was loaded, and that
    the file came from an export whose sidecar claims the chain's hash. Only
    this compares the approved scoring itself with the table: a load projection
    that dropped or moved a value would satisfy every other check (external
    review, C2).

    Two of the artifact's 24 columns -- leverage_component and
    assists_component -- are diagnostics the table never stores, so they cannot
    be compared against it; the other 22 can, by key, in one pass.
    """
    facts["comparison_artifact_sha256"] = _file_sha256(comparison_path)
    if facts["comparison_artifact_sha256"] != expect_comparison_sha256:
        problems.append(f"the comparison artifact hashes to {facts['comparison_artifact_sha256']}, "
                        f"not the chain's {expect_comparison_sha256}")
        return

    stored = [name for name in COMPARISON_HEADER if name in set(load_columns())]
    facts["compared_columns"] = stored
    facts["columns_the_table_never_stores"] = [n for n in COMPARISON_HEADER if n not in set(load_columns())]
    with open(comparison_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader))
        if header != COMPARISON_HEADER:
            problems.append(f"the comparison artifact's header is not the recorded one: {header}")
            return
        at = [header.index(name) for name in stored]
        rows, differing = 0, []
        result = db.execute(text(
            f"SELECT {', '.join(stored)} FROM {table} ORDER BY round_id, match_player_id"))
        for row in result:
            approved = next(reader, None)
            if approved is None:
                problems.append(f"{table} has more rows than the approved artifact")
                return
            rows += 1
            if [render_field(value) for value in row] != [approved[i] for i in at]:
                differing.append(f"{approved[0]}:{approved[1]}")
        if next(reader, None) is not None:
            problems.append(f"the approved artifact has more rows than {table}")
        facts["approved_rows_compared"] = rows
        if differing:
            problems.append(f"{len(differing)} rows differ from the approved scoring "
                            f"(first {differing[:3]})")


def _check_approved(db, table: str, approved_path: str, manifest_path: str | None,
                    facts: dict, problems: list) -> None:
    """The owner's approved review, compared against the table -- completely.

    Checking only the rows the file happens to carry is no check at all: an
    empty or truncated report passes, and so does one from another candidate
    (external review, C3). The file must name the manifest it came from, cover
    exactly the matches that manifest declares, and carry exactly the rows the
    table holds for each of them.
    """
    with open(approved_path, encoding="utf-8") as handle:
        approved = json.load(handle)
    fields = list(approved.get("fields") or [])
    persisted = [c for c in load_columns() if c not in ("round_id", "match_player_id")]
    if sorted(fields) != sorted(persisted):
        problems.append(f"the approved results label their values {fields}, which is not the "
                        f"table's persisted fields {persisted}")
        return

    if manifest_path is None:
        problems.append("approved results were given without the manifest they belong to")
        return
    manifest = load_manifest(manifest_path)
    facts["manifest_lf_sha256"] = lf_sha256(manifest_path)
    if approved.get("manifest_lf_sha256") != facts["manifest_lf_sha256"]:
        problems.append(f"the approved results were written against manifest "
                        f"{approved.get('manifest_lf_sha256')}, not {facts['manifest_lf_sha256']}")
        return
    for name in ("candidate_id", "release_comparator"):
        if approved.get(name) != manifest.get(name):
            problems.append(f"the approved results are for {name} {approved.get(name)!r}, "
                            f"the manifest for {manifest.get(name)!r}")
    declared = set((manifest.get("source_snapshots") or {}).get("matches") or {})
    reviewed = set(approved.get("matches") or {})
    facts["approved_matches"] = sorted(reviewed, key=int)
    if reviewed != declared:
        problems.append(f"the approved results cover {len(reviewed)} of the manifest's "
                        f"{len(declared)} declared matches (missing {sorted(declared - reviewed, key=int)[:5]}, "
                        f"unexpected {sorted(reviewed - declared, key=int)[:5]})")
        return

    # scoring_version is provenance. The review ran under the review-time
    # calculation version and the built table must carry the activation
    # version, so comparing it row by row would fail exactly the rows that
    # are right -- and dropping it silently would hide that it was never
    # checked. It is asserted explicitly elsewhere (expect_scoring_version) and
    # reported here; every other field is compared exactly.
    version_at = fields.index("scoring_version")
    compared = [f for f in fields if f != "scoring_version"]
    positions = [fields.index(f) for f in compared]
    differing, incomplete, review_versions = [], [], set()
    for match_id, match in approved["matches"].items():
        built = {
            f"{r[0]}:{r[1]}": [render_field(v) for v in r[2:]]
            for r in db.execute(text(
                f"SELECT b.round_id, b.match_player_id, {', '.join(compared)} FROM {table} b "
                "JOIN rounds r ON r.id = b.round_id WHERE r.match_id = :m"), {"m": int(match_id)})
        }
        rows = match.get("rows") or {}
        if set(rows) != set(built):
            incomplete.append(f"{match_id} ({len(rows)} approved rows, {len(built)} in the table)")
            continue
        frozen_fingerprint = (manifest.get("source_snapshots") or {}).get("matches", {}).get(match_id)
        if match.get("source_fingerprint") != frozen_fingerprint:
            problems.append(f"match {match_id} was reviewed against source rows the manifest did not freeze")
        for key, values in rows.items():
            review_versions.add(values[version_at])
            if built[key] != [render_field(values[i]) for i in positions]:
                differing.append(f"{match_id}:{key}")
    facts["approved_rows_checked"] = sum(len(m.get("rows") or {}) for m in approved["matches"].values())
    facts["approved_review_scoring_versions"] = sorted(review_versions)
    if incomplete:
        problems.append(f"{len(incomplete)} approved matches do not cover their rows (first {incomplete[0]})")
    if differing:
        problems.append(f"{len(differing)} approved rows differ (first {differing[0]})")


def _check_sidecar(db, sidecar: dict, facts: dict, problems: list, *,
                   expect_scoring_version: int, expect_comparison_sha256: str) -> None:
    """Tie the loaded file to the export that produced it, and that export to the chain."""
    recorded_load = (sidecar.get("load_artifact") or {}).get("sha256")
    if recorded_load != facts["artifact_sha256"]:
        problems.append(f"the sidecar describes load artifact {recorded_load}, but the file loaded "
                        f"is {facts['artifact_sha256']}")
    facts["comparison_sha256"] = (sidecar.get("artifact") or {}).get("sha256")
    if facts["comparison_sha256"] != expect_comparison_sha256:
        problems.append(f"the export's comparison hash {facts['comparison_sha256']} is not the chain's "
                        f"{expect_comparison_sha256}: this is not the scoring that was approved")
    exported_version = (sidecar.get("configuration") or {}).get("impact_calculation_version")
    if exported_version != expect_scoring_version:
        problems.append(f"the export ran at impact_calculation_version {exported_version}, "
                        f"expected {expect_scoring_version}")
    exported_from = (sidecar.get("inputs") or {}).get("database")
    if exported_from != facts["database"]:
        problems.append(f"the export read database {exported_from}, but this is {facts['database']}")


def _check_inputs(db, sidecar: dict, facts: dict, problems: list) -> None:
    """Every match's source rows, fingerprinted again and compared with the
    export's. The slow part: four queries per match."""
    inputs = sidecar.get("inputs") or {}
    recorded = inputs.get("match_source_fingerprints")
    if not recorded:
        problems.append("the export recorded no input fingerprints (--no-fingerprints), so it "
                        "cannot be loaded")
        return
    current_ids = [str(m) for (m,) in db.execute(text("SELECT id FROM matches ORDER BY id")).all()]
    added = sorted(set(current_ids) - set(recorded), key=int)
    removed = sorted(set(recorded) - set(current_ids), key=int)
    if added or removed:
        problems.append(f"the match set changed since the export: {len(added)} added "
                        f"(first {added[:5]}), {len(removed)} removed (first {removed[:5]})")
        return
    started = time.time()
    current = {match_id: match_source_fingerprint(db, int(match_id)) for match_id in current_ids}
    facts["fingerprint_minutes"] = round((time.time() - started) / 60, 2)
    facts["cohort_fingerprint"] = hashlib.sha256(canonical_json(current).encode("utf-8")).hexdigest()
    changed = [m for m in current_ids if current[m] != recorded[m]]
    if changed or facts["cohort_fingerprint"] != inputs.get("cohort_fingerprint"):
        problems.append(f"{len(changed)} matches' source rows changed since the export "
                        f"(first {changed[:5]})")


def verify_build(db, artifact_path: str, *, sidecar_path: str, comparison_path: str,
                 expect_scoring_version: int, expect_comparison_sha256: str,
                 approved_path: str | None, manifest_path: str | None = None,
                 table: str = BUILT) -> dict:
    """Read-only. The CLI runs it inside one REPEATABLE READ snapshot.

    `table` is the built table before a swap, and the live table after one
    (`verify-live`, plan v2 8.4): the same checks prove what the site now reads."""
    problems = []
    facts = {"database": _scalar(db, "SELECT current_database()"), "table": table,
             "built_oid": _table_oid(db, table)}
    if facts["built_oid"] is None:
        facts["problems"] = [f"{table} does not exist: build first"]
        return facts

    facts["write_counters"] = _write_counters(db, (table, *SOURCE_TABLES))
    facts["rows"] = _scalar(db, f"SELECT count(*) FROM {table}")
    facts["stat_rows"] = _scalar(db, "SELECT count(*) FROM round_player_stats")
    facts["max_match_id"] = _scalar(db, "SELECT max(id) FROM matches")
    missing = _scalar(db, f"""
        SELECT count(*) FROM round_player_stats s
        LEFT JOIN {table} b ON b.round_id = s.round_id AND b.match_player_id = s.match_player_id
        WHERE b.round_id IS NULL""")
    unexpected = _scalar(db, f"""
        SELECT count(*) FROM {table} b
        LEFT JOIN round_player_stats s ON s.round_id = b.round_id
             AND s.match_player_id = b.match_player_id
        WHERE s.id IS NULL""")
    facts["stat_rows_without_a_score"] = missing
    facts["scores_without_a_stat_row"] = unexpected
    if missing or unexpected:
        problems.append(f"key set differs from round_player_stats: {missing} missing, "
                        f"{unexpected} unexpected")

    versions = [v for (v,) in db.execute(text(
        f"SELECT DISTINCT scoring_version FROM {table} ORDER BY 1")).all()]
    facts["scoring_versions"] = versions
    if versions != [expect_scoring_version]:
        problems.append(f"scoring_version is {versions}, expected [{expect_scoring_version}]")

    with open(artifact_path, "r", encoding="utf-8", newline="") as handle:
        expected = handle.read()
    read_back = _canonical_rows(db, table)
    facts["artifact_sha256"] = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    facts["read_back_sha256"] = hashlib.sha256(read_back.encode("utf-8")).hexdigest()
    if facts["artifact_sha256"] != facts["read_back_sha256"]:
        problems.append(f"{table} does not read back as the artifact that was loaded")

    with open(sidecar_path, encoding="utf-8") as handle:
        sidecar = json.load(handle)
    _check_sidecar(db, sidecar, facts, problems, expect_scoring_version=expect_scoring_version,
                   expect_comparison_sha256=expect_comparison_sha256)
    _check_against_approved_scoring(db, table, comparison_path, facts, problems,
                                    expect_comparison_sha256=expect_comparison_sha256)

    if approved_path:
        _check_approved(db, table, approved_path, manifest_path, facts, problems)

    if problems:
        # Fingerprinting every match takes minutes over the link; it is only
        # worth paying for a build that could otherwise be swapped in.
        facts["input_fingerprints"] = "not checked: fix the problems above first"
    else:
        _check_inputs(db, sidecar, facts, problems)
    facts["problems"] = problems
    return facts


def verify_and_record(db, artifact_path: str, *, operation: str = "verify-build", **kwargs) -> dict:
    """verify_build, then its entry in the log. The verification's own
    transaction is read-only, so the entry is written after it ends."""
    facts = verify_build(db, artifact_path, **kwargs)
    db.rollback()
    record(db, operation, "failed" if facts["problems"] else "clean", facts)
    db.commit()
    return facts


# ---- swap and rollback ---------------------------------------------------------------

def swap(db) -> dict:
    """One transaction. Either every rename lands, with its log entry, or none does."""
    started = time.time()
    _require_gate_closed(db, "swap")
    if not _table_exists(db, BUILT):
        raise Refused(f"{BUILT} does not exist: build and verify it first")
    if _table_exists(db, PREVIOUS):
        raise Refused(f"{PREVIOUS} already exists, so this database was already swapped "
                      "(see `state`); a second swap would have nowhere to put the live table")
    db.execute(text(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'"))
    db.execute(text(f"SET LOCAL statement_timeout = '{SWAP_STATEMENT_TIMEOUT}'"))
    # The cache first, as requests read it first -- but by DELETE, which takes
    # no lock a reader conflicts with. See _clear_player_cache.
    _clear_player_cache(db)
    db.execute(text(f"LOCK TABLE {LIVE}, {BUILT} IN ACCESS EXCLUSIVE MODE"))
    _lock_gate_closed(db, "swap")
    verified = _require_clean_verification(db)

    renamed = _rename_owned_objects(db, LIVE, LIVE, PREVIOUS)
    db.execute(text(f"ALTER TABLE {LIVE} RENAME TO {PREVIOUS}"))
    db.execute(text(f"ALTER TABLE {BUILT} RENAME TO {LIVE}"))
    renamed += _rename_owned_objects(db, LIVE, BUILT, LIVE)
    result = {**verified, "renamed": renamed, "seconds": round(time.time() - started, 2)}
    record(db, "swap", "swapped", result)
    return result


def rollback(db) -> dict:
    """R1. Valid only while nothing has been ingested since the swap (plan v2, D11)."""
    started = time.time()
    if not _table_exists(db, PREVIOUS):
        raise Refused(f"{PREVIOUS} does not exist: there is nothing to roll back to")
    if _table_exists(db, ROLLED_BACK):
        raise Refused(f"{ROLLED_BACK} exists from an earlier rollback: inspect it and drop it first")
    _require_gate_closed(db, "rollback")
    db.execute(text(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'"))
    db.execute(text(f"SET LOCAL statement_timeout = '{SWAP_STATEMENT_TIMEOUT}'"))
    _clear_player_cache(db)
    db.execute(text(f"LOCK TABLE {LIVE}, {PREVIOUS} IN ACCESS EXCLUSIVE MODE"))
    _lock_gate_closed(db, "rollback")

    swapped = _latest(db, ("swap", "swapped"), ("rollback", "rolled back"))
    if swapped is None or swapped.operation != "swap":
        raise Refused("the log's newest committed swap-or-rollback is not a swap, so there is "
                      "no swap this rollback can be sure it undoes")
    max_match_id = _scalar(db, "SELECT max(id) FROM matches")
    if max_match_id != swapped.details.get("max_match_id"):
        raise Refused(f"matches were ingested after the swap (max id {swapped.details.get('max_match_id')} "
                      f"then, {max_match_id} now): restoring {PREVIOUS} would lose their scores. R1 "
                      "no longer applies (plan v2, D11) -- fix forward, or capture the current "
                      "scores first")

    renamed = _rename_owned_objects(db, LIVE, LIVE, ROLLED_BACK)
    db.execute(text(f"ALTER TABLE {LIVE} RENAME TO {ROLLED_BACK}"))
    db.execute(text(f"ALTER TABLE {PREVIOUS} RENAME TO {LIVE}"))
    renamed += _rename_owned_objects(db, LIVE, PREVIOUS, LIVE)
    # Nothing writes scores again until the owner decides what should.
    db.execute(text("UPDATE scoring_gate SET state = 'closed', note = :n, updated_at = now() "
                    "WHERE id"), {"n": "closed by swap_impact_scores.py rollback"})
    result = {"undoes_swap_entry": swapped.id, "max_match_id": max_match_id, "renamed": renamed,
              "seconds": round(time.time() - started, 2)}
    record(db, "rollback", "rolled back", result)
    return result


def state(db) -> dict:
    tables = {name: _table_exists(db, name) for name in (LIVE, BUILT, PREVIOUS, ROLLED_BACK)}
    return {
        "database": _scalar(db, "SELECT current_database()"),
        "tables": tables,
        "rows": {name: _scalar(db, f"SELECT count(*) FROM {name}")
                 for name, exists in tables.items() if exists},
        "oids": {name: _table_oid(db, name) for name, exists in tables.items() if exists},
        "gate": str(read_gate(db)),
        "player_view_cache": _scalar(db, "SELECT count(*) FROM player_view_cache"),
        "max_match_id": _scalar(db, "SELECT max(id) FROM matches"),
        "log": [
            {"id": entry_id, "at": at.isoformat(), "operation": operation, "outcome": outcome,
             "identity": identity}
            for entry_id, at, operation, outcome, identity in db.execute(text(
                f"SELECT id, at, operation, outcome, identity FROM {LOG} ORDER BY id DESC LIMIT 10")).all()
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("build", "verify-build", "swap", "verify-live", "rollback",
                                            "state"))
    parser.add_argument("--expect-database", required=True,
                        help="the database this step must touch; any other is refused")
    parser.add_argument("--artifact", help="the load projection CSV (<label>.load.csv)")
    parser.add_argument("--sidecar", help="the export's sidecar (<label>.json)")
    parser.add_argument("--comparison", help="the export's comparison projection (<label>.csv)")
    parser.add_argument("--approved", help="review-results.json to compare against")
    parser.add_argument("--manifest", help="the frozen manifest those approved results belong to")
    parser.add_argument("--expect-comparison-sha256",
                        help="the chain's comparison hash, which the export must carry")
    parser.add_argument("--expect-scoring-version", type=int, default=3)
    parser.add_argument("--identity", default="rc3-runbook", help="the gate's admin identity")
    parser.add_argument("--yes", action="store_true", help="required for swap and rollback")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        database = _scalar(db, "SELECT current_database()")
        print(f"database {database}")
        if database != args.expect_database:
            print(f"REFUSED: connected to {database}, but this step expects {args.expect_database}")
            return EXIT_REFUSED
        _identity(db, args.identity)
        db.commit()  # the identity is a session setting: commit it so later rollbacks keep it

        if args.command == "state":
            print(canonical_json(state(db)))
            return 0

        if args.command == "build":
            if not args.artifact:
                raise SystemExit("build needs --artifact")
            try:
                result = build(db, args.artifact)
                db.commit()
            except Exception as exc:
                db.rollback()
                record(db, "build", "failed", {"error": repr(exc)[:500]})
                db.commit()
                raise
            print(f"built {BUILT}: {result['rows']:,} rows in {result['minutes']} min "
                  f"(oid {result['built_oid']})")
            return 0

        if args.command in ("verify-build", "verify-live"):
            missing = [flag for flag, value in (("--artifact", args.artifact), ("--sidecar", args.sidecar),
                                                ("--comparison", args.comparison),
                                                ("--approved", args.approved),
                                                ("--manifest", args.manifest),
                                                ("--expect-comparison-sha256", args.expect_comparison_sha256))
                       if not value]
            if missing:
                raise SystemExit(f"{args.command} needs {', '.join(missing)}")
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            facts = verify_and_record(db, args.artifact, operation=args.command, sidecar_path=args.sidecar,
                                      comparison_path=args.comparison,
                                      expect_scoring_version=args.expect_scoring_version,
                                      expect_comparison_sha256=args.expect_comparison_sha256,
                                      approved_path=args.approved, manifest_path=args.manifest,
                                      table=BUILT if args.command == "verify-build" else LIVE)
            print(canonical_json(facts))
            if facts["problems"]:
                print("VERIFY FAILED")
                return EXIT_VERIFY_FAILED
            print(f"{args.command}: clean")
            return 0

        if not args.yes:
            raise SystemExit(f"{args.command} changes the live table: pass --yes")
        try:
            result = swap(db) if args.command == "swap" else rollback(db)
            db.commit()
        except Refused as exc:
            db.rollback()
            record(db, args.command, "refused", {"reason": str(exc)})
            db.commit()
            print(f"REFUSED, nothing changed: {exc}")
            return EXIT_REFUSED
        except OperationalError as exc:
            db.rollback()
            if getattr(exc.orig, "pgcode", None) != LOCK_NOT_AVAILABLE:
                raise
            record(db, args.command, "lock timeout", {"lock_timeout": SWAP_LOCK_TIMEOUT})
            db.commit()
            print(f"{args.command} could not take its locks within {SWAP_LOCK_TIMEOUT}: nothing "
                  "changed, retry")
            return EXIT_LOCK_TIMEOUT
        print(f"{args.command} committed in {result['seconds']}s")
        for line in result["renamed"]:
            print(f"  renamed {line}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
