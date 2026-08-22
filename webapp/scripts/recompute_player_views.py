"""
Batch recompute of the player_view_cache table (both "recent" and "career"
scopes) for every player, or just those already cached.

Run this after: bumping PLAYER_VIEW_CACHE_SCHEMA_VERSION, a bulk import
(ingest_full_history, the map-diversity crawl), or any manual DB fixup that
touches match/round/kill_event/player rows -- including a player-row merge
after a Riot ID rename.

Usage:
    .venv\\Scripts\\python.exe scripts\\recompute_player_views.py
    .venv\\Scripts\\python.exe scripts\\recompute_player_views.py --cached-only
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import MatchPlayer, Player, PlayerViewCache
from app.services.player_view_cache import recompute_player_views


def _players_to_recompute(db, cached_only: bool) -> list[int]:
    if cached_only:
        query = db.query(PlayerViewCache.player_id).distinct()
    else:
        query = db.query(Player.id).join(MatchPlayer, MatchPlayer.player_id == Player.id).distinct()
    return sorted(pid for (pid,) in query.all())


def main(cached_only: bool) -> None:
    db = SessionLocal()
    try:
        player_ids = _players_to_recompute(db, cached_only)
        if not player_ids:
            print("No players to recompute")
            return

        for i, player_id in enumerate(player_ids):
            print(f"[{i + 1}/{len(player_ids)}] recomputing player {player_id}...")
            try:
                recompute_player_views(db, player_id)
            except Exception as e:
                db.rollback()
                print(f"  error recomputing player {player_id}, skipping: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cached-only", action="store_true", help="only recompute players who already have cache rows"
    )
    args = parser.parse_args()
    main(args.cached_only)
