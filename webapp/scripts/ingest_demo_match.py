import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.demo_match_source import load_match
from app.db import SessionLocal
from app.models import ImpactScore, Match, MatchPlayer, Player, Round
from app.scoring.impact import compute_impact_for_match
from app.services.player_view_cache import filter_cached_player_ids, invalidate_player_cache


def main(filename: str) -> None:
    db = SessionLocal()
    try:
        # demo_match_source.load_match deletes-and-recreates an existing match's
        # children and commits once at the end, so the OLD roster must be
        # captured BEFORE the call.
        old_ids: set[int] = set()
        existing = db.query(Match).filter_by(external_id=filename).one_or_none()
        if existing is not None:
            old_ids = {
                pid for (pid,) in db.query(MatchPlayer.player_id).filter_by(match_id=existing.id).all()
            }

        match = load_match(db, filename)               # commits internally
        new_ids = {
            pid for (pid,) in db.query(MatchPlayer.player_id).filter_by(match_id=match.id).all()
        }

        # Invalidate BETWEEN load_match's commit and impact scoring, not after
        # both (same ordering rule as the tracker.gg ingestion path): if
        # compute_impact_for_match raises, invalidating first still leaves the
        # cache empty rather than permanently stale.
        affected = filter_cached_player_ids(db, old_ids | new_ids)
        if affected:
            invalidate_player_cache(db, affected)
            db.commit()

        compute_impact_for_match(db, match.id)          # commits internally

        rows = (
            db.query(Player.display_name, ImpactScore.impact)
            .join(MatchPlayer, MatchPlayer.player_id == Player.id)
            .join(ImpactScore, ImpactScore.match_player_id == MatchPlayer.id)
            .join(Round, Round.id == ImpactScore.round_id)
            .filter(Round.match_id == match.id)
            .all()
        )

        totals: dict[str, list[float]] = {}
        for display_name, impact in rows:
            totals.setdefault(display_name, []).append(impact)

        averages = {name: sum(values) / len(values) for name, values in totals.items()}
        for name, avg in sorted(averages.items(), key=lambda item: item[1], reverse=True):
            print(f"{name}: {round(avg)}")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/ingest_demo_match.py <filename>")
        sys.exit(1)
    main(sys.argv[1])
