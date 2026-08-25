"""
One-off backfill: pages back through full Competitive history (via
ingest_full_history) for a fixed list of tracked_players.json members that
are currently under 100 matches in the DB, to bring everyone up to at least
~100 matches for the /stats "All Players" sample size. Unlike
crawl_map_diversity_data.py this does NOT snowball into newly-discovered
opponents -- it only touches the players listed in TARGETS below.

Requires scripts/launch_trackergg_chrome.ps1 to already be running.

Usage:
    .venv\\Scripts\\python.exe scripts\\backfill_to_100.py
"""

import json
import random
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from sqlalchemy import text

from app.adapters.trackergg_browserstate_source import ingest_full_history
from app.db import SessionLocal
from app.services.player_view_cache import prewarm_player_cache
from app.services.site_stats import refresh_site_stats

CDP_URL = "http://localhost:9222"
MIN_PLAYER_DELAY_SECONDS = 5
MAX_PLAYER_DELAY_SECONDS = 12
TARGET_MATCH_COUNT = 100
# Bounds how many raw (pre-dedup) matches discovery will page back through
# per player -- Beef Shortrib had 565 available uncapped, which ran for
# hours. 150 gives comfortable buffer over the ~100 target after dedup
# overlap with matches already ingested via shared lobbies.
MAX_MATCHES_PER_PLAYER = 150

# Beef Shortrib is already past 100 (don't pull more); SambuUwU is at
# tracker.gg's apparent history ceiling (~72, confirmed via a prior full
# crawl that found 0 new matches across 3 more acts) so isn't included.
TARGETS = [
    "Yosher#Toshi",
    "ternstyle#GIGI",
    "zopecow#1570",
    "DoubleBl1nd#BEEF",
    "flatcat#woof",
]


def match_count(db, riot_id: str) -> int:
    return db.execute(
        text("""
            SELECT count(mp.match_id)
            FROM players p
            JOIN match_players mp ON mp.player_id = p.id
            WHERE lower(p.display_name) = lower(:riot_id)
        """),
        {"riot_id": riot_id},
    ).scalar()


def main() -> None:
    db = SessionLocal()
    all_dirty: set[int] = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]

            stale_pages = list(context.pages)
            page = context.new_page()
            if stale_pages:
                print(f"closing {len(stale_pages)} stale page(s) from a previous run...")
                for stale_page in stale_pages:
                    try:
                        stale_page.close()
                    except Exception:
                        pass

            for i, riot_id in enumerate(TARGETS):
                before = match_count(db, riot_id)

                if before >= TARGET_MATCH_COUNT:
                    print(f"\n=== {riot_id}: already at {before}, skipping ===")
                    continue

                # Release the connection back to the pool before the long,
                # DB-idle discovery phase below (can be 10-20+ min for a deep
                # history) -- otherwise it sits idle on one held connection
                # that Neon may silently close, and pool_pre_ping never gets
                # a chance to catch that since the connection was never
                # checked back in.
                db.commit()
                print(f"\n=== {riot_id}: {before} matches before ===")
                try:
                    dirty = ingest_full_history(db, page, riot_id, max_matches=MAX_MATCHES_PER_PLAYER)
                    all_dirty |= dirty
                except Exception as e:
                    print(f"  error backfilling {riot_id}, skipping: {e}")
                    db.rollback()

                after = match_count(db, riot_id)
                print(f"=== {riot_id}: {after} matches after (+{after - before}) ===")

                if i < len(TARGETS) - 1:
                    delay = random.uniform(MIN_PLAYER_DELAY_SECONDS, MAX_PLAYER_DELAY_SECONDS)
                    print(f"waiting {delay:.1f}s before next player...")
                    time.sleep(delay)

            page.close()

        if all_dirty:
            print(f"\npre-warming cache for {len(all_dirty)} player(s)...")
            prewarm_player_cache(db, all_dirty)
            print("refreshing site stats cache...")
            refresh_site_stats(db)

        print("\n=== final counts ===")
        for riot_id in TARGETS:
            print(f"  {riot_id}: {match_count(db, riot_id)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
