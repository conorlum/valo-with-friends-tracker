"""Stores one coaching report from a JSON file (the payload app/services/coach.py validate_payload accepts).

Local database (reads .env):
    $PY scripts/coach_store_report.py <match uuid> "<player>" <report.json>
Friends database (PUBLISHES the report on the live site):
    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_store_report.py <match uuid> "<player>" <report.json>

Each run adds the next version for that match and player; nothing is overwritten. Exits 2 when refused."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def resolve(db, match_uuid: str, player: str) -> tuple[int, int]:
    from app.models import MatchPlayer, Player
    from app.models.replay import Replay

    replay = (db.query(Replay).filter(Replay.match_uuid == match_uuid.lower(), Replay.link_status == "linked",
                                      Replay.match_id.isnot(None)).one_or_none())
    if replay is None:
        raise ValueError(f"no linked replay for {match_uuid}")
    rows = (db.query(Player.id, Player.display_name).join(MatchPlayer, MatchPlayer.player_id == Player.id)
            .filter(MatchPlayer.match_id == replay.match_id).all())
    wanted = player.strip().lower()
    found = [pid for pid, name in rows if name.lower() == wanted]
    if len(found) != 1:
        raise ValueError(f"{player!r} is not a player of this match; players: " + ", ".join(sorted(n for _, n in rows)))
    return replay.match_id, found[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("match_uuid")
    parser.add_argument("player")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.services import coach

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        try:
            match_id, player_id = resolve(db, args.match_uuid, args.player)
            report = coach.store_report(db, match_id, player_id, payload)
        except (ValueError, coach.ReportError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return 2
        print(f"stored version {report.version}: /replays/{args.match_uuid.lower()}/coach?player={player_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
