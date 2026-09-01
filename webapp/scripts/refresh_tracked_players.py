"""
Batch refresh: walks scripts/tracked_players.json and pulls each player's
`count` most recent matches, same as running ingest_trackergg_player.py once
per Riot ID -- except it reuses a single Chrome/CDP connection across the
whole roster and keeps going if one player's profile is private or otherwise
fails, instead of stopping the batch.

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

from app.adapters.trackergg_browserstate_source import backfill_unscored_matches, ingest_recent_matches
from app.db import SessionLocal
from app.services.player_view_cache import prewarm_player_cache
from app.services.site_stats import refresh_site_stats

CDP_URL = "http://localhost:9222"
ROSTER_PATH = Path(__file__).resolve().parent / "tracked_players.json"
MIN_PLAYER_DELAY_SECONDS = 5
MAX_PLAYER_DELAY_SECONDS = 12


def main(count: int, no_prewarm: bool) -> None:
    roster = json.loads(ROSTER_PATH.read_text())
    db = SessionLocal()
    try:
        all_dirty: set[int] = backfill_unscored_matches(db)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()

            for i, riot_id in enumerate(roster):
                try:
                    all_dirty |= ingest_recent_matches(db, page, riot_id, count)
                except Exception as e:
                    print(f"  error ingesting {riot_id}, skipping: {e}")

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=20, help="how many recent matches to consider per player"
    )
    parser.add_argument(
        "--no-prewarm", action="store_true", help="skip the post-refresh cache pre-warm (see module docstring)"
    )
    args = parser.parse_args()
    main(args.count, args.no_prewarm)
