"""Catch-up inventory for reopening ingestion after a scoring release (process
section I, gate G7). READ-ONLY: it reads tracker.gg history pages and only
SELECTs from the database. No ingestion, no gate change, no scoring -- so it
may run while the release write gate is still closed.

Per roster player it walks the All-Acts history, most recent first, until
that player's OWN newest-ingested external_id (the boundary) appears. Every id
above the boundary is that player's expected set. It never stops at the first
already-known match: another friend's ingestion can have placed a newer match
in the database while an older one of this player's is still missing.

A boundary that is not reached is INCOMPLETE, never covered. Note that the
adapter reports a private profile as NO_HISTORY, not PRIVATE; for a player the
database already has matches for, read NO_HISTORY as private.

The boundary file is tab-separated, one row per player:
    display_name, player_id, match_count, newest_played_at, newest_external_id, max_match_id
(the per-player boundary query in docs/superpowers/impact-v4/README.md, section I).

Usage, from webapp/, with the scraper Chrome up on port 9222:
    DATABASE_URL="$PROD" .venv313/Scripts/python.exe scripts/catchup_inventory.py <boundary.tsv> <out.json> [riot_id ...]
Naming riot ids limits the run to them (probe one player first). Exits 0 when
every player is COMPLETE, 2 otherwise.
"""

import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from sqlalchemy import text

from app.adapters.trackergg_browserstate_source import (
    DiscoveryStatus,
    discover_match_ids_paginated,
)
from app.db import SessionLocal

CDP_URL = "http://localhost:9222"
ROSTER_PATH = Path(__file__).resolve().parent / "tracked_players.json"
# A second, deeper pass runs only when the boundary was not within the first.
COUNTS = (40, 120)
EXIT_INCOMPLETE = 2


def load_boundary(path: Path) -> dict:
    boundary = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, pid, n, played_at, ext_id, _max_id = line.split("\t")
        boundary[name] = {
            "player_id": int(pid),
            "matches": int(n),
            "played_at": played_at,
            "external_id": ext_id,
        }
    return boundary


def inventory_player(page, db, riot_id: str, b: dict | None) -> dict:
    rec = {"riot_id": riot_id, "boundary": b, "attempts": []}
    if b is None:
        rec.update(status="INCOMPLETE", reason="no database boundary row for this player")
        return rec
    for count in COUNTS:
        d = discover_match_ids_paginated(page, riot_id, count)
        rec["attempts"].append(
            {"count": count, "summary": d.summary(), "status": d.status.value,
             "pages": d.pages_fetched, "reached": d.reached}
        )
        ids = d.match_ids
        if b["external_id"] in ids:
            idx = ids.index(b["external_id"])
            rec.update(status="COMPLETE", reason=f"boundary found at position {idx}",
                       expected=ids[:idx], boundary_position=idx)
            break
        if d.status != DiscoveryStatus.COMPLETE:
            # PRIVATE, NO_HISTORY, EXHAUSTED without the boundary, INCOMPLETE:
            # none of them proves coverage.
            rec.update(status="INCOMPLETE",
                       reason=f"boundary not reached; discovery {d.status.value}: {d.reason}",
                       discovered=ids)
            break
        rec.update(status="INCOMPLETE", reason=f"boundary not within {count} matches",
                   discovered=ids)
        time.sleep(random.uniform(5, 12))
    if rec.get("expected") is not None:
        rows = db.execute(
            text("SELECT external_id, id FROM matches WHERE external_id = ANY(:ids)"),
            {"ids": rec["expected"]},
        ).all()
        present = {r[0]: r[1] for r in rows}
        rec["already_in_db"] = present
        rec["to_ingest"] = [x for x in rec["expected"] if x not in present]
    return rec


def main(boundary_path: Path, out_path: Path, only: set[str]) -> int:
    boundary = load_boundary(boundary_path)
    roster = json.loads(ROSTER_PATH.read_text())
    if only:
        roster = [r for r in roster if r in only]

    started = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    db = SessionLocal()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            page = browser.contexts[0].new_page()
            for i, riot_id in enumerate(roster):
                rec = inventory_player(page, db, riot_id, boundary.get(riot_id))
                results.append(rec)
                print(riot_id, rec["status"], rec["reason"],
                      "expected", len(rec.get("expected") or []),
                      "to_ingest", len(rec.get("to_ingest") or []), flush=True)
                out_path.write_text(json.dumps(
                    {"started": started, "partial": True, "results": results}, indent=1))
                if i < len(roster) - 1:
                    time.sleep(random.uniform(5, 12))
            page.close()
    finally:
        db.rollback()
        db.close()

    union = sorted({x for r in results for x in (r.get("to_ingest") or [])})
    complete = all(r["status"] == "COMPLETE" for r in results)
    out_path.write_text(json.dumps({
        "started": started,
        "finished": datetime.now(timezone.utc).isoformat(),
        "partial": False,
        "complete": complete,
        "incomplete_players": [r["riot_id"] for r in results if r["status"] != "COMPLETE"],
        "to_ingest_union": union,
        "results": results,
    }, indent=1))
    print("DONE complete=%s to_ingest_union=%d incomplete=%s"
          % (complete, len(union), [r["riot_id"] for r in results if r["status"] != "COMPLETE"]))
    return 0 if complete else EXIT_INCOMPLETE


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), set(sys.argv[3:])))
