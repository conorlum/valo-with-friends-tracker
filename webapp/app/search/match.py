"""The one replay-search matcher (R1): decides every hit for the library, CLI, route and coach.

Standard library only; no database, no `app.services`. SQL is only a prefilter. A row is a dict with exactly
`ROW_KEYS`; this module defines it, and the index producer, store and readers share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # filters imports this module for KINDS/BUYS
    from app.search.filters import Filters

KINDS = ("state", "kill", "death", "plant", "defuse", "cast", "fight", "backshot")
BUYS = ("pistol", "save", "eco", "force", "full")

ROW_KEYS = (
    # where
    "replay_id", "uuid", "map", "played_at", "match_id", "round", "seq",
    # round
    "attack_team", "won_team", "buy1", "buy2", "score1", "score2", "planted", "site", "plant_t",
    # moment
    "kind", "t", "t_end", "team", "slot", "player_id", "agent",
    "other_slot", "other_player_id", "other_agent", "other_team",
    "ability", "ability_key", "ult", "first",
    "alive1", "alive2", "alive_slots", "post_plant", "tags", "weapon",
)

SPIKE_SECONDS = 45.0  # D11: seconds on the spike from the plant


@dataclass(frozen=True)
class Context:
    ours: dict[int, str]  # replay_id -> our team, from `as`
    player_slots: dict[int, int]  # replay_id -> slot, from `player`
    allowed: frozenset[int] | None = None  # replay ids from `with` / `circle`


def _other(team: str) -> str:
    return "team-2" if team == "team-1" else "team-1"


def our_team(f: Filters, ctx: Context, row: dict) -> str | None:
    """Our team in this row's replay: from `as`, from `side`, else None."""
    if f.as_ is not None:
        return ctx.ours.get(row["replay_id"])
    if f.side is not None:
        attack = row["attack_team"]
        if attack is None:  # no known attacker, so no known defender either
            return None
        return attack if f.side == "attack" else _other(attack)
    return None


def _in_range(rng: tuple[int, int] | None, value) -> bool:
    return rng is None or (value is not None and rng[0] <= value <= rng[1])


_SLASHED_AGENT = "KAY/O/"  # the one agent whose name holds a slash


def _ability_tail(row: dict) -> str | None:
    """The ability's own name: `ability` is "<agent>/<ability>", and an agent's name may hold a slash
    ("KAY/O/NULL/cmd" -> "NULL/cmd"). Stripped in order: "KAY/O/" (any case), the row's agent plus "/", else the
    text after the first "/"."""
    ability = row.get("ability")
    if not ability:
        return None
    if ability[:len(_SLASHED_AGENT)].casefold() == _SLASHED_AGENT.casefold():
        return ability[len(_SLASHED_AGENT):]
    agent = row.get("agent")
    if agent and ability.casefold().startswith(agent.casefold() + "/"):
        return ability[len(agent) + 1:]
    return ability.split("/", 1)[-1]


def _ability_ok(wanted: str, row: dict) -> bool:
    """`ability=` is a case-insensitive part of the ability's name; a keybind is never searched."""
    w = wanted.casefold()
    tail = _ability_tail(row)
    return tail is not None and w in tail.casefold()


