"""The Competitive map pool's history: look at it, run the patch-notes check once, or reload the committed seed.

    .\\.venv313\\Scripts\\python.exe scripts\\map_pool.py show [--at 2026-01-01T00:00:00+00:00]
    .\\.venv313\\Scripts\\python.exe scripts\\map_pool.py refresh
    .\\.venv313\\Scripts\\python.exe scripts\\map_pool.py seed

`show` prints the pool at a time (default now) and when it last changed. `refresh` runs the daily patch-notes
check once, whatever MAP_POOL_AUTO says. `seed` replaces the database's source='seed' rows of map_pool_changes
with the committed seed (app/data/map_pool_seed.json) in one transaction; fetched rows stay.
`refresh` and `seed` write, and refuse in demo mode.

Exits 0 when done, 2 when refused.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.config import settings  # noqa: E402

REFUSED = "Refused: demo mode is on, and this command writes the map pool."


def _parse_at(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def cmd_show(db, at: str | None) -> int:
    from app.services.map_pool import load_pool_history

    history = load_pool_history(db)
    t = _parse_at(at)
    print(f"Pool at {t.isoformat()}:")
    for name in sorted(history.pool_at(t)):
        print(f"  {name}")
    latest = history.latest_change_at()
    print(f"latest change: {latest.isoformat() if latest else 'none'}")
    return 0


def cmd_refresh(session_factory, fetch) -> int:
    if settings.demo_mode:
        print(REFUSED)
        return 2
    from app.services import map_pool_refresh

    counts = map_pool_refresh.check(session_factory, fetch)
    print("refresh:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "done")
    return 0


def cmd_seed(session_factory, database: str | None) -> int:
    if settings.demo_mode:
        print(REFUSED)
        return 2
    from sqlalchemy import delete

    from app.models.map_pool import MapPoolChange
    from app.services import map_pool
    from app.services.map_pool_refresh import _insert

    changes = map_pool.seed_history().changes
    print(f"Writing {len(changes)} seed rows to database: {database}")
    with session_factory() as db:
        with db.begin():
            db.execute(delete(MapPoolChange).where(MapPoolChange.source == "seed"))
            insert = _insert(db.get_bind())
            for c in changes:
                # A change the refresh already fetched keeps its fetched row.
                db.execute(insert(MapPoolChange).values(map_name=c.map_name, in_pool=c.in_pool,
                                                        effective_at=c.effective_at, patch=c.patch,
                                                        source="seed").on_conflict_do_nothing())
    map_pool.clear_pool_cache()
    print("seed rows replaced")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_show = sub.add_parser("show", help="print the pool at a time")
    p_show.add_argument("--at", help="ISO time (default now)")
    sub.add_parser("refresh", help="run the patch-notes check once")
    sub.add_parser("seed", help="replace the seed rows with the committed seed")
    args = parser.parse_args(argv)

    from app.db import SessionLocal, engine

    if args.command == "show":
        with SessionLocal() as db:
            return cmd_show(db, args.at)
    if args.command == "refresh":
        from app.services.map_pool_refresh import http_fetch

        return cmd_refresh(SessionLocal, http_fetch)
    return cmd_seed(SessionLocal, engine.url.database)


if __name__ == "__main__":
    sys.exit(main())
