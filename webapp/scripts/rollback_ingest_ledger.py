"""
Backs a single ingest run out of the database, using the JSONL ledger that
run wrote (see IngestLedger in app/adapters/trackergg_browserstate_source.py).

DRY RUN BY DEFAULT. Without --execute this only reports what it would delete;
nothing is written. Deleting match data is irreversible, so the destructive
path has to be asked for explicitly.

Deletion order matters and is not handled by the ORM alone: impact_scores
references rounds.id and match_players.id with plain foreign keys and no
cascade, so deleting a match without clearing its scores first raises a
foreign-key violation. The unwind is therefore, per match:

    impact_scores  ->  kill_events  ->  round_player_spend
                   ->  round_player_stats
                   ->  rounds  ->  match_players  ->  matches

(round_player_spend has ondelete=CASCADE from round_player_stats, but it is
deleted explicitly anyway so the row counts reported are real rather than
implied.)

Only matches whose external_id the ledger recorded are touched, and each one
is re-checked against the ledger's own match_id before deletion -- if a row
with that external_id no longer has the id the ledger recorded, it is skipped
and reported rather than guessed at.

Every player whose cached views could have included a deleted match is
invalidated, and the site-wide stats cache is dropped, so nothing is left
pointing at rows that no longer exist.

Usage:
    .venv313\\Scripts\\python.exe scripts\\rollback_ingest_ledger.py <ledger.jsonl>
    .venv313\\Scripts\\python.exe scripts\\rollback_ingest_ledger.py <ledger.jsonl> --execute
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db import SessionLocal
from app.models import Match
from app.services.player_view_cache import invalidate_player_cache
from app.services.site_stats_cache import invalidate_site_stats_cache


def read_ledger(path: Path) -> list[dict]:
    """The `ingested` records of a ledger, in the order they were written."""
    entries = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # A run killed mid-write can leave one torn final line. Report it
            # rather than dropping it silently -- it may name a real match.
            print(f"  WARNING: line {line_no} is not valid JSON, skipping: {line[:120]}")
            continue
        if record.get("event") == "ingested":
            entries.append(record)
    return entries


def _delete_counts(db, match_id: int) -> dict[str, int]:
    """How many rows hang off this match, per table, without deleting them."""
    q = lambda sql: db.execute(text(sql), {"mid": match_id}).scalar() or 0  # noqa: E731
    return {
        "impact_scores": q(
            "SELECT count(*) FROM impact_scores WHERE round_id IN "
            "(SELECT id FROM rounds WHERE match_id = :mid)"
        ),
        "kill_events": q(
            "SELECT count(*) FROM kill_events WHERE round_id IN "
            "(SELECT id FROM rounds WHERE match_id = :mid)"
        ),
        "round_player_spend": q(
            "SELECT count(*) FROM round_player_spend WHERE round_player_stat_id IN "
            "(SELECT rps.id FROM round_player_stats rps "
            " JOIN rounds r ON r.id = rps.round_id WHERE r.match_id = :mid)"
        ),
        "round_player_stats": q(
            "SELECT count(*) FROM round_player_stats WHERE round_id IN "
            "(SELECT id FROM rounds WHERE match_id = :mid)"
        ),
        "rounds": q("SELECT count(*) FROM rounds WHERE match_id = :mid"),
        "match_players": q("SELECT count(*) FROM match_players WHERE match_id = :mid"),
    }


def _delete_match(db, match_id: int) -> None:
    """Unwinds one match, children first. See the module docstring for why the
    order is explicit rather than left to the ORM's cascades."""
    p = {"mid": match_id}
    db.execute(text(
        "DELETE FROM impact_scores WHERE round_id IN "
        "(SELECT id FROM rounds WHERE match_id = :mid)"), p)
    db.execute(text(
        "DELETE FROM kill_events WHERE round_id IN "
        "(SELECT id FROM rounds WHERE match_id = :mid)"), p)
    db.execute(text(
        "DELETE FROM round_player_spend WHERE round_player_stat_id IN "
        "(SELECT rps.id FROM round_player_stats rps "
        " JOIN rounds r ON r.id = rps.round_id WHERE r.match_id = :mid)"), p)
    db.execute(text(
        "DELETE FROM round_player_stats WHERE round_id IN "
        "(SELECT id FROM rounds WHERE match_id = :mid)"), p)
    db.execute(text("DELETE FROM rounds WHERE match_id = :mid"), p)
    # impact_scores also references match_players.id, and every such row was
    # removed by the first delete above (they are keyed by round as well).
    db.execute(text("DELETE FROM match_players WHERE match_id = :mid"), p)
    db.execute(text("DELETE FROM matches WHERE id = :mid"), p)


def main(ledger_path: Path, execute: bool) -> int:
    entries = read_ledger(ledger_path)
    print(f"ledger: {ledger_path}")
    print(f"  {len(entries)} match(es) recorded as ingested by that run\n")
    if not entries:
        print("Nothing to back out.")
        return 0

    db = SessionLocal()
    try:
        present, missing, mismatched = [], [], []
        for e in entries:
            match = db.query(Match).filter_by(external_id=e["external_id"]).one_or_none()
            if match is None:
                missing.append(e)
            elif match.id != e["match_id"]:
                mismatched.append((e, match.id))
            else:
                present.append(e)

        totals: dict[str, int] = {}
        player_ids: set[int] = set()
        for e in present:
            for table, n in _delete_counts(db, e["match_id"]).items():
                totals[table] = totals.get(table, 0) + n
            for (pid,) in db.execute(
                text("SELECT DISTINCT player_id FROM match_players WHERE match_id = :mid"),
                {"mid": e["match_id"]},
            ):
                player_ids.add(pid)

        print(f"  {len(present)} still present and will be deleted")
        if missing:
            print(f"  {len(missing)} already gone (someone deleted them since the run)")
        if mismatched:
            print(f"  {len(mismatched)} SKIPPED -- external_id now maps to a different row:")
            for e, actual in mismatched[:10]:
                print(f"    {e['external_id']}: ledger said id={e['match_id']}, db has {actual}")

        print("\n  rows that would be deleted:")
        print(f"    {'matches':<20} {len(present):>8}")
        for table in ("match_players", "rounds", "round_player_stats",
                      "round_player_spend", "kill_events", "impact_scores"):
            print(f"    {table:<20} {totals.get(table, 0):>8}")
        print(f"\n  player_view_cache rows to invalidate for {len(player_ids)} player(s)")

        if not execute:
            print("\nDRY RUN -- nothing was deleted. Re-run with --execute to apply.")
            return 0

        print("\nDeleting...")
        for i, e in enumerate(present, 1):
            _delete_match(db, e["match_id"])
            if i % 25 == 0 or i == len(present):
                db.commit()
                print(f"  {i}/{len(present)} deleted")
        if player_ids:
            invalidate_player_cache(db, player_ids)
        invalidate_site_stats_cache(db)
        db.commit()
        print(f"\nBacked out {len(present)} match(es). Caches invalidated.")
        print("Run scripts/recompute_player_views.py to re-warm, or let pages recompute lazily.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ledger", type=Path, help="the run's JSONL ledger")
    parser.add_argument(
        "--execute", action="store_true",
        help="actually delete (default is a dry run that writes nothing)",
    )
    args = parser.parse_args()
    if not args.ledger.exists():
        sys.exit(f"ledger not found: {args.ledger}")
    sys.exit(main(args.ledger, args.execute))
