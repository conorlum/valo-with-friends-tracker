"""Ability objects and shots from a parser export, stored in each round blob's `util`.

`condense_export_dir` runs this after `condense()`, so local ingest and the upload worker store
the same thing. Each round's rows become two `util` kinds (a new `k` needs no format `v` bump):

- `{"k": "ability", "t": <spawn>, "by": <slot | null>, "t1", "kind", "code", "name", "agent",
  "owner_by", "u", "v", ["yaw"], ["thrown"], ["path"], ["owner_d", "other_d"]}`;
- `{"k": "shot", "t", "by", "u", "v", ["u1", "v1"], "gun", "n"}`.

- **Abilities.** Every `actor_spawned` whose archetype is `Default__<Kind>_<AgentCode>_<Name>_C`
  with a kind in ABILITY_KINDS (placed objects, zones, patches, projectiles, possessable
  pawns), from its spawn point until its `actor_closed`. The owner is, in order: the slot of
  the actor's replicated `Instigator` (a character pawn), the one slot playing that agent, or
  the nearest player of that agent at spawn time (`owner_by` says which). Equippable
  `Ability_*` actors are the held item, not something in the world, and are skipped.
- **Shots.** `valorant_shot_received` with a `firing_player_state` that resolves to a slot:
  the shot's origin, its first attack vector (a direction) and the gun.

Times are seconds since the round's InRound start, like the blobs; `u`/`v` are minimap ints.
"""

from __future__ import annotations

import bisect
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.replays import format as fmt
from app.replays.condense import MapInfo, PlayerTable

ABILITY_ARCHETYPE = re.compile(r"^Default__(GameObject|Projectile|Zone|Patch|Pawn)_([A-Za-z0-9]+)_(.+)_C$")
ABILITY_KINDS = ("GameObject", "Projectile", "Zone", "Patch", "Pawn")
# The planted spike: its spawn point is the plant site, its close the explosion or defuse.
BOMB_ARCHETYPE = "Default__TimedBomb_C"

# How far a tracer is drawn along its attack vector, in world units (about 25 m).
TRACER_UNITS = 2500.0

_NEEDLES = ('"actor_spawned"', '"actor_closed"', '"valorant_shot_received"', '"Instigator"')


@dataclass
class _Actor:
    guid: int
    t_ms: int
    kind: str
    code: str
    name: str
    x: float
    y: float
    yaw: float | None
    instigator: int | None = None
    closed_ms: int | None = None


@dataclass
class Extras:
    # round number -> {"abilities": [...], "shots": [...]}
    rounds: dict[int, dict] = field(default_factory=dict)
    report: dict = field(default_factory=dict)


def _round_of(t_ms: int, windows: list[tuple[int, int, int]], buy_phase: bool = False) -> int | None:
    """The round whose playback window holds t; with `buy_phase`, also the time between the
    previous round's end and this round's start (setups placed before the barriers drop)."""
    previous_end = -1
    for n, (start, _, end) in enumerate(windows, 1):
        if (start if not buy_phase else previous_end + 1) <= t_ms <= end:
            return n
        previous_end = end
    return None


def _seconds(t_ms: int, start: int) -> float:
    return round((t_ms - start) / 1000.0, 3)


def read_raw(events_path: Path) -> tuple[dict[int, _Actor], list[dict]]:
    """One streaming pass: ability actors (with instigators and closes) and raw shot rows."""
    actors: dict[int, _Actor] = {}
    instigators: dict[int, int] = {}
    closes: dict[int, int] = {}
    shots: list[dict] = []
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not any(needle in line for needle in _NEEDLES):
                continue
            data = json.loads(line)
            kind = data.get("type")
            t_ms = int(data.get("time_ms", 0))
            if kind == "actor_spawned":
                archetype = str(data.get("archetype_path") or "")
                match = ABILITY_ARCHETYPE.match(archetype)
                location = data.get("location") or {}
                if location.get("x") is None:
                    continue
                if match is not None:
                    kind, code, name = match.groups()
                elif archetype == BOMB_ARCHETYPE:
                    kind, code, name = "Bomb", "", "Spike"
                else:
                    continue
                guid = int(data["actor_net_guid"])
                rotation = data.get("rotation") or {}
                actors[guid] = _Actor(guid, t_ms, kind, code, name,
                                      float(location["x"]), float(location["y"]), rotation.get("yaw"))
            elif kind == "actor_closed":
                closes.setdefault(int(data.get("actor_net_guid") or 0), t_ms)
            elif kind == "valorant_shot_received":
                shots.append(data)
            elif kind == "export_group_received" and data.get("is_actor"):
                payload = data.get("payload")
                if isinstance(payload, dict) and payload.get("Instigator"):
                    instigators.setdefault(int(data.get("actor_net_guid") or 0), int(payload["Instigator"]))
    for guid, actor in actors.items():
        actor.instigator = instigators.get(guid)
        # An actor GUID can be reused after a close: only a close after the spawn counts.
        closed = closes.get(guid)
        actor.closed_ms = closed if closed is not None and closed >= actor.t_ms else None
    return actors, shots


