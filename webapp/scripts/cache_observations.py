"""Build (or inspect) the ex-ante observation cache.

Run this once after an ingest; every weight/target experiment afterwards reads
the cache and runs in seconds instead of re-replaying 1,151 matches.

    .venv\\Scripts\\python.exe scripts\\cache_observations.py
    .venv\\Scripts\\python.exe scripts\\cache_observations.py --describe
    .venv\\Scripts\\python.exe scripts\\cache_observations.py --refresh

Read-only with respect to the database.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.services.impact_eval_cache import cache_path, describe, load_observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true",
                        help="print cache metadata without replaying or unpickling rows")
    parser.add_argument("--refresh", action="store_true",
                        help="rebuild even if the cached identity still matches")
    parser.add_argument("--path", type=Path, default=None, help="override the cache location")
    args = parser.parse_args()

    if args.describe:
        print(json.dumps(describe(args.path), indent=2, default=str))
        return 0

    db = SessionLocal()
    try:
        report: dict = {}
        observations = load_observations(db, path=args.path, refresh=args.refresh, report=report)
    finally:
        db.close()

    print(f"{len(observations)} observations  ({report['source']})")
    print(f"cache: {report['path']}")
    print(json.dumps(report["identity"], indent=2))
    for note in report.get("notes", []):
        print(f"  note: {note}")
    if report["source"] == "cache":
        print("\nAlready current. Pass --refresh to rebuild anyway.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
