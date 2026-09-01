"""
One-command pipeline: given a Riot ID, find their N most recent tracker.gg
matches, skip any already in the DB (dedup by tracker.gg's own match ID, so
the same match is never double-ingested even when reached via a different
player's history), and ingest+score the rest.

Requires scripts/launch_trackergg_chrome.ps1 to already be running (a real,
human-navigable Chrome tab -- this script paces itself between matches rather
than firing requests back-to-back).

Before any new ingestion, also checks the whole DB for a match that
previously committed but never got scored (a stranded match left behind by a
prior run that was killed/crashed mid-ingest) and backfills it -- see
backfill_unscored_matches in app.adapters.trackergg_browserstate_source.

After ingesting, any player whose player_view_cache rows were invalidated gets
a full career recompute (a pre-warm) so their next page load is a cache hit
instead of a live compute -- a few extra seconds per affected player. The
site-wide "All Players" stats cache (app.services.site_stats_cache) is
refreshed the same way whenever any match was ingested. Pass --no-prewarm to
skip the per-player pre-warm and let both caches repopulate lazily on next
visit -- the site-wide refresh isn't gated by --no-prewarm since it's one
cheap call, not a per-player fan-out.

Usage:
    .venv\\Scripts\\python.exe scripts\\ingest_trackergg_player.py "NPrightdolphin#NA1" --count 5
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import backfill_unscored_matches, ingest_recent_matches
from app.db import SessionLocal
from app.services.player_view_cache import prewarm_player_cache
from app.services.site_stats import refresh_site_stats

CDP_URL = "http://localhost:9222"


def main(riot_id: str, count: int, no_prewarm: bool) -> None:
    db = SessionLocal()
    try:
        dirty = backfill_unscored_matches(db)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()
            dirty |= ingest_recent_matches(db, page, riot_id, count)
            page.close()

        if dirty and not no_prewarm:
            print(f"pre-warming cache for {len(dirty)} player(s)...")
            prewarm_player_cache(db, dirty)

        if dirty:
            print("refreshing site stats cache...")
            refresh_site_stats(db)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("riot_id", help="Riot ID, e.g. 'NPrightdolphin#NA1'")
    parser.add_argument("--count", type=int, default=5, help="how many recent matches to consider")
    parser.add_argument(
        "--no-prewarm", action="store_true", help="skip the post-ingest cache pre-warm (see module docstring)"
    )
    args = parser.parse_args()
    main(args.riot_id, args.count, args.no_prewarm)
