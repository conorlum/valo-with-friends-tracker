"""Capture which players to prewarm before a swap empties the cache, then
prewarm exactly those players afterwards.

The swap truncates player_view_cache, and it must: every cached page was built
from the old scores. Two groups matter afterwards
(docs/superpowers/plans/2026-09-16-rc3-ship-plan-v2.md, 8.3 and 8.6):
- the roster (scripts/tracked_players.json), whose pages must be warm and proven
  usable before the release is called done;
- everyone else who had a cache row before the swap, i.e. recently viewed pages,
  prewarmed after the roster.

recompute_player_views.py cannot do this: it recomputes every player (hours) or
only players who still have cache rows, which after the swap is nobody.

    # before the swap (read-only)
    python scripts/prewarm_player_cache_ids.py capture --roster-out roster-ids.txt --recent-out recent-ids.txt

    # after the activation deploy is live, FROM THE ACTIVATION CHECKOUT: the cache
    # version includes IMPACT_CALCULATION_VERSION, so rows written by any other
    # checkout are ignored by the site
    python scripts/prewarm_player_cache_ids.py prewarm --ids-file roster-ids.txt
    python scripts/verify_player_cache_coverage.py --ids-file roster-ids.txt

Exit codes: capture 0, or 1 if a roster Riot ID matches no player (usually a
rename; see the notes on merging renamed players); prewarm 0, or 1 if any player
failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.models import Player, PlayerViewCache
from app.services import player_view_cache

ROSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracked_players.json")


def roster_ids(db, roster: list[str]) -> tuple[set[int], list[str]]:
    """(player ids, roster names with no player), matched exactly as ingestion matches."""
    found = dict(db.query(Player.display_name, Player.id).filter(Player.display_name.in_(roster)).all())
    return set(found.values()), [name for name in roster if name not in found]


def cached_ids(db) -> set[int]:
    return {player_id for (player_id,) in db.query(PlayerViewCache.player_id).distinct().all()}


def capture(db, roster: list[str]) -> dict:
    roster_found, missing = roster_ids(db, roster)
    return {"roster": roster_found, "recent": cached_ids(db) - roster_found, "missing": missing}


def write_ids(path: str, ids: set[int]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(f"{player_id}\n" for player_id in sorted(ids))


def read_ids(path: str) -> list[int]:
    with open(path, encoding="utf-8") as handle:
        return sorted({int(line) for line in handle if line.strip()})


def prewarm(db, player_ids) -> list[str]:
    """Both scopes for each player, one commit each. Unlike
    prewarm_player_cache, failures are returned rather than only logged: a
    release step must know about them."""
    failures = []
    for index, player_id in enumerate(player_ids, start=1):
        started = time.time()
        try:
            player_view_cache.recompute_player_views(db, player_id)
            print(f"[{index}/{len(player_ids)}] player {player_id}: {time.time() - started:.1f}s", flush=True)
        except Exception as exc:
            db.rollback()
            failures.append(f"player {player_id}: {exc!r}"[:300])
            print(f"[{index}/{len(player_ids)}] player {player_id}: FAILED {exc!r}"[:300], flush=True)
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--roster-out", required=True)
    capture_parser.add_argument("--recent-out", required=True)
    prewarm_parser = commands.add_parser("prewarm")
    prewarm_parser.add_argument("--ids-file", required=True)
    args = parser.parse_args(argv)

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        database = db.execute(text("SELECT current_database()")).scalar()
        print(f"database {database}")
        if args.command == "capture":
            with open(ROSTER_PATH, encoding="utf-8") as handle:
                roster = json.load(handle)
            captured = capture(db, roster)
            db.rollback()
            write_ids(args.roster_out, captured["roster"])
            write_ids(args.recent_out, captured["recent"])
            print(f"roster: {len(captured['roster'])} of {len(roster)} players -> {args.roster_out}")
            print(f"recently cached, beyond the roster: {len(captured['recent'])} -> {args.recent_out}")
            for name in captured["missing"]:
                print(f"  NOT FOUND: {name}")
            return 1 if captured["missing"] else 0

        player_ids = read_ids(args.ids_file)
        failures = prewarm(db, player_ids)
        print(f"prewarmed {len(player_ids) - len(failures)} of {len(player_ids)} players")
        for failure in failures:
            print(f"  {failure}")
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
