"""
One-command pipeline: given a Riot ID, find their N most recent tracker.gg
matches, skip any already in the DB (dedup by tracker.gg's own match ID, so
the same match is never double-ingested even when reached via a different
player's history), and ingest+score the rest.

Requires scripts/launch_trackergg_chrome.ps1 to already be running (a real,
human-navigable Chrome tab -- this script paces itself between matches rather
than firing requests back-to-back).

Usage:
    .venv\\Scripts\\python.exe scripts\\ingest_trackergg_player.py "NPrightdolphin#NA1" --count 5
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import ingest_recent_matches
from app.db import SessionLocal

CDP_URL = "http://localhost:9222"


def main(riot_id: str, count: int) -> None:
    db = SessionLocal()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()
            ingest_recent_matches(db, page, riot_id, count)
            page.close()
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("riot_id", help="Riot ID, e.g. 'NPrightdolphin#NA1'")
    parser.add_argument("--count", type=int, default=5, help="how many recent matches to consider")
    args = parser.parse_args()
    main(args.riot_id, args.count)
