"""Reads one match's replay data from the friends database into a local snapshot for the coach.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/coach_snapshot.py <match uuid> --out <dir>

Read-only. The snapshot holds player names: keep <dir> outside the repository. Exits 2 when refused."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if WEBAPP_ROOT.parent in args.out.resolve().parents:
        print("refused: --out is inside the repository", file=sys.stderr)
        return 2
    from app.coach import snapshot
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            data = snapshot.read(db, args.match_uuid.lower())
        except snapshot.SnapshotError as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
    snapshot.save(data, args.out)
    gaps = {n: r["gaps"] for n, r in data["rounds"].items() if r["gaps"]}
    print(f"snapshot of {len(data['rounds'])} rounds written to {args.out}; rounds with gaps: {gaps or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
