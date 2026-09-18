"""Did the prewarm actually produce usable pages? Prove it per player.

prewarm_player_cache is deliberately best-effort: one player's failure is logged
and skipped so the batch finishes. That is the right behaviour for a background
job and the wrong evidence for a release step -- printing "prewarmed 12" after
it proves only that twelve were REQUESTED (external review, A13).

This reads each player's cache rows back and applies the same test a real page
load does (decode_cache_row): the row exists, carries the version the RUNNING
code expects, and its blob validates and decodes. Both scopes, every player.
Run it from the checkout that is deployed, so the version it expects is the
version the site will ask for.

    DATABASE_URL=... python scripts/verify_player_cache_coverage.py --ids-file prewarm-roster-ids.txt
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Player, PlayerViewCache
from app.services.player_view_cache import cache_version, decode_cache_row

SCOPES = ("recent", "career")


def coverage(db, player_ids) -> dict:
    expected = cache_version()
    ok, problems = [], []
    for player_id in sorted(player_ids):
        player = db.get(Player, player_id)
        if player is None:
            problems.append(f"player {player_id}: no such player")
            continue
        rows = {row.scope: row for row in db.query(PlayerViewCache).filter_by(player_id=player_id).all()}
        for scope in SCOPES:
            row = rows.get(scope)
            if row is None:
                problems.append(f"player {player_id} ({player.display_name}) {scope}: no cache row")
            elif row.version != expected:
                problems.append(f"player {player_id} ({player.display_name}) {scope}: version "
                                f"{row.version}, the running code expects {expected}")
            elif decode_cache_row(row, player) is None:
                problems.append(f"player {player_id} ({player.display_name}) {scope}: blob does not "
                                "validate or decode")
            else:
                ok.append((player_id, scope))
    return {"expected_version": expected, "ok": len(ok), "problems": problems,
            "requested": len(player_ids) * len(SCOPES)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids-file", required=True, help="one player id per line")
    args = parser.parse_args(argv)
    player_ids = {int(line) for line in open(args.ids_file, encoding="utf-8") if line.strip()}

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        result = coverage(db, player_ids)
        db.rollback()
    finally:
        db.close()
    print(f"expected cache version {result['expected_version']}: "
          f"{result['ok']} of {result['requested']} player-scopes usable")
    for problem in result["problems"]:
        print(f"  {problem}")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
