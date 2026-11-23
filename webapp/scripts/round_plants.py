"""Owner maintenance: status, sync-replays [--dry-run], backfill [--limit N] [--archive DIR], pause.

Pausing a backfill: press Ctrl+C once (the current match finishes, then the run stops
and prints its resume command; a second Ctrl+C stops at once, still losing nothing
already committed), or run `round_plants.py pause` from any shell, which stops the run
before its next match. Starting a backfill removes a leftover pause file.
"""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import random
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Match, Round, RoundPlant
from app.services import round_plants as plants


def fetch_match_json(page, external_id):
    # Keep status and replay sync independent of the browser adapter.
    from app.adapters.trackergg_browserstate_source import fetch_match_json as fetch
    return fetch(page, external_id)


@contextmanager
def tracker_page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            yield page
        finally:
            page.close()


@contextmanager
def owner_stop():
    """Yield a ``should_stop`` for ``backfill``: the pause file or a first Ctrl+C.

    The first Ctrl+C only records the request, so the match in flight can finish
    and commit; the second restores Python's default and interrupts at once.
    """
    interrupted = []

    def on_interrupt(signum, frame):
        if interrupted:
            raise KeyboardInterrupt
        interrupted.append(True)
        print("\nPAUSING: finishing this match, then stopping (Ctrl+C again to stop now).", flush=True)

    previous = signal.signal(signal.SIGINT, on_interrupt)
    try:
        yield lambda: "paused" if interrupted or plants.pause_requested() else None
    finally:
        signal.signal(signal.SIGINT, previous)


WEBAPP = Path(__file__).resolve().parents[1]


def resume_python():
    """The `.venv313` interpreter, relative to webapp: a worktree has none of its own,
    and `with_friends_db.py` then uses the main checkout's."""
    from scripts.with_friends_db import find_ingest_python

    path = Path(os.path.relpath(find_ingest_python(WEBAPP), WEBAPP))
    text = str(path).replace("/", "\\")
    return text if text.startswith("..") else ".\\" + text


def _progress(done, left):
    print(f"done: {done}; left: {left}; estimated pacing time: {left * 5}-{left * 12} s + page loads")


def _print_coverage(db):
    """Per map: planted rounds, stored plants by source, and the share of planted rounds covered."""
    planted = {name: (rounds, covered) for name, rounds, covered in db.execute(
        select(Match.map_name, func.count(Round.id), func.count(RoundPlant.round_id))
        .select_from(Round).join(Match, Match.id == Round.match_id)
        .outerjoin(RoundPlant, RoundPlant.round_id == Round.id)
        .where(Round.planted.is_(True)).group_by(Match.map_name))}
    stored = {}
    for name, source, count in db.execute(
            select(Match.map_name, RoundPlant.source, func.count(RoundPlant.round_id))
            .select_from(RoundPlant).join(Match, Match.id == RoundPlant.match_id)
            .group_by(Match.map_name, RoundPlant.source)):
        stored.setdefault(name, {})[source] = count
    for name in sorted(set(planted) | set(stored), key=str):
        rounds, covered = planted.get(name, (0, 0))
        by_source = stored.get(name, {})
        coverage = f"{100 * covered / rounds:.1f}%" if rounds else "n/a"
        print(f"{name}: planted rounds {rounds}; stored plants {sum(by_source.values())} "
              f"(replay {by_source.get('replay', 0)}, tracker {by_source.get('tracker', 0)}); "
              f"coverage {coverage}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    sync = commands.add_parser("sync-replays")
    sync.add_argument("--dry-run", action="store_true")
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--limit", type=int)
    backfill.add_argument("--retry-unavailable", action="store_true")
    backfill.add_argument("--archive", type=Path,
                          help="also save each match's raw tracker.gg payload here as <id>.json.gz")
    commands.add_parser("pause", help="stop a running backfill before its next match")
    args = parser.parse_args(argv)
    if args.command == "backfill" and args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.command == "pause":
        plants.request_pause()
        print(f"pause requested ({plants.PAUSE_PATH}); the backfill stops before its next match")
        return 0
    with SessionLocal() as db:
        if args.command == "status":
            unavailable = plants.load_unavailable()
            stored = db.scalar(select(func.count()).select_from(RoundPlant))
            planted = db.scalar(select(func.count()).select_from(Round).where(Round.planted.is_(True)))
            print(f"stored plants: {stored}; planted rounds: {planted}; skip-list: {len(unavailable)}")
            print(f"pending matches: {len(plants.pending_matches(db, skip=unavailable))}")
            _print_coverage(db)
            return 0
        if args.command == "sync-replays":
            result = plants.sync_replays(db, dry_run=args.dry_run)
            print(("dry-run: " if args.dry_run else "") + "; ".join(
                f"{key}: {value}" for key, value in result.items()))
            return 0
        if plants.clear_pause():
            print("removed a leftover pause file")
        pending = plants.pending_matches(db, args.limit,
                                         skip=() if args.retry_unavailable else plants.load_unavailable())
        _progress(0, len(pending))
        if not pending:
            return 0
        with tracker_page() as page, owner_stop() as should_stop:
            result = plants.backfill(db, page, limit=args.limit, fetch=fetch_match_json,
                                    sleep=time.sleep, uniform=random.uniform,
                                    retry_unavailable=args.retry_unavailable,
                                    should_stop=should_stop, archive=args.archive)
        _progress(result["done"], result["left"])
        print(f"stored this run: inserted {result['inserted']}, updated {result['updated']}, "
              f"skipped {result['skipped']}")
        print(f"unavailable: {result['unavailable']}; failed: {result['failed']}")
        if result["stopped"]:
            print(f"stopped: {result['stopped']}")
            resume = [resume_python(), r"scripts\with_friends_db.py",
                      "--expect-database", "valowithfriendsdb", r"scripts\round_plants.py", "backfill"]
            if args.limit is not None:
                resume += ["--limit", str(args.limit)]
            if args.retry_unavailable:
                resume.append("--retry-unavailable")
            if args.archive is not None:
                resume += ["--archive", f'"{args.archive}"']
            print("Run the resume command from webapp.")
            print("Resume: " + " ".join(resume))
            # A stop the owner asked for is not a failure.
            if result["stopped"] in ("paused", "interrupted"):
                plants.clear_pause()
                return 1 if result["failed"] else 0
            return 2
        return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
