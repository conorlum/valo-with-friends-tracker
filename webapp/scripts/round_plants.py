"""Owner maintenance: status, sync-replays [--dry-run], backfill [--limit N]."""
import argparse
from contextlib import contextmanager
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Round, RoundPlant
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


def _progress(done, left):
    print(f"done: {done}; left: {left}; estimated pacing time: {left * 5}-{left * 12} s + page loads")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    sync = commands.add_parser("sync-replays")
    sync.add_argument("--dry-run", action="store_true")
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--limit", type=int)
    backfill.add_argument("--retry-unavailable", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "backfill" and args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    with SessionLocal() as db:
        if args.command == "status":
            unavailable = plants.load_unavailable()
            stored = db.scalar(select(func.count()).select_from(RoundPlant))
            planted = db.scalar(select(func.count()).select_from(Round).where(Round.planted.is_(True)))
            print(f"stored plants: {stored}; planted rounds: {planted}; skip-list: {len(unavailable)}")
            print(f"pending matches: {len(plants.pending_matches(db, skip=unavailable))}")
            return 0
        if args.command == "sync-replays":
            result = plants.sync_replays(db, dry_run=args.dry_run)
            print(("dry-run: " if args.dry_run else "") + "; ".join(
                f"{key}: {value}" for key, value in result.items()))
            return 0
        pending = plants.pending_matches(db, args.limit,
                                         skip=() if args.retry_unavailable else plants.load_unavailable())
        _progress(0, len(pending))
        if not pending:
            return 0
        with tracker_page() as page:
            result = plants.backfill(db, page, limit=args.limit, fetch=fetch_match_json,
                                    sleep=time.sleep, uniform=random.uniform,
                                    retry_unavailable=args.retry_unavailable)
        _progress(result["done"], result["left"])
        print(f"unavailable: {result['unavailable']}; failed: {result['failed']}")
        if result["stopped"]:
            print(f"stopped: {result['stopped']}")
            resume = [r".\.venv313\Scripts\python.exe", r"scripts\with_friends_db.py",
                      "--expect-database", "valowithfriendsdb", r"scripts\round_plants.py", "backfill"]
            if args.limit is not None:
                resume += ["--limit", str(args.limit)]
            if args.retry_unavailable:
                resume.append("--retry-unavailable")
            print("Run the resume command from webapp.")
            print("Resume: " + " ".join(resume))
            return 2
        return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
