import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import ImpactScore, Match, MatchPlayer, Player, Round

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "impact_snapshot.json"


def build_snapshot(db) -> list[dict]:
    rows = (
        db.query(ImpactScore, Round, MatchPlayer, Match, Player)
        .join(Round, ImpactScore.round_id == Round.id)
        .join(MatchPlayer, ImpactScore.match_player_id == MatchPlayer.id)
        .join(Match, Round.match_id == Match.id)
        .join(Player, MatchPlayer.player_id == Player.id)
        .all()
    )

    records = []
    for impact_score, round_row, match_player, match, player in rows:
        records.append(
            {
                "match_external_id": match.external_id,
                "round_number": round_row.round_number,
                "round_id": impact_score.round_id,
                "match_player_id": impact_score.match_player_id,
                "player_name": player.display_name,
                "team": match_player.team.value,
                "kill_impact": impact_score.kill_impact,
                "death_impact": impact_score.death_impact,
                "impact": impact_score.impact,
                "breakdown": impact_score.breakdown,
            }
        )
    return records


def main() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_OUT

    db = SessionLocal()
    try:
        records = build_snapshot(db)
    finally:
        db.close()

    if not records:
        print("No impact_scores found in the database")
        sys.exit(1)

    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"Snapshotted {len(records)} impact_scores rows to {out_path}")


if __name__ == "__main__":
    main()
