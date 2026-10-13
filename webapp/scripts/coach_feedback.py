"""Feedback on coach reports: what is open, and marking a report's feedback handled once the owner has decided.

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/coach_feedback.py list
    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_feedback.py handled --report <id> [<id> ...]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("handled").add_argument("--report", type=int, nargs="+", required=True)
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.services import coach

    with SessionLocal() as db:
        if args.command == "list":
            rows = coach.open_feedback(db)
            for row in rows:
                print(f"report {row['report_id']:>4}  {row['open']:>2} open  {row['player']}  "
                      f"/replays/{row['match_uuid']}/coach")
            print(f"{len(rows)} reports with open feedback")
        else:
            for report_id in args.report:
                print(f"report {report_id}: marked {coach.mark_handled(db, report_id)} feedback rows handled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
