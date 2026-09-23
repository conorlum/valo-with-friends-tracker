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
admin identity, which is given explicitly (--identity): there is no default.

NO RELEASE-SPECIFIC DEFAULTS (Impact v4 plan, section 4.1, R6). The retained
table (--previous-table), the table a rollback sets aside (--rolled-back-table)
and the scoring version verify-build and verify-live expect
(--expect-scoring-version) are all explicit. They were rc3's constants, and a
v4 swap with rc3's impact_scores_v1 still present was refused while a default
of 3 would have verified the wrong version. Names are validated, quoted where
they reach SQL, and recorded in the swap's and the rollback's log entries.

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

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.scoring.impact_manifest import (
    fingerprint_contract_problem,
    lf_sha256,
    load_manifest,
    match_source_fingerprint,
)
from app.scoring.write_gate import WRITE_IDENTITY_SETTING, install_write_identity, read_gate
from scripts.export_impact_artifact import (
    COMPARISON_HEADER,
    canonical_json,
    load_columns,
    render_field,
)

LIVE = "impact_scores"
BUILT = "impact_scores_new"
LOG = "scoring_release_log"

#: A lowercase unquoted PostgreSQL identifier. Anything else is refused rather
#: than quoted into shape: a name that needs quoting is a typo.
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
#: The longest name derived from a table here is its match_player_id foreign
#: key, "<table>_match_player_id_fkey"; PostgreSQL silently truncates past 63
#: bytes, which could make two derived names collide.
_LONGEST_SUFFIX = len("_match_player_id_fkey")
_MAX_IDENTIFIER = 63


def _quoted(name: str) -> str:
    return f'"{name}"'


@dataclass(frozen=True)
class SwapNames:
    """The two release-specific table names, validated together.

    `previous`: where swap() puts the live table, and where rollback() restores
    it from (rc3 used impact_scores_v1; v4 uses impact_scores_v3).
    `rolled_back`: where rollback() sets the abandoned live table aside.
    Construct with SwapNames.validated(); there are no defaults."""

    previous: str
    rolled_back: str

    @classmethod
    def validated(cls, *, previous: str, rolled_back: str) -> "SwapNames":
        for role, name in (("previous", previous), ("rolled_back", rolled_back)):
            if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
                raise ValueError(f"{role} table {name!r} is not a lowercase SQL identifier")
            if len(name) + _LONGEST_SUFFIX > _MAX_IDENTIFIER:
                raise ValueError(f"{role} table {name!r} is too long: its derived constraint names "
                                 f"would pass PostgreSQL's {_MAX_IDENTIFIER}-byte limit")
            if name == LIVE:
                raise ValueError(f"{role} table cannot be the live table {LIVE}")
            if name == BUILT:
                raise ValueError(f"{role} table cannot be the staged table {BUILT}")
            if not name.startswith(LIVE + "_"):
                raise ValueError(f"{role} table {name!r} must be named impact_scores_<something>, "
                                 "so it can never be a source table or the release log")
        if previous == rolled_back:
            raise ValueError(f"the previous and rolled-back tables must differ (both {previous!r})")
        return cls(previous=previous, rolled_back=rolled_back)

#: Per lock acquisition, not for the whole transaction: a swap takes three lock
#: statements and a rollback two, so this bounds each wait rather than the total.
#: One that cannot take a lock in this long changes nothing and is retried.
SWAP_LOCK_TIMEOUT = "5s"

#: A backstop for the statements themselves. Nothing here should take seconds.
SWAP_STATEMENT_TIMEOUT = "60s"

EXIT_VERIFY_FAILED = 2
EXIT_REFUSED = 3
EXIT_LOCK_TIMEOUT = 4

#: SQLSTATE lock_not_available, raised when lock_timeout expires.
LOCK_NOT_AVAILABLE = "55P03"

#: Everything the scorer reads. A verification says these rows produced the
#: staged scores, so a write to any of them makes it stale. `players` joined for
#: Impact v4, which maps assistants to players by display_name: without it a
#: rename between export and swap passed every check (plan 2026-09-21, R1).
SOURCE_TABLES = ("matches", "match_players", "rounds", "round_player_stats", "kill_events",
                 "players")


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


