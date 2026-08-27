"""
Snowball data crawl for reverse-engineering Riot's map-diversity algorithm
(see the `map-prediction-feature` plan). Seeds from scripts/tracked_players.json,
deep-ingests each player's *entire* Competitive history (not just their most
recent matches), then also deep-crawls every opponent newly discovered along
the way -- match_players rows already capture all 10 players per match via
the existing adapter, so any opponent encountered is already a Player row in
the DB; this script just also pulls *their* full history, snowballing outward
from the seed roster.

This is a multi-hour job (5-12s pacing between both players and matches, and
now also between acts within a player). Progress is checkpointed to
--state-file after every player, so it's safe to Ctrl+C and rerun -- already-
crawled players and already-ingested matches are both skipped on resume.

Requires scripts/launch_trackergg_chrome.ps1 to already be running.

Usage:
    .venv\\Scripts\\python.exe scripts\\crawl_map_diversity_data.py --max-players 60
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

# Riot IDs discovered while snowballing frequently contain non-ASCII
# characters (CJK, symbols, etc.) that crash `print()` under Windows'
# default cp1252 console encoding -- force UTF-8 so any riot_id is safe to
# print, here and in the adapter functions this script calls.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import ingest_full_history
from app.db import SessionLocal
from app.models import Player

CDP_URL = "http://localhost:9222"
ROSTER_PATH = Path(__file__).resolve().parent / "tracked_players.json"
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "map_crawl_state.json"
MIN_PLAYER_DELAY_SECONDS = 5
MAX_PLAYER_DELAY_SECONDS = 12


def _load_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {"crawled": [], "queue": []}


def _save_state(state_path: Path, state: dict) -> None:
    state_path.write_text(json.dumps(state, indent=2))


def _enqueue_new_opponents(db, state: dict) -> int:
    """Any Player.display_name in the DB that we haven't crawled or already
    queued is a newly-discovered opponent from a just-ingested match --
    add them to the queue. Returns how many were newly enqueued."""
    known = set(state["crawled"]) | set(state["queue"])
    all_names = {p.display_name for p in db.query(Player.display_name).all()}
    new_names = sorted(all_names - known)
    state["queue"].extend(new_names)
    return len(new_names)


def main(max_players: int | None, max_matches_per_player: int | None, max_acts: int | None, state_path: Path) -> None:
    state = _load_state(state_path)
    if not state["queue"] and not state["crawled"]:
        state["queue"] = json.loads(ROSTER_PATH.read_text())

    db = SessionLocal()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]

            # A prior run crashing or being force-stopped can leave its page
            # (and tracker.gg's dozens of ad/tracking iframes on it) attached
            # to the shared Chrome session. Left alone these accumulate across
            # resumed runs until connect_over_cdp's target enumeration times
            # out entirely -- so start every run from a clean slate. Open the
            # new page *before* closing old ones so the tab count never hits
            # zero -- Chrome quits when its last tab closes, taking the CDP
            # connection down with it.
            stale_pages = list(context.pages)
            page = context.new_page()
            if stale_pages:
                print(f"closing {len(stale_pages)} stale page(s) from a previous run...")
                for stale_page in stale_pages:
                    try:
                        stale_page.close()
                    except Exception:
                        pass

            while state["queue"]:
                if max_players is not None and len(state["crawled"]) >= max_players:
                    print(f"reached --max-players={max_players}, stopping")
                    break

                riot_id = state["queue"].pop(0)
                if riot_id in state["crawled"]:
                    continue

                print(f"\n=== crawling {riot_id} ({len(state['crawled'])} done, {len(state['queue'])} queued) ===")
                try:
                    ingest_full_history(
                        db, page, riot_id, max_matches=max_matches_per_player, max_acts=max_acts
                    )
                except Exception as e:
                    print(f"  error crawling {riot_id}, skipping: {e}")
                    db.rollback()

                state["crawled"].append(riot_id)
                added = _enqueue_new_opponents(db, state)
                if added:
                    print(f"  discovered {added} new opponent(s), added to queue")
                _save_state(state_path, state)

                if state["queue"]:
                    delay = random.uniform(MIN_PLAYER_DELAY_SECONDS, MAX_PLAYER_DELAY_SECONDS)
                    print(f"waiting {delay:.1f}s before next player...")
                    time.sleep(delay)

            page.close()
    finally:
        db.close()

    print(f"\ndone: {len(state['crawled'])} player(s) crawled, {len(state['queue'])} still queued")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-players", type=int, default=None, help="stop after crawling this many players total")
    parser.add_argument(
        "--max-matches-per-player", type=int, default=None, help="cap on matches ingested per player (default: no cap)"
    )
    parser.add_argument(
        "--max-acts", type=int, default=None, help="cap on how many Episodes/Acts to page back through per player"
    )
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH, help="checkpoint file path")
    args = parser.parse_args()
    main(args.max_players, args.max_matches_per_player, args.max_acts, args.state_file)
