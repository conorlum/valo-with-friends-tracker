"""Freeze an Impact scoring candidate into a manifest, from committed code only.

rc2 was frozen with an uncommitted scratchpad script, so nothing in the
repository could reproduce its freeze. This is the committed replacement,
declared for rc3 on 2026-09-16 in docs/superpowers/2026-09-07-predeclared-values.md
and specified in docs/superpowers/plans/2026-09-16-rc3-ship-plan-v2.md (3.6).

It refuses to freeze a checkout that is not exactly a commit: any tracked change
or untracked file under webapp/ stops it, because the manifest names HEAD as
`scorer_revision` and a freeze of anything else would name the wrong code.

What it records, beyond build_manifest's code identity:
- scorer_revision: HEAD;
- source_snapshots: match_source_fingerprint for the declared matches, all read
  inside ONE repeatable-read, read-only transaction;
- freeze_context: match count, max match id, the snapshot id and the database
  NAME (never the host or credentials -- the repository is public);
- environment: the full interpreter version and `pip freeze`, which the
  ingestion preflight later requires the scoring machine to match.

    DATABASE_URL=... python scripts/freeze_impact_candidate.py \
        --candidate-id impact-rc3 --release-comparator impact_rc3 --activation-version 3 \
        --matches 3104,3129,3130,3131,3113,3118,3121,3114,3115,3116,3117,3120,3133 \
        --out ../docs/superpowers/impact-rc3/candidate-manifest.json

Commit the manifest on its own and never edit it afterwards: review results are
tied to its hash.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa

from app.scoring.impact_manifest import (
    build_manifest,
    lf_sha256,
    load_manifest,
    match_source_fingerprint,
    verify_manifest,
    write_manifest,
)

WEBAPP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(WEBAPP)


class FreezeRefused(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def require_clean_webapp(run_git=git) -> str:
    """HEAD, if webapp/ is exactly that commit; otherwise refuse.

    Untracked files count: an uncommitted script is exactly how rc2's freeze
    became unreproducible.
    """
    status = run_git("status", "--porcelain", "--untracked-files=all", "--", "webapp")
    dirty = [line for line in status.splitlines() if line.strip()]
    if dirty:
        raise FreezeRefused("webapp/ is not exactly a commit, so HEAD would name the wrong code:\n  "
                            + "\n  ".join(dirty[:20]))
    return run_git("rev-parse", "HEAD").strip()


def installed_packages() -> list[str]:
    frozen = subprocess.run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
                            capture_output=True, text=True, timeout=120, check=True)
    return sorted(line.strip() for line in frozen.stdout.splitlines() if line.strip())


def freeze_context(db, match_ids) -> tuple[dict, dict]:
    """(source_snapshots, freeze_context), read in the caller's transaction."""
    snapshots = {str(m): match_source_fingerprint(db, m) for m in match_ids}
    dialect = db.get_bind().dialect.name
    context = {
        "database": (db.execute(sa.text("SELECT current_database()")).scalar()
                     if dialect == "postgresql" else dialect),
        "snapshot": (db.execute(sa.text("SELECT txid_current_snapshot()::text")).scalar()
                     if dialect == "postgresql" else None),
        "match_count": db.execute(sa.text("SELECT count(*) FROM matches")).scalar(),
        "max_match_id": db.execute(sa.text("SELECT max(id) FROM matches")).scalar(),
        "declared_matches": [int(m) for m in match_ids],
    }
    return {"matches": snapshots}, context


def freeze(db, *, candidate_id, release_comparator, activation_version, match_ids, out_path,
           scorer_revision, packages, notes=()):
    source_snapshots, context = freeze_context(db, match_ids)
    manifest = build_manifest(
        candidate_id=candidate_id,
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        scorer_revision=scorer_revision,
        activation_impact_calculation_version=activation_version,
        source_snapshots=source_snapshots,
        release_comparator=release_comparator,
        notes=notes,
    )
    manifest["freeze_context"] = context
    manifest["environment"] = {"python": sys.version, "packages": list(packages)}
    write_manifest(out_path, manifest)
    verify_manifest(load_manifest(out_path))
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--release-comparator", required=True)
    parser.add_argument("--activation-version", type=int, required=True)
    parser.add_argument("--matches", required=True, help="comma-separated declared match ids")
    parser.add_argument("--out", required=True)
    parser.add_argument("--note", action="append", default=[])
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    try:
        revision = require_clean_webapp()
    except FreezeRefused as exc:
        print(f"REFUSED: {exc}")
        return 3
    match_ids = [int(m) for m in args.matches.split(",")]

    db = SessionLocal()
    try:
        db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        manifest = freeze(db, candidate_id=args.candidate_id, release_comparator=args.release_comparator,
                          activation_version=args.activation_version, match_ids=match_ids,
                          out_path=args.out, scorer_revision=revision, packages=installed_packages(),
                          notes=args.note)
        db.rollback()
    finally:
        db.close()

    print(f"froze {manifest['candidate_id']} at {revision}")
    print(f"  database {manifest['freeze_context']['database']}, {manifest['freeze_context']['match_count']} "
          f"matches (max id {manifest['freeze_context']['max_match_id']})")
    print(f"  {len(match_ids)} declared matches fingerprinted in one snapshot")
    print(f"  wrote {args.out}  LF-SHA-256 {lf_sha256(args.out)}")
    print("  verified. Commit it on its own; never edit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