def _nearest(slots: list[int], positions: dict[int, tuple[float, float]], x: float, y: float
             ) -> tuple[int | None, float | None, float | None]:
    """(the nearest slot, its distance, the runner-up's distance) in world units."""
    ranked = sorted((math.hypot(at[0] - x, at[1] - y), slot) for slot in slots
                    if (at := positions.get(slot)) is not None)
    if not ranked:
        return None, None, None
    return ranked[0][1], ranked[0][0], ranked[1][0] if len(ranked) > 1 else None


# The nearest-player guess counts only when it is clear: the owner within this many world units
# (about 15 m) and the other player of that agent at least NEAREST_MARGIN times as far. On the
# first competitive export it was right for 23 of 23 projectiles (which spawn at the thrower,
# median 15-25 units away) and wrong for 34 of 87 of Omen's smokes (placed across the map).
NEAREST_MAX_UNITS = 1500.0
NEAREST_MARGIN = 3.0
# A placed object inherits the owner of the same agent's object it spawned inside of (within
# INSIDE_UNITS), of the projectile that closed within LANDING_MS of its spawn (a thrown cage
# landing), or of the anchor placed within LANDING_MS of it.
LANDING_MS = 300
INSIDE_UNITS = 300.0


def _owner(actor: _Actor, agent: str | None, slots_of_agent: dict[str, list[int]], players: PlayerTable,
           positions_at, known: list[tuple[_Actor, int]], counts: Counter):
    """(slot, how, nearest evidence). In order: the replicated Instigator; the player possessing
    this pawn (a camera, a drone); the only player of that agent; for a placed object, the owner
    of its parent (the object it spawned inside of, the projectile that landed as it spawned, or
    the anchor it was placed with); a clear nearest player of that agent. Otherwise no owner:
    drawn neutral, never guessed."""
    candidates = slots_of_agent.get(agent, []) if agent else []
    near = _nearest(candidates, positions_at(actor.t_ms), actor.x, actor.y) if len(candidates) > 1 else None
    if actor.instigator is not None:
        slot = players.resolve(actor.instigator, actor.t_ms)
        if slot is not None:
            if near is not None and near[0] is not None:
                # How the nearest-player guess does where the owner is known.
                counts["nearest_check_" + ("agree" if near[0] == slot else "disagree")] += 1
            return slot, "instigator", near
    possessors = {owner for _, _, owner in players.possession.get(actor.guid, [])}
    if len(possessors) == 1:
        return possessors.pop(), "possessed", near
    if len(candidates) == 1:
        return candidates[0], "agent", near
    # A throw is where its thrower stands: only the guess below applies to it, never inheritance.
    if actor.kind != "Projectile":
        inside = [(math.hypot(parent.x - actor.x, parent.y - actor.y), slot) for parent, slot in known
                  if parent.code == actor.code and parent.kind != "Projectile" and parent.t_ms <= actor.t_ms
                  and (parent.closed_ms is None or parent.closed_ms >= actor.t_ms)
                  and math.hypot(parent.x - actor.x, parent.y - actor.y) <= INSIDE_UNITS]
        if inside:
            return min(inside)[1], "parent", near
        landed = [(abs(parent.closed_ms - actor.t_ms), slot) for parent, slot in known
                  if parent.kind == "Projectile" and parent.code == actor.code and parent.closed_ms is not None
                  and abs(parent.closed_ms - actor.t_ms) <= LANDING_MS]
        if landed:
            return min(landed)[1], "landed", near
        # A second anchor placed with its first (a trapwire's two ends).
        paired = [(abs(other.t_ms - actor.t_ms), slot) for other, slot in known
                  if other.code == actor.code and other.kind == actor.kind
                  and abs(other.t_ms - actor.t_ms) <= LANDING_MS]
        if paired:
            return min(paired)[1], "paired", near
    if near is not None and near[0] is not None and near[1] <= NEAREST_MAX_UNITS and (
            near[2] is None or near[2] >= NEAREST_MARGIN * near[1]):
        return near[0], "nearest", near
    return None, None, near


