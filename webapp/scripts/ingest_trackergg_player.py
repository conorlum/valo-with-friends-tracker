"""
One-command pipeline: given a Riot ID, find their N most recent tracker.gg
matches, skip any already in the DB (dedup by tracker.gg's own match ID, so
the same match is never double-ingested even when reached via a different
player's history), and ingest+score the rest.

Discovery pages through tracker.gg's "Load More" control across the All-Acts
history view, so --count above 20 reaches past the single server-rendered
batch (which holds exactly 20, and is scoped to the CURRENT act). Every run
prints what it REACHED against what was REQUESTED plus a named stop reason,
so "100 requested, 20 reached" can never print as plain success. Exits
non-zero when the run ends INCOMPLETE -- i.e. when the number reached is a
floor rather than the player's real history.

Requires scripts/launch_trackergg_chrome.ps1 to already be running (a real,
human-navigable Chrome tab -- this script paces itself between matches rather
than firing requests back-to-back).

Before any new ingestion, also checks the whole DB for a match that
previously committed but never got scored (a stranded match left behind by a
prior run that was killed/crashed mid-ingest) and backfills it -- see
backfill_unscored_matches in app.adapters.trackergg_browserstate_source.

After ingesting, any player whose player_view_cache rows were invalidated gets
a full career recompute (a pre-warm) so their next page load is a cache hit
instead of a live compute -- a few extra seconds per affected player. The
site-wide "All Players" stats cache (app.services.site_stats_cache) is
refreshed the same way whenever any match was ingested. Pass --no-prewarm to
skip the per-player pre-warm and let both caches repopulate lazily on next
visit -- the site-wide refresh isn't gated by --no-prewarm since it's one
cheap call, not a per-player fan-out.

Usage:
    .venv\\Scripts\\python.exe scripts\\ingest_trackergg_player.py "NPrightdolphin#NA1" --count 5
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import (
    IngestLedger,
    backfill_unscored_matches,
    ingest_paginated_history,
)
from app.db import SessionLocal
from app.services.player_view_cache import prewarm_player_cache
from app.services.site_stats import refresh_site_stats

CDP_URL = "http://localhost:9222"
# Reached fewer than --count with no end-of-history to justify it. Distinct
# from 1 (a crash), matching refresh_tracked_players.py.
EXIT_INCOMPLETE = 2
# Gitignored -- ledgers name real match IDs and are run artifacts, not source.
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ingest_ledgers"


def _default_ledger_path(riot_id: str) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    safe = "".join(c if c.isalnum() else "-" for c in riot_id)
    return LEDGER_DIR / f"ingest-{safe}-{stamp}.jsonl"


def main(riot_id: str, count: int, no_prewarm: bool, ledger_path: Path) -> int:
    ledger = IngestLedger(
        ledger_path, run_label=f"ingest_trackergg_player {riot_id} --count {count}"
    )
    print(f"ledger: {ledger.path}")
    db = SessionLocal()
    try:
        dirty = backfill_unscored_matches(db)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()
            new_dirty, outcome = ingest_paginated_history(
                db, page, riot_id, count, ledger
            )
            dirty |= new_dirty
            page.close()

        # Discovered vs requested AND ingested vs attempted, always -- a bare
        # "discovered 20" reads the same whether 20 is all they have or all we
        # could get, and a bare failure reads as though nothing was added.
        print(f"\n{outcome.summary()}")
        if not outcome.discovery.is_conclusive:
            print(
                f"  WARNING: discovered {outcome.discovery.reached}/"
                f"{outcome.discovery.requested} is a FLOOR, not their history."
            )
        if outcome.failed_ids:
            print(f"  WARNING: {len(outcome.failed_ids)} match(es) skipped after "
                  f"retries. Re-run to pick them up -- dedup means only the "
                  f"missing ones are fetched:")
            for match_id in outcome.failed_ids[:10]:
                print(f"    {match_id}")
            if len(outcome.failed_ids) > 10:
                print(f"    ... and {len(outcome.failed_ids) - 10} more (see the ledger)")
        if outcome.error:
            untouched = outcome.attempted - outcome.ingested - len(outcome.failed_ids)
            print(f"  WARNING: gave up mid-ingest -- added {outcome.ingested}, "
                  f"{untouched} never attempted.")

        if dirty and not no_prewarm:
            print(f"pre-warming cache for {len(dirty)} player(s)...")
            prewarm_player_cache(db, dirty)

        if dirty:
            print("refreshing site stats cache...")
            refresh_site_stats(db)
    finally:
        ledger.record_end(f"{riot_id}: {ledger.count} added")
        db.close()

    print(f"\nLEDGER: {ledger.count} match(es) added this run")
    print(f"  {ledger.path}")
    if ledger.count:
        print("  back this run out with:")
        print(f"    python scripts\\rollback_ingest_ledger.py \"{ledger.path}\"")

    # Non-zero when discovery was INCOMPLETE or the ingest was cut short --
    # the "we don't know what we missed" bucket. PRIVATE and NO_HISTORY are
    # definite answers, printed by name, and exit 0.
    return 0 if outcome.ok else EXIT_INCOMPLETE


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("riot_id", help="Riot ID, e.g. 'NPrightdolphin#NA1'")
    parser.add_argument(
        "--count", type=int, default=5, help="how many recent matches to reach (pages past 20)"
    )
    parser.add_argument(
        "--no-prewarm", action="store_true", help="skip the post-ingest cache pre-warm (see module docstring)"
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="where to record the matches this run adds (default: "
             "webapp/ingest_ledgers/ingest-<riot id>-<timestamp>.jsonl). Feed "
             "it to scripts/rollback_ingest_ledger.py to back the run out.",
    )
    args = parser.parse_args()
    sys.exit(
        main(
            args.riot_id,
            args.count,
            args.no_prewarm,
            args.ledger or _default_ledger_path(args.riot_id),
        )
    )
