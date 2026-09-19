"""
Batch refresh: walks scripts/tracked_players.json and pulls each player's
`count` most recent matches, same as running ingest_trackergg_player.py once
per Riot ID -- except it reuses a single Chrome/CDP connection across the
whole roster and keeps going if one player's profile is private or otherwise
fails, instead of stopping the batch.

Discovery pages through tracker.gg's "Load More" control over the All-Acts
history view, so --count above 20 reaches past the one server-rendered batch.
The run ends with a per-player REACHED/REQUESTED table naming why each player
stopped where they did, and exits non-zero if any player ended INCOMPLETE --
a roster that quietly returns 20 apiece can no longer read as success.

Requires scripts/launch_trackergg_chrome.ps1 to already be running.

Before any new ingestion, also checks the whole DB for a match that
previously committed but never got scored (a stranded match left behind by a
prior run that was killed/crashed mid-ingest) and backfills it -- see
backfill_unscored_matches in app.adapters.trackergg_browserstate_source.

After the whole roster is refreshed, every player whose player_view_cache rows
were invalidated by ANY ingested match gets a full career recompute (a
pre-warm), once each -- not once per match, which would be quadratic over a
12-player x 20-match refresh. That's still roughly (number of players with
new matches) x several seconds. The site-wide "All Players" stats cache
(app.services.site_stats_cache) is refreshed once too, regardless of
--no-prewarm (it's a single cheap call, not a per-player fan-out). Pass
--no-prewarm to skip the per-player pre-warm and let that cache repopulate
lazily as pages are visited.

Usage:
    .venv\\Scripts\\python.exe scripts\\refresh_tracked_players.py --count 20
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import (
    DiscoveryResult,
    DiscoveryStatus,
    backfill_unscored_matches,
    ingest_paginated_history,
)
from app.db import SessionLocal
from app.services.player_view_cache import prewarm_player_cache
from app.services.site_stats import refresh_site_stats

CDP_URL = "http://localhost:9222"
ROSTER_PATH = Path(__file__).resolve().parent / "tracked_players.json"
MIN_PLAYER_DELAY_SECONDS = 5
MAX_PLAYER_DELAY_SECONDS = 12
# The run completed but fell short of --count for at least one player. Distinct
# from 1 (a crash) so callers can tell the two apart.
EXIT_INCOMPLETE = 2


def _print_roster_report(results: list[DiscoveryResult], count: int) -> None:
    """Reached vs requested for every player, with the ones that fell short
    called out. A roster refresh that quietly returns 20 for nine players is
    the exact failure this table exists to make impossible to miss."""
    print("\n" + "=" * 72)
    print(f"DISCOVERY REPORT -- {count} requested per player")
    print("=" * 72)
    width = max((len(r.riot_id) for r in results), default=0)
    for r in results:
        flag = "" if r.is_conclusive else "   <- FLOOR, not their history"
        print(f"  {r.riot_id:<{width}}  {r.reached:>4}/{r.requested:<4} "
              f"{r.status.value:<11}{flag}")

    incomplete = [r for r in results if r.status is DiscoveryStatus.INCOMPLETE]
    if incomplete:
        print(f"\n  {len(incomplete)} player(s) INCOMPLETE -- reason given per player:")
        for r in incomplete:
            print(f"    {r.riot_id}: {r.reason}")
    empty = [r for r in results
             if r.status in (DiscoveryStatus.NO_HISTORY, DiscoveryStatus.PRIVATE)]
    if empty:
        print(f"\n  {len(empty)} player(s) returned nothing, by named cause:")
        for r in empty:
            print(f"    {r.riot_id}: {r.status.value} -- {r.reason}")
    print("=" * 72)


def main(count: int, no_prewarm: bool) -> int:
    roster = json.loads(ROSTER_PATH.read_text())
    results: list[DiscoveryResult] = []
    db = SessionLocal()
    try:
        all_dirty: set[int] = backfill_unscored_matches(db)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()

            for i, riot_id in enumerate(roster):
                try:
                    dirty, result = ingest_paginated_history(db, page, riot_id, count)
                    all_dirty |= dirty
                    results.append(result)
                except Exception as e:
                    # One player's failure never aborts the batch -- but it is
                    # recorded as INCOMPLETE so it cannot vanish into the log.
                    print(f"  error ingesting {riot_id}, skipping: {e}")
                    results.append(
                        DiscoveryResult(
                            riot_id=riot_id,
                            requested=count,
                            status=DiscoveryStatus.INCOMPLETE,
                            reason=f"{type(e).__name__}: {e}",
                        )
                    )

                if i < len(roster) - 1:
                    delay = random.uniform(MIN_PLAYER_DELAY_SECONDS, MAX_PLAYER_DELAY_SECONDS)
                    print(f"waiting {delay:.1f}s before next player...")
                    time.sleep(delay)

            page.close()

        if all_dirty and not no_prewarm:
            print(f"pre-warming cache for {len(all_dirty)} player(s)...")
            prewarm_player_cache(db, all_dirty)

        if all_dirty:
            print("refreshing site stats cache...")
            refresh_site_stats(db)
    finally:
        db.close()

    _print_roster_report(results, count)
    # Exit 2 (not 1) if ANY player ended INCOMPLETE: the run itself worked, but
    # it did not reach what it was asked for, and that must not read as success
    # to a shell caller. 1 stays reserved for an actual crash, so
    # refresh_remote.ps1 can tell "fell short" from "blew up".
    return EXIT_INCOMPLETE if any(
        r.status is DiscoveryStatus.INCOMPLETE for r in results
    ) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=20, help="how many recent matches to reach per player"
    )
    parser.add_argument(
        "--no-prewarm", action="store_true", help="skip the post-refresh cache pre-warm (see module docstring)"
    )
    args = parser.parse_args()
    sys.exit(main(args.count, args.no_prewarm))
