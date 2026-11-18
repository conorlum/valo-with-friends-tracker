"""Kit names for held abilities (`static/data/held_abilities.json`), read by condense, the coach and the viewer.

A hold row names a held ability by the parts of its equippable archetype `Default__Ability_<stem>_C`: `code` (the
agent's code name, or `Pawn`/`AggroBot` for Gekko's creatures), `key` and `name`. The `key` is the replay's slot
letter (`4` read as `C`), which is not the key the player pressed: Omen's smoke is `Wraith_4_Smoke` but Dark Cover is
on E. So the label comes from this table, keyed `<code>` -> `<key or "">/<name>`, never from the letter. A value of
null is a known item that is no ability to name (a passive, the knife's base ability).

Every held ability without an entry is a bug: condense counts them (`report["extras"]["holds_ability_unnamed"]`,
recorded per match as Site health's `held_ability_unnamed`), and tests/replays/test_held_names.py fails on any
archetype seen in a replay that the table lacks.
"""

from functools import lru_cache
import json
import re
from pathlib import Path

TABLE_PATH = Path(__file__).resolve().parents[1] / "static" / "data" / "held_abilities.json"
EQUIPPABLE_ARCHETYPE = re.compile(r"^Default__Ability_([A-Za-z0-9]+)_(.+)_C$")
# An agent's equippable outside Default__Ability_* names its slot second: Iso's Kill Contract teleport is
# `Default__Equippable_Sequoia_X_ArenaTeleport_C`.
AGENT_EQUIPPABLE = re.compile(r"^Default__Equippable_([A-Za-z0-9]+)_([CQEX4])_(.+)_C$")
# Flex (cosmetic) items: `Default__Equippable_Totem_PDA_C`, `Default__Equippable_Snake_Totem_C`.
FLEX_ARCHETYPE = re.compile(r"^Default__Equippable_(.*_)?Totem(_.*)?_C$")
SLOTS = frozenset({"C", "Q", "E", "X", "4"})


def flex_name(archetype: str | None) -> str | None:
    """The flex item's own name from its archetype (`Snake`, `Tactibear`, `PDA`; an event tag with digits such as
    `DuosDay26` is dropped, so `DuosDay26_Duck` is `Duck`); None for anything else."""
    flex = FLEX_ARCHETYPE.match(archetype or "")
    if flex is None:
        return None
    words = [part for part in f"{flex[1] or ''}{flex[2] or ''}".split("_") if part and not re.search(r"\d", part)]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", " ".join(words)) or "Flex"   # SoccerBall -> Soccer Ball


def parts(archetype: str | None) -> tuple[str, str | None, str] | None:
    """(code, key, name) of a held-ability archetype, as a hold row stores them; None for anything else."""
    agent = AGENT_EQUIPPABLE.match(archetype or "")
    if agent is not None:
        return agent[1], ("C" if agent[2] == "4" else agent[2]), agent[3]
    held = EQUIPPABLE_ARCHETYPE.match(archetype or "")
    if held is None:
        return None
    tokens = [held[1], *held[2].split("_")]
    if tokens[0] in SLOTS:
        key, code, *rest = tokens
    else:
        code, *rest = tokens
        key = rest.pop(0) if rest[0] in SLOTS else None
    return code, ("C" if key == "4" else key), "_".join(rest)


def entry(key: str | None, name: str | None) -> str:
    return f"{key or ''}/{name or ''}"


@lru_cache(maxsize=1)
def table() -> dict:
    data = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    return {code: kit for code, kit in data.items() if not code.startswith("_")}


@lru_cache(maxsize=1)
def kit_names() -> frozenset:
    kits = json.loads((TABLE_PATH.parent / "abilities.json").read_text(encoding="utf-8"))
    return frozenset(name for kit in kits.values() for name in kit)


def lookup(code: str | None, key: str | None, name: str | None) -> tuple[bool, str | None]:
    """(known, kit name): known is False when the table has no entry; the name is None for a known non-ability.
    A row with no code is an ability gun named by the gun table or this match's shots (Neon's Overdrive): its name
    is already the display name, known when it is a kit name."""
    if code is None:
        return (True, name) if name in kit_names() else (False, None)
    kit = table().get(code or "")
    if kit is None or entry(key, name) not in kit:
        return False, None
    return True, kit[entry(key, name)]
