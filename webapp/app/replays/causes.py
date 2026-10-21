"""What caused a damage or heal notify (revision 21): the ability that did it, named like the extras' `ability` rows.

A damage notify carries `DamageCauser` and `EquippableUsed` (`{"NetGuid", ...}`); a heal notify (`MulticastNotifyHeal`)
carries `Causer`. Each is an actor guid whose `actor_spawned` row gives its archetype. Measured on four 13.08 exports
(0b137bdb, 02dd307f, 0b8fbb74, 5d93a5f9; 2026-10-21):

- every `MulticastNotifyDamage_Base` on a player but two had a causer that is an ability object (Phoenix's molly
  patch, Blaze, Fusion Blast, Fade's tether, Tejo's ult segments); the two named the attacker's own pawn;
- a gun hit (`_Point`) names a causer with no spawn row and a weapon as `EquippableUsed`. A few `_Point` hits are
  abilities: Tejo's grenades and a Cypher trapwire name their object as causer, Jett's ult knives name no spawned
  causer but her `Ability_Wushu_X_Dagger_Production` equippable;
- a heal's causer is an ability object (Skye's `GameObject_Guide_4_Heal_AOE`, Phoenix's Blaze, Miks's
  `GameObject_Thumper_Heal`, named `Iris` by extras.CODE_ALIASES) or the ability's equippable itself (Sage's
  `Ability_Thorne_Q_Heal_Production_New`, Phoenix's Hot Hands `Ability_Phoenix_4_Molotov_Production`, Reyna's
  `Ability_Vampire_Q_Heal` and
  `Ability_Vampire_X_Frenzy`, Clove's `Ability_Smonk_BFArmor_Reactive`, Miks's `Ability_Iris_Thumper`).

A cause is `{"code", "name", "from"}`, plus `"id"` (the causer actor's guid) when the causer is an ability object: the
extras draw those as `ability` rows with the same `id` (`attach_extras` keeps the `id` only where that row is in the
round). `from` is "causer" or "equippable" (only `EquippableUsed` named it). Anything else (a gun, a pawn, nothing
spawned) has no cause. Stdlib only; the archetype rules are the extras', imported when first used (extras imports
the condenser)."""

from __future__ import annotations

import bisect
from collections import defaultdict


class Archetypes:
    """Each actor guid's spawns as [(spawn ms, archetype)], from the export's `actor_spawned` rows: a guid can come
    back for a later actor, so a lookup takes the latest spawn at or before its time."""

    def __init__(self, export):
        spawns: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for row in export.of_type("actor_spawned"):
            archetype = row.data.get("archetype_path")
            if isinstance(archetype, str):
                spawns[int(row.data.get("actor_net_guid") or 0)].append((row.time_ms, archetype))
        self.times = {guid: [t for t, _ in rows] for guid, rows in spawns.items()}
        self.spawns = dict(spawns)

    def at(self, guid: int, t_ms: int) -> str | None:
        times = self.times.get(guid)
        if not times:
            return None
        i = bisect.bisect_right(times, t_ms) - 1
        return self.spawns[guid][i][1] if i >= 0 else None


def _named(archetype: str | None, agents_by_code: dict[str, str]) -> tuple[str, str, bool] | None:
    """(code, name, is an ability object) for an ability object's or an ability equippable's archetype, else None."""
    from app.replays.extras import (ABILITY_ARCHETYPE, ARCHETYPE_ALIASES, EQUIPPABLE_ARCHETYPE,
                                    normalize_archetype)

    if not archetype:
        return None
    archetype = ARCHETYPE_ALIASES.get(archetype, archetype)
    placed = ABILITY_ARCHETYPE.match(archetype)
    if placed is not None:
        code, name = normalize_archetype(placed.group(2), placed.group(3), agents_by_code)
        return code, name, True
    held = EQUIPPABLE_ARCHETYPE.match(archetype)
    if held is not None:
        code, name = normalize_archetype(*held.groups(), agents_by_code)
        return code, name, False
    return None


def cause_of(archetypes: Archetypes, agents_by_code: dict[str, str], t_ms: int, causer, equippable=None) -> dict | None:
    """The cause of a notify at `t_ms` whose causer is `causer` and (damage only) whose `EquippableUsed` guid is
    `equippable`, or None."""
    causer = int(causer or 0)
    named = _named(archetypes.at(causer, t_ms), agents_by_code) if causer else None
    if named is not None:
        code, name, placed = named
        return {"code": code, "name": name, "from": "causer", **({"id": causer} if placed else {})}
    equippable = int(equippable or 0)
    named = _named(archetypes.at(equippable, t_ms), agents_by_code) if equippable else None
    if named is not None and not named[2]:
        return {"code": named[0], "name": named[1], "from": "equippable"}
    return None


def cause_key(cause: dict | None) -> tuple:
    """What separates two runs: hits by one player on one target with different causes are different runs."""
    return () if cause is None else (cause["code"], cause["name"], cause.get("id", 0))
