"""
One-off, time-boxed burst: drains scripts/map_crawl_state.json's already-
discovered opponent queue (8000+ players found by an earlier map-diversity
snowball crawl, never processed) to add as many new matches as possible
within a fixed wall-clock budget.

Unlike crawl_map_diversity_data.py, this pulls only each player's ~20 most
recent matches (ingest_recent_matches -- one page load to discover) rather
than full Competitive history (ingest_full_history -- pages through up to
37 acts, many minutes of discovery per player). That maximizes distinct new
matches per minute of budget, which is what matters for a fixed-time run.
Does NOT snowball further (no new opponents enqueued) and does NOT mutate
map_crawl_state.json -- that file stays untouched for the separate
map-diversity project to resume correctly later.

Requires scripts/launch_trackergg_chrome.ps1 to already be running.

Usage:
    .venv\\Scripts\\python.exe scripts\\snowball_1hour.py --minutes 55
"""

import argparse
import random
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import ingest_recent_matches
from app.db import SessionLocal
from app.services.player_view_cache import prewarm_player_cache
from app.services.site_stats import refresh_site_stats
from sqlalchemy import text

CDP_URL = "http://localhost:9222"
MIN_PLAYER_DELAY_SECONDS = 5
MAX_PLAYER_DELAY_SECONDS = 12
MATCHES_PER_PLAYER = 20
STATE_PATH = Path(__file__).resolve().parent / "map_crawl_state.json"


def total_match_count(db) -> int:
    return db.execute(text("SELECT count(*) FROM matches")).scalar()


def main(minutes: float) -> None:
    deadline = time.time() + minutes * 60
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    already_crawled = set(state.get("crawled", []))
    queue = [rid for rid in state.get("queue", []) if rid not in already_crawled]
    print(f"queue has {len(queue)} untouched player(s) to draw from")

    db = SessionLocal()
    all_dirty: set[int] = set()
    players_done = 0
    before_total = total_match_count(db)

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

            for riot_id in queue:
                remaining = deadline - time.time()
                if remaining <= 0:
                    print(f"\ntime budget exhausted, stopping (processed {players_done} players)")
                    break

                db.commit()  # release connection before this player's discovery page load
                print(f"\n--- {riot_id} ({remaining / 60:.1f} min left) ---")
                try:
                    dirty = ingest_recent_matches(db, page, riot_id, MATCHES_PER_PLAYER)
                    all_dirty |= dirty
                except Exception as e:
                    print(f"  error on {riot_id}, skipping: {e}")
                    db.rollback()

                players_done += 1

                if deadline - time.time() > 0:
                    delay = random.uniform(MIN_PLAYER_DELAY_SECONDS, MAX_PLAYER_DELAY_SECONDS)
                    time.sleep(delay)
            else:
                print("\nran through the entire available queue before time ran out")

            page.close()

        if all_dirty:
            print(f"\npre-warming cache for {len(all_dirty)} player(s)...")
            prewarm_player_cache(db, all_dirty)
            print("refreshing site stats cache...")
            refresh_site_stats(db)

        after_total = total_match_count(db)
        print("\n=== summary ===")
        print(f"players processed: {players_done}")
        print(f"matches in DB: {before_total} -> {after_total} (+{after_total - before_total})")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=55, help="time budget in minutes")
    args = parser.parse_args()
    main(args.minutes)
