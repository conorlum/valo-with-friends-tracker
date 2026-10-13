"""Named map areas: a point's area is its nearest in-game callout (app/static/data/callouts.json, written by
scripts/fetch_callouts.py). A map the file does not have has no areas; callers get None, never a guess."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CALLOUTS_FILE = Path(__file__).resolve().parents[1] / "static" / "data" / "callouts.json"
SITES = frozenset({"A", "B", "C"})


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(CALLOUTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def callouts(map_name: str, table: dict | None = None) -> list[dict]:
    return (table if table is not None else _table()).get(map_name, [])


def area_at(map_name: str, u: float, v: float, table: dict | None = None) -> dict | None:
    best = None
    for c in callouts(map_name, table):
        d = (c["u"] - u) ** 2 + (c["v"] - v) ** 2
        if best is None or d < best[0]:
            best = (d, c)
    return None if best is None else {"name": best[1]["name"], "super": best[1]["super"]}
