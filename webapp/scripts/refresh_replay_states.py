"""Runs the match-state refresh (app/services/replay_match_states.py) once against DATABASE_URL and prints its counts.

For local use and the migration check. The site refreshes by itself; nothing needs this after a deploy.

    .venv\\Scripts\\python.exe scripts\\refresh_replay_states.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.services.replay_match_states import refresh


def main() -> None:
    db = SessionLocal()
    try:
        print(refresh(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