SLOT_LETTERS = frozenset({"C", "Q", "E", "X", "4"})
# Agents with a second code for some of their objects. Inferred, not decoded: `Thumper` objects
# (Concuss, ConcussPulse, Heal) carry no Instigator, and appear in exactly the three exports with a
# Miks (Split, Sunset, Haven) and in none of the four without one.
CODE_ALIASES = {"Thumper": "Iris"}


def normalize_archetype(code: str, name: str, known_codes) -> tuple[str, str]:
    """(agent code, name) in the usual `<Code>_<Slot>_<Name>` shape. A few archetypes put the slot
    letter first (`E_Aggrobot_DiscTurret_PowerWave`, `C_Grenadier_Flash`, `Q_BountyHunter_...`) or
    wrap the agent's name (`RemovableObject_GumshoeTrackingDart`); read as they come, those have
    no agent, so no owner and no icon. Anything else is returned unchanged."""
    lowered = {c.lower(): c for c in known_codes}
    if code in CODE_ALIASES:
        return CODE_ALIASES[code], name
    head, _, rest = name.partition("_")
    if code in SLOT_LETTERS and head.lower() in lowered:
        return lowered[head.lower()], f"{code}_{rest}" if rest else code
    if code == "RemovableObject":
        for low, real in sorted(lowered.items(), key=lambda kv: -len(kv[0])):
            if name.lower().startswith(low):
                return real, f"{code}_{name}"
    return code, name


PATH_STEP_MS = 100   # a possessable pawn's path (a drone, Trailblazer), one point per this long


def _thrown(actor: _Actor, actors: dict[int, _Actor]) -> _Actor | None:
    """The same agent's projectile that closed within LANDING_MS of this object's spawn: the throw
    it came from (drawn as an arc from the thrower), whatever the owner evidence said."""
    if actor.kind == "Projectile" or not actor.code:
        return None
    hits = [(abs(p.closed_ms - actor.t_ms), p.guid, p) for p in actors.values()
            if p.kind == "Projectile" and p.code == actor.code and p.closed_ms is not None
            and abs(p.closed_ms - actor.t_ms) <= LANDING_MS and p.t_ms <= actor.t_ms]
    return min(hits)[2] if hits else None