def _row_digests(db, tables) -> dict:
    """Row count and an order-independent hash of every row, per table.

    A verification is a statement about the staged table AND the source rows it
    was scored from, and the admin identity may write both at any time (that is
    what it is for). Comparing these at the swap notices a deliberate correction
    that landed after the verification -- which neither the table's oid nor the
    highest match id would show (external review, C6).

    This reads THE ROWS, under the caller's locks. It replaces
    pg_stat_all_tables' n_tup_ins/upd/del, which were the C6 fix and were not
    sufficient: those counters are collected asynchronously and lag a
    just-committed writer, so the guard could pass while an admin write sat
    invisible behind the statistics, and the swap would commit an edited table
    and report success. Taking the table lock does not make another backend's
    statistics current. The test for it only passed because it called
    pg_stat_force_next_flush() first, which excluded exactly the failing case
    (external review, finding 2).

    hashtextextended over each row's text form moves for any realistic change to
    any row; summing is order-independent, and sum(bigint) is numeric, so it
    cannot overflow. The sum is kept as a string because numeric is not JSON.

    It is a probabilistic change detector, NOT proof of equality, and must not
    be described as one (external review round 2). These are finite,
    non-cryptographic 64-bit hashes: individual collisions and compensating
    changes are mathematically possible, and an edit that is later restored to
    identical contents is invisible to it by design. No accidental
    score-relevant mutation in this schema is known to evade it, and against
    the maintenance edits this guards it is a large improvement over counters
    -- but it is not cryptographic artifact identity, and it is not an audit of
    every intervening write. The chain's SHA-256 over the exported bytes is the
    artifact identity; this is the "did anything move under us" check.
    Measured over the real corpus: about 31 s for the staged table and the five
    source tables together, which is why swap() digests BEFORE it locks the
    live table (see there).
    """
    # The hash is over each row's TEXT rendering, so anything that changes how a
    # value prints changes the digest without any row changing. These tables hold
    # double precision (kill_events, rounds) and timestamptz (matches), whose text
    # depends on extra_float_digits, DateStyle and TimeZone. A verification taken
    # under one setting and a swap under another would disagree, and the refusal
    # says "verify again" -- which would reproduce the same disagreement and could
    # block the release with nothing actually wrong. So the settings are pinned
    # here, in the function, rather than assumed equal at the two call sites.
    # extra_float_digits 3 is the round-trip-exact rendering.
    db.execute(text("SET LOCAL DateStyle = 'ISO, YMD'"))
    db.execute(text("SET LOCAL IntervalStyle = 'postgres'"))
    db.execute(text("SET LOCAL TimeZone = 'UTC'"))
    db.execute(text("SET LOCAL extra_float_digits = 3"))
    digests = {}
    for name in tables:
        count, digest = db.execute(text(
            f"SELECT count(*), coalesce(sum(hashtextextended(t.*::text, 0)), 0) FROM {name} AS t"
        )).one()
        digests[name] = [int(count), str(digest)]
    return digests


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
    digests = _row_digests(db, (BUILT, *SOURCE_TABLES))
    verified_digests = details.get("row_digests") or {}
    if not verified_digests:
        raise Refused(f"verify-build entry {latest.id} recorded no row digests: it predates the "
                      "digest guard, so it cannot show the rows are unchanged. Verify again")
    moved = sorted(name for name, digest in digests.items() if verified_digests.get(name) != digest)
    if moved:
        raise Refused(f"{', '.join(moved)} changed after verification (the rows themselves differ): "
                      "verify again before swapping")
    # row_digests goes into the swap's own log entry so a later rollback can ask
    # whether the SOURCES still match what these scores were verified against.
    # Without it R1's only evidence is max(matches.id), which shows nothing about
    # an edit to an existing row (external review round 2, finding 1).
    return {"verify_entry": latest.id, "built_oid": built_oid, "max_match_id": max_match_id,
            "rows": details.get("rows"), "comparison_sha256": details.get("comparison_sha256"),
            "artifact_sha256": details.get("artifact_sha256"), "row_digests": digests}


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
    problem = fingerprint_contract_problem(
        (manifest.get("source_snapshots") or {}).get("fingerprint_version"), "the manifest")
    if problem:
        problems.append(problem)
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
    # Before any row is read: fingerprints taken under another contract never
    # match this checkout's, and saying "every match changed" would hide why.
    problem = fingerprint_contract_problem(inputs.get("fingerprint_version"), "the export") if recorded else None
    if problem:
        problems.append(problem)
        return
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

    facts["row_digests"] = _row_digests(db, (table, *SOURCE_TABLES))
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

