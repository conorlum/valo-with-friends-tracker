"""Links stored replays to their crawled matches, and stores their per-kill Impact split.

    .\.venv313\Scripts\python.exe scripts\with_friends_db.py --expect-database valowithfriendsdb scripts\link_replays.py
    ... scripts\link_replays.py --uuid <match uuid> [--uuid ...]
    ... scripts\link_replays.py --refresh-impact

Without `--uuid` it links every replay with `match_id IS NULL` whose match row now exists
(docs/replay-viewer-plan.md, "Linking later"); with it, those replays whatever their state.
Each link is its own locked transaction and never rewrites a blob or `link_inputs`. Then every
linked replay missing its per-kill split gets one (`--refresh-impact`: every linked replay, e.g.
after a rescore), each in its own transaction (app/services/replay_impact.py). It writes: run it
through `with_friends_db.py` without `--read-only`. The crawl scripts run the same pass at the end.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uuid", action="append", default=[], help="a replay's match UUID (repeatable)")
    parser.add_argument("--refresh-impact", action="store_true", help="recompute every linked replay's split")
    args = parser.parse_args(argv)

    from app.config import settings
    from app.services.replay_impact import link_pending_replays

    if settings.demo_mode:
        print("REFUSED: demo mode; the ValoMaths demo has no replays", file=sys.stderr)
        return 3
    if session_factory is None:
        from app.db import SessionLocal as session_factory
        from app.scoring.ingest_preflight import IngestRefused
        from app.services.replay_impact import claim_write_identity

        try:
            claim_write_identity(session_factory)  # the `players.riot_subject` backfill is gated
        except IngestRefused as refused:
            print(f"REFUSED: the scoring ingest preflight: {refused}", file=sys.stderr)
            return 3
    counts = link_pending_replays(session_factory, args.uuid or None, args.refresh_impact)
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 1 if "skipped" in counts or counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