def build_extras(events_path: Path, players: PlayerTable, windows: list[tuple[int, int, int]],
                 game_map: MapInfo, agents_by_code: dict[str, str], positions_at, pawn_path=None) -> Extras:
    """`positions_at(t_ms)` -> {slot: (x, y)}: each player's world position at that moment, from
    the full movement stream (buy phase included, where the round tracks don't reach).
    `pawn_path(guid)` -> [(t_ms, x, y)]: a non-player pawn's movement (a drone), or None."""
    actors, raw_shots = read_raw(events_path)
    agent_code = {code.lower(): name for code, name in agents_by_code.items()}
    counts_unknown: Counter[str] = Counter()
    for guid, actor in list(actors.items()):
        if actor.code:
            actor.code, actor.name = normalize_archetype(actor.code, actor.name, agents_by_code)
            if actor.code.lower() not in agent_code:
                # Not an agent's: cosmetics such as `Infinity_Rain_Instance`, `Juliett_TheaterLights_Instance`
                # and `Finisher_Ego2`, which never have an owner. Counted, not drawn.
                counts_unknown[actor.code] += 1
                del actors[guid]
    slots_of_agent: dict[str, list[int]] = defaultdict(list)
    for slot, agent in enumerate(players.agents):
        slots_of_agent[agent].append(slot)

    out = Extras()
    counts: Counter[str] = Counter()
    # Actors whose owner came from evidence (or, for a projectile, a clear nearest thrower).
    known: list[tuple[_Actor, int]] = []
    for actor in sorted(actors.values(), key=lambda a: (a.t_ms, a.guid)):
        agent = agent_code.get(actor.code.lower()) if actor.code else None
        # Owners first, for every actor, so a buy-phase throw can still hand its owner on.
        slot, owner_by, near = _owner(actor, agent, slots_of_agent, players, positions_at, known, counts)
        if slot is not None and (owner_by != "nearest" or actor.kind == "Projectile"):
            known.append((actor, slot))
        n = _round_of(actor.t_ms, windows, buy_phase=True)
        if n is None:
            counts["abilities_outside_rounds"] += 1
            continue
        start, _, end = windows[n - 1]
        if actor.closed_ms is not None and actor.closed_ms < start:
            counts["abilities_buy_phase_only"] += 1  # thrown and gone before the round began
            continue
        counts["abilities_buy_phase"] += actor.t_ms < start
        u, v = game_map.to_uv(actor.x, actor.y)
        counts[f"owner_by_{owner_by}"] += 1
        entry = {"t0": max(0.0, _seconds(actor.t_ms, start)),
                 "t1": _seconds(min(actor.closed_ms, end), start) if actor.closed_ms is not None else None,
                 "kind": actor.kind, "code": actor.code, "name": actor.name, "agent": agent,
                 "slot": slot, "owner_by": owner_by, "u": u, "v": v}
        if owner_by == "nearest":
            # The guess's evidence: world units to the owner and to the other player of that agent.
            entry["owner_d"] = round(near[1])
            entry["other_d"] = None if near[2] is None else round(near[2])
        if actor.yaw is not None:
            entry["yaw"] = game_map.yaw_to_map(float(actor.yaw))
        throw = _thrown(actor, actors)
        if throw is not None:
            entry["thrown"] = {"t0": _seconds(throw.t_ms, start), "t1": _seconds(throw.closed_ms, start),
                               **dict(zip(("u", "v"), game_map.to_uv(throw.x, throw.y)))}
            counts["abilities_thrown"] += 1
        if actor.kind == "Pawn" and pawn_path is not None:
            last_t, points = -PATH_STEP_MS, []
            until = min(actor.closed_ms, end) if actor.closed_ms is not None else end
            for t_ms, x, y in pawn_path(actor.guid) or []:
                if max(actor.t_ms, start) <= t_ms <= until and t_ms - last_t >= PATH_STEP_MS:
                    points.append([_seconds(t_ms, start), *game_map.to_uv(x, y)])
                    last_t = t_ms
            if points:
                entry["path"] = points
                counts["pawn_paths"] += 1
        out.rounds.setdefault(n, {"abilities": [], "shots": []})["abilities"].append(entry)
        counts["abilities"] += 1

    for data in raw_shots:
        t_ms = int(data.get("time_ms", 0))
        shot = data.get("shot") or {}
        state = shot.get("firing_player_state")
        location = shot.get("location") or {}
        if not state or location.get("x") is None:
            counts["shots_without_shooter"] += 1
            continue
        slot = players.resolve(int(state), t_ms)
        if slot is None:
            counts["shots_unresolved"] += 1
            continue
        n = _round_of(t_ms, windows)
        if n is None:
            counts["shots_outside_rounds"] += 1
            continue
        start = windows[n - 1][0]
        x, y = float(location["x"]), float(location["y"])
        u, v = game_map.to_uv(x, y)
        entry = {"t": _seconds(t_ms, start), "slot": slot, "u": u, "v": v,
                 "gun": ((shot.get("equippable") or {}).get("name")), "n": shot.get("num_projectiles") or 1}
        vectors = shot.get("attack_vectors") or []
        if vectors and vectors[0].get("x") is not None:
            dx, dy = float(vectors[0]["x"]), float(vectors[0]["y"])
            norm = math.hypot(dx, dy)
            if norm > 1e-6:
                u1, v1 = _unclamped_uv(game_map, x + dx / norm * TRACER_UNITS, y + dy / norm * TRACER_UNITS)
                entry["u1"], entry["v1"] = u1, v1
        out.rounds.setdefault(n, {"abilities": [], "shots": []})["shots"].append(entry)
        counts["shots"] += 1
    out.report = dict(sorted(counts.items()))
    if counts_unknown:
        out.report["skipped_not_agent"] = dict(sorted(counts_unknown.items()))
    return out