def matches(f: Filters, ctx: Context, row: dict) -> bool:
    if f.kind is not None and row["kind"] != f.kind:
        return False
    if f.map is not None and (row["map"] or "").casefold() != f.map.casefold():
        return False
    if f.since is not None or f.until is not None:
        played = row["played_at"]
        if not played:
            return False
        day = played[:10]
        if f.since is not None and day < f.since.isoformat():
            return False
        if f.until is not None and day > f.until.isoformat():
            return False
    rid = row["replay_id"]
    if ctx.allowed is not None and rid not in ctx.allowed:
        return False
    if f.as_ is not None and rid not in ctx.ours:
        return False
    if f.player is not None and rid not in ctx.player_slots:
        return False

    our = our_team(f, ctx, row)
    perspective = f.as_ is not None or f.side is not None
    if perspective and our is None:
        return False
    is_one = our == "team-1"

    if f.won is not None:
        won = row["won_team"]
        if won is None or (won == our) != f.won:
            return False
    if f.our_buy is not None and row["buy1" if is_one else "buy2"] != f.our_buy:
        return False
    if f.their_buy is not None and row["buy2" if is_one else "buy1"] != f.their_buy:
        return False
    if not _in_range(f.our_score, row["score1" if is_one else "score2"]):
        return False
    if not _in_range(f.their_score, row["score2" if is_one else "score1"]):
        return False
    if not _in_range(f.round, row["round"]):
        return False
    if f.planted is not None and bool(row["planted"]) != f.planted:
        return False
    if f.site is not None and row["site"] != f.site:
        return False

    if f.state is not None:
        a1, a2 = row["alive1"], row["alive2"]
        if a1 is None or a2 is None:
            return False
        ours_alive, theirs_alive = (a1, a2) if is_one else (a2, a1)
        if (ours_alive, theirs_alive) != f.state:
            return False
    if f.post_plant is not None and bool(row["post_plant"]) != f.post_plant:
        return False
    if f.left is not None:
        plant_t = row["plant_t"]
        if not row["post_plant"] or plant_t is None or plant_t + SPIKE_SECONDS - row["t"] < f.left:
            return False
    if f.after is not None and not row["t"] >= f.after:
        return False
    if f.before is not None and not row["t"] <= f.before:
        return False

    if f.player is not None:
        slot = ctx.player_slots[rid]
        if row["kind"] == "state":
            if slot not in (row["alive_slots"] or ()):
                return False
        elif row["slot"] != slot:
            return False
    if f.agent is not None:
        agent = row["agent"]
        if agent is None or agent.casefold() != f.agent.casefold():
            return False
    if f.team is not None:
        team = row["team"]
        if team is None or (team == our) != (f.team == "ours"):
            return False
    if f.ability is not None and not _ability_ok(f.ability, row):
        return False
    if f.ult is not None and bool(row["ult"]) != f.ult:
        return False
    if f.first is not None and bool(row["first"]) != f.first:
        return False
    if f.tags:
        have = row["tags"] or ()
        if any(t not in have for t in f.tags):
            return False
    return True


def _sort_rows(rows: list[dict]) -> list[dict]:
    # Stable passes from the least to the most significant key.
    out = sorted(rows, key=lambda r: (r["round"], r["t"], r["seq"]))
    out.sort(key=lambda r: r["replay_id"], reverse=True)
    out.sort(key=lambda r: (r["played_at"] is not None, r["played_at"] or ""), reverse=True)
    return out


def select(f: Filters, ctx: Context, rows) -> list[dict]:
    """The rows that match, ordered played_at desc (None last), replay_id desc, round, t, seq."""
    return _sort_rows([r for r in rows if matches(f, ctx, r)])


def _label(slot, agent, name_of: Callable[[int], str | None], our: str | None) -> str:
    name = name_of(slot) if slot is not None else None
    if name:
        return name
    if our is None:
        return agent or "someone"
    return f"the enemy {agent}" if agent else "the enemy"


def _ability_name(row: dict) -> str:
    return _ability_tail(row) or "an ability"


def summary(row: dict, name_of: Callable[[int], str | None], our: str | None) -> str:
    """One line for a hit. `name_of(slot)` names the slots the caller wants named; the rest are
    "the enemy <agent>", or the agent alone when there is no perspective (`our` is None)."""
    actor = _label(row.get("slot"), row.get("agent"), name_of, our)
    other = _label(row.get("other_slot"), row.get("other_agent"), name_of, our)
    weapon = f" ({row['weapon']})" if row.get("weapon") else ""
    kind = row["kind"]
    if kind == "kill":
        head = f"{actor} killed {other}{weapon}"
    elif kind == "death":
        head = f"{actor} was killed by {other}{weapon}"
    elif kind == "plant":
        where = f" on {row['site']}" if row.get("site") else ""
        head = f"{actor} planted the spike{where}" if row.get("slot") is not None else f"Spike planted{where}"
    elif kind == "defuse":
        head = f"{actor} defused the spike" if row.get("slot") is not None else "Spike defused"
    elif kind == "cast":
        head = f"{actor} used their ultimate, {_ability_name(row)}" if row.get("ult") else f"{actor} cast {_ability_name(row)}"
    elif kind == "fight":
        head = f"{actor} in a fight"
    elif kind == "backshot":
        head = f"{actor} was shot in the back"
    else:
        head = "Round state"
    parts = [head]
    a1, a2 = row.get("alive1"), row.get("alive2")
    if a1 is not None and a2 is not None:
        ours_alive, theirs_alive = (a2, a1) if our == "team-2" else (a1, a2)
        count = f"{ours_alive}v{theirs_alive}"
        parts.append(count + (" post-plant" if row.get("post_plant") else ""))
    elif row.get("post_plant"):
        parts.append("post-plant")
    if row.get("post_plant") and row.get("plant_t") is not None:
        left = max(0, round(row["plant_t"] + SPIKE_SECONDS - row["t"]))
        parts.append(f"{left} s on the spike")
    return ", ".join(parts)
