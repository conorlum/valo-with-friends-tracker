"""Names for held items, without loading the site or replay extractor."""

from functools import lru_cache
import json
from pathlib import Path
import re

from app.replays import guns, held_names

UNNAMED = "an ability"   # a held ability the kit-name table lacks: Site health's held_ability_unnamed


@lru_cache(maxsize=1)
def _cast_names() -> list:
    """(pattern, kit name) from the Critical Util table, whose cast rows match `<code>_<name>`."""
    table = json.loads((Path(__file__).parents[1] / "critutil/abilities.json").read_text(encoding="utf-8"))
    return [(re.compile(a["match"]), a["ability"]) for a in table["abilities"] if a.get("match")]


def ability_name(agent: str | None, row: dict) -> str | None:
    """The kit name of a held ability (a hold row) or of a placed camera/drone being looked through (a cast row).
    Never from the slot letter, which is the replay's and not the key pressed (held_names). None for a passive."""
    name = row.get("name") or ""
    if row.get("k") == "ability":
        cast = f"{row.get('code') or ''}_{name}"
        found = next((kit for pattern, kit in _cast_names() if pattern.match(cast)), None)
        return found or name.replace("_", " ").strip() or None
    known, kit = held_names.lookup(row.get("code"), row.get("key"), name)
    return kit if known else UNNAMED


def item_name(agent: str | None, row: dict | None) -> str | None:
    if not row or guns.tier(row.get("cat")) is not None:
        return None
    category = row.get("cat")
    if category == "melee":
        return "unarmed" if row.get("item") == "unarmed" else "Knife"
    if category == "ability":
        return ability_name(agent, row)
    return {"spike": "Spike", "flex": "flex item"}.get(category)
