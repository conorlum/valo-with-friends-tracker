"""Ability objects and shots from a parser export, stored in each round blob's `util`.

`condense_export_dir` runs this after `condense()`, so local ingest and the upload worker store
the same thing. Each round's rows become three `util` kinds (a new `k` needs no format `v` bump):

- `{"k": "ability", "t": <spawn>, "by": <slot | null>, "t1", "kind", "code", "name", "agent",
  "owner_by", "u", "v", ["z"], ["yaw"], ["thrown"], ["path"], ["owner_d", "other_d"], ["end", "end_z"],
  ["defuses"], ["points", "on"], ["fx"], ["gone"], ["possessed"], ["yaws"], ["off"]}` (`possessed` and
  `yaws`, on pawns, are map control's inputs: `_control_inputs`; `z`, `end_z`, `thrown.z` and a path
  point's fourth value are heights in decimetres of world z, each left out when the export gave none:
  format.py, revision 11; `off`, on a Killjoy turret or alarmbot, is `[[from, to | null], ...]`: when it
  was switched off, KJ out of range: `device_off_spans`, revision 12; `arms`, on Deadlock's Barrier Mesh
  root, is `[[u, v, z dm, gone t | null], ...]`: each node's place and when its arm went before the wall
  did, with `on` = when the mesh was solid and `arms_missing` = nodes with no known place: `mesh_arms`);
- `{"k": "shot", "t", "by", "u", "v", ["u1", "v1"], "gun", "n"}`;
- `{"k": "reveal", "t", "by": <revealer>, "t1", "target": <revealed slot>, "code", "name"}`;
- `{"k": "status", "t", "by": <applier | null>, "t1", "target", "code", "name", "status", "from"}`.

- **Abilities.** Every `actor_spawned` whose archetype is `Default__<Kind>_<AgentCode>_<Name>_C`
  with a kind in ABILITY_KINDS (placed objects, zones, patches, projectiles, possessable
  pawns), from its spawn point until its `actor_closed`. The owner is, in order: the slot of
  the actor's replicated `Instigator` (a character pawn), its possessor, the owner of the
  equippable that placed it (Stage 5: `equippable_claims`, exact for every trapwire, setups
  included; an equippable that lists an owned actor inherits its owner, so Sova -> drone ->
  dart is one chain), what caused it (`caused_by`: the Chamber trap that fired, the ult kill in
  its tick, the plant that began 4 s before the spike), the one slot playing that agent, a
  parent's owner, or, for a thrown projectile only, the nearest player of that agent at spawn
  time (`owner_by` says which). Equippable `Ability_*` actors are the held item,
  not something in the world: they are not drawn, only read for owners. A trapwire's first
  anchor carries its second anchor's point (`end`); the planted spike its defuse attempts
  (`defuses`: [[from, to | null, slot, finished]]); Viper's wall its laid line (`points`) and
  when it was up (`on`: [[from, to | null]]).
- **Shots.** `valorant_shot_received` with a `firing_player_state` that resolves to a slot:
  the shot's origin, its first attack vector (a direction) and the gun.
- **Reveals.** Each time a player was revealed by an enemy's recon, haunt, dart or Neural Theft,
  at that moment: a continuous reveal until it stopped, a ping (a drone dart's hit and its two
  pulses, Neural Theft's +3 s and +7 s) for PING_MS (`find_reveals`, `dart_tags`).
- **Pops and statuses** (Stage 6, docs/replay-status-effects-plan.md). An ability that goes off in an
  instant but lives on in the replay keeps the times it played effects on itself (`fx`); a player an
  enemy's ability concussed, hindered, suppressed, made fragile, tethered, decayed or slowed gets a
  `status` row for as long as it lasted (`find_statuses`). A trapwire tethers, then when it goes off
  concusses and reveals (and stays armed). An ability object shot and destroyed before its object
  closes has `gone`.

Times are seconds since the round's InRound start, like the blobs; `u`/`v` are minimap ints.
"""

from __future__ import annotations

import base64
import bisect
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.replays import format as fmt
from app.replays.condense import MapInfo, PlayerTable

# `Gameobject_` (lower-case o) is how KAY/O's ZERO/point pulse is spelled; it is read as GameObject.
ABILITY_ARCHETYPE = re.compile(r"^Default__(GameObject|Gameobject|Projectile|Zone|Patch|Pawn)_([A-Za-z0-9]+)_(.+)_C$")
ABILITY_KINDS = ("GameObject", "Projectile", "Zone", "Patch", "Pawn")
# The planted spike: its spawn point is the plant site, its close the explosion or defuse.
BOMB_ARCHETYPE = "Default__TimedBomb_C"

# How far a tracer is drawn along its attack vector, in world units (about 25 m).
TRACER_UNITS = 2500.0

_NEEDLES = ('"actor_spawned"', '"actor_closed"', '"valorant_shot_received"', '"Instigator"')

# Ability actors whose archetype doesn't follow `<Kind>_<AgentCode>_<Name>`, by their exact name: read as the
# archetype on the right. Deadlock's (measured on the Summit export, 2026-10-05): the Barrier Mesh's root has
# no name part at all, its four nodes and its throw are coded `CableJam`, the Sonic Sensor `StealthingTrap`,
# its concuss `SoundSensor`, and Annihilation's hook is an `Actor_`. Exact names only, so a lookalike that
# isn't an agent's is never taken in; the hook's path warnings, its spline and `MotherNode` stay out (they
# aren't casts) and are counted with the other skipped archetypes.
ARCHETYPE_ALIASES = {
    "Default__GameObject_CableJamRoot_C": "Default__GameObject_Cable_E_CableJam_Root_C",
    "Default__GameObject_CableJam_CableDeployer_Precomputed_C": "Default__GameObject_Cable_E_CableJam_Node_C",
    "Default__Projectile_CableJam_InAir_C": "Default__Projectile_Cable_E_CableJam_C",
    "Default__GameObject_StealthingTrap_SoundSensor_C": "Default__GameObject_Cable_Q_SoundSensor_C",
    "Default__GameObject_SoundSensor_SweetSpotFissure_C": "Default__GameObject_Cable_Q_SoundSensor_Fissure_C",
    "Default__Actor_FishingHook_C": "Default__GameObject_Cable_X_FishingHook_C",
    "Default__GameObject_FishingHook_CageSphere_C": "Default__GameObject_Cable_X_FishingHook_Cage_C",
}
# Deadlock's Barrier Mesh: the root row carries its nodes as `arms`; a node is never a row of its own.
MESH_ROOT, MESH_NODE = "E_CableJam_Root", "E_CableJam_Node"
MESH_NODE_UNITS = 50.0       # a node spawns on its root (measured: the same point, 56 units lower)
# The mesh is solid once its root swaps effects, 3.0 s after it lands (3003-3006 ms on all eight walls).
MESH_FORM_MS = (500, 6000)


def packed_vector(bit_count: int, data: bytes) -> tuple[float, float, float] | None:
    """A raw property the parser left as `{"BitCount", "Data"}` read as UE5's packed vector: a 7-bit header
    (the low 6 bits are each component's width, bit 6 says the values are scaled ints), then x, y and z as
    two's-complement ints of that width, least significant bit first, in hundredths of a world unit. None
    when the bits don't have that shape. Checked on the Summit export: a mesh root's `DamageOrigin` decodes
    to its own spawn point, to the unit."""
    def take(start: int, width: int) -> int:
        value = 0
        for i in range(width):
            bit = start + i
            value |= ((data[bit >> 3] >> (bit & 7)) & 1) << i
        return value

    if bit_count < 7 or len(data) * 8 < bit_count:
        return None
    header = take(0, 7)
    width, scaled = header & 63, bool(header & 64)
    if not scaled or width < 1 or 7 + 3 * width != bit_count:
        return None
    out = []
    for k in range(3):
        raw = take(7 + k * width, width)
        if raw & (1 << (width - 1)):
            raw -= 1 << width
        out.append(raw / 100.0)
    return out[0], out[1], out[2]


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
    z: float | None = None     # world z at spawn, or None when the export gave none


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


def _dm(z: float) -> int:
    """World z (cm) as the blob stores a height: whole decimetres, no map offset."""
    return int(round(z / 10.0))


@dataclass
class _Equip:
    """An equippable (`Default__Ability_<Code>_<Name>_C`): the held item a player places or throws
    an ability with. Its owner comes from `votes`: the continuous effects played on a character
    pawn that name this equippable (slot -> count)."""
    guid: int
    t_ms: int
    code: str
    name: str
    x: float
    y: float
    votes: Counter = field(default_factory=Counter)
    slot: int | None = None
    how: str | None = None


@dataclass
class _Context:
    """An equippable's placement: the actors it lists as just created (`ActorListTransitionContext`)."""
    t_ms: int
    equip: _Equip
    listed: frozenset[int]


