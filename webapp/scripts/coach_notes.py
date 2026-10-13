"""The coach's owner-written notes that stay out of the public repository (voice samples).

    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_notes.py add voice <file.txt>
    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/coach_notes.py list voice
    $PY scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/coach_notes.py off <id>

`add` stores each non-empty line of the file as one note. Keep the file outside the repository."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("kind")
    add.add_argument("file", type=Path)
    sub.add_parser("list").add_argument("kind")
    sub.add_parser("off").add_argument("id", type=int)
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.models.coach import CoachNote
    from app.services import coach

    with SessionLocal() as db:
        if args.command == "add":
            lines = [line.strip() for line in args.file.read_text(encoding="utf-8").splitlines() if line.strip()]
            for line in lines:
                coach.add_note(db, args.kind, line)
            print(f"added {len(lines)} {args.kind} notes")
        elif args.command == "list":
            for note in db.query(CoachNote).filter(CoachNote.kind == args.kind).order_by(CoachNote.id):
                print(f"{note.id:>4} {'on ' if note.active else 'off'} {note.body}")
        else:
            print("switched off" if coach.set_note_active(db, args.id, False) else "no such note")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
