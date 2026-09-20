"""
Batch refresh: walks scripts/tracked_players.json and pulls each player's
`count` most recent matches, same as running ingest_trackergg_player.py once
per Riot ID -- except it reuses a single Chrome/CDP connection across the
whole roster and keeps going if one player's profile is private or otherwise
fails, instead of stopping the batch.

Discovery pages through tracker.gg's "Load More" control over the All-Acts
history view, so --count above 20 reaches past the one server-rendered batch.
The run ends with a per-player REACHED/REQUESTED table naming why each player
stopped where they did, and exits non-zero if any player ended INCOMPLETE --
a roster that quietly returns 20 apiece can no longer read as success.

Requires scripts/launch_trackergg_chrome.ps1 to already be running.

Before any new ingestion, also checks the whole DB for a match that
previously committed but never got scored (a stranded match left behind by a
prior run that was killed/crashed mid-ingest) and backfills it -- see
backfill_unscored_matches in app.adapters.trackergg_browserstate_source.

After the whole roster is refreshed, every player whose player_view_cache rows
were invalidated by ANY ingested match gets a full career recompute (a
pre-warm), once each -- not once per match, which would be quadratic over a
12-player x 20-match refresh. That's still roughly (number of players with
new matches) x several seconds. The site-wide "All Players" stats cache
(app.services.site_stats_cache) is refreshed once too, regardless of
--no-prewarm (it's a single cheap call, not a per-player fan-out). Pass
--no-prewarm to skip the per-player pre-warm and let that cache repopulate
lazily as pages are visited.

Usage:
    .venv\\Scripts\\python.exe scripts\\refresh_tracked_players.py --count 20
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.adapters.trackergg_browserstate_source import (
    DiscoveryResult,
    DiscoveryStatus,
    IngestLedger,
    IngestOutcome,
    backfill_unscored_matches,
    ingest_paginated_history,
)
from app.db import SessionLocal
from app.services.player_view_cache import prewarm_player_cache
from app.services.site_stats import refresh_site_stats

CDP_URL = "http://localhost:9222"
ROSTER_PATH = Path(__file__).resolve().parent / "tracked_players.json"
# Gitignored -- ledgers name real match IDs and are run artifacts, not source.
LEDGER_DIR = Path(__file__).resolve().parents[1] / "ingest_ledgers"
MIN_PLAYER_DELAY_SECONDS = 5
MAX_PLAYER_DELAY_SECONDS = 12
# The run completed but fell short of --count for at least one player. Distinct
# from 1 (a crash) so callers can tell the two apart.
EXIT_INCOMPLETE = 2


def _print_roster_report(outcomes: list[IngestOutcome], count: int) -> None:
    """Discovered vs requested AND ingested vs attempted, per player.

    Two columns rather than one, deliberately. They are different questions
    and they fail independently: a player can be fully discovered and only
    partly ingested (a dropped connection), or fully ingested and only partly
    discovered (a cap). Reporting one number for both is what printed `0/200`
    for a player who had in fact been discovered 200/200 and ingested 28."""
    print("\n" + "=" * 78)
    print(f"ROSTER REPORT -- {count} requested per player")
    print("=" * 78)
    width = max((len(o.riot_id) for o in outcomes), default=0)
    print(f"  {'player':<{width}}  {'discovered':>10}  {'status':<11} {'ingested':>9}")
    for o in outcomes:
        d = o.discovery
        ing = f"{o.ingested}/{o.attempted}" if o.attempted else "-"
        flag = ""
        if not d.is_conclusive:
            flag = "   <- FLOOR, not their history"
        elif o.error:
            flag = "   <- GAVE UP MID-INGEST"
        elif o.failed_ids:
            flag = f"   <- {len(o.failed_ids)} match(es) SKIPPED"
        print(f"  {o.riot_id:<{width}}  {d.reached:>4}/{d.requested:<4}  "
              f"{d.status.value:<11} {ing:>9}{flag}")

    total_ingested = sum(o.ingested for o in outcomes)
    total_skipped = sum(len(o.failed_ids) for o in outcomes)
    print(f"\n  {total_ingested} match(es) added this run")
    if total_skipped:
        print(f"  {total_skipped} match(es) skipped after retries")

    failed = [o for o in outcomes if o.failed_ids or o.error]
    if failed:
        print(f"\n  {len(failed)} player(s) with match-level failures "
              f"(their discovery figure still stands):")
        for o in failed:
            gave_up = " -- GAVE UP, rest of their list untouched" if o.error else ""
            print(f"    {o.riot_id}: added {o.ingested}/{o.attempted}, "
                  f"{len(o.failed_ids)} skipped{gave_up}")
            for match_id in o.failed_ids[:5]:
                print(f"      skipped {match_id}")
            if len(o.failed_ids) > 5:
                print(f"      ... and {len(o.failed_ids) - 5} more (see the ledger)")
            if o.error:
                print(f"      {o.error}")
        print("    Re-run to pick these up -- ingestion dedupes on external_id.")

    incomplete = [o for o in outcomes
                  if o.discovery.status is DiscoveryStatus.INCOMPLETE]
    if incomplete:
        print(f"\n  {len(incomplete)} player(s) whose DISCOVERY was INCOMPLETE:")
        for o in incomplete:
            print(f"    {o.riot_id}: {o.discovery.reason}")
    empty = [o for o in outcomes
             if o.discovery.status in (DiscoveryStatus.NO_HISTORY, DiscoveryStatus.PRIVATE)]
    if empty:
        print(f"\n  {len(empty)} player(s) returned nothing, by named cause:")
        for o in empty:
            print(f"    {o.riot_id}: {o.discovery.status.value} -- {o.discovery.reason}")
    print("=" * 78)


def _default_ledger_path() -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    return LEDGER_DIR / f"ingest-{stamp}.jsonl"


def main(count: int, no_prewarm: bool, ledger_path: Path) -> int:
    roster = json.loads(ROSTER_PATH.read_text())
    results: list[IngestOutcome] = []
    ledger = IngestLedger(ledger_path, run_label=f"refresh_tracked_players --count {count}")
    print(f"ledger: {ledger.path}")
    db = SessionLocal()
    try:
        all_dirty: set[int] = backfill_unscored_matches(db)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()

            for i, riot_id in enumerate(roster):
                try:
                    dirty, outcome = ingest_paginated_history(
                        db, page, riot_id, count, ledger
                    )
                    all_dirty |= dirty
                    results.append(outcome)
                except Exception as e:
                    # Last-resort net. ingest_paginated_history already reports
                    # discovery and ingest failures without raising, so this
                    # only catches something unforeseen -- and unlike the
                    # version that printed `0/200` over a real 200/200, it no
                    # longer pretends to know what discovery found.
                    print(f"  unexpected error for {riot_id}, skipping: {e}")
                    results.append(
                        IngestOutcome(
                            discovery=DiscoveryResult(
                                riot_id=riot_id,
                                requested=count,
                                status=DiscoveryStatus.INCOMPLETE,
                                reason=f"{type(e).__name__}: {e}",
                            ),
                            error=f"{type(e).__name__}: {e}",
                        )
                    )

                if i < len(roster) - 1:
                    delay = random.uniform(MIN_PLAYER_DELAY_SECONDS, MAX_PLAYER_DELAY_SECONDS)
                    print(f"waiting {delay:.1f}s before next player...")
                    time.sleep(delay)

            page.close()

        if all_dirty and not no_prewarm:
            print(f"pre-warming cache for {len(all_dirty)} player(s)...")
            prewarm_player_cache(db, all_dirty)

        if all_dirty:
            print("refreshing site stats cache...")
            refresh_site_stats(db)
    finally:
        ledger.record_end(f"{len(results)} player(s) processed")
        db.close()

    _print_roster_report(results, count)
    print(f"\nLEDGER: {ledger.count} match(es) added this run")
    print(f"  {ledger.path}")
    if ledger.count:
        print("  back out the whole run with:")
        print(f"    python scripts\\rollback_ingest_ledger.py \"{ledger.path}\"")
    # Exit 2 (not 1) if ANY player fell short -- whether discovery was
    # INCOMPLETE or ingestion was cut short partway. The run itself worked, but
    # it did not reach what it was asked for, and that must not read as success
    # to a shell caller. 1 stays reserved for an actual crash, so
    # refresh_remote.ps1 can tell "fell short" from "blew up".
    return EXIT_INCOMPLETE if any(not o.ok for o in results) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=20, help="how many recent matches to reach per player"
    )
    parser.add_argument(
        "--no-prewarm", action="store_true", help="skip the post-refresh cache pre-warm (see module docstring)"
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="where to record the matches this run adds (default: "
             "webapp/ingest_ledgers/ingest-<timestamp>.jsonl). Feed it to "
             "scripts/rollback_ingest_ledger.py to back the run out.",
    )
    args = parser.parse_args()
    sys.exit(main(args.count, args.no_prewarm, args.ledger or _default_ledger_path()))
