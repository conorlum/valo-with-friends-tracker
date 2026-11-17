"""Names for held items, without loading the site or replay extractor."""

from functools import lru_cache
import json
from pathlib import Path

from app.replays import guns


@lru_cache(maxsize=1)
def _kit_names() -> dict:
    icons = {"grenade": "C", "ability1": "Q", "ability2": "E", "ultimate": "X"}
    table = json.loads((Path(__file__).parents[1] / "static/data/abilities.json").read_text(encoding="utf-8"))
    return {agent: {icons[Path(icon).stem]: name for name, icon in kit.items()
                    if Path(icon).stem in icons} for agent, kit in table.items()}


def ability_name(agent: str | None, row: dict) -> str | None:
    name = row.get("name") or ""
    key = row.get("key")
    if key is None and row.get("k") == "ability":
        # Placed camera/drone rows keep the archetype key in their name.
        key = name.split("_", 1)[0] if "_" in name else None
        key = "C" if key == "4" else key
    return _kit_names().get(agent, {}).get(key) or name.replace("_", " ").strip() or None


def item_name(agent: str | None, row: dict | None) -> str | None:
    if not row or guns.tier(row.get("cat")) is not None:
        return None
    category = row.get("cat")
    if category == "melee":
        return "unarmed" if row.get("item") == "unarmed" else "Knife"
    if category == "ability":
        return ability_name(agent, row)
    return {"spike": "Spike", "flex": "flex item"}.get(category)
