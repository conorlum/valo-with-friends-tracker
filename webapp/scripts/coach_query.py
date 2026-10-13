"""One coach question about a round, answered from a local snapshot and logged. No database, no network.

    $PY scripts/coach_query.py --run <dir> where <round> <t>
    $PY scripts/coach_query.py --run <dir> knew <round> <t> <A|B|player>
    $PY scripts/coach_query.py --run <dir> distance <round> <t> <player> <player>
    $PY scripts/coach_query.py --run <dir> control <round> <t0> <t1> "<area>"
    $PY scripts/coach_query.py --run <dir> timeline <round> [<t0> <t1>]
    $PY scripts/coach_query.py --run <dir> impact <round> <player>

<dir> holds the snapshot and receives <dir>/log.jsonl. Prints JSON with the call's log id. Times are seconds into
the round. Always exits 0: an error is an answer (`{"id", "error"}`), so the caller reads it instead of retrying
blind."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, type=Path, action="append")
    parser.add_argument("command")
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    if len(args.run) != 1:
        print("refused: --run may be given only once", file=sys.stderr)
        return 2
    args.run = args.run[0]
    from app.coach import query, snapshot

    print(json.dumps(query.run(snapshot.load(args.run), args.run, args.command, args.args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
