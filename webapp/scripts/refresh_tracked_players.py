"""
Batch refresh: walks scripts/tracked_players.json and pulls each player's
`count` most recent matches, same as running ingest_trackergg_player.py once
per Riot ID -- except it reuses a single Chrome/CDP connection across the
whole roster and keeps going if one player's profile is private or otherwise
fails, instead of stopping the batch.

Requires scripts/launch_trackergg_chrome.ps1 to already be running.

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

from app.adapters.trackergg_browserstate_source import ingest_recent_matches
from app.db import SessionLocal

CDP_URL = "http://localhost:9222"
ROSTER_PATH = Path(__file__).resolve().parent / "tracked_players.json"
MIN_PLAYER_DELAY_SECONDS = 5
MAX_PLAYER_DELAY_SECONDS = 12


def main(count: int) -> None:
    roster = json.loads(ROSTER_PATH.read_text())
    db = SessionLocal()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()

            for i, riot_id in enumerate(roster):
                try:
                    ingest_recent_matches(db, page, riot_id, count)
                except Exception as e:
                    print(f"  error ingesting {riot_id}, skipping: {e}")

                if i < len(roster) - 1:
                    delay = random.uniform(MIN_PLAYER_DELAY_SECONDS, MAX_PLAYER_DELAY_SECONDS)
                    print(f"waiting {delay:.1f}s before next player...")
                    time.sleep(delay)

            page.close()
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=20, help="how many recent matches to consider per player"
    )
    args = parser.parse_args()
    main(args.count)