def swap(db, names: SwapNames) -> dict:
    """One transaction. Either every rename lands, with its log entry, or none does."""
    started = time.time()
    previous = names.previous
    _require_gate_closed(db, "swap")
    if not _table_exists(db, BUILT):
        raise Refused(f"{BUILT} does not exist: build and verify it first")
    if _table_exists(db, previous):
        raise Refused(f"{previous} already exists, so this database was already swapped "
                      "(see `state`); a second swap would have nowhere to put the live table")
    db.execute(text(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'"))
    db.execute(text(f"SET LOCAL statement_timeout = '{SWAP_STATEMENT_TIMEOUT}'"))
    # Three lock statements, in this order, and the order is the design. (Three
    # statements, not three locks: the first takes every source table.)
    #
    # This order is NOT globally deadlock-free, and no claim is made that it is
    # (external review round 2). install_release_write_gate.py rebuilds triggers
    # starting at impact_scores and ending at the sources -- the opposite order
    # -- so a gate install overlapping a swap can cycle: the installer holds the
    # live table and waits for a source table this transaction holds, while this
    # transaction waits for the live table. PostgreSQL aborts one of them, so
    # nothing commits half-done, but the abort arrives as a generic error and
    # NOT as exit 4, so it takes the state-inspection path. The runbook is
    # sequential and never overlaps these tools; do not run gate installation,
    # a swap or a rollback concurrently.
    #
    # The staged table and the sources come first: SHARE stops writers without
    # stopping readers, so the site is untouched while _require_clean_verification
    # digests every row of seven tables -- about 31 s measured on the real corpus
    # for six, before `players` joined them (small: one row per player).
    # That work CANNOT sit inside the live table's ACCESS EXCLUSIVE, which stops
    # page loads dead; 31 s there would blow the 5 s stall budget by six times.
    db.execute(text(f"LOCK TABLE {', '.join(SOURCE_TABLES)} IN SHARE MODE"))
    db.execute(text(f"LOCK TABLE {BUILT} IN ACCESS EXCLUSIVE MODE"))
    _lock_gate_closed(db, "swap")
    verified = _require_clean_verification(db)

    # Only now the live table, so its exclusive lock covers the renames alone.
    # The cache first, as requests read it first -- but by DELETE, which takes
    # no lock a reader conflicts with. See _clear_player_cache.
    _clear_player_cache(db)
    db.execute(text(f"LOCK TABLE {LIVE} IN ACCESS EXCLUSIVE MODE"))

    # The oid the live table has now is the oid the previous table will have
    # after the rename, so a later rollback can tell the retained table apart
    # from one dropped and recreated under the same name.
    retained_oid = _table_oid(db, LIVE)
    renamed = _rename_owned_objects(db, LIVE, LIVE, previous)
    db.execute(text(f"ALTER TABLE {LIVE} RENAME TO {_quoted(previous)}"))
    db.execute(text(f"ALTER TABLE {BUILT} RENAME TO {LIVE}"))
    renamed += _rename_owned_objects(db, LIVE, BUILT, LIVE)
    result = {**verified, "retained_oid": retained_oid, "renamed": renamed,
              "previous_table": previous, "rolled_back_table": names.rolled_back,
              "seconds": round(time.time() - started, 2)}
    record(db, "swap", "swapped", result)
    return result


def rollback(db, names: SwapNames, accept_source_drift: bool = False) -> dict:
    """R1. Valid only while nothing has been ingested since the swap (plan v2, D11).

    R1 restores the scores the site had before the swap. It deliberately does
    NOT ask whether the current rc3 scores are right -- that they are wrong is
    the usual reason to be here. What it does ask is whether the table it is
    about to restore is still the intended target, and whether the source rows
    those restored scores describe are still the ones they were computed from
    (external review round 2, finding 1: max(matches.id) shows neither, and an
    admin correction during the hold changes no match id).

    `accept_source_drift` performs the rollback anyway and records that it was
    overridden. A recovery path that can refuse outright is its own hazard, so
    the refusal is a stop sign, not a wall.
    """
    started = time.time()
    previous, rolled_back = names.previous, names.rolled_back
    if not _table_exists(db, previous):
        raise Refused(f"{previous} does not exist: there is nothing to roll back to")
    if _table_exists(db, rolled_back):
        raise Refused(f"{rolled_back} exists from an earlier rollback: inspect it and drop it first")
    _require_gate_closed(db, "rollback")
    db.execute(text(f"SET LOCAL lock_timeout = '{SWAP_LOCK_TIMEOUT}'"))
    db.execute(text(f"SET LOCAL statement_timeout = '{SWAP_STATEMENT_TIMEOUT}'"))
    # Sources first and under SHARE, for the reason swap() gives: the digest is
    # ~16 s over these five tables, and it must not run inside the live table's
    # exclusive lock. Readers are unaffected.
    db.execute(text(f"LOCK TABLE {', '.join(SOURCE_TABLES)} IN SHARE MODE"))
    _clear_player_cache(db)
    db.execute(text(f"LOCK TABLE {LIVE}, {_quoted(previous)} IN ACCESS EXCLUSIVE MODE"))
    _lock_gate_closed(db, "rollback")

    swapped = _latest(db, ("swap", "swapped"), ("rollback", "rolled back"))
    if swapped is None or swapped.operation != "swap":
        raise Refused("the log's newest committed swap-or-rollback is not a swap, so there is "
                      "no swap this rollback can be sure it undoes")
    # A swap logged before the names were parameters (rc3's) did not record
    # them; the retained-oid check below still ties the table to that swap.
    recorded_previous = swapped.details.get("previous_table")
    if recorded_previous is not None and recorded_previous != previous:
        raise Refused(f"the swap this would undo (entry {swapped.id}) set the live table aside as "
                      f"{recorded_previous}, not {previous}: name the table that swap retained")
    max_match_id = _scalar(db, "SELECT max(id) FROM matches")
    if max_match_id != swapped.details.get("max_match_id"):
        raise Refused(f"matches were ingested after the swap (max id {swapped.details.get('max_match_id')} "
                      f"then, {max_match_id} now): restoring {previous} would lose their scores. R1 "
                      "no longer applies (plan v2, D11) -- fix forward, or capture the current "
                      "scores first")

    retained_oid = _table_oid(db, previous)
    expected_oid = swapped.details.get("retained_oid")
    if expected_oid is not None and expected_oid != retained_oid:
        raise Refused(f"{previous} is oid {retained_oid}, but the swap retained oid {expected_oid}: "
                      f"the table under that name is not the one the swap set aside. Inspect it "
                      f"before restoring anything")

    drift = []
    unrecorded = []
    swap_digests = swapped.details.get("row_digests") or {}
    if swap_digests:
        now = _row_digests(db, SOURCE_TABLES)
        # A swap recorded before a table joined SOURCE_TABLES has no digest for
        # it (`players`, Impact v4). "Absent" is not "changed": comparing None
        # against the current digest reported a drift that never happened, and
        # refused every such rollback -- during exactly the incident rollback
        # exists for. Absent tables are reported instead, as the previous-table
        # check above reports an unrecorded name.
        unrecorded = sorted(name for name in now if name not in swap_digests)
        drift = sorted(name for name, digest in now.items()
                       if name in swap_digests and swap_digests[name] != digest)
    if drift and not accept_source_drift:
        raise Refused(
            f"{', '.join(drift)} changed since the swap, so the scores in {previous} were computed "
            f"from source rows that no longer exist as they were: restoring them would describe "
            f"different inputs, and nothing afterwards would say so. Check what changed. To roll "
            f"back regardless -- which is the right call if the live scores are the emergency -- "
            f"re-run with --accept-source-drift")

    renamed = _rename_owned_objects(db, LIVE, LIVE, rolled_back)
    db.execute(text(f"ALTER TABLE {LIVE} RENAME TO {_quoted(rolled_back)}"))
    db.execute(text(f"ALTER TABLE {_quoted(previous)} RENAME TO {LIVE}"))
    renamed += _rename_owned_objects(db, LIVE, previous, LIVE)
    # Nothing writes scores again until the owner decides what should.
    db.execute(text("UPDATE scoring_gate SET state = 'closed', note = :n, updated_at = now() "
                    "WHERE id"), {"n": "closed by swap_impact_scores.py rollback"})
    result = {"undoes_swap_entry": swapped.id, "max_match_id": max_match_id, "renamed": renamed,
              "previous_table": previous, "rolled_back_table": rolled_back,
              "source_digests_not_recorded": unrecorded,
              "retained_oid": retained_oid, "source_drift": drift,
              "accepted_source_drift": bool(drift) and accept_source_drift,
              "seconds": round(time.time() - started, 2)}
    record(db, "rollback", "rolled back", result)
    return result


def state(db, names: SwapNames) -> dict:
    tables = {name: _table_exists(db, name) for name in (LIVE, BUILT, names.previous, names.rolled_back)}
    # Every other impact_scores* relation, so `state` still answers "was this
    # database already swapped?" when the caller named a different release's
    # tables: it is the tool's discovery command, and it now requires the names
    # it used to default (Impact v4 plan, section 4.1).
    others = [name for (name,) in db.execute(text(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname LIKE :like "
        "ORDER BY c.relname"), {"like": LIVE + "%"}).all() if name not in tables]
    return {
        "database": _scalar(db, "SELECT current_database()"),
        "tables": tables,
        "other_impact_scores_tables": others,
        "rows": {name: _scalar(db, f"SELECT count(*) FROM {_quoted(name)}")
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
    parser.add_argument("--expect-scoring-version", type=int,
                        help="verify-build and verify-live: the scoring_version every row must carry "
                             "(required there; no default)")
    parser.add_argument("--identity", required=True,
                        help="the gate's admin identity for this release (e.g. v4-runbook)")
    parser.add_argument("--previous-table",
                        help="swap, rollback, state: the retained table (v4: impact_scores_v3)")
    parser.add_argument("--rolled-back-table",
                        help="swap, rollback, state: where a rollback sets the live table aside")
    parser.add_argument("--yes", action="store_true", help="required for swap and rollback")
    parser.add_argument("--accept-source-drift", action="store_true",
                        help="rollback only: restore even though the source tables changed since the "
                             "swap. The restored scores will describe inputs that have since moved; the "
                             "log entry records that this was overridden")
    args = parser.parse_args(argv)
    # Every release-specific value is checked before a connection is made.
    if args.command in ("verify-build", "verify-live") and args.expect_scoring_version is None:
        parser.error(f"{args.command} needs --expect-scoring-version: there is no default")
    names = None
    if args.command in ("swap", "rollback", "state"):
        for flag, value in (("--previous-table", args.previous_table),
                            ("--rolled-back-table", args.rolled_back_table)):
            if not value:
                parser.error(f"{args.command} needs {flag}: there is no default")
        try:
            names = SwapNames.validated(previous=args.previous_table, rolled_back=args.rolled_back_table)
        except ValueError as exc:
            parser.error(str(exc))

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
            print(canonical_json(state(db, names)))
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
            result = (swap(db, names) if args.command == "swap"
                      else rollback(db, names, accept_source_drift=args.accept_source_drift))
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
