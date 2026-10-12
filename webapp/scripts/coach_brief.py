"""Prints one player's match brief from a local snapshot (scripts/coach_snapshot.py). No database.

    $PY scripts/coach_brief.py --run <dir> --player "Name#TAG" [--flags-only]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--player", required=True)
    parser.add_argument("--flags-only", action="store_true")
    args = parser.parse_args()
    from app.coach import brief, snapshot

    try:
        snap = snapshot.load(args.run)
        built = brief.build(snap, snap.slot_of(args.player))
    except snapshot.SnapshotError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.flags_only:
        for r in built["rounds"]:
            for f in r["flags"]:
                print(f'r{f["round"]:>2} {f["t"]:>6.1f}s {f["kind"]:<24} {json.dumps(f["values"])}')
        return 0
    print(json.dumps(built, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
