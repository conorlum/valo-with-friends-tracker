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
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import (
    ProfilePrivateError,
    discover_recent_match_ids,
    fetch_match_json,
    load_match,
)
from app.db import SessionLocal
from app.models import Match
from app.scoring.impact import compute_impact_for_match

CDP_URL = "http://localhost:9222"
MIN_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 12


def main(riot_id: str, count: int) -> None:
    db = SessionLocal()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()

            try:
                match_ids = discover_recent_match_ids(page, riot_id, count)
            except ProfilePrivateError as e:
                print(f"skipping {riot_id}: {e}")
                return
            print(f"discovered {len(match_ids)} recent match(es) for {riot_id}")

            new_ids = []
            for match_id in match_ids:
                if db.query(Match).filter_by(external_id=match_id).one_or_none() is not None:
                    print(f"  {match_id}: already ingested, skipping")
                else:
                    new_ids.append(match_id)

            for i, match_id in enumerate(new_ids):
                print(f"[{i + 1}/{len(new_ids)}] capturing {match_id}")
                match_json = fetch_match_json(page, match_id)
                match = load_match(db, match_json)
                compute_impact_for_match(db, match.id)
                print(f"  ingested {match.map_name} ({match_id})")

                if i < len(new_ids) - 1:
                    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
                    print(f"  waiting {delay:.1f}s before next match...")
                    time.sleep(delay)

            page.close()
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("riot_id", help="Riot ID, e.g. 'NPrightdolphin#NA1'")
    parser.add_argument("--count", type=int, default=5, help="how many recent matches to consider")
    args = parser.parse_args()
    main(args.riot_id, args.count)
