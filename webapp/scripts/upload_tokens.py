"""List, issue or revoke replay uploader keys. Run through scripts/with_friends_db.py:

    python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/upload_tokens.py list
    python scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/upload_tokens.py issue "laptop"
    python scripts/with_friends_db.py --expect-database valowithfriendsdb scripts/upload_tokens.py revoke 1

Only issue prints a key, once. Listing never prints keys or their hashes.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import upload_tokens  # noqa: E402


def _when(value):
    return value.isoformat() if value else "-"


def main(argv=None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description="Manage replay uploader keys.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list keys' metadata")
    issue = sub.add_parser("issue", help="issue a key and print it once")
    issue.add_argument("label")
    revoke = sub.add_parser("revoke", help="revoke a key by id")
    revoke.add_argument("id", type=int)
    args = parser.parse_args(argv)
    if session_factory is None:
        from app.db import SessionLocal as session_factory
    with session_factory() as db:
        if args.command == "list":
            print("id\tlabel\tcreated\tlast used\trevoked")
            for row in upload_tokens.list_tokens(db):
                print(f"{row['id']}\t{row['label']}\t{_when(row['created_at'])}\t"
                      f"{_when(row['last_used_at'])}\t{_when(row['revoked_at'])}")
        elif args.command == "issue":
            _, key = upload_tokens.mint(db, args.label)
            print(key)
        else:
            if not upload_tokens.revoke(db, args.id, datetime.now(timezone.utc)):
                print(f"No upload key with id {args.id}.", file=sys.stderr)
                return 1
            print(f"Revoked upload key {args.id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
