"""Loads seed_data/demo_matches.sql, the public ValoMaths demo's sample data.

The seed is a data-only dump taken at the schema head (see dump_seed_data.py),
so a rebuild of the demo database is, all through with_demo_db.py:

    python scripts/with_demo_db.py -m alembic upgrade head
    python scripts/with_demo_db.py scripts/load_seed_data.py
    python scripts/with_demo_db.py scripts/recompute_player_views.py

The site-stats cache fills itself on the first /stats visit.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import engine

_SEED_FILE = Path(__file__).resolve().parents[1] / "seed_data" / "demo_matches.sql"


def main() -> None:
    sql = _SEED_FILE.read_text(encoding="utf-8-sig")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    print(f"Loaded seed data from {_SEED_FILE.name} -- now run scripts/recompute_player_views.py")


if __name__ == "__main__":
    main()
