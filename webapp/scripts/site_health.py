"""List the stored site health rows (`site_events`), one line each: kind, key, count, first seen, last seen, the
error's first line. Sorted by kind, then last seen (newest first).

    python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/site_health.py list [--kind K]

On the friends DB, run it through with_friends_db.py as above. The script never writes. Rows clear on a later
success of the same job, or through a PR entry in app/data/site_health_resolved.json.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from sqlalchemy import select  # noqa: E402

from app.models.site_events import SiteEvent  # noqa: E402


def _when(d):
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else "-"


def list_rows(session_factory, kind=None) -> int:
    with session_factory() as session:
        query = select(SiteEvent).order_by(SiteEvent.kind, SiteEvent.last_seen.desc())
        if kind:
            query = query.where(SiteEvent.kind == kind)
        for r in session.scalars(query):
            first_line = (r.error or "").splitlines()[0] if (r.error or "").strip() else ""
            print(f"{r.kind}\t{r.key}\t{r.count}\t{_when(r.first_seen)}\t{_when(r.last_seen)}\t{first_line}")
    return 0


def main(argv=None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description="Read the stored site health rows.")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="print every stored row")
    listing.add_argument("--kind", help="only this kind")
    args = parser.parse_args(argv)
    from app.config import settings

    if settings.demo_mode:
        print("site_health: the demo never records site health; refusing to read its database", file=sys.stderr)
        return 2
    if session_factory is None:
        from app.db import SessionLocal as session_factory
    return list_rows(session_factory, args.kind)


if __name__ == "__main__":
    sys.exit(main())
