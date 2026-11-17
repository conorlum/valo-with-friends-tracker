"""Gun identities, firearm tiers and range bands shared with the replay worker."""

from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import Literal


_TABLE_DIR = Path(__file__).parent
_GUNS = json.loads((_TABLE_DIR / "guns.json").read_text(encoding="utf-8"))
_RANGES = json.loads((_TABLE_DIR / "gun_ranges.json").read_text(encoding="utf-8"))
SHORT_RANGE_M = _RANGES["short_range_m"]

# The Melee code of EQUIPPABLE_ARCHETYPE; kept local to avoid an import cycle.
_MELEE = re.compile(r"^Default__Ability_Melee_.+_C$")


def lookup(archetype: str | None,
           learned: Mapping[str, tuple[str, str]] | None = None) -> tuple[str | None, str | None]:
    """Prefer the measured table, then identities learned from this match's shots."""
    row = _GUNS["archetypes"].get(archetype)
    if row is not None:
        return row["name"], row["category"]
    return learned.get(archetype, (None, None)) if learned is not None else (None, None)


def is_melee(archetype: str | None) -> bool:
    return archetype == "Default__Equippable_Unarmed_C" or bool(archetype and _MELEE.match(archetype))


def tier(category: str | None) -> int | None:
    """Abilities, melee and unknown categories have no firearm tier."""
    return _GUNS["tiers"].get(category)


def effective_m(name: str | None) -> float | None:
    """Return the owner's range limit, or None for a gun without one."""
    limit = _RANGES["effective_m"].get(name)
    return float(limit) if limit is not None else None


def range_verdict(name: str | None, metres: float | None) -> Literal["in", "beyond"] | None:
    if name is None or metres is None:
        return None
    limit = effective_m(name)
    return "beyond" if limit is not None and metres > limit else "in"


def is_close_range(name: str | None) -> bool:
    return effective_m(name) is not None