@dataclass
class _Effect:
    """An effect played on an actor (a character pawn or the planted spike), with the actors its
    context names and, for a continuous one, when it stopped. A one-shot (`oneshot`) has no stop."""
    t_ms: int
    actor: int
    effect_id: int | None
    container: int | None
    context: tuple[int, ...]
    stop_ms: int | None = None
    oneshot: bool = False


@dataclass
class Raw:
    actors: dict[int, _Actor]
    shots: list[dict]
    equips: list[_Equip] = field(default_factory=list)
    contexts: list[_Context] = field(default_factory=list)
    effects: list[_Effect] = field(default_factory=list)
    oneshots: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    wall_points: dict[int, list[tuple[int, float, float]]] = field(default_factory=lambda: defaultdict(list))
    wall_states: dict[int, list[tuple[int, bool]]] = field(default_factory=lambda: defaultdict(list))
    # Effects played on ability objects (a Chamber trap firing): guid -> times.
    object_effects: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    # One-shot effects played on ability objects (a Fault Line firing, a ZERO/point pulse): guid -> times.
    object_oneshots: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    # Lethal hits: (time, the killer's pawn).
    kills: list[tuple[int, int]] = field(default_factory=list)
    # Ability objects shot and destroyed (a lethal hit on the object itself): guid -> time.
    destroyed: dict[int, int] = field(default_factory=dict)
    # A plant starting: (time, the planter's pawn), an effect naming the carried spike on its character.
    plant_starts: list[tuple[int, int]] = field(default_factory=list)
    # Every effect on a Killjoy turret or alarmbot (KJ_DEVICE), for `device_off_spans`: guid ->
    # [(time, "play" | "stop" | "oneshot", effect id | None, container | None)], from its latest spawn.
    device_fx: dict[int, list[tuple[int, str, int | None, int | None]]] = field(default_factory=dict)
    # Where damage was dealt to a Barrier Mesh node (its `DamageOrigin`, decoded): the node's real place,
    # which its spawn row doesn't have. guid -> (x, y, z), the first one seen.
    origins: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    # The `FXC.Distance` of a one-shot an ability object played on itself: a ZERO/point pulse's suppress
    # radius in world units (1500 on every knife of the first KAY/O export). guid -> the first one seen.
    object_ranges: dict[int, float] = field(default_factory=dict)


EQUIPPABLE_ARCHETYPE = re.compile(r"^Default__Ability_([A-Za-z0-9]+)_(.+)_C$")
RPC_PLAY = "MulticastPlayContinuousEffect"
RPC_STOP = "MulticastStopContinuousEffect"
RPC_ONESHOT = "MulticastPlayOneShotEffect"
RPC_WALL_POINT = "MulticastAddSmokeScreenPoint"   # Viper's Toxic Screen, one per point laid
# The carried spike: its equippable is named by an effect on the planter's character as the plant begins.
BOMB_EQUIPPABLE_ARCHETYPE = "Default__BombEquippable_C"
_DEVICE_FX = {RPC_PLAY: "play", RPC_STOP: "stop", RPC_ONESHOT: "oneshot"}
_RAW_NEEDLES = _NEEDLES + (f'"{RPC_PLAY}"', f'"{RPC_STOP}"', f'"{RPC_ONESHOT}"', f'"{RPC_WALL_POINT}"',
                           '"Actors"', '"WallActivated"', '"DamageKilledTarget":true', '"DamageKilledTarget": true',
                           '"DamageOrigin"')


def packed_ints(data: bytes) -> list[int]:
    """Unreal's SerializeIntPacked values (7 bits a byte, the low bit says another byte follows)
    after an `ActorListTransitionContext`'s 3-byte header: the list's net GUIDs, after a leading
    count value. Checked on the Ascent export: every trapwire placement lists its second anchor."""
    out, i = [], 3
    while i < len(data):
        value, shift = 0, 0
        while i < len(data):
            byte = data[i]
            i += 1
            value |= (byte >> 1) << shift
            shift += 7
            if not byte & 1:
                break
        out.append(value)
    return out


def _context_values(payload: dict) -> tuple[int, ...]:
    return tuple(int(fv["Value"]) for fv in payload.get("FunctionObjectValues") or []
                 if isinstance(fv, dict) and isinstance(fv.get("Value"), int) and fv["Value"])


