"""Display-only clockwise quarter turns, chosen from each map's spawn callouts."""

import json
from functools import lru_cache
from math import atan2, degrees
from pathlib import Path


@lru_cache(maxsize=1)
def _callouts() -> dict:
    path = Path(__file__).resolve().parents[1] / "static" / "data" / "callouts.json"
    return json.loads(path.read_text(encoding="utf-8"))


def quarter_turns(map_name: str | None, table: dict | None = None) -> int:
    """Orient the attacker spawn below the defender; unknown maps stay unrotated."""
    if map_name is None:
        return 0
    rows = (table if table is not None else _callouts()).get(map_name.split("_", 1)[0], [])
    attacker = next((row for row in rows if row["name"] == "Attacker Side Spawn"), None)
    defender = next((row for row in rows if row["name"] == "Defender Side Spawn"), None)
    if attacker is None or defender is None:
        return 0
    angle = degrees(atan2(attacker["v"] - defender["v"], attacker["u"] - defender["u"]))
    return round(((90 - angle) % 360) / 90) % 4
