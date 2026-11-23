"""Read anonymous utility gallery examples from recent replay blobs.

Run from webapp through the friends database's read-only wrapper:

    python scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only scripts/harvest_util_samples.py --replays 120 --per-key 3

Output defaults to the temporary directory and must stay outside the repository.
Only ability names and slot numbers are retained; identity metadata is omitted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.replays.format import decode_blob  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _anonymous(value, *, ability_row=False):
    """Keep replay geometry, omitting identity fields even inside nested metadata.

    A row's top-level `name` is the ability code suffix, not a player's name.
    """
    if isinstance(value, list):
        return [_anonymous(item) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        normalized = "".join(c for c in key.lower() if c.isalnum())
        if (normalized.startswith(("player", "account", "riot", "match", "subject"))
                or normalized in {"puuid", "uuid", "tag", "tagline", "displayname", "gamename"}
                or (normalized == "name" and not ability_row)):
            continue
        out[key] = _anonymous(item)
    return out


def harvest(conn, replays=120, per_key=3) -> dict:
    """SELECT only; count every ability and prefer one sample per replay per key.

    After distinct replays, fill spare slots with the earliest remaining rows in
    traversal order. Replay IDs are used internally and never enter the result.
    """
    if replays < 0 or per_key < 0:
        raise ValueError("replays and per_key must be nonnegative")
    recent = conn.execute(text(
        "SELECT id, map_name FROM replays ORDER BY created_at DESC, id DESC LIMIT :n"
    ), {"n": replays}).mappings().all()
    keys = {}
    for replay in recent:
        rounds = conn.execute(text(
            "SELECT data FROM replay_rounds WHERE replay_id = :id ORDER BY round_number"
        ), {"id": replay["id"]}).scalars()
        for data in rounds:
            blob = decode_blob(data)
            rows = [row for row in blob.get("util", []) if row.get("k") == "ability"]
            sides = {player["slot"]: player.get("side") for player in blob.get("players", [])}
            projectiles = {}
            for row in rows:
                if row.get("kind") == "Projectile":
                    projectiles.setdefault(row.get("by"), []).append(row)
            for row in rows:
                key = f"{row.get('kind')}|{row.get('code')}_{row.get('name')}"
                entry = keys.setdefault(key, {"count": 0, "seen": set(), "first": [], "extra": []})
                entry["count"] += 1
                distinct = replay["id"] not in entry["seen"]
                entry["seen"].add(replay["id"])
                samples = entry["first"] if distinct else entry["extra"]
                if len(samples) >= per_key:
                    continue
                companions = [
                    _anonymous(other, ability_row=True)
                    for other in projectiles.get(row.get("by"), [])
                    if other is not row and abs(other["t"] - row["t"]) <= 0.5
                ]
                samples.append({
                    "map": replay["map_name"], "t_end": blob.get("t_end"),
                    "side": sides.get(row.get("by")), "row": _anonymous(row, ability_row=True),
                    "companions": companions,
                })
    return {
        "made": datetime.now(timezone.utc).isoformat(), "replays": len(recent),
        "keys": {key: {"count": entry["count"], "replays": len(entry["seen"]),
                       "samples": (entry["first"] + entry["extra"])[:per_key]}
                 for key, entry in keys.items()},
    }


def _nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replays", type=_nonnegative, default=120)
    parser.add_argument("--per-key", type=_nonnegative, default=3)
    parser.add_argument("--out", type=Path,
                        default=Path(tempfile.gettempdir()) / "valo-util-gallery" / "samples.json")
    args = parser.parse_args(argv)
    output = args.out.resolve()
    if output.is_relative_to(REPO_ROOT):
        parser.error("--out must resolve outside the repo")

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as conn:
            result = harvest(conn, replays=args.replays, per_key=args.per_key)
    finally:
        engine.dispose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{result['replays']} replays, {len(result['keys'])} keys -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
