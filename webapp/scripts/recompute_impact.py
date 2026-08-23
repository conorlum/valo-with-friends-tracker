import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import Match
from app.scoring.impact import compute_impact_for_match
from app.services.player_view_cache import invalidate_all_player_caches


def main() -> None:
    db = SessionLocal()
    try:
        matches = db.query(Match).all()
        if not matches:
            print("No matches found in the database")
            sys.exit(1)

        # Invalidate BEFORE recomputing (not after, and not per-match): this
        # recomputes EVERY match's ImpactScore rows, which player_view_cache's
        # profile/econ aggregates are now derived from (Step 3a/3b,
        # docs/player_page_render_speed.txt) -- invalidating first means a
        # mid-run failure leaves the cache EMPTY (falls back to a live
        # recompute) rather than serving stale numbers from before this run
        # started. Every player's cache is affected here (every match is
        # being rescored), so this deletes the whole table rather than
        # discovering which players have rows match-by-match.
        invalidate_all_player_caches(db)
        db.commit()

        for match in matches:
            compute_impact_for_match(db, match.id)
            print(f"Recomputed {match.external_id}")
        print("Cleared player_view_cache -- run scripts/recompute_player_views.py to repopulate it.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
