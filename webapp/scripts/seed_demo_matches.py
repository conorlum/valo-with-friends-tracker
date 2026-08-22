import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.demo_match_source import load_match
from app.db import SessionLocal
from app.models import Match, MatchPlayer
from app.scoring.impact import compute_impact_for_match
from app.services.player_view_cache import filter_cached_player_ids, invalidate_player_cache

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MATCH_JSONS_DIR = _REPO_ROOT / "MatchHTMLJsons"


def main() -> None:
    filenames = sorted(p.stem for p in _MATCH_JSONS_DIR.glob("*.json"))
    if not filenames:
        print(f"No match JSONs found under {_MATCH_JSONS_DIR}")
        sys.exit(1)

    db = SessionLocal()
    try:
        for filename in filenames:
            # demo_match_source.load_match deletes-and-recreates an existing
            # match's children and commits once at the end, so the OLD roster
            # must be captured BEFORE the call.
            old_ids: set[int] = set()
            existing = db.query(Match).filter_by(external_id=filename).one_or_none()
            if existing is not None:
                old_ids = {
                    pid for (pid,) in db.query(MatchPlayer.player_id).filter_by(match_id=existing.id).all()
                }

            match = load_match(db, filename)                # commits internally
            new_ids = {
                pid for (pid,) in db.query(MatchPlayer.player_id).filter_by(match_id=match.id).all()
            }

            # Invalidate BETWEEN load_match's commit and impact scoring, not
            # after both -- see ingest_demo_match.py for the ordering rationale.
            affected = filter_cached_player_ids(db, old_ids | new_ids)
            if affected:
                invalidate_player_cache(db, affected)
                db.commit()

            compute_impact_for_match(db, match.id)          # commits internally
            print(f"Seeded {filename}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
