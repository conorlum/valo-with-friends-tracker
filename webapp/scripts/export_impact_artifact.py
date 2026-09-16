"""Canonical score artifacts for the rc3 release chain.

Declared 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md
("rc3: the owner's lock after measurement, and the replays declared before the
freeze"). One artifact is one (code revision, Python, configuration) scored
over the declared cohort, written as a keyed CSV and identified by the SHA-256
of its exact bytes. The chain K1..K5 in that entry compares those hashes, so
two runs that disagree on a single value in a single player-round disagree on
the hash -- which the old unkeyed, positional arrays could not detect.

THE HEADER IS RECORDED, NOT DERIVED. `COMPARISON_HEADER` below is fixed at
commit A and must never be re-evaluated from `impact.PERSISTED_FIELDS` at run
time: migration 0010 adds `trade_credit` (already in the header) and
`scoring_version` (excluded by the declaration, because review rows legitimately
carry 2 while activation rows carry 3). Re-deriving it would silently change the
projection mid-chain. tests/test_impact_artifact_export.py holds that contract.

Usage (read-only; from webapp/, with DATABASE_URL set):

    .venv313/Scripts/python.exe scripts/export_impact_artifact.py \
        --out "C:/Users/Conor Lum/Documents/valo-backups/rc3-artifacts" \
        --label K2-on --credit on --weights 1.0,2.5,2.5,100.0,1.0

`--no-fingerprints` skips contract (c)'s per-match source fingerprints, which
cost one round trip per match per query; use it only for rehearsals and tests,
never for an artifact the ledger's chain refers to.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa

from app.scoring import econ_buy_disruption as bd
from app.scoring import impact as impact_module
from app.scoring.impact import FormulaWeights, build_impact_rows_for_match
from app.scoring.impact_manifest import match_source_fingerprint

ARTIFACT_CONTRACT_VERSION = 1

KEY_FIELDS = ("round_id", "match_player_id")

#: Contract (a), the comparison projection. RECORDED at commit A: the keys, then
#: every field in `impact.PERSISTED_FIELDS` as it stood there, then the three
#: row fields the table never stores. `scoring_version` is deliberately absent.
COMPARISON_HEADER = (
    "round_id",
    "match_player_id",
    "kill_impact",
    "death_impact",
    "impact",
    "damage",
    "econ_impact",
    "time_impact",
    "swing_impact",
    "econ_kill",
    "econ_death",
    "clutch_kill",
    "clutch_death",
    "post_plant_kill",
    "post_plant_death",
    "traded_teammate",
    "traded_by_teammate",
    "trade_detail",
    "kill_order_bonus",
    "econ_component",
    "econ_pickup",
    "trade_credit",
    "leverage_component",
    "assists_component",
)

#: Fields the declaration excludes from the comparison projection even though
#: they are persisted. Provenance, not arithmetic.
EXCLUDED_FROM_COMPARISON = ("scoring_version",)


def canonical_json(value) -> str:
    """The one JSON form used on both sides of every comparison.

    Sorted keys and no whitespace, so neither Python's insertion order nor
    PostgreSQL's jsonb text form can change the bytes. NaN/Infinity are refused
    rather than emitted as JavaScript-only literals.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def render_field(value) -> str:
    """One cell of the artifact.

    SQL NULL is the empty field; a JSON null *inside* trade_detail stays inside
    its JSON text. That distinction is why the empty field is reserved: a row
    with no trade detail and a row whose trade detail is the JSON value null
    must not render identically.
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # before int: bool is an int subclass
        raise TypeError("booleans have no place in a score artifact")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (dict, list)):
        return canonical_json(value)
    raise TypeError(f"unexpected artifact value type: {type(value).__name__}")


def row_fields(row) -> list[str]:
    return [render_field(getattr(row, name)) for name in COMPARISON_HEADER]


def sort_key(row):
    return (row.round_id, row.match_player_id)


def write_artifact(rows, path) -> dict:
    """Write the comparison projection and return {sha256, rows, bytes}.

    The hash covers the exact file bytes, header included, so a reordering, an
    encoding change or a header change all move it.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COMPARISON_HEADER)
    count = 0
    for row in sorted(rows, key=sort_key):
        writer.writerow(row_fields(row))
        count += 1
    payload = buffer.getvalue().encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(payload)
    return {"sha256": hashlib.sha256(payload).hexdigest(), "rows": count, "bytes": len(payload)}


