"""Plant sites that moved in a patch: only plants from matches played on or after the move count.

`app/data/plant_site_changes.json` lists each moved site as (map, site, from date). A plant on that
map and site from an earlier match is left out of the plant statistics, the /plants counts and the
naming page, but its row stays in `round_plants` (deleting it would make the backfill fetch the
match again). A match with no `played_at` is kept. The owner's choice (2026-11-25): count a moved
site forward from its move only, rather than giving its old area a layout of its own.
"""
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "data" / "plant_site_changes.json"


@lru_cache(maxsize=1)
def _raw() -> bytes:
    return PATH.read_bytes() if PATH.exists() else b'{"changes": []}'


@lru_cache(maxsize=1)
def cutoffs() -> dict:
    """{(map name, site): first datetime that counts, UTC}."""
    result = {}
    for change in json.loads(_raw().decode("utf-8"))["changes"]:
        day = date.fromisoformat(change["from"])
        result[change["map"], change["site"]] = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return result


def digest() -> str:
    """SHA-256 of the file's bytes: a new move changes which rows count."""
    return hashlib.sha256(_raw()).hexdigest()


def counts(map_name, site, played_at) -> bool:
    """Whether a plant on this map and site, from a match played then, is counted."""
    start = cutoffs().get((map_name, site))
    if start is None or played_at is None:
        return True
    if played_at.tzinfo is None:
        played_at = played_at.replace(tzinfo=timezone.utc)
    return played_at >= start