def _unclamped_uv(game_map: MapInfo, x: float, y: float) -> tuple[int, int]:
    """Like MapInfo.to_uv, without clamping, so a tracer's far end keeps its direction."""
    return (int(round((y * game_map.x_mult + game_map.x_add) * fmt.UV_SCALE)),
            int(round((x * game_map.y_mult + game_map.y_add) * fmt.UV_SCALE)))


# ---------------------------------------------------------------- stored util (Stage 2)


class WorldPositions:
    """Each player's world position over the whole recording, buy phases included, thinned to
    one sample per STEP_MS; `at(t_ms)` gives the samples within 0.5 s of t. The same pass keeps
    every other pawn's movement (drones, Trailblazer), for `path(guid)`. `movement` is iterated
    once: the streaming loader's movement is a re-iterable stream, so this is a second pass over
    the file, never a copy of it."""

    STEP_MS = 100

    def __init__(self, movement, players: PlayerTable):
        self.rows: dict[int, tuple[list[int], list[tuple[float, float]]]] = {}
        by_slot: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
        self.others: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
        for row in movement:
            data = row.data
            position = data.get("position") or {}
            if data.get("error_sentinel") or position.get("x") is None or position.get("y") is None:
                continue
            pawn = int(data.get("shooter_character_net_guid") or data.get("actor_net_guid") or 0)
            slot = players.pawn_slot.get(pawn)
            target = by_slot[slot] if slot is not None else self.others[pawn]
            if not target or row.time_ms - target[-1][0] >= self.STEP_MS:
                target.append((row.time_ms, float(position["x"]), float(position["y"])))
        for slot, samples in by_slot.items():
            samples.sort()
            self.rows[slot] = ([t for t, _, _ in samples], [(x, y) for _, x, y in samples])
        for samples in self.others.values():
            samples.sort()

    def path(self, guid: int) -> list[tuple[int, float, float]]:
        return self.others.get(guid, [])

    def at(self, t_ms: int) -> dict[int, tuple[float, float]]:
        out = {}
        for slot, (times, points) in self.rows.items():
            i = bisect.bisect_left(times, t_ms)
            best = min((j for j in (i - 1, i) if 0 <= j < len(times)), key=lambda j: abs(times[j] - t_ms),
                       default=None)
            if best is not None and abs(times[best] - t_ms) <= 500:
                out[slot] = points[best]
        return out


def util_entries(round_extras: dict) -> list[dict]:
    """One round's abilities and shots as `util` entries: the envelope's `t` and `by` replace the
    row's `t0`/`t` and `slot`; every other field is kept as it is."""
    out = []
    for ability in round_extras.get("abilities", []):
        rest = {k: v for k, v in ability.items() if k not in ("t0", "slot")}
        out.append({"k": "ability", "t": ability["t0"], "by": ability["slot"], **rest})
    for shot in round_extras.get("shots", []):
        rest = {k: v for k, v in shot.items() if k not in ("t", "slot")}
        out.append({"k": "shot", "t": shot["t"], "by": shot["slot"], **rest})
    return out


def rounds_extras(util: list[dict]) -> dict:
    """The inverse of `util_entries`: a stored round's `{"abilities", "shots"}` (the viewer's and
    the tests' shape)."""
    abilities, shots = [], []
    for entry in util:
        rest = {k: v for k, v in entry.items() if k not in ("k", "t", "by")}
        if entry.get("k") == "ability":
            abilities.append({"t0": entry["t"], "slot": entry["by"], **rest})
        elif entry.get("k") == "shot":
            shots.append({"t": entry["t"], "slot": entry["by"], **rest})
    return {"abilities": abilities, "shots": shots}