def read_raw(events_path: Path, pawns=frozenset()) -> Raw:
    """One streaming pass: ability actors (with instigators and closes), raw shot rows, and the
    evidence Stage 5 reads: equippables with their owners' effects and placements, continuous and
    one-shot effects on character pawns (`pawns`), effects on the planted spike and on ability
    objects, lethal hits, plant starts, and Viper's wall points and on/off states."""
    actors: dict[int, _Actor] = {}
    instigators: dict[int, int] = {}
    closes: dict[int, int] = {}
    raw = Raw(actors, [])
    equip_now: dict[int, _Equip] = {}      # the equippable alive under each GUID
    bombs: set[int] = set()
    carried: set[int] = set()              # the spike's equippable, alive under each GUID
    walls: set[int] = set()
    open_effects: dict[tuple[int, int], _Effect] = {}
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not any(needle in line for needle in _RAW_NEEDLES):
                continue
            data = json.loads(line)
            kind = data.get("type")
            t_ms = int(data.get("time_ms", 0))
            guid = int(data.get("actor_net_guid") or 0)
            if kind == "actor_spawned":
                archetype = str(data.get("archetype_path") or "")
                archetype = ARCHETYPE_ALIASES.get(archetype, archetype)
                raw.origins.pop(guid, None)
                equip_now.pop(guid, None)
                bombs.discard(guid)
                carried.discard(guid)
                walls.discard(guid)
                raw.device_fx.pop(guid, None)
                if KJ_DEVICE.match(archetype):
                    raw.device_fx[guid] = []
                if archetype == BOMB_EQUIPPABLE_ARCHETYPE:
                    carried.add(guid)
                    continue
                match = ABILITY_ARCHETYPE.match(archetype)
                location = data.get("location") or {}
                if location.get("x") is None:
                    continue
                held = EQUIPPABLE_ARCHETYPE.match(archetype)
                if held is not None:
                    equip = _Equip(guid, t_ms, *held.groups(), float(location["x"]), float(location["y"]))
                    equip_now[guid] = equip
                    raw.equips.append(equip)
                    continue
                if match is not None:
                    kind, code, name = match.groups()
                    kind = "GameObject" if kind == "Gameobject" else kind
                elif archetype == BOMB_ARCHETYPE:
                    kind, code, name = "Bomb", "", "Spike"
                    bombs.add(guid)
                else:
                    continue
                if name.endswith("SmokeScreenManager"):
                    walls.add(guid)
                rotation = data.get("rotation") or {}
                actors[guid] = _Actor(guid, t_ms, kind, code, name,
                                      float(location["x"]), float(location["y"]), rotation.get("yaw"),
                                      z=None if location.get("z") is None else float(location["z"]))
            elif kind == "actor_closed":
                closes.setdefault(guid, t_ms)
            elif kind == "valorant_shot_received":
                raw.shots.append(data)
            elif kind == "rpc_received":
                function = data.get("function_name")
                payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
                if guid in raw.device_fx and function in _DEVICE_FX:
                    raw.device_fx[guid].append((t_ms, _DEVICE_FX[function], payload.get("EffectId"),
                                                payload.get("EffectContainer")))
                if function == RPC_PLAY and (guid in pawns or guid in bombs):
                    context = _context_values(payload)
                    for value in context:
                        if guid in pawns and value in equip_now:
                            equip_now[value].votes[guid] += 1
                        if guid in pawns and value in carried:
                            raw.plant_starts.append((t_ms, guid))
                    if context:
                        effect = _Effect(t_ms, guid, payload.get("EffectId"), payload.get("EffectContainer"), context)
                        raw.effects.append(effect)
                        open_effects[(guid, effect.effect_id)] = effect
                elif function == RPC_PLAY and guid in actors:
                    raw.object_effects[guid].append(t_ms)
                elif function == RPC_ONESHOT and guid in pawns:
                    context = _context_values(payload)
                    if context:
                        raw.effects.append(_Effect(t_ms, guid, None, payload.get("EffectContainer"), context,
                                                   oneshot=True))
                elif function == RPC_ONESHOT and guid in actors and guid not in bombs:
                    raw.object_oneshots[guid].append(t_ms)
                    for value in payload.get("FunctionFloatValues") or []:
                        if isinstance(value, dict) and (value.get("Name") or {}).get("TagName") == "FXC.Distance" \
                                and isinstance(value.get("Value"), (int, float)):
                            raw.object_ranges.setdefault(guid, float(value["Value"]))
                elif function == RPC_STOP:
                    effect = open_effects.pop((guid, payload.get("EffectId")), None)
                    if effect is not None:
                        effect.stop_ms = t_ms
                elif function == RPC_ONESHOT and guid in bombs:
                    raw.oneshots[guid].append(t_ms)
                elif function == RPC_WALL_POINT and guid in walls:
                    point = payload.get("Translation") or {}
                    if point.get("x") is not None and point.get("y") is not None:
                        raw.wall_points[guid].append((t_ms, float(point["x"]), float(point["y"])))
                if payload.get("DamageKilledTarget") is True and payload.get("EventInstigatorPawn"):
                    raw.kills.append((t_ms, int(payload["EventInstigatorPawn"])))
                if function and function.startswith("MulticastNotifyDamage") and guid in actors                         and payload.get("DamageKilledTarget") is True:
                    raw.destroyed.setdefault(guid, t_ms)
                if function and function.startswith("MulticastNotifyDamage") and guid in actors \
                        and actors[guid].name == MESH_NODE and guid not in raw.origins:
                    origin = payload.get("DamageOrigin")
                    if isinstance(origin, dict) and origin.get("Data"):
                        try:
                            point = packed_vector(int(origin.get("BitCount") or 0), base64.b64decode(origin["Data"]))
                        except (ValueError, TypeError):
                            point = None
                        if point is not None:
                            raw.origins[guid] = point
            elif kind == "export_group_received":
                payload = data.get("payload")
                if not isinstance(payload, dict):
                    continue
                if data.get("is_actor") and payload.get("Instigator"):
                    instigators.setdefault(guid, int(payload["Instigator"]))
                if guid in walls and isinstance(payload.get("WallActivated"), bool):
                    raw.wall_states[guid].append((t_ms, payload["WallActivated"]))
                actor_list = payload.get("Actors")
                if guid in equip_now and isinstance(actor_list, dict) and actor_list.get("Data"):
                    try:
                        listed = frozenset(v for v in packed_ints(base64.b64decode(actor_list["Data"]))[1:] if v)
                    except (ValueError, TypeError):
                        listed = frozenset()
                    if listed:
                        raw.contexts.append(_Context(t_ms, equip_now[guid], listed))
    for guid, actor in actors.items():
        actor.instigator = instigators.get(guid)
        # An actor GUID can be reused after a close: only a close after the spawn counts.
        closed = closes.get(guid)
        actor.closed_ms = closed if closed is not None and closed >= actor.t_ms else None
    return raw


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
           positions_at, known: list[tuple[_Actor, int]], counts: Counter, claimed: int | None = None,
           caused: tuple[int, str] | None = None):
    """(slot, how, nearest evidence). In order: the replicated Instigator; the player possessing
    this pawn (a camera, a drone); the owner of the equippable that placed it (`claimed`, see
    `equippable_claims`); what caused it (`caused`: the trap that fired, the ult kill, the plant;
    see `caused_by`); the only player of that agent; for a projectile, the owner of the placed
    pawn it was fired from (a Spycam's dart); for a placed object, the owner of its
    parent (the object it spawned inside of, the projectile that landed as it spawned, or the
    anchor it was placed with); for a projectile only, a clear nearest player of that agent (a
    throw starts at its thrower; anything else can land on, or be set off by, someone else, as a
    drone dart that tags the other Sova did). Otherwise no owner: drawn neutral, never guessed."""
    candidates = slots_of_agent.get(agent, []) if agent else []
    near = _nearest(candidates, positions_at(actor.t_ms), actor.x, actor.y) if len(candidates) > 1 else None
    if actor.instigator is not None:
        slot = players.resolve(actor.instigator, actor.t_ms)
        if slot is not None:
            if near is not None and near[0] is not None:
                # How the nearest-player guess does where the owner is known.
                counts["nearest_check_" + ("agree" if near[0] == slot else "disagree")] += 1
            if claimed is not None:
                counts["equippable_check_" + ("agree" if claimed == slot else "disagree")] += 1
            return slot, "instigator", near
    possessors = {owner for _, _, owner in players.possession.get(actor.guid, [])}
    if len(possessors) == 1:
        return possessors.pop(), "possessed", near
    if claimed is not None:
        if near is not None and near[0] is not None:
            counts["nearest_check_" + ("agree" if near[0] == claimed else "disagree")] += 1
        return claimed, "equippable", near
    if caused is not None:
        return caused[0], caused[1], near
    if len(candidates) == 1:
        return candidates[0], "agent", near
    if (actor.code, actor.name) in TRIGGERED_BY or (actor.code, actor.name) in KILL_SPAWNED:
        return None, None, near   # only what set it off names its owner, never where it landed
    if actor.kind == "Projectile":
        # A shot from a placed pawn of the same agent (a Spycam's tracking dart) is its owner's.
        fired = [(math.hypot(parent.x - actor.x, parent.y - actor.y), slot) for parent, slot in known
                 if parent.kind == "Pawn" and parent.code == actor.code and parent.t_ms <= actor.t_ms
                 and (parent.closed_ms is None or parent.closed_ms >= actor.t_ms)
                 and math.hypot(parent.x - actor.x, parent.y - actor.y) <= INSIDE_UNITS]
        if fired:
            return min(fired)[1], "fired", near
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
    if actor.kind == "Projectile" and near is not None and near[0] is not None and near[1] <= NEAREST_MAX_UNITS and (
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
# Map control (revision 10): a pawn's facing over time (`yaws`) keeps a point when it turned this far.
YAW_STEP_DEG = 2

# An equippable's placement names what it created: its context lists the actors (a trapwire's
# second anchor, a thrown projectile) and arrives in the same tick as their spawns (a trapwire's
# first anchor, a smoke). Within LISTED_MS a listed actor is claimed; within SAME_TICK_MS an
# unlisted object of the same agent is, when only one owner placed something then.
LISTED_MS = 100
SAME_TICK_MS = 20
# Equippables a player is given together (a Cypher's trapwire, cage and camera at the round's
# start) spawn at the same moment and place: an unvoted one takes a voted sibling's owner.
SIBLING_MS = 50
SIBLING_UNITS = 150.0


def resolve_equippables(equips: list[_Equip], players: PlayerTable, agent_code: dict[str, str],
                        counts: Counter) -> None:
    """Sets each equippable's owner: the slot whose character played the most continuous effects
    naming it, when that slot plays the equippable's agent (a melee knife is anyone's); else a
    voted sibling's (see SIBLING_MS). Unresolved ones stay None."""
    for equip in equips:
        votes = Counter()
        for pawn, n in equip.votes.items():
            slot = players.pawn_slot.get(pawn)
            if slot is not None and agent_code.get(equip.code.lower()) == players.agents[slot]:
                votes[slot] += n
        if votes:
            equip.slot, equip.how = votes.most_common(1)[0][0], "effects"
    voted = [e for e in equips if e.slot is not None]
    for equip in equips:
        if equip.slot is not None:
            continue
        near = {e.slot for e in voted if e.code == equip.code and abs(e.t_ms - equip.t_ms) <= SIBLING_MS
                and math.hypot(e.x - equip.x, e.y - equip.y) <= SIBLING_UNITS}
        if len(near) == 1:
            equip.slot, equip.how = near.pop(), "sibling"
    for equip in equips:
        counts["equippables_" + (equip.how or "unowned")] += 1


def inherit_listed(contexts: list[_Context], owned: dict[int, int], counts: Counter) -> bool:
    """An unowned equippable that lists an owned actor is that owner's: a Sova's drone carries its
    own ability equippable, which lists the drone when it launches (and later places its darts), so
    Sova -> deploy-drone equippable -> drone -> drone equippable -> dart is one chain, even with two
    Sovas droning at once. `owned`: actor guid -> slot. Whether anything changed."""
    changed = False
    for context in contexts:
        if context.equip.slot is not None:
            continue
        owners = {owned[g] for g in context.listed if g in owned}
        if len(owners) == 1:
            context.equip.slot, context.equip.how = owners.pop(), "lists"
            counts["equippables_lists"] += 1
            changed = True
    return changed


def equippable_claims(actors: dict[int, _Actor], contexts: list[_Context]) -> dict[int, tuple[int, _Context]]:
    """{actor guid: (owner slot, the placement it came from)} for the ability actors an owned
    equippable placed: listed in its context, or spawned in the same tick as its only-owner
    context of that agent."""
    owned = sorted((c for c in contexts if c.equip.slot is not None), key=lambda c: c.t_ms)
    times = [c.t_ms for c in owned]
    out: dict[int, tuple[int, _Context]] = {}
    for actor in actors.values():
        lo = bisect.bisect_left(times, actor.t_ms - LISTED_MS)
        hi = bisect.bisect_right(times, actor.t_ms + LISTED_MS)
        near = owned[lo:hi]
        listed = [c for c in near if actor.guid in c.listed]
        if listed:
            best = min(listed, key=lambda c: abs(c.t_ms - actor.t_ms))
            out[actor.guid] = (best.equip.slot, best)
            continue
        same = [c for c in near if c.equip.code.lower() == actor.code.lower()
                and abs(c.t_ms - actor.t_ms) <= SAME_TICK_MS]
        if len({c.equip.slot for c in same}) == 1:
            out[actor.guid] = (same[0].equip.slot, same[0])
    return out


# Objects set off by another of the same agent's. A Chamber trap that fires (it plays an effect)
# shoots a dart 0.90-0.91 s later from about 150 units away, and its slow lands where the dart hits,
# 0.91-0.94 s after (checked on Summit and Abyss: every dart and trap slow follows exactly one trap).
# A slow with no trap firing comes from the ult: it appears in the same tick as the ult's kill.
# (code, name) -> (parent name, window ms).
TRIGGERED_BY = {("Deadeye", "E_Slow_Large"): ("E_Trap", 800, 1100),
                ("Deadeye", "4_Trap_Dart"): ("E_Trap", 800, 1100)}
KILL_SPAWNED = frozenset({("Deadeye", "E_Slow_Large")})
# The plant takes 4 s; its start is an effect on the planter's character naming the carried spike.
PLANT_MS = 4000
PLANT_SLACK_MS = 1000


def caused_by(actor: _Actor, known: list[tuple[_Actor, int]], raw: "Raw", players: PlayerTable,
              agent: str | None) -> tuple[int, str] | None:
    """(slot, how) for an object another event made: the trap that fired it ("triggered"), the ult
    kill in its tick ("ult_kill"), or, for the spike, the plant that began PLANT_MS before
    ("planted"). None when nothing (or more than one owner) fits."""
    if actor.kind == "Bomb":
        starts = [(abs(actor.t_ms - PLANT_MS - t), players.pawn_slot.get(pawn)) for t, pawn in raw.plant_starts
                  if actor.t_ms - PLANT_MS - PLANT_SLACK_MS <= t <= actor.t_ms]
        starts = [(dt, slot) for dt, slot in starts if slot is not None]
        return (min(starts)[1], "planted") if starts else None
    key = (actor.code, actor.name)
    parent = TRIGGERED_BY.get(key)
    if parent is not None:
        name, lo, hi = parent
        owners = {slot for other, slot in known if other.code == actor.code and other.name == name
                  and other.t_ms <= actor.t_ms and (other.closed_ms is None or other.closed_ms >= actor.t_ms - hi)
                  and any(lo <= actor.t_ms - t <= hi for t in raw.object_effects.get(other.guid, []))}
        if len(owners) == 1:
            return owners.pop(), "triggered"
    if key in KILL_SPAWNED:
        owners = {slot for t, pawn in raw.kills if abs(t - actor.t_ms) <= SAME_TICK_MS
                  and (slot := players.pawn_slot.get(pawn)) is not None and players.agents[slot] == agent}
        if len(owners) == 1:
            return owners.pop(), "ult_kill"
    return None


# The objects that reveal enemies (a recon bolt's ping, a haunt, a tracking dart, Neural Theft,
# Tejo's drone ping). The reveal itself is an effect played on each revealed player's character
# naming the revealer's character; its container ID varies by replay, so it is found by where it
# happens (`find_reveals`).
REVEAL_SOURCES = re.compile(r"^(Hunter_Q_SonarPing|Hunter_E_Drone_RevealDart|BountyHunter_E_LoSReveal.*|"
                            r"Cashew_4_SonarPing|Gumshoe_RemovableObject_GumshoeTrackingDart|Gumshoe_X_InterrogateHat)$")
# Neural Theft pings at +3.0 s and +7.0 s; a drone dart at +1.6 s and +2.8 s after the hit.
REVEAL_WINDOW_MS = 8000
REVEAL_MIN_PLAYS = 2
REVEAL_SHARE = 0.8
REVEAL_MIN_MS, REVEAL_MAX_MS, REVEAL_DEFAULT_MS = 1000, 6000, 2000
# A one-shot reveal is a ping: shown this long from the moment it happened.
PING_MS = 1000
SAME_REVEAL_MS = 50


def find_reveals(effects: list[_Effect], actors: dict[int, _Actor], players: PlayerTable,
                 code_of_agent: dict[str, str], counts: Counter, equips: list[_Equip] = (),
                 teams: dict[int, str | None] | None = None) -> list[dict]:
    """[{t_ms, t1_ms, by, target, code, name}]: each time a player was revealed by an enemy's reveal
    ability, at the moment it happened.

    A candidate is an effect on one player's character whose context names the revealer: their
    character (recon, haunt), their player state (a drone dart's pings) or their owned equippable
    (Neural Theft's pings name Cypher's ult). An effect container counts as the reveal when at
    least REVEAL_SHARE of its candidates (and REVEAL_MIN_PLAYS) start within REVEAL_WINDOW_MS after
    a reveal source of the revealer's agent spawned. Each is its own reveal: a continuous one until
    it stops, a one-shot a PING_MS ping, so a recon's two pulses and Neural Theft's two pings show
    when they happened instead of as one long reveal. With `teams` (slot -> side group), only an
    enemy of the revealer counts: Neural Theft also plays an effect naming Cypher on every player,
    teammates included."""
    sources = sorted((a.t_ms, a.code.lower(), a.code, a.name) for a in actors.values()
                     if a.code and REVEAL_SOURCES.match(f"{a.code}_{a.name}"))
    equip_slot: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for equip in equips:
        if equip.slot is not None:
            equip_slot[equip.guid].append((equip.t_ms, equip.slot))

    def revealer(value: int, t_ms: int) -> int | None:
        slot = players.pawn_slot.get(value)
        if slot is None:
            slot = players.other_slot.get(value)
        if slot is None:
            held = [s for t, s in equip_slot.get(value, []) if t <= t_ms]
            slot = held[-1] if held else None
        return slot

    by_container: dict[tuple, list[tuple[_Effect, int, int, tuple | None]]] = defaultdict(list)
    for effect in effects:
        target = players.pawn_slot.get(effect.actor)
        if target is None:
            continue
        revealers = [slot for c in effect.context if (slot := revealer(c, effect.t_ms)) is not None and slot != target]
        if not revealers:
            continue
        by = revealers[0]
        if teams and teams.get(by) is not None and teams.get(by) == teams.get(target):
            continue
        code = (code_of_agent.get(players.agents[by]) or "").lower()
        source = None
        i = bisect.bisect_right(sources, (effect.t_ms, "\uffff"))
        for t, source_code, raw_code, name in reversed(sources[max(0, i - 60):i]):
            if effect.t_ms - t > REVEAL_WINDOW_MS:
                break
            if source_code == code:
                source = (raw_code, name)
                break
        by_container[(effect.container, effect.oneshot)].append((effect, by, target, source))
    out = []
    for container, plays in by_container.items():
        hits = [p for p in plays if p[3] is not None]
        if len(hits) < REVEAL_MIN_PLAYS or len(hits) < REVEAL_SHARE * len(plays):
            continue
        counts["reveal_containers"] += 1
        for effect, by, target, (code, name) in hits:
            if effect.oneshot:
                t1 = effect.t_ms + PING_MS
            else:
                lasted = (effect.stop_ms - effect.t_ms) if effect.stop_ms is not None else REVEAL_DEFAULT_MS
                t1 = effect.t_ms + min(REVEAL_MAX_MS, max(REVEAL_MIN_MS, lasted))
            same = [r for r in out if r["by"] == by and r["target"] == target
                    and abs(r["t_ms"] - effect.t_ms) <= SAME_REVEAL_MS]
            if same:
                same[0]["t1_ms"] = max(same[0]["t1_ms"], t1)
                continue
            out.append({"t_ms": effect.t_ms, "t1_ms": t1, "by": by, "target": target, "code": code, "name": name})
    counts["reveals"] += len(out)
    return sorted(out, key=lambda r: (r["t_ms"], r["target"]))


def dart_tags(actors: dict[int, _Actor], claims: dict[int, tuple[int, "_Context"]], players: PlayerTable,
              counts: Counter) -> list[dict]:
    """A reveal source placed by an equippable that, in the same placement, lists a character
    other than its owner's: the player it stuck to (a drone dart's hit), revealed at that moment."""
    out = []
    for guid, (slot, context) in claims.items():
        actor = actors.get(guid)
        if actor is None or not REVEAL_SOURCES.match(f"{actor.code}_{actor.name}"):
            continue
        for value in sorted(context.listed):
            target = players.pawn_slot.get(value)
            if target is not None and target != slot:
                out.append({"t_ms": actor.t_ms, "t1_ms": actor.t_ms + PING_MS, "by": slot, "target": target,
                            "code": actor.code, "name": actor.name})
    counts["reveal_tags"] += len(out)
    return out


# ---------------------------------------------------------------- Stage 6: pops and statuses

# Abilities that go off in an instant (or pulse) but whose object lives on in the replay: they keep
# the times of the effects they play on themselves (`fx`, round seconds), which the viewer draws as
# pops. Measured: M-pulse pulses at +0/+2/+4 s, Fault Line fires at +1.1 s, ZERO/point at +1.0 s.
POP_ARCHETYPES = re.compile(r"^(Terra_C_TimeSlowGrenade_Explosion|Iris_Concuss|Cashew_Q_ShellShockGrenade|"
                            r"Breach_E_SweetSpotFissure|Breach_X_Shockwave|Breach_4_FusionBlast|Rift_Q_FlashBurst|"
                            r"Grenadier_E_SuppressionPulse|Smonk_Q_DecayExplosion)$")
FX_GAP_MS = 200

# The abilities whose hit puts a status on a player, and which status (see find_statuses).
# A trapwire tethers whoever walks into it (effects naming its anchors while they're caught), and if
# they don't break it, goes off: one-shots naming it on them (the reveal and concuss) and 5 damage.
TRIP = re.compile(r"^Gumshoe_4_TripWire(_SecondWire)?$")
STATUS_OBJECTS = (
    (TRIP, "tethered"),
    (re.compile(r"^Terra_C_TimeSlowGrenade_Explosion$"), "hindered"),
    (re.compile(r"^Iris_Concuss$"), "concussed"),
    (re.compile(r"^Cashew_Q_ShellShockGrenade$"), "concussed"),
    (re.compile(r"^Grenadier_E_SuppressionPulse$"), "suppressed"),
    # Undercut: effects naming the missile are the path warning (0.2-1 s, on anyone in its path);
    # the Fragile itself names Iso and lasts 4.0 s, so only the caster-named shape counts.
    (re.compile(r"^Sequoia_Q_FragileMissile_TrajectoryWarning$"), "fragile"),
    (re.compile(r"^(BountyHunter|Pine)_Q_Tether_SphereExpansion$"), "tethered"),
    (re.compile(r"^Aggrobot_E_DiscTurret_PowerWave$"), "concussed"),
    (re.compile(r"^Breach_(E_SweetSpotFissure|X_Shockwave)$"), "concussed"),
    (re.compile(r"^Rift_Q_FlashBurst$"), "concussed"),
    (re.compile(r"^Smonk_Q_DecayExplosion$"), "decayed"),
    (re.compile(r"^(Deadeye_E_Slow_Large|Thorne_4_SlowField_Production)$"), "slowed"),
)
CASTER_ONLY = re.compile(r"^Sequoia_Q_FragileMissile_TrajectoryWarning$")
# Agents in no archived replay yet (Neon, Deadlock, Harbor) and Astra: any of their objects that
# hits an enemy is shown as a status, named after the object, until a replay names them.
STATUS_AGENTS = frozenset({"Sprinter", "Cable", "Mage", "Rift"})
# A caster-named status (Iso's Fragile, Gekko's concuss) counts within this long after its source.
STATUS_WINDOW_MS = 3000
STATUS_SHARE = 0.8
STATUS_MIN_PLAYS = 2
STATUS_MERGE_MS = 300
# A one-shot marks only the moment of the hit: shown for STATUS_PING_MS, or for the status's own
# duration where it matters and the replay doesn't carry it (a game value, not measured).
STATUS_PING_MS = 1000
STATUS_KNOWN_MS = {"suppressed": 8000}


def status_of(actor: _Actor, oneshot: bool = False) -> str | None:
    key = f"{actor.code}_{actor.name}"
    if oneshot and TRIP.match(key):
        return "concussed"   # a trip's one-shots on a player are it going off
    for pattern, label in STATUS_OBJECTS:
        if pattern.match(key):
            return label
    if actor.code in STATUS_AGENTS and actor.kind != "Pawn":
        return "hit"
    return None


def pop_times(actor: _Actor, raw: "Raw") -> list[int]:
    """The distinct moments (ms) a pop ability played an effect on itself, FX_GAP_MS apart."""
    until = actor.closed_ms if actor.closed_ms is not None else float("inf")
    times = sorted(t for t in raw.object_effects.get(actor.guid, []) + raw.object_oneshots.get(actor.guid, [])
                   if actor.t_ms <= t <= until)
    out: list[int] = []
    for t in times:
        if not out or t - out[-1] >= FX_GAP_MS:
            out.append(t)
    return out


def find_statuses(effects: list[_Effect], actors: dict[int, _Actor], owner_of: dict[int, int | None],
                  players: PlayerTable, counts: Counter, equips: list[_Equip] = (),
                  teams: dict[int, str | None] | None = None) -> list[dict]:
    """[{t_ms, t1_ms, by, target, code, name, status, from}]: players an enemy's ability put a status
    on, from when to when. Two shapes, both effects played on the hit player's character:

    - naming the ability object ("object"): a live object whose status_of is known. The applier is
      the object's owner (`owner_of`, guid -> slot; None when unknown).
    - naming the caster ("caster"): the caster's character, player state or owned equippable, in a
      container that plays at least STATUS_SHARE of the time within STATUS_WINDOW_MS after one of
      that caster's status objects spawned (Iso's Fragile, Gekko's concuss). The source is that object.

    Never the applier themself, and with `teams`, only their enemies. A continuous effect lasts until
    it stops; a one-shot STATUS_PING_MS, or STATUS_KNOWN_MS for its status. Overlapping ones from the
    same source on the same player merge."""
    def alive(actor: _Actor, t_ms: int) -> bool:
        return actor.t_ms <= t_ms and (actor.closed_ms is None or t_ms <= actor.closed_ms)

    def enemies(by: int | None, target: int) -> bool:
        if by is not None and by == target:
            return False
        return not (teams and by is not None and teams.get(by) is not None and teams.get(by) == teams.get(target))

    def span(effect: _Effect, label: str) -> int:
        if effect.oneshot:
            return STATUS_KNOWN_MS.get(label, STATUS_PING_MS)
        return (effect.stop_ms - effect.t_ms) if effect.stop_ms is not None else STATUS_PING_MS

    equip_slot: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for equip in equips:
        if equip.slot is not None:
            equip_slot[equip.guid].append((equip.t_ms, equip.slot))

    def caster(value: int, t_ms: int) -> int | None:
        slot = players.pawn_slot.get(value)
        if slot is None:
            slot = players.other_slot.get(value)
        if slot is None:
            held = [s for t, s in equip_slot.get(value, []) if t <= t_ms]
            slot = held[-1] if held else None
        return slot

    sources = sorted((a.t_ms, a.guid) for a in actors.values() if a.code and status_of(a))
    source_times = [t for t, _ in sources]
    hits: list[tuple[int, int, int, int, str, str]] = []   # (t, t1, source guid, target, status, from)
    by_container: dict[tuple, list] = defaultdict(list)
    for effect in effects:
        target = players.pawn_slot.get(effect.actor)
        if target is None:
            continue
        named = [actors[v] for v in effect.context if v in actors and alive(actors[v], effect.t_ms)
                 and status_of(actors[v])]
        if named:
            source = named[0]
            label = status_of(source, effect.oneshot)
            if CASTER_ONLY.match(f"{source.code}_{source.name}"):
                continue
            if enemies(owner_of.get(source.guid), target):
                hits.append((effect.t_ms, effect.t_ms + span(effect, label), source.guid, target, label, "object"))
            continue
        casters = [slot for v in effect.context if (slot := caster(v, effect.t_ms)) is not None and slot != target]
        if not casters or not enemies(casters[0], target):
            continue
        i = bisect.bisect_right(source_times, effect.t_ms)
        source = None
        for t, guid in reversed(sources[max(0, i - 60):i]):
            if effect.t_ms - t > STATUS_WINDOW_MS:
                break
            if owner_of.get(guid) == casters[0]:
                source = guid
                break
        by_container[(effect.container, effect.oneshot)].append((effect, target, source))
    for plays in by_container.values():
        inside = [p for p in plays if p[2] is not None]
        if len(inside) < STATUS_MIN_PLAYS or len(inside) < STATUS_SHARE * len(plays):
            continue
        counts["status_containers"] += 1
        for effect, target, source in inside:
            label = status_of(actors[source])
            hits.append((effect.t_ms, effect.t_ms + span(effect, label), source, target, label, "caster"))
    # One run per (source, target, status): a trip's two anchors count as one source, its tether and
    # its going off as two statuses.
    merged: dict[tuple, list[list]] = defaultdict(list)
    for t, t1, source, target, label, how in sorted(hits):
        actor = actors[source]
        key = (actor.code, actor.name.replace("_SecondWire", ""), owner_of.get(source), target, label)
        runs = merged[key]
        if runs and t <= runs[-1][1] + STATUS_MERGE_MS:
            runs[-1][1] = max(runs[-1][1], t1)
        else:
            runs.append([t, t1, how])
    out = []
    for (code, name, by, target, label), runs in merged.items():
        for t, t1, how in runs:
            out.append({"t_ms": t, "t1_ms": t1, "by": by, "target": target, "code": code,
                        "name": name, "status": label, "from": how})
            counts[f"statuses_{how}"] += 1
    return sorted(out, key=lambda r: (r["t_ms"], r["target"]))


# A defuse takes 7 s; one held for DEFUSE_HALF_S or more leaves the spike half defused.
DEFUSE_ONESHOT_MS = 150


def defuse_attempts(bomb: _Actor, effects: list[_Effect], oneshots: dict[int, list[int]],
                    players: PlayerTable) -> list[tuple[int, int | None, int, bool]]:
    """[(start ms, stop ms | None, defuser slot, finished)]: the continuous effects played on the
    planted spike that name a player's character (the defuser). A finished defuse ends with a
    one-shot effect on the spike (checked on the Ascent export: every DB defuse)."""
    out = []
    until = bomb.closed_ms if bomb.closed_ms is not None else float("inf")
    for effect in effects:
        if effect.actor != bomb.guid or not bomb.t_ms <= effect.t_ms <= until:
            continue
        slots = [players.pawn_slot[c] for c in effect.context if c in players.pawn_slot]
        if not slots:
            continue
        done = effect.stop_ms is not None and any(abs(t - effect.stop_ms) <= DEFUSE_ONESHOT_MS
                                                  for t in oneshots.get(bomb.guid, []))
        out.append((effect.t_ms, effect.stop_ms, slots[0], done))
    return out


# Killjoy's turret and alarmbot switch off when she walks out of their range and on again when she
# comes back (2026-10-04, docs/superpowers/plans/2026-10-04-control-bugs-impl.md). The replay has no
# flag for it, only effects on the device, whose container IDs vary by replay, so it is found by shape
# (checked on every device of a Sunset and a Lotus replay, 80 in all):
# - a turret's *spawn effect* is the continuous one played as it spawns; an alarmbot's *boot effect* the
#   first one KJ_BOOT_MS after (a turret's own boot starts ~2 s in, an alarmbot's ~1.7 s). The latest play
#   in that effect's container is the device's watching effect.
# - Off at T: the watching effect stops and, in that same millisecond, a continuous effect in another
#   container starts while the watching container doesn't replay (a replay is it coming back on). Not
#   when that new effect stops within KJ_OFF_MIN_MS, or stops with a one-shot (an alarmbot going off), or
#   the device closes within KJ_OFF_MIN_MS (destroyed): those are a trigger, an attack or a kill.
# - On again: that new effect stops while the device is still open (a turret's containers replay then).
# KJ's death also switches them off; the engine already drops a dead owner's watchers.
KJ_DEVICE = re.compile(r"^Default__Pawn_Killjoy_(E_Turret|Q_StealthAlarmbot)_C$")
# First-pass figures from two replays; no alarmbot coming back on was seen in either.
KJ_SPAWN_FX_MS = 50
KJ_BOOT_MS = (1400, 2600)
KJ_OFF_MIN_MS = 1500
KJ_ONESHOT_MS = 10


def device_off_spans(fx: list[tuple[int, str, int | None, int | None]], spawn_ms: int, closed_ms: int | None,
                     turret: bool) -> list[list[int | None]]:
    """[[off ms, on ms | None], ...]: when a Killjoy device (`fx` from Raw.device_fx) was switched off; None
    is off until it closed."""
    mine = sorted((e for e in fx if e[0] >= spawn_ms and (closed_ms is None or e[0] <= closed_ms)),
                  key=lambda e: e[0])
    plays = [(t, eid, cont) for t, kind, eid, cont in mine if kind == "play"]
    lo, hi = (0, KJ_SPAWN_FX_MS) if turret else KJ_BOOT_MS
    first = next(((eid, cont) for t, eid, cont in plays if lo <= t - spawn_ms <= hi), None)
    if first is None:
        return []
    stops: dict = defaultdict(list)
    for t, kind, eid, _ in mine:
        if kind == "stop":
            stops[eid].append(t)
    oneshots = [t for t, kind, _, _ in mine if kind == "oneshot"]
    watching, container = {first[0]}, first[1]
    spans: list[list[int | None]] = []
    off: tuple[int, int] | None = None
    for t in sorted({e[0] for e in mine}):
        now = [e for e in mine if e[0] == t]
        started = [(eid, cont) for _, kind, eid, cont in now if kind == "play"]
        stopped = {eid for _, kind, eid, _ in now if kind == "stop"}
        replays = {eid for eid, cont in started if cont == container}
        if off is not None:
            if off[1] in stopped and (closed_ms is None or t < closed_ms):
                spans.append([off[0], t])
                off = None
                watching = replays or watching
            continue
        if watching & stopped and not replays:
            for eid, cont in started:
                if eid in watching:
                    continue
                ends = [s for s in stops.get(eid, []) if s > t]
                end = min(ends) if ends else None
                if end is not None and end - t <= KJ_OFF_MIN_MS:
                    continue
                if closed_ms is not None and closed_ms - t <= KJ_OFF_MIN_MS:
                    continue
                if end is not None and any(abs(o - end) <= KJ_ONESHOT_MS for o in oneshots):
                    continue
                off = (t, eid)
                break
        elif replays:
            watching = replays
    if off is not None:
        spans.append([off[0], None])
    return spans


WALL_MAX_POINTS = 48


WALL_START_UNITS = 150.0


def wall_line(points: list[tuple[int, float, float]], actor: _Actor) -> list[tuple[float, float]]:
    """Viper's wall as laid: its points in order, thinned evenly to WALL_MAX_POINTS (ends kept).
    The wall starts where it was cast (the manager's spawn point); a replay can miss the first
    points (round 2 of one export began 2,100 units out), so a line that starts further than
    WALL_START_UNITS away gets the cast point first."""
    until = actor.closed_ms if actor.closed_ms is not None else float("inf")
    mine = [(x, y) for t, x, y in points if actor.t_ms <= t <= until]
    if mine and math.hypot(mine[0][0] - actor.x, mine[0][1] - actor.y) > WALL_START_UNITS:
        mine.insert(0, (actor.x, actor.y))
    if len(mine) <= WALL_MAX_POINTS:
        return mine
    step = (len(mine) - 1) / (WALL_MAX_POINTS - 1)
    return [mine[round(i * step)] for i in range(WALL_MAX_POINTS)]


def wall_on(states: list[tuple[int, bool]], actor: _Actor, start: int, end: int) -> list[list[float | None]]:
    """[[on, off | None], ...] in round seconds: when the wall was up (`WallActivated`)."""
    until = actor.closed_ms if actor.closed_ms is not None else float("inf")
    spans, since = [], None
    for t, up in sorted(states):
        if not actor.t_ms <= t <= until:
            continue
        if up and since is None:
            since = t
        elif not up and since is not None:
            spans.append([since, t])
            since = None
    if since is not None:
        spans.append([since, None])
    out = []
    for lo, hi in spans:
        if hi is not None and hi < start:
            continue
        out.append([max(0.0, _seconds(lo, start)), None if hi is None else _seconds(min(hi, end), start)])
    return out


# KAY/O's ZERO/point once it has landed: it pulses once (a one-shot on itself, 1.000 s after landing on all nine
# knives of the first KAY/O export, carrying its radius), and each player it suppresses gets a one-shot naming it
# within the next KNIFE_HIT_MS (measured 0.10-0.30 s).
KNIFE_PULSE = re.compile(r"^Grenadier_E_SuppressionPulse$")
KNIFE_HIT_MS = 1000


def knife_pulse(actor: _Actor, raw: "Raw", players: PlayerTable, owner: int | None) -> dict:
    """{"state", "evidence", "diagnostics", "t_ms", "range", "hits"} for a landed knife. `completed` only when
    its own pulse one-shot is in the export: a knife destroyed before it pulsed, or one with no such row, is
    `unknown` and proves nothing about who it hit. `hits`: the slots (never its owner) that a one-shot naming
    it was played on within KNIFE_HIT_MS of the pulse."""
    until = actor.closed_ms if actor.closed_ms is not None else float("inf")
    fired = sorted(t for t in raw.object_oneshots.get(actor.guid, []) if actor.t_ms <= t <= until)
    if not fired:
        return {"state": "unknown", "evidence": None, "diagnostics": ["no_pulse_row"], "t_ms": None, "range": None,
                "hits": []}
    t_ms = fired[0]
    hits = sorted({players.pawn_slot[e.actor] for e in raw.effects
                   if e.oneshot and actor.guid in e.context and e.actor in players.pawn_slot
                   and t_ms <= e.t_ms <= t_ms + KNIFE_HIT_MS and players.pawn_slot[e.actor] != owner})
    return {"state": "completed", "evidence": "pulse_oneshot", "diagnostics": [], "t_ms": t_ms,
            "range": raw.object_ranges.get(actor.guid), "hits": hits}


def mesh_arms(root: _Actor, actors: dict[int, _Actor], raw: "Raw") -> tuple[list[tuple[float, float, float, int | None]], int]:
    """A Barrier Mesh's arms: ([(x, y, z, the ms the arm went | None)], nodes with no known place). An arm
    runs from the root to one of the nodes spawned with it; the node's place is where damage was dealt to it
    (`Raw.origins`). It went when its node closed or was destroyed before the root did; None: it lasted as
    long as the root. A node nothing ever damaged has no place and is counted, never guessed."""
    arms, missing = [], 0
    until = root.closed_ms
    for node in sorted(actors.values(), key=lambda a: a.guid):
        if node.name != MESH_NODE or node.code != root.code or abs(node.t_ms - root.t_ms) > SAME_TICK_MS \
                or math.hypot(node.x - root.x, node.y - root.y) > MESH_NODE_UNITS:
            continue
        place = raw.origins.get(node.guid)
        if place is None:
            missing += 1
            continue
        ends = [t for t in (node.closed_ms, raw.destroyed.get(node.guid)) if t is not None and t >= node.t_ms]
        gone = min(ends) if ends else None
        if gone is not None and until is not None and gone >= until:
            gone = None
        arms.append((*place, gone))
    return arms, missing


def mesh_up_ms(root: _Actor, raw: "Raw") -> int | None:
    """When a Barrier Mesh became solid: its root's first continuous effect MESH_FORM_MS after it landed."""
    lo, hi = MESH_FORM_MS
    plays = [t for t in raw.object_effects.get(root.guid, []) if lo <= t - root.t_ms <= hi]
    return min(plays) if plays else None


def _thrown(actor: _Actor, actors: dict[int, _Actor]) -> _Actor | None:
    """The same agent's projectile that closed within LANDING_MS of this object's spawn: the throw
    it came from (drawn as an arc from the thrower), whatever the owner evidence said."""
    if actor.kind == "Projectile" or not actor.code:
        return None
    hits = [(abs(p.closed_ms - actor.t_ms), p.guid, p) for p in actors.values()
            if p.kind == "Projectile" and p.code == actor.code and p.closed_ms is not None
            and abs(p.closed_ms - actor.t_ms) <= LANDING_MS and p.t_ms <= actor.t_ms]
    return min(hits)[2] if hits else None


def _control_inputs(entry: dict, actor: _Actor, players: PlayerTable, pawn_yaws, game_map: MapInfo,
                    start: int, end: int, counts: Counter) -> None:
    """Map control's inputs on a pawn row (docs/replay-map-control-plan.md, "Inputs the blob lacks"):
    `possessed`, when a player was in it (a camera, a drone), from PlayerTable.possession, in round
    seconds clipped to the round; and `yaws`, its minimap facing over time from its movement rows, a
    point when it turned YAW_STEP_DEG or more, at most one per PATH_STEP_MS."""
    until = min(actor.closed_ms, end) if actor.closed_ms is not None else end
    spans = []
    for lo, hi, _ in players.possession.get(actor.guid, []):
        if (hi is not None and hi < max(actor.t_ms, start)) or lo > until:
            continue
        spans.append([max(0.0, _seconds(max(lo, start), start)), None if hi is None else _seconds(min(hi, until), start)])
    entry["possessed"] = spans
    if spans:
        counts["pawns_possessed"] += 1
    if pawn_yaws is None:
        return
    yaws, last_t, last_yaw = [], -PATH_STEP_MS, None
    for t_ms, yaw in pawn_yaws(actor.guid) or []:
        if not max(actor.t_ms, start) <= t_ms <= until or t_ms - last_t < PATH_STEP_MS:
            continue
        mapped = game_map.yaw_to_map(float(yaw))
        if last_yaw is None or abs((mapped - last_yaw + 180) % 360 - 180) >= YAW_STEP_DEG:
            yaws.append([_seconds(t_ms, start), mapped])
            last_t, last_yaw = t_ms, mapped
    entry["yaws"] = yaws
    counts["pawn_yaw_points"] += len(yaws)


def build_extras(events_path: Path, players: PlayerTable, windows: list[tuple[int, int, int]],
                 game_map: MapInfo, agents_by_code: dict[str, str], positions_at, pawn_path=None,
                 teams: dict[int, str | None] | None = None, pawn_yaws=None) -> Extras:
    """`positions_at(t_ms)` -> {slot: (x, y)}: each player's world position at that moment, from
    the full movement stream (buy phase included, where the round tracks don't reach).
    `pawn_path(guid)` -> [(t_ms, x, y[, z | None])]: a non-player pawn's movement (a drone), or None.
    `pawn_yaws(guid)` -> [(t_ms, world yaw)]: its facing, for map control's `yaws`, or None.
    `teams`: slot -> the condenser's side group (None when unresolved), so reveals count enemies only."""
    raw = read_raw(events_path, frozenset(players.pawn_slot))
    actors, raw_shots = raw.actors, raw.shots
    agent_code = {code.lower(): name for code, name in agents_by_code.items()}
    code_of_agent = {name: code for code, name in agents_by_code.items()}
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
    for equip in raw.equips:
        equip.code, equip.name = normalize_archetype(equip.code, equip.name, agents_by_code)
    resolve_equippables(raw.equips, players, agent_code, counts)
    claims = equippable_claims(actors, raw.contexts)
    possessed = {guid: spans[0][2] for guid, spans in players.possession.items()
                 if len({owner for _, _, owner in spans}) == 1}
    for _ in range(4):
        owned = {**possessed, **{guid: slot for guid, (slot, _) in claims.items()}}
        if not inherit_listed(raw.contexts, owned, counts):
            break
        claims = equippable_claims(actors, raw.contexts)
    # A placement's actors, to find a trapwire's second anchor from its first.
    placed: dict[int, list[_Actor]] = defaultdict(list)
    for guid, (_, context) in claims.items():
        placed[id(context)].append(actors[guid])
    # Actors whose owner came from evidence (or, for a projectile, a clear nearest thrower).
    known: list[tuple[_Actor, int]] = []
    owner_of: dict[int, int | None] = {}
    for actor in sorted(actors.values(), key=lambda a: (a.t_ms, a.guid)):
        agent = agent_code.get(actor.code.lower()) if actor.code else None
        claim = claims.get(actor.guid)
        # Owners first, for every actor, so a buy-phase throw can still hand its owner on.
        caused = caused_by(actor, known, raw, players, agent) if claim is None else None
        slot, owner_by, near = _owner(actor, agent, slots_of_agent, players, positions_at, known, counts,
                                      claimed=claim[0] if claim else None, caused=caused)
        if slot is not None and (owner_by != "nearest" or actor.kind == "Projectile"):
            known.append((actor, slot))
        owner_of[actor.guid] = slot
        if actor.name == MESH_NODE and actor.code == "Cable":
            counts["mesh_nodes"] += 1      # on its root's row (`arms`), never a row of its own
            continue
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
        if actor.z is not None:
            entry["z"] = _dm(actor.z)
        if owner_by == "nearest":
            # The guess's evidence: world units to the owner and to the other player of that agent.
            entry["owner_d"] = round(near[1])
            entry["other_d"] = None if near[2] is None else round(near[2])
        if actor.yaw is not None:
            entry["yaw"] = game_map.yaw_to_map(float(actor.yaw))
        parts = [actor]
        if claim is not None and actor.name == "4_TripWire":
            # The trapwire's far end: the second anchor its placement listed.
            ends = [a for a in placed[id(claim[1])] if a.name == "4_TripWire_SecondWire"]
            if ends:
                entry["end"] = list(game_map.to_uv(ends[0].x, ends[0].y))
                if ends[0].z is not None:
                    entry["end_z"] = _dm(ends[0].z)
                counts["wires_paired"] += 1
                parts.append(ends[0])
        # Gone before its object closes: shot and destroyed (for a trapwire, either anchor). Going off
        # doesn't use utility up: a trip, a Vyse flash or a Killjoy turret stays until it's destroyed.
        gone = [raw.destroyed[a.guid] for a in parts if a.guid in raw.destroyed and a.t_ms <= raw.destroyed[a.guid]]
        if gone and (actor.closed_ms is None or min(gone) < actor.closed_ms):
            entry["gone"] = max(0.0, _seconds(min(min(gone), end), start))
            counts["abilities_gone_early"] += 1
        if actor.kind == "Bomb":
            attempts = defuse_attempts(actor, raw.effects, raw.oneshots, players)
            if attempts:
                entry["defuses"] = [[_seconds(t0, start), None if t1 is None else _seconds(min(t1, end), start),
                                     by, done] for t0, t1, by, done in attempts]
                counts["defuse_attempts"] += len(attempts)
        if actor.name.endswith("SmokeScreenManager"):
            line = wall_line(raw.wall_points.get(actor.guid, []), actor)
            if line:
                entry["points"] = [list(game_map.to_uv(x, y)) for x, y in line]
                entry["on"] = wall_on(raw.wall_states.get(actor.guid, []), actor, start, end)
                counts["walls_drawn"] += 1
        if actor.name == MESH_ROOT and actor.code == "Cable":
            arms, lost = mesh_arms(actor, actors, raw)
            entry["arms"] = []
            for x, y, z, gone_ms in arms:
                arm = [*game_map.to_uv(x, y), _dm(z),
                       None if gone_ms is None else max(0.0, _seconds(min(gone_ms, end), start))]
                entry["arms"].append(arm)
            if lost:
                entry["arms_missing"] = lost
                counts["mesh_arms_without_a_place"] += lost
            up = mesh_up_ms(actor, raw)
            if up is not None and up <= end:
                entry["on"] = [[max(0.0, _seconds(up, start)), entry["t1"]]]
            else:
                counts["mesh_without_a_form_time"] += 1
            counts["mesh_walls"] += 1
        if actor.guid in raw.device_fx:
            spans = device_off_spans(raw.device_fx[actor.guid], actor.t_ms, actor.closed_ms,
                                     turret=actor.name.endswith("E_Turret"))
            off = [[max(0.0, _seconds(lo, start)), None if hi is None else _seconds(min(hi, end), start)]
                   for lo, hi in spans if lo <= end and (hi is None or hi >= start)]
            if off:
                entry["off"] = off
                counts["kj_off_spans"] += len(off)
        if POP_ARCHETYPES.match(f"{actor.code}_{actor.name}"):
            fx = [max(0.0, _seconds(t, start)) for t in pop_times(actor, raw) if t <= end]
            if fx:
                entry["fx"] = fx
                counts["abilities_with_fx"] += 1
        if KNIFE_PULSE.match(f"{actor.code}_{actor.name}"):
            pulse = knife_pulse(actor, raw, players, slot)
            if pulse["state"] == "completed":
                entry["pulse"] = {"t": max(0.0, _seconds(min(pulse["t_ms"], end), start)), "hits": pulse["hits"]}
                if pulse["range"] is not None:
                    entry["pulse"]["r"] = int(round(pulse["range"] * abs(game_map.x_mult) * fmt.UV_SCALE))
            entry["activation"] = {"state": pulse["state"], "evidence": pulse["evidence"],
                                   "targets_complete": pulse["state"] == "completed", "diagnostics": pulse["diagnostics"]}
            counts[f"knife_pulses_{pulse['state']}"] += 1
        throw = _thrown(actor, actors)
        if throw is not None:
            entry["thrown"] = {"t0": _seconds(throw.t_ms, start), "t1": _seconds(throw.closed_ms, start),
                               **dict(zip(("u", "v"), game_map.to_uv(throw.x, throw.y)))}
            if throw.z is not None:
                entry["thrown"]["z"] = _dm(throw.z)
            counts["abilities_thrown"] += 1
        if actor.kind == "Pawn" and pawn_path is not None:
            last_t, points = -PATH_STEP_MS, []
            until = min(actor.closed_ms, end) if actor.closed_ms is not None else end
            for t_ms, x, y, *height in pawn_path(actor.guid) or []:
                if max(actor.t_ms, start) <= t_ms <= until and t_ms - last_t >= PATH_STEP_MS:
                    point = [_seconds(t_ms, start), *game_map.to_uv(x, y)]
                    if height and height[0] is not None:
                        point.append(_dm(height[0]))     # [t, u, v, z]; three values when it had no height
                    points.append(point)
                    last_t = t_ms
            if points:
                entry["path"] = points
                counts["pawn_paths"] += 1
        if actor.kind == "Pawn":
            _control_inputs(entry, actor, players, pawn_yaws, game_map, start, end, counts)
        out.rounds.setdefault(n, {"abilities": [], "shots": []})["abilities"].append(entry)
        counts["abilities"] += 1

    reveals = find_reveals(raw.effects, actors, players, code_of_agent, counts, raw.equips, teams)
    for tag in dart_tags(actors, claims, players, counts):
        if not any(r["by"] == tag["by"] and r["target"] == tag["target"] and abs(r["t_ms"] - tag["t_ms"]) <= SAME_REVEAL_MS
                   for r in reveals):
            reveals.append(tag)
    for reveal in sorted(reveals, key=lambda r: (r["t_ms"], r["target"])):
        n = _round_of(reveal["t_ms"], windows)
        if n is None:
            continue
        start, _, end = windows[n - 1]
        out.rounds.setdefault(n, {"abilities": [], "shots": []}).setdefault("reveals", []).append({
            "t0": _seconds(reveal["t_ms"], start), "t1": _seconds(min(reveal["t1_ms"], end), start),
            "slot": reveal["by"], "target": reveal["target"], "code": reveal["code"], "name": reveal["name"]})

    statuses = find_statuses(raw.effects, actors, owner_of, players, counts, raw.equips, teams)
    for status in statuses:
        if status["code"] == "Gumshoe" and status["name"] == "4_TripWire" and status["status"] == "concussed":
            # Going off reveals the player it caught (it's also a concuss).
            n = _round_of(status["t_ms"], windows)
            if n is not None:
                start, _, end = windows[n - 1]
                out.rounds.setdefault(n, {"abilities": [], "shots": []}).setdefault("reveals", []).append({
                    "t0": _seconds(status["t_ms"], start), "t1": _seconds(min(status["t_ms"] + PING_MS, end), start),
                    "slot": status["by"], "target": status["target"], "code": "Gumshoe", "name": "4_TripWire"})
                counts["reveals_trip"] += 1
    for status in statuses:
        n = _round_of(status["t_ms"], windows)
        if n is None:
            continue
        start, _, end = windows[n - 1]
        out.rounds.setdefault(n, {"abilities": [], "shots": []}).setdefault("statuses", []).append({
            "t0": _seconds(status["t_ms"], start), "t1": _seconds(min(status["t1_ms"], end), start),
            "slot": status["by"], "target": status["target"], "code": status["code"], "name": status["name"],
            "status": status["status"], "from": status["from"]})

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
    the file, never a copy of it. Other pawns also keep their yaw at the same samples (`yaws`), for
    map control's facing of a camera, turret or drone."""

    STEP_MS = 100

    def __init__(self, movement, players: PlayerTable):
        self.rows: dict[int, tuple[list[int], list[tuple[float, float]]]] = {}
        by_slot: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
        self.others: dict[int, list[tuple[int, float, float, float | None]]] = defaultdict(list)
        self.other_yaws: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for row in movement:
            data = row.data
            position = data.get("position") or {}
            if data.get("error_sentinel") or position.get("x") is None or position.get("y") is None:
                continue
            pawn = int(data.get("shooter_character_net_guid") or data.get("actor_net_guid") or 0)
            slot = players.pawn_slot.get(pawn)
            target = by_slot[slot] if slot is not None else self.others[pawn]
            if not target or row.time_ms - target[-1][0] >= self.STEP_MS:
                point = (row.time_ms, float(position["x"]), float(position["y"]))
                if slot is None:
                    # a drone's height rides along (revision 11), None when the row had none
                    z = position.get("z")
                    point = (*point, None if z is None else float(z))
                target.append(point)
                if slot is None and data.get("yaw") is not None:
                    self.other_yaws[pawn].append((row.time_ms, float(data["yaw"])))
        for slot, samples in by_slot.items():
            samples.sort()
            self.rows[slot] = ([t for t, _, _ in samples], [(x, y) for _, x, y in samples])
        for samples in self.others.values():
            samples.sort(key=lambda sample: sample[:3])     # a missing height (None) doesn't order
        for samples in self.other_yaws.values():
            samples.sort()

    def path(self, guid: int) -> list[tuple[int, float, float, float | None]]:
        return self.others.get(guid, [])

    def yaws(self, guid: int) -> list[tuple[int, float]]:
        return self.other_yaws.get(guid, [])

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
    for reveal in round_extras.get("reveals", []):
        rest = {k: v for k, v in reveal.items() if k not in ("t0", "slot")}
        out.append({"k": "reveal", "t": reveal["t0"], "by": reveal["slot"], **rest})
    for status in round_extras.get("statuses", []):
        rest = {k: v for k, v in status.items() if k not in ("t0", "slot")}
        out.append({"k": "status", "t": status["t0"], "by": status["slot"], **rest})
    for shot in round_extras.get("shots", []):
        rest = {k: v for k, v in shot.items() if k not in ("t", "slot")}
        out.append({"k": "shot", "t": shot["t"], "by": shot["slot"], **rest})
    return out


def rounds_extras(util: list[dict]) -> dict:
    """The inverse of `util_entries`: a stored round's `{"abilities", "shots"}` (the viewer's and
    the tests' shape)."""
    abilities, shots, reveals, statuses = [], [], [], []
    for entry in util:
        rest = {k: v for k, v in entry.items() if k not in ("k", "t", "by")}
        if entry.get("k") == "ability":
            abilities.append({"t0": entry["t"], "slot": entry["by"], **rest})
        elif entry.get("k") == "shot":
            shots.append({"t": entry["t"], "slot": entry["by"], **rest})
        elif entry.get("k") == "reveal":
            reveals.append({"t0": entry["t"], "slot": entry["by"], **rest})
        elif entry.get("k") == "status":
            statuses.append({"t0": entry["t"], "slot": entry["by"], **rest})
    out = {"abilities": abilities, "shots": shots}
    if reveals:
        out["reveals"] = reveals
    if statuses:
        out["statuses"] = statuses
    return out
