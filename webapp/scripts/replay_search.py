"""Search the stored replay moments: `replay_search.py [filters...] [--format json|coach] [--status]`.

    python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/replay_search.py kind=death first=1 side=attack --format coach

Filters are `key=value` arguments (kind, map, since, until, with, circle, as, side, round, state, agent, ...).
json (default): one hit per line, then a final {"total","page","pages"} line. coach: one line per hit.
`--status` prints the index counts (linked, indexed, missing, stale, failed). A bad filter prints
`error: <message>` to stderr and exits 2. The script never writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.search.filters import FilterError  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Search the replay moment index.")
    parser.add_argument("filters", nargs="*", help="key=value filters")
    parser.add_argument("--format", choices=("json", "coach"), default="json")
    parser.add_argument("--status", action="store_true", help="print the index status and exit")
    args = parser.parse_args(argv)

    from app import db as app_db
    from app.services import replay_search, replay_search_index

    with app_db.SessionLocal() as db:
        if args.status:
            print(json.dumps(replay_search_index.status(db)))
            return 0
        try:
            result = replay_search.search(db, args.filters)
        except FilterError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.format == "coach":
        for line in replay_search.coach_lines(result):
            print(line)
    else:
        for hit in result["hits"]:
            print(json.dumps(hit))
        print(json.dumps({"total": result["total"], "page": result["page"], "pages": result["pages"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