def read_artifact(path) -> list[dict]:
    """Parse an artifact back into dicts, for verification against a database."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader))
        if header != COMPARISON_HEADER:
            raise ValueError(f"artifact header is not the recorded one: {header}")
        return [dict(zip(header, row)) for row in reader]


def build_kwargs_for(*, weights: FormulaWeights, credit_on: bool) -> dict:
    """The locked rc3 structure with credit explicitly on or off.

    The OFF artifact the declaration requires differs from the ON artifact in
    exactly this one flag, so it is derived here rather than passed separately.
    """
    return {
        "enable_econ_component": True,
        "econ_model": bd.MODEL_V2_30_80_BONUS_DENIAL,
        "weights": weights,
        "use_realized_swing": True,
        "enable_trade_credit": credit_on,
    }


def export_rows(db, match_ids, build_kwargs) -> list:
    rows = []
    for match_id in match_ids:
        rows.extend(build_impact_rows_for_match(db, match_id, **build_kwargs))
    return rows


def _environment() -> dict:
    try:
        frozen = subprocess.run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
                                capture_output=True, text=True, timeout=120)
        packages = sorted(line.strip() for line in frozen.stdout.splitlines() if line.strip())
    except Exception as exc:  # pragma: no cover - environment dependent
        packages = [f"pip freeze failed: {exc!r}"]
    return {
        "python": sys.version,
        "python_short": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "packages": packages,
    }


def _inputs(db, match_ids, *, fingerprints: bool) -> dict:
    """Contract (c). The per-match fingerprints are the expensive part: four
    queries per match, all inside the caller's snapshot."""
    counts = {
        name: db.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar()
        for name in ("matches", "match_players", "rounds", "round_player_stats", "kill_events",
                     "impact_scores")
    }
    inputs = {
        "database": db.execute(sa.text("SELECT current_database()")).scalar(),
        "snapshot": db.execute(sa.text("SELECT txid_current_snapshot()::text")).scalar(),
        "alembic_version": db.execute(sa.text("SELECT version_num FROM alembic_version")).scalar(),
        "counts": counts,
        "max_match_id": db.execute(sa.text("SELECT max(id) FROM matches")).scalar(),
        "cohort_size": len(match_ids),
    }
    if fingerprints:
        per_match = {str(m): match_source_fingerprint(db, m) for m in match_ids}
        inputs["match_source_fingerprints"] = per_match
        inputs["cohort_fingerprint"] = hashlib.sha256(
            canonical_json(per_match).encode("utf-8")).hexdigest()
    else:
        inputs["match_source_fingerprints"] = None
        inputs["cohort_fingerprint"] = None
    return inputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="directory for the artifact and its sidecar")
    parser.add_argument("--label", required=True, help="chain link, e.g. K1-on, K2-on, K2-off")
    parser.add_argument("--credit", choices=("on", "off"), required=True)
    parser.add_argument("--weights", default="1.0,2.5,2.5,100.0,1.0",
                        help="A,B,C,D,trade_credit_scale (default: the locked rc3 weights)")
    parser.add_argument("--matches", help="comma-separated match ids (default: every match)")
    parser.add_argument("--no-fingerprints", action="store_true",
                        help="skip contract (c) source fingerprints -- rehearsals only")
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    a, b, c, d, scale = (float(x) for x in args.weights.split(","))
    weights = FormulaWeights(damage=a, leverage=b, econ=c, assists=d, trade_credit_scale=scale)
    build_kwargs = build_kwargs_for(weights=weights, credit_on=args.credit == "on")

    os.makedirs(args.out, exist_ok=True)
    artifact_path = os.path.join(args.out, f"{args.label}.csv")
    sidecar_path = os.path.join(args.out, f"{args.label}.json")

    started = time.time()
    db = SessionLocal()
    try:
        db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        if args.matches:
            match_ids = [int(x) for x in args.matches.split(",")]
        else:
            match_ids = [m for (m,) in db.execute(sa.text("SELECT id FROM matches ORDER BY id")).all()]
        print(f"{args.label}: {len(match_ids)} matches, credit {args.credit}, "
              f"weights A={a} B={b} C={c} D={d} scale={scale}", flush=True)

        rows = export_rows(db, match_ids, build_kwargs)
        written = write_artifact(rows, artifact_path)
        print(f"  scored {written['rows']:,} player-rounds in {(time.time() - started) / 60:.1f} min",
              flush=True)

        inputs = _inputs(db, match_ids, fingerprints=not args.no_fingerprints)
        db.rollback()
    finally:
        db.close()

    sidecar = {
        "artifact_contract_version": ARTIFACT_CONTRACT_VERSION,
        "label": args.label,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "projection": "comparison",
        "header": list(COMPARISON_HEADER),
        "excluded_from_comparison": list(EXCLUDED_FROM_COMPARISON),
        "configuration": {
            "weights": {"damage": a, "leverage": b, "econ": c, "assists": d,
                        "trade_credit_scale": scale},
            "enable_trade_credit": args.credit == "on",
            "enable_econ_component": True,
            "econ_model": bd.MODEL_V2_30_80_BONUS_DENIAL,
            "use_realized_swing": True,
            "impact_calculation_version": impact_module.IMPACT_CALCULATION_VERSION,
        },
        "artifact": {"file": os.path.basename(artifact_path), **written},
        "inputs": inputs,
        "environment": _environment(),
        "minutes": round((time.time() - started) / 60, 2),
    }
    with open(sidecar_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(sidecar))

    print(f"  sha256 {written['sha256']}", flush=True)
    print(f"  cohort fingerprint {inputs['cohort_fingerprint']}", flush=True)
    print(f"  wrote {artifact_path} and {sidecar_path} in {sidecar['minutes']:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
