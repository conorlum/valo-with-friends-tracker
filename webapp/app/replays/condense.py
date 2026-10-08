"""Condenses a parser export into per-round JSON v1 blobs plus a private player table.

Pure Python over an `Export` (see contract.py); the same code serves local ingest and
the upload worker. The output holds:

- `rounds`: round number -> the v1 blob (format.py), in replay terms only: slots, agents,
  unlabelled side groups A/B, replay-clock times. No names, Subjects or PUUIDs;
- `players`: the private slot table (Subject, agent, side group), never served. A player
  is a character pawn (or the pawns sharing one player state); the Subject is None when
  no decoded player state names it, which is the case for the first real export;
- `link_inputs`: what a later link needs without the parser (decoded round results, start
  positions and kills in slot terms). No Subjects;
- `report`: counts, checks and sizes for the preview and the Stage 1a gate.

Its rules were chosen before any real export existed and were frozen at the Stage 1b gate
from six competitive matches (docs/replay-viewer-plan.md, 2026-09-27; approved as AFK run
decision D7).
"""

from __future__ import annotations

import bisect
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from app.replays import format as fmt
from app.replays.header import header_match_uuid
from app.replays.contract import (
    CHARACTER_PLAYER_STATE,
    GAME_STATE_PATH,
    PLAYER_STATE_PATH,
    RPC_DAMAGE,
    RPC_KILLED_ENEMY,
    RPC_PHASE_BEGIN,
    RPC_PHASE_ENDED,
    RPC_RESURRECT,
    RPC_SET_PHASE,
    ContractError,
    Export,
    ParserPin,
    check_manifest,
    classify_diagnostics,
    fold_export_groups,
    load_export,
    load_export_streaming,
    load_pin,
    map_codes_in_file,
)

# EAresGamePhase (parser source): RoundStarting=3, InRound=4, RoundEnding=5.
PHASE_IN_ROUND = 4
PHASE_ROUND_ENDING = 5

# Segment rules from the plan, frozen at the Stage 1b gate (2026-09-27).
SEGMENT_GAP_S = 1.0
TELEPORT_UNITS = 600.0
# 1a gate limits, reported (not enforced) by the condenser.
MIN_ALIVE_COVERAGE = 0.90
MAX_TRACK_GAP_S = 3.0
SPAWN_CLUSTER_RADIUS = 1500.0
START_POSITION_WINDOW_S = 2.0
# Pass-6 rules the 1b gate freezes. A ClientGamePhaseEnded must sit within
# this of its ClientGamePhaseBegin (the Swiftplay export shows equal times), and a decoded
# RoundNumber is 0-based, as D8 assumes for RoundResults (finding 16).
PHASE_ENDED_TOLERANCE_MS = 0
ROUND_NUMBER_BASE = 0

MAP_PATH = re.compile(r"/Game/Maps/([^/.]+)/")
# A player character's archetype, e.g. `Default__Wushu_PC_C` (Jett). Clove's post-death
# form (`Default__Smonk_PostDeath_PC_C`) has an underscore in its code, so it isn't one.
CHARACTER_ARCHETYPE = re.compile(r"(?:^|\.)Default__([A-Za-z0-9]+)_PC_C$")


@dataclass(frozen=True)
class MapInfo:
    name: str
    code: str
    x_mult: float
    y_mult: float
    x_add: float
    y_add: float

    def to_uv(self, x: float, y: float) -> tuple[int, int]:
        u = y * self.x_mult + self.x_add
        v = x * self.y_mult + self.y_add
        return _to_int_uv(u), _to_int_uv(v)

    def yaw_to_map(self, yaw_degrees: float) -> int:
        """World yaw -> minimap degrees (0 = +u, 90 = +v), through the same affine transform."""
        rad = math.radians(yaw_degrees)
        du = math.sin(rad) * self.x_mult  # u follows world y
        dv = math.cos(rad) * self.y_mult  # v follows world x
        return int(round(math.degrees(math.atan2(dv, du)))) % 360


def _to_int_uv(value: float) -> int:
    return max(0, min(fmt.UV_SCALE, int(round(value * fmt.UV_SCALE))))


def load_maps(static_dir: Path = fmt.STATIC_DIR) -> dict[str, MapInfo]:
    table = json.loads((static_dir / "data" / "maps.json").read_text(encoding="utf-8"))
    return {name: MapInfo(name, e["code"], e["xMultiplier"], e["yMultiplier"], e["xScalarToAdd"], e["yScalarToAdd"])
            for name, e in table.items()}


def load_agents(static_dir: Path = fmt.STATIC_DIR) -> dict[str, str]:
    return json.loads((static_dir / "data" / "agents.json").read_text(encoding="utf-8"))


@dataclass
class CondensedReplay:
    match_uuid: str
    map_name: str
    game_branch: str
    source_sha256: str
    recipe: str
    hz: int
    rounds: dict[int, dict]
    players: list[dict]
    link_inputs: dict
    report: dict = field(default_factory=dict)

    @property
    def round_count(self) -> int:
        return len(self.rounds)

    def encoded_rounds(self) -> dict[int, bytes]:
        return {n: fmt.encode_blob(blob) for n, blob in self.rounds.items()}


@dataclass(frozen=True)
class Sample:
    t_ms: int
    x: float
    y: float
    z: float | None      # world z, or None when the parser gave none (never read as 0)
    yaw: float
    pawn: int


# ---------------------------------------------------------------- discovery


def discover_map(export: Export, maps: dict[str, MapInfo],
                 vrf_map_codes: dict[str, int] | None = None) -> tuple[MapInfo | None, dict]:
    """The map from actor paths in the export, else from the `.vrf`'s own bytes.

    The first real export named no map at all; its `.vrf` did (`/Game/Maps/Infinity/Infinity`).
    """
    by_code = {m.code: m for m in maps.values()}
    seen: Counter[str] = Counter()
    for row in export.events:
        data = row.data
        for key in ("actor_path", "archetype_path", "object_path", "outer_path"):
            path = data.get(key)
            if isinstance(path, str):
                for code in MAP_PATH.findall(path):
                    seen[code] += 1
    known = sorted(code for code in seen if code in by_code)
    evidence = {"source": "export", "codes_seen": dict(seen.most_common(5)), "known": known}
    if len(known) == 1:
        return by_code[known[0]], evidence
    if not known and vrf_map_codes:
        known = sorted(code for code in vrf_map_codes if code in by_code)
        evidence = {"source": "vrf", "codes_seen": dict(Counter(vrf_map_codes).most_common(5)), "known": known}
        if len(known) == 1:
            return by_code[known[0]], evidence
    return None, evidence


def character_code(row: dict) -> str | None:
    """The agent code name of a player character's `actor_spawned` row, else None."""
    path = row.get("archetype_path")
    match = CHARACTER_ARCHETYPE.search(path) if isinstance(path, str) else None
    return match.group(1) if match else None


# ---------------------------------------------------------------- players


@dataclass
class PlayerTable:
    subjects: list[str | None]        # per slot; None when no decoded player state names one
    agents: list[str]                 # per slot, display name
    pawn_slot: dict[int, int]         # player character guid -> slot; tracks follow these
    other_slot: dict[int, int]        # player states -> slot, for kills and revives only
    pawn_changes: dict[int, list[tuple[int, int]]]  # slot -> [(spawn ms, pawn guid)]
    gone_ms: dict[int, int]           # slot -> when its last pawn closed, for a player who left
    spawn_xy: dict[int, tuple[float, float]] = field(default_factory=dict)  # slot -> first pawn's spawn point
    # P-b: a possessed pawn that isn't one of the player's own characters (a drone, a pet)
    # resolves only while possessed: pawn -> [(from ms, to ms | None, slot)].
    possession: dict[int, list[tuple[int, int | None, int]]] = field(default_factory=dict)
    ownership: dict = field(default_factory=dict)  # counts for the report
    # slot -> [(from ms, to ms)]: a player who dropped and reconnected, from their pawn's close
    # to their replacement pawn's spawn (read_lifecycle).
    away: dict[int, list[tuple[int, int]]] = field(default_factory=dict)

    def away_at(self, slot: int, t_ms: int) -> tuple[int, int] | None:
        return next(((lo, hi) for lo, hi in self.away.get(slot, []) if lo <= t_ms < hi), None)

    def resolve(self, guid: int, t_ms: int | None = None) -> int | None:
        slot = self.pawn_slot.get(guid)
        if slot is not None:
            return slot
        slot = self.other_slot.get(guid)
        if slot is not None:
            return slot
        for lo, hi, owner in self.possession.get(guid, []):
            if t_ms is not None and lo <= t_ms and (hi is None or t_ms < hi):
                return owner
        return None


def build_players(export: Export, agents_by_code: dict[str, str]) -> PlayerTable:
    """Ten players from the character pawns.

    A pawn belongs to the player state its character names (`PlayerState`), or that names
    it (`SpawnedCharacter`, the legacy BombPlayerState shape); a pawn with neither is a
    player of its own. The Subject comes from any decoded export group on that player
    state that carries one.
    """
    spawned_at: dict[int, int] = {}
    spawned_xy: dict[int, tuple[float, float]] = {}
    codes: dict[int, str] = {}
    # valorant-api.com and the game disagree on case ("Aggrobot" vs the pawn's "AggroBot_PC_C").
    table_key = {k.lower(): k for k in agents_by_code}
    for row in export.of_type("actor_spawned"):
        code = character_code(row.data)
        if code is None:
            continue
        code = table_key.get(code.lower(), code)
        if code not in agents_by_code:
            raise ContractError("unknown_agent", f"agent code name {code!r} is not in agents.json")
        guid = int(row.data["actor_net_guid"])
        codes[guid] = code
        spawned_at.setdefault(guid, row.time_ms)
        location = row.data.get("location") or {}
        if location.get("x") is not None and location.get("y") is not None:
            spawned_xy.setdefault(guid, (float(location["x"]), float(location["y"])))

    # P-b: every non-null claim is kept with its time; contradictions refuse (first-claim-wins
    # would silently pick one side of a conflict).
    claims: dict[int, list[tuple[int, int, str]]] = defaultdict(list)  # pawn -> [(ms, player state, source)]
    subjects_seen: dict[int, list[tuple[int, str]]] = defaultdict(list)  # player state -> [(ms, Subject)]
    possessions: dict[int, list[tuple[int, int | None]]] = defaultdict(list)  # player state -> [(ms, pawn)]
    for row in export.of_type("export_group_received"):
        payload = row.data.get("payload")
        if not isinstance(payload, dict):
            continue
        guid = int(row.data.get("actor_net_guid") or 0)
        if guid in codes and payload.get(CHARACTER_PLAYER_STATE):
            claims[guid].append((row.time_ms, int(payload[CHARACTER_PLAYER_STATE]), "PlayerState"))
        if payload.get("Subject"):
            subjects_seen[guid].append((row.time_ms, str(payload["Subject"]).lower()))
        if payload.get("SpawnedCharacter") and int(payload["SpawnedCharacter"]) in codes:
            claims[int(payload["SpawnedCharacter"])].append((row.time_ms, guid, "SpawnedCharacter"))
        if "PossessedCharacter" in payload:
            pawn = int(payload["PossessedCharacter"] or 0) or None
            possessions[guid].append((row.time_ms, pawn))
            if pawn in codes:
                claims[pawn].append((row.time_ms, guid, "PossessedCharacter"))
    state_of: dict[int, int] = {}      # pawn -> player-state guid
    for pawn, pawn_claims in claims.items():
        states = {state for _, state, _ in pawn_claims}
        if len(states) > 1:
            sources = sorted({source for _, _, source in pawn_claims})
            raise ContractError("ownership", f"character {pawn} is claimed by {len(states)} player states "
                                             f"({', '.join(sources)})")
        state_of[pawn] = states.pop()
    subject_of_state: dict[int, str] = {}
    for state, seen in subjects_seen.items():
        if len({subject for _, subject in seen}) > 1:
            raise ContractError("ownership", f"player state {state}'s Subject changes")
        subject_of_state[state] = seen[0][1]

    groups: dict[tuple[str, int], list[int]] = defaultdict(list)
    for pawn in sorted(codes, key=lambda g: (spawned_at[g], g)):
        state = state_of.get(pawn)
        groups[("state", state) if state is not None else ("pawn", pawn)].append(pawn)
    if len(groups) != 10:
        raise ContractError("players", f"{len(groups)} players (character pawns by player state), expected 10")

    # Slots in order of each player's first pawn (spawn time, then guid):
    # stable for one export and reveals nothing. Tracks follow the player's own characters
    # only; a possessed drone or pet just helps resolve kills. Side group A holds slot 0.
    ordered = sorted(groups.items(), key=lambda kv: (spawned_at[kv[1][0]], kv[1][0]))
    subjects: list[str | None] = []
    agents: list[str] = []
    pawn_slot: dict[int, int] = {}
    other_slot: dict[int, int] = {}
    pawn_changes: dict[int, list[tuple[int, int]]] = {}
    gone_ms: dict[int, int] = {}
    spawn_xy: dict[int, tuple[float, float]] = {}
    for slot, ((kind, key), pawns) in enumerate(ordered):
        names = {agents_by_code[codes[p]] for p in pawns}
        if len(names) != 1:
            raise ContractError("agent", f"a player has {len(names)} agents, expected 1")
        agents.append(names.pop())
        subjects.append(subject_of_state.get(key) if kind == "state" else None)
        for pawn in pawns:
            pawn_slot[pawn] = slot
        if kind == "state":
            other_slot[key] = slot
        pawn_changes[slot] = [(spawned_at[p], p) for p in pawns]
        if pawns[0] in spawned_xy:
            spawn_xy[slot] = spawned_xy[pawns[0]]
    state_slot = {key: slot for slot, ((kind, key), _) in enumerate(ordered) if kind == "state"}
    # Possession as intervals: from each PossessedCharacter update to that state's next one.
    possession: dict[int, list[tuple[int, int | None, int]]] = defaultdict(list)
    intervals = 0
    for state, updates in possessions.items():
        if state not in state_slot:
            continue
        for (lo, pawn), following in zip(updates, [*updates[1:], None]):
            if pawn is None or pawn in pawn_slot:
                continue
            possession[pawn].append((lo, following[0] if following else None, state_slot[state]))
            intervals += 1
    named = [s for s in subjects if s]
    if len(set(named)) != len(named):
        raise ContractError("subjects", "two players share a Subject")
    ownership = {"claims": sum(len(c) for c in claims.values()), "pawns": len(codes),
                 "possession_intervals": intervals}
    return PlayerTable(subjects, agents, pawn_slot, other_slot, pawn_changes, gone_ms, spawn_xy,
                       dict(possession), ownership)


# ---------------------------------------------------------------- utility (W-e)

# The parser's typed utility rows: cast rows name the caster, hit rows the target, and both
# carry the ability actor's GUID, which ties hits to their cast.
UTIL_KINDS = {
    "valorant_flash_cast": ("flash", "flash_actor_net_guid", "flash_kind"),
    "valorant_nearsight_cast": ("nearsight", "nearsight_actor_net_guid", "nearsight_kind"),
}
UTIL_HITS = {"valorant_flash_player_hit": "flash_actor_net_guid",
             "valorant_nearsight_player_hit": "nearsight_actor_net_guid"}


def _hit_duration(data: dict) -> float | None:
    """A hit's own duration in seconds: a flash's blind (`initial_duration_seconds`, which already
    says how badly the victim was flashed), a nearsight's configured length; None when unknown or
    lasting until removed (Reyna's Leer)."""
    if data.get("type") == "valorant_flash_player_hit":
        value = data.get("initial_duration_seconds")
    elif data.get("duration_until_removed"):
        value = None
    else:
        value = data.get("configured_duration_seconds")
    return round(float(value), 3) if isinstance(value, (int, float)) else None


# A flash's explosion and path (2026-10-05). The export mixes units: a path sample from the actor's spawn
# transform is in world units (cm), one from its replicated movement is in metres (the same instant of one
# Curveball: spawn (4787.3, 8758.9, 265.2), replicated (47.87, 87.59, 2.65)). So a position's unit follows the
# source that produced it, never its size: nothing is multiplied by 100 because it looks small.
SAMPLE_SCALE = {"spawn_transform": 1.0, "replicated_movement": 100.0}
# An explosion's evidence -> the scale of its own location: a flash source actor's spawn point is in world
# units; `stop_projectile_rpc` copies the projectile's last replicated sample, so its unit and its age are
# that sample's (`_pop`).
POP_SOURCE_SCALE = {"skye_flash_source": 1.0, "vyse_flash_source": 1.0}
# Evidence that the flash really went off. Anything else (an actor that only closed) proves nothing.
COMPLETED_EVIDENCE = frozenset({"stop_projectile_rpc", *POP_SOURCE_SCALE})
# A copied sample this old at the explosion is where the flash was, not where it popped: the position is
# left out (a Curveball's only replicated sample is its cast).
POP_FRESH_MS = 250
POP_MATCH_UNITS = 0.011          # the export rounds a replicated sample to 0.01
UTIL_PATH_STEP_MS = 100
UTIL_PATH_MAX_POINTS = 40
# A hit that names no cast (`unresolved`, or an actor with no cast row) this long after a flash popped may be
# that flash's: its target list is then not proven complete.
HIT_WINDOW_MS = 1000


@dataclass(frozen=True)
class UtilCast:
    t_ms: int
    kind: str
    ability: str
    by: int
    x: float | None
    y: float | None
    targets: tuple[int, ...]
    # Map control's input (revision 10): each hit as (target slot, hit ms, duration s | None).
    hits: tuple[tuple[int, int, float | None], ...] = ()
    # 2026-10-05, flashes only (a nearsight row is as before): the ability actor (the cast's identity), the
    # cast's height, the explosion (`_pop`), the flight, and what proves it went off and hit only these.
    actor: int = 0
    z: float | None = None
    pop: dict | None = None
    path: tuple[tuple[int, float, float, float | None], ...] = ()
    activation: dict | None = None


def _resolve_actor(players: PlayerTable, character, player_state, t_ms: int) -> int | None:
    """A caster or target: their character pawn (or a pawn they possess) first, else their player state."""
    slot = players.resolve(int(character or 0), t_ms) if character else None
    if slot is None and player_state:
        slot = players.other_slot.get(int(player_state))
    return slot


def read_util(export: Export, players: PlayerTable) -> tuple[list[UtilCast], dict]:
    """Flash and nearsight casts with their targets (W-e), and counts for the report.

    The rule (approved 2026-09-25): a cast whose caster resolves to no player is dropped and counted; a hit
    whose target resolves to no player is left out of `targets` and counted; a hit with no
    cast is counted. A caster or target who had left the match refuses, like a kill would (P-c).
    """
    counts: Counter[str] = Counter()
    # An ability actor's GUID can be used again later in the match: every row belongs to that actor's latest
    # cast at or before it (a row before its actor's first cast, to that first cast).
    cast_times: dict[int, list[int]] = defaultdict(list)
    for row in export.events:
        spec = UTIL_KINDS.get(row.data.get("type"))
        if spec is not None:
            cast_times[int(row.data.get(spec[1]) or 0)].append(row.time_ms)

    def cast_key(actor: int, t_ms: int) -> tuple[int, int]:
        times = cast_times.get(actor)
        if not times:
            return actor, -1
        i = bisect.bisect_right(times, t_ms) - 1
        return actor, times[max(0, i)]

    hits: dict[tuple[int, int], list[int]] = defaultdict(list)
    timed: dict[tuple[int, int], set[tuple[int, int, float | None]]] = defaultdict(set)
    loose: list[int] = []                                  # times of hits that name no cast
    unresolved: dict[tuple[int, int], int] = defaultdict(int)   # a cast's hits whose target resolved to nobody
    samples: dict[tuple[int, int], list[tuple[int, float, float, float | None, str, tuple]]] = defaultdict(list)
    pops: dict[tuple[int, int], tuple[int, dict, str]] = {}
    for row in export.events:
        kind = row.data.get("type")
        if kind == "valorant_flash_path_updated":
            data = row.data
            where, source = data.get("location") or {}, str(data.get("source") or "")
            scale = SAMPLE_SCALE.get(source)
            if scale is None or where.get("x") is None or where.get("y") is None:
                counts["path_samples_without_a_known_source"] += 1
                continue
            z = where.get("z")
            samples[cast_key(int(data.get("flash_actor_net_guid") or 0), row.time_ms)].append(
                (row.time_ms, float(where["x"]) * scale, float(where["y"]) * scale,
                 None if z is None else float(z) * scale, source,
                 (float(where["x"]), float(where["y"]), None if z is None else float(z))))
        elif kind == "valorant_flash_exploded":
            data = row.data
            key = cast_key(int(data.get("flash_actor_net_guid") or 0), row.time_ms)
            pops.setdefault(key, (row.time_ms, data.get("location") or {}, str(data.get("evidence") or "")))
        if kind in UTIL_HITS:
            data = row.data
            actor = int(data.get(UTIL_HITS[kind]) or 0)
            named = actor in cast_times and data.get("correlation") != "unresolved"
            if not named and kind == "valorant_flash_player_hit":
                loose.append(row.time_ms)
            slot = _resolve_actor(players, data.get("target_character_net_guid"),
                                  data.get("target_player_state_net_guid"), row.time_ms)
            if slot is None:
                counts["unresolved_targets"] += 1
                unresolved[cast_key(actor, row.time_ms)] += 1
                continue
            gone = players.gone_ms.get(slot)
            if gone is not None and row.time_ms >= gone:
                raise ContractError("lifecycle", f"a utility hit on slot {slot} at {row.time_ms} ms, after that "
                                                 f"player left at {gone} ms")
            away = players.away_at(slot, row.time_ms)
            if away is not None:
                raise ContractError("lifecycle", f"a utility hit on slot {slot} at {row.time_ms} ms, while that "
                                                 f"player was disconnected ({away[0]}-{away[1]} ms)")
            key = cast_key(actor, row.time_ms)
            hits[key].append(slot)
            timed[key].add((slot, row.time_ms, _hit_duration(data)))
            counts["hits"] += 1
    loose.sort()
    casts: list[UtilCast] = []
    cast_actors: set[int] = set()
    for row in export.events:
        spec = UTIL_KINDS.get(row.data.get("type"))
        if spec is None:
            continue
        short, actor_key, kind_key = spec
        data = row.data
        slot = _resolve_actor(players, data.get("caster_character_net_guid"),
                              data.get("caster_player_state_net_guid"), row.time_ms)
        actor = int(data.get(actor_key) or 0)
        cast_actors.add(actor)
        if slot is None:
            counts["unresolved_casters"] += 1
            continue
        gone = players.gone_ms.get(slot)
        if gone is not None and row.time_ms >= gone:
            raise ContractError("lifecycle", f"a {short} by slot {slot} at {row.time_ms} ms, after that player left "
                                             f"at {gone} ms")
        away = players.away_at(slot, row.time_ms)
        if away is not None:
            raise ContractError("lifecycle", f"a {short} by slot {slot} at {row.time_ms} ms, while that player was "
                                             f"disconnected ({away[0]}-{away[1]} ms)")
        location = data.get("location") or {}
        ability = str(data.get(kind_key) or "")
        key = (actor, row.time_ms)
        more: dict = {}
        if short == "flash":
            flight = sorted(samples.get(key, []), key=lambda s: s[0])
            pop = _pop(pops.get(key), flight, counts)
            until = pop["t_ms"] if pop is not None else None
            state = "completed" if pop is not None and pop["evidence"] in COMPLETED_EVIDENCE else "unknown"
            diagnostics = []
            if pop is None:
                diagnostics.append("no_explosion_row")
            elif state != "completed":
                diagnostics.append(f"explosion_evidence_{pop['evidence'] or 'missing'}")
            if unresolved.get(key):
                diagnostics.append("unresolved_target")
            if until is not None:
                lo = bisect.bisect_left(loose, row.time_ms)
                if lo < len(loose) and loose[lo] <= until + HIT_WINDOW_MS:
                    diagnostics.append("unattributed_hit_in_window")
            z = location.get("z")
            more = {"actor": actor, "z": None if z is None else float(z), "pop": pop,
                    "path": _thin_path([s[:4] for s in flight if until is None or s[0] <= until]),
                    "activation": {"state": state, "evidence": pop["evidence"] if pop is not None else None,
                                   "targets_complete": state == "completed" and not diagnostics,
                                   "diagnostics": diagnostics}}
            counts[f"flash_activation_{state}"] += 1
            counts["flash_targets_complete"] += bool(more["activation"]["targets_complete"])
        casts.append(UtilCast(row.time_ms, short, ability if ability.replace("_", "").isalnum() else "", slot,
                              location.get("x"), location.get("y"), tuple(sorted(set(hits.get(key, [])))),
                              tuple(sorted(timed.get(key, ()), key=lambda h: (h[1], h[0], -1.0 if h[2] is None else h[2]))),
                              **more))
        counts[short] += 1
    counts["orphan_hits"] = sum(len(slots) for (actor, _), slots in hits.items() if actor not in cast_actors)
    return casts, dict(sorted(counts.items()))


def _pop(found: tuple[int, dict, str] | None, flight: list, counts: Counter) -> dict | None:
    """A flash's explosion as {"t_ms", "evidence"} plus, when its place is known in a known unit and is fresh,
    {"x", "y", "z", "src", "age_ms"}. A flash-source explosion is at the source's own spawn point. One by
    `stop_projectile_rpc` repeats a replicated path sample: it is placed only when that sample is found (so
    its unit is known) and is at most POP_FRESH_MS old; otherwise it has a time and no place, and says why
    (`place`: "stale" or "unverified")."""
    if found is None:
        return None
    t_ms, where, evidence = found
    out: dict = {"t_ms": t_ms, "evidence": evidence}
    if where.get("x") is None or where.get("y") is None:
        out["place"] = "unverified"
        return out
    x, y, z = float(where["x"]), float(where["y"]), where.get("z")
    scale = POP_SOURCE_SCALE.get(evidence)
    if scale is not None:
        out.update({"x": x * scale, "y": y * scale, "z": None if z is None else float(z) * scale,
                    "src": "flash_source", "age_ms": 0})
        counts["flash_pops_placed"] += 1
        return out
    same = [s for s in flight if s[4] == "replicated_movement" and s[0] <= t_ms
            and abs(s[5][0] - x) <= POP_MATCH_UNITS and abs(s[5][1] - y) <= POP_MATCH_UNITS]
    if not same:
        out["place"] = "unverified"
        counts["flash_pops_unverified"] += 1
        return out
    sample = same[-1]
    age = t_ms - sample[0]
    if age > POP_FRESH_MS:
        out.update({"place": "stale", "age_ms": age})
        counts["flash_pops_stale"] += 1
        return out
    out.update({"x": sample[1], "y": sample[2], "z": sample[3], "src": "replicated_movement", "age_ms": age})
    counts["flash_pops_placed"] += 1
    return out


def _thin_path(points: list[tuple[int, float, float, float | None]]) -> tuple:
    """A flight's samples at most one per UTIL_PATH_STEP_MS and UTIL_PATH_MAX_POINTS in all, ends kept."""
    kept: list = []
    for point in points:
        if not kept or point[0] - kept[-1][0] >= UTIL_PATH_STEP_MS:
            kept.append(point)
    if points and kept[-1] is not points[-1]:
        kept.append(points[-1])
    if len(kept) > UTIL_PATH_MAX_POINTS:
        step = (len(kept) - 1) / (UTIL_PATH_MAX_POINTS - 1)
        kept = [kept[round(i * step)] for i in range(UTIL_PATH_MAX_POINTS)]
    return tuple(kept)


# ---------------------------------------------------------------- lifecycle (P-c)


# A close for any reason but dormancy can mean the player left (the parser
# writes `destroyed`, `dormancy`, or a raw number; ChannelCloseReason.cs). Frozen by finding 25.
DORMANT_CLOSE = "dormancy"


@dataclass
class Lifecycle:
    left: dict[int, int]                          # slot -> when their last pawn closed for good
    unobserved: dict[int, list[tuple[int, int]]]  # slot -> [(from ms, to ms)] alive but unseen
    report: dict
    away: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # slot -> [(close ms, rejoin ms)]


def read_lifecycle(export: Export, players: PlayerTable, last_decided_ms: int, end_ms: int) -> Lifecycle:
    """Classifies every player pawn's channel close (P-c).

    A close followed by a reopen of the same GUID, or a dormant close, is an unobserved span:
    the player is alive and unseen. A player left only when their last pawn closes for another
    reason with no reopen, before the final round was decided (later closes are the match
    ending). A pawn that closes that way and is replaced by a later pawn is a disconnect: the
    player is away from the close until the new pawn spawns. A competitive player keeps one pawn
    all match, and the first real one (9b73ca26, 2026-09-28) was a player with no pawn for a
    whole round, whom tracker.gg shows with no buy and no score.
    """
    spawns: dict[int, list[int]] = defaultdict(list)
    for row in export.of_type("actor_spawned"):
        guid = int(row.data.get("actor_net_guid") or 0)
        if guid in players.pawn_slot:
            spawns[guid].append(row.time_ms)
    closes_by_reason: Counter[str] = Counter()
    left: dict[int, int] = {}
    unobserved: dict[int, list[tuple[int, int]]] = defaultdict(list)
    away: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in export.of_type("actor_closed"):
        guid = int(row.data.get("actor_net_guid") or 0)
        slot = players.pawn_slot.get(guid)
        if slot is None:
            continue
        reason = row.data.get("reason")
        if not reason:
            raise ContractError("lifecycle", f"character {guid}'s channel close at {row.time_ms} ms has no reason")
        closes_by_reason[str(reason)] += 1
        reopen = next((t for t in spawns[guid] if t > row.time_ms), None)
        if reopen is not None:
            unobserved[slot].append((row.time_ms, reopen))
            continue
        if reason == DORMANT_CLOSE:
            unobserved[slot].append((row.time_ms, end_ms))
            continue
        later_pawns = [spawned for spawned, pawn in players.pawn_changes[slot] if pawn != guid and spawned > row.time_ms]
        if later_pawns:
            if row.time_ms <= last_decided_ms:
                away[slot].append((row.time_ms, min(later_pawns)))
            continue
        if guid != players.pawn_changes[slot][-1][1] or row.time_ms > last_decided_ms:
            continue
        left[slot] = row.time_ms
    report = {"closes_by_reason": dict(sorted(closes_by_reason.items())),
              "left": {str(slot): _seconds(t, 0) for slot, t in sorted(left.items())},
              "away": {str(slot): [[_seconds(lo, 0), _seconds(hi, 0)] for lo, hi in spans]
                       for slot, spans in sorted(away.items())},
              "unobserved_spans": sum(len(spans) for spans in unobserved.values())}
    return Lifecycle(left, dict(unobserved), report, dict(away))


# ---------------------------------------------------------------- rounds


@dataclass
class GameState:
    match_uuid: str | None
    # (InRound ms, RoundEnding ms = t_decided, playback end ms = t_end) per played round
    windows: list[tuple[int, int, int]]
    dropped_final_start: int | None
    round_number_at_start: list[int | None]
    round_results: dict[int, dict]          # decoded index -> merged fields
    round_results_fallback: bool
    phase_ended_checked: bool = False
    dropped_final_end: int | None = None    # where the dropped final round's window ends


def _history_at(history: list[tuple[int, object]], t_ms: int) -> object | None:
    value = None
    for when, item in history:
        if when > t_ms:
            break
        value = item
    return value


def read_game_state(export: Export) -> GameState:
    rows = list(export.export_groups(GAME_STATE_PATH))
    folded = fold_export_groups(iter(rows))
    history: dict[str, list] = defaultdict(list)
    for state in folded.values():
        for key, items in state.history.items():
            history[key].extend(items)
    for items in history.values():
        items.sort(key=lambda item: item[0])

    match_ids = {str(v).lower() for _, v in history.get("MatchID", []) if v}
    if len(match_ids) > 1:
        raise ContractError("match_id", "more than one MatchID in one replay")

    transitions = [(t, int(p)) for t, p in history.get("Phase", []) if p is not None]
    begins = [(row.time_ms, int(row.data["payload"]["NewPhase"])) for row in export.rpcs(RPC_PHASE_BEGIN)
              if isinstance(row.data.get("payload"), dict) and row.data["payload"].get("NewPhase") is not None]
    transitions += begins
    transitions += [(row.time_ms, int(row.data["payload"]["NewPhase"])) for row in export.rpcs(RPC_SET_PHASE)
                    if isinstance(row.data.get("payload"), dict) and row.data["payload"].get("NewPhase") is not None]
    transitions.sort(key=lambda item: item[0])
    collapsed: list[tuple[int, int]] = []
    for t, phase in transitions:
        if not collapsed or collapsed[-1][1] != phase:
            collapsed.append((t, phase))

    phase_ended_checked = check_phase_ended(begins, export)
    windows, open_start, dropped_end = phase_windows(collapsed, export.end_ms)

    fallback = False
    results: dict[int, dict] = {}
    for _, value in history.get("RoundResults", []):
        if not isinstance(value, list):
            fallback = True
            continue
        for item in value:
            if not isinstance(item, dict) or "RoundNumber" not in item:
                fallback = True
                continue
            merged = results.setdefault(int(item["RoundNumber"]), {})
            for key in ("WinningTeam", "WinningTeamRole", "RoundResult"):
                if item.get(key) is not None:
                    merged[key] = item[key]

    numbers = [_history_at(history.get("RoundNumber", []), start) for start, _, _ in windows]
    for n, number in enumerate(numbers, 1):
        if number is not None and int(number) != n - 1 + ROUND_NUMBER_BASE:
            raise ContractError("round_number", f"played round {n} carries RoundNumber {number}, expected "
                                                f"{n - 1 + ROUND_NUMBER_BASE} (base {ROUND_NUMBER_BASE})")
    return GameState(match_ids.pop() if match_ids else None, windows, open_start, numbers, results, fallback,
                     phase_ended_checked, dropped_end)


def phase_windows(collapsed: list[tuple[int, int]], end_ms: int) -> tuple[list[tuple[int, int, int]], int | None,
                                                                            int | None]:
    """Validated round windows from the collapsed phase sequence (P-a).

    A round is a 4 → 5 cycle with nothing between; its playback runs on to the next phase
    after the 5 (the recording's end for the last round). Refuses an orphan 5, a second 4
    while one is open, and a 4 meeting another phase while a later 4 exists. Returns
    (windows, the dropped final round's start, where that round's window ends).
    """
    fours = [i for i, (_, phase) in enumerate(collapsed) if phase == PHASE_IN_ROUND]
    last_four = fours[-1] if fours else -1
    windows: list[tuple[int, int, int]] = []
    open_start: int | None = None
    pending: tuple[int, int] | None = None
    dropped: int | None = None
    dropped_end: int | None = None
    for i, (t, phase) in enumerate(collapsed):
        if pending is not None:
            windows.append((*pending, t))
            pending = None
        if phase == PHASE_IN_ROUND:
            if open_start is not None:
                raise ContractError("phase_cycle", f"a second InRound at {t} ms while the one at {open_start} ms "
                                                   f"is open")
            open_start = t
        elif phase == PHASE_ROUND_ENDING:
            if open_start is None:
                raise ContractError("phase_cycle", f"RoundEnding at {t} ms with no open InRound")
            pending = (open_start, t)
            open_start = None
        elif open_start is not None:
            if i < last_four:
                raise ContractError("phase_cycle", f"InRound at {open_start} ms meets phase {phase} at {t} ms "
                                                   f"before any RoundEnding")
            # The final InRound (no later 4) may end in another phase, a
            # surrender's match end (finding 26). It is dropped and reported; a 5 after it
            # is then an orphan and refuses.
            dropped = open_start
            open_start = None
            dropped_end = t
    if pending is not None:
        windows.append((*pending, max(end_ms, pending[1])))
    if open_start is not None:
        dropped, dropped_end = open_start, max(end_ms, open_start)
    elif dropped is not None and dropped_end is None:
        dropped_end = max(end_ms, dropped)
    return windows, dropped, dropped_end


def check_phase_ended(begins: list[tuple[int, int]], export: Export) -> bool:
    """Cross-checks each ClientGamePhaseBegin against its ClientGamePhaseEnded (P-a).

    Over the raw rows: the k-th Ended pairs with the k-th Begin within
    PHASE_ENDED_TOLERANCE_MS, and for k >= 1 its OldPhase is Begin[k-1]'s NewPhase (the first
    Ended closes a phase that began before the recording). The last Begin may lack its Ended.
    Returns False when the export has no Ended RPC at all (nothing to check against).
    """
    ends = [(row.time_ms, (row.data.get("payload") or {}).get("OldPhase")) for row in export.rpcs(RPC_PHASE_ENDED)]
    if not ends:
        return False
    if len(ends) not in (len(begins), len(begins) - 1):
        raise ContractError("phase_ended", f"{len(ends)} ClientGamePhaseEnded for {len(begins)} ClientGamePhaseBegin")
    for k, (t_end, old) in enumerate(ends):
        t_begin, _ = begins[k]
        if abs(t_end - t_begin) > PHASE_ENDED_TOLERANCE_MS:
            raise ContractError("phase_ended", f"phase end {k} at {t_end} ms is {abs(t_end - t_begin)} ms from its "
                                               f"begin")
        if k >= 1 and (old is None or int(old) != begins[k - 1][1]):
            raise ContractError("phase_ended", f"phase end {k} closes phase {old!r}, but phase "
                                               f"{begins[k - 1][1]} was running")
    return True


# ---------------------------------------------------------------- movement


def read_movement(export: Export, players: PlayerTable) -> tuple[dict[int, list[Sample]], dict, set]:
    """Each slot's samples, the row counts, and the channels that carried kept samples (P-f)."""
    by_slot: dict[int, list[Sample]] = defaultdict(list)
    counts = Counter()
    channels: set = set()
    for row in export.movement:
        data = row.data
        if data.get("error_sentinel"):
            counts["error_sentinel"] += 1
            continue
        pawn = int(data.get("shooter_character_net_guid") or data.get("actor_net_guid") or 0)
        slot = players.pawn_slot.get(pawn)
        position = data.get("position") or {}
        if slot is None or position.get("x") is None or position.get("y") is None:
            counts["unresolved"] += 1
            continue
        counts["kept"] += 1
        channels.add(data.get("channel"))
        z = position.get("z")
        by_slot[slot].append(Sample(row.time_ms, float(position["x"]), float(position["y"]),
                                    None if z is None else float(z), float(data.get("yaw") or 0.0), pawn))
    # Contract order per slot: by time, ties in file order (a no-op when the rows came sorted;
    # the streaming loader reads movement in file order, W-b).
    for samples in by_slot.values():
        samples.sort(key=lambda sample: sample.t_ms)
    return by_slot, dict(counts), channels


def native_hz(samples: dict[int, list[Sample]]) -> int:
    """The exported rate, as the median interval between a player's samples."""
    gaps = [(b.t_ms - a.t_ms) for rows in samples.values() for a, b in zip(rows, rows[1:])
            if 0 < b.t_ms - a.t_ms <= 500]
    if not gaps:
        raise ContractError("movement", "no movement samples to measure a rate from")
    return max(1, int(round(1000.0 / median(gaps))))


# ---------------------------------------------------------------- per-round assembly


@dataclass(frozen=True)
class Kill:
    t_ms: int
    killer: int
    victim: int
    # "rpc" (MulticastNotifyKilledEnemy) or "damage" (a lethal damage notify with no kill RPC).
    source: str = "rpc"


# A lethal damage notify and the kill RPC for the same victim are this close in time, or they
# are two deaths. On the Abyss and Ascent exports every kill RPC had its notify within this.
DAMAGE_KILL_MATCH_MS = 1000


def read_kills(export: Export, players: PlayerTable) -> tuple[list[Kill], dict]:
    """Every player death with its killer, in time order, and counts for the report.

    From MulticastNotifyKilledEnemy, and from a lethal damage notify (`DamageKilledTarget`) on a
    player's pawn that has no kill RPC for that victim within DAMAGE_KILL_MATCH_MS: some kills
    (three of Gekko's on the Abyss export) arrive only that way, and without them the victim
    plays on as a frozen, living dot until round end. Lethal hits on objects (cameras, walls)
    resolve to no player and are ignored; one whose killer resolves to no player is skipped
    and counted.
    """
    kills = []
    for row in export.rpcs(RPC_KILLED_ENEMY):
        payload = row.data.get("payload") or {}
        killer = players.resolve(int(payload.get("KillerCharacter") or 0), row.time_ms)
        victim = players.resolve(int(payload.get("KilledCharacter") or 0), row.time_ms)
        if killer is None or victim is None:
            raise ContractError("unresolved_kill", f"a kill at {row.time_ms} ms has no player for the "
                                                   f"{'killer' if killer is None else 'victim'}")
        kills.append(Kill(row.time_ms, killer, victim))
    counts = {"from_damage": 0, "damage_without_killer": 0}
    for row in export.events:
        data = row.data
        if data.get("type") != "rpc_received" or data.get("function_name") not in RPC_DAMAGE:
            continue
        payload = data.get("payload") or {}
        if payload.get("DamageKilledTarget") is not True:
            continue
        victim = players.resolve(int(payload.get("Character") or 0), row.time_ms)
        if victim is None:
            continue
        if any(k.victim == victim and abs(k.t_ms - row.time_ms) <= DAMAGE_KILL_MATCH_MS for k in kills):
            continue
        killer = players.resolve(int(payload.get("EventInstigatorPawn") or 0), row.time_ms)
        if killer is None:
            counts["damage_without_killer"] += 1
            continue
        kills.append(Kill(row.time_ms, killer, victim, "damage"))
        counts["from_damage"] += 1
    kills.sort(key=lambda k: k.t_ms)
    return kills, counts


def read_revives(export: Export, players: PlayerTable) -> list[tuple[int, int]]:
    revives = []
    for row in export.rpcs(RPC_RESURRECT):
        payload = row.data.get("payload") or {}
        slot = players.resolve(int(payload.get("ResurrectedPlayer") or 0), row.time_ms)
        if slot is not None:
            revives.append((row.time_ms, slot))
    return revives


# Map control's damage input (docs/replay-map-control-plan.md, "Inputs the blob lacks"): hits in a
# row from one attacker on one player, of one kind, this close together are one run.
DAMAGE_MERGE_MS = 500


@dataclass
class DamageRun:
    t_ms: int
    t1_ms: int
    by: int
    target: int
    src: str        # "gun" (MulticastNotifyDamage_Point) or "ability" (_Base: mollies and the like)
    wall: bool      # a wallbang (IsWallPenetration; only gun hits carry it)
    n: int = 1


def read_damage(export: Export, players: PlayerTable) -> tuple[list[DamageRun], dict]:
    """Every damage notify on a player's own character, as runs (DamageRun), and counts.

    The attacker is the instigating pawn's player (a drone or pet resolves to its possessor), else
    the `DamagerPlayerState`'s. A hit whose attacker resolves to no player is dropped, and so is one
    a player took from themself (their own molly; spike and fall damage name no other player)."""
    counts: Counter[str] = Counter()
    hits: list[tuple[int, int, int, str, bool]] = []
    for row in export.events:
        data = row.data
        if data.get("type") != "rpc_received" or data.get("function_name") not in RPC_DAMAGE:
            continue
        payload = data.get("payload") or {}
        target = players.pawn_slot.get(int(payload.get("Character") or 0))
        if target is None:
            continue   # an object (a camera, a wall) or a possessed pawn
        by = players.resolve(int(payload.get("EventInstigatorPawn") or 0), row.time_ms)
        if by is None and payload.get("DamagerPlayerState"):
            by = players.other_slot.get(int(payload["DamagerPlayerState"]))
        if by is None:
            counts["damage_unresolved"] += 1
            continue
        if by == target:
            counts["damage_self"] += 1
            continue
        point = data.get("function_name") == "MulticastNotifyDamage_Point"
        hits.append((row.time_ms, by, target, "gun" if point else "ability", payload.get("IsWallPenetration") is True))
        counts["damage_hits"] += 1
    runs: list[DamageRun] = []
    open_runs: dict[tuple, DamageRun] = {}
    for t_ms, by, target, src, wall in sorted(hits):
        key = (by, target, src, wall)
        run = open_runs.get(key)
        if run is not None and t_ms - run.t1_ms <= DAMAGE_MERGE_MS:
            run.t1_ms, run.n = t_ms, run.n + 1
            continue
        run = DamageRun(t_ms, t_ms, by, target, src, wall)
        open_runs[key] = run
        runs.append(run)
    counts["damage_runs"] = len(runs)
    counts["wallbang_runs"] = sum(r.wall for r in runs)
    return runs, dict(sorted(counts.items()))


def _damage_entry(run: DamageRun, start: int) -> dict:
    return {"k": "damage", "t": _seconds(run.t_ms, start), "t1": _seconds(run.t1_ms, start), "by": run.by,
            "target": run.target, "src": run.src, "wall": run.wall, "n": run.n}


def _seconds(t_ms: int, start_ms: int) -> float:
    return round((t_ms - start_ms) / 1000.0, 3)


def alive_intervals(start: int, end: int, deaths: list[int], revives: list[int],
                    pawn_changes: list[int], gone_ms: int | None = None,
                    self_kills: list[int] = (), unobserved: list[tuple[int, int]] = (),
                    away: list[tuple[int, int]] = ()) -> list[list]:
    """[[from_ms, to_ms | None, cause(, flags)]] for one slot in one round window (times absolute).

    `gone_ms` is when a player who left lost their last pawn: nothing after it, and an
    open interval then ends with cause "left". Gone before the round starts: no intervals.
    `away` spans are a disconnect: a life open at a span's start ends there with cause "left",
    a round that starts inside one opens no life, and the rejoin (the new pawn, one of
    `pawn_changes`) opens the next.
    A round's start opens a new life. A second death with no revive or new pawn between
    refuses (P-c), except a self-kill with no decoded revive (Clove's ult running out after
    a revive the parser didn't decode): that life is flagged "uncertain" and still ends at
    its first death. An interval that overlaps an unobserved span is flagged "unobserved".
    """
    if gone_ms is not None and gone_ms <= start:
        return []
    self_set = set(self_kills)
    events = sorted([(t, 0, "death") for t in deaths] + [(t, 1, "away") for t, _ in away if start < t <= end]
                    + [(t, 2, "revive") for t in revives + pawn_changes])
    away_at_start = any(lo <= start < hi for lo, hi in away)
    intervals: list[list] = [] if away_at_start else [[start, None, "round_end"]]
    for t, _, kind in events:
        if not intervals:
            if kind == "death":
                raise ContractError("lifecycle", f"a death at {t} ms while the player was disconnected")
            if kind == "revive":
                intervals.append([t, None, "round_end"])
            continue
        current = intervals[-1]
        is_open = current[1] is None and current[2] == "round_end"
        if kind == "away":
            if is_open:
                current[1], current[2] = t, "left"
        elif kind == "death" and is_open:
            current[1], current[2] = t, "kill"
        elif kind == "death":
            if t not in self_set:
                raise ContractError("lifecycle", f"a second death at {t} ms with no revive or new pawn since the "
                                                 f"death at {current[1]} ms")
            if len(current) == 3:
                current.append(["uncertain"])
            elif "uncertain" not in current[3]:
                current[3].append("uncertain")
        elif kind == "revive" and not is_open:
            intervals.append([t, None, "round_end"])
    if gone_ms is not None and gone_ms <= end:
        intervals = [iv for iv in intervals if iv[0] < gone_ms]
        last = intervals[-1] if intervals else None
        if last is not None and (last[1] is None or last[1] > gone_ms):
            last[1], last[2] = gone_ms, "left"
    for interval in intervals:
        lo, hi = interval[0], interval[1] if interval[1] is not None else end
        if any(a < hi and b > lo for a, b in unobserved):
            if len(interval) == 3:
                interval.append([])
            if "unobserved" not in interval[3]:
                interval[3].append("unobserved")
    return intervals


def _interval_index(intervals: list[list], t_ms: int, end: int) -> int | None:
    for i, interval in enumerate(intervals):
        lo, hi = interval[0], interval[1]
        if lo <= t_ms <= (hi if hi is not None else end):
            return i
    return None


def _uncovered(a: int, b: int, spans: list[tuple[int, int]]) -> int:
    """The part of [a, b] (ms) outside every unobserved span."""
    cut = sorted((max(a, lo), min(b, hi)) for lo, hi in spans if lo < b and hi > a)
    covered, reach = 0, a
    for lo, hi in cut:
        lo = max(lo, reach)
        if hi > lo:
            covered += hi - lo
            reach = hi
    return (b - a) - covered


def build_segments(samples: list[Sample], intervals: list[list], start: int, end: int,
                   hz: int, game_map: MapInfo, unobserved: list[tuple[int, int]] = ()) -> tuple[list[dict], dict]:
    """Splits a slot's samples into grid-snapped segments; returns them and coverage stats.

    Coverage and gaps measure observed alive time: unobserved spans (P-c) don't count."""
    runs: list[list[tuple[Sample, int]]] = []
    previous: tuple[Sample, int] | None = None
    for sample in samples:
        if not start <= sample.t_ms <= end:
            continue
        interval = _interval_index(intervals, sample.t_ms, end)
        if interval is None:
            continue
        brk = (previous is None
               or interval != previous[1]
               or sample.pawn != previous[0].pawn
               or (sample.t_ms - previous[0].t_ms) / 1000.0 > SEGMENT_GAP_S
               # a missing height counts as 0 here, as before revision 11, so the runs are the same
               or math.dist((sample.x, sample.y, sample.z or 0.0),
                            (previous[0].x, previous[0].y, previous[0].z or 0.0)) > TELEPORT_UNITS)
        if brk:
            runs.append([])
        runs[-1].append((sample, interval))
        previous = (sample, interval)

    segments = []
    covered_points = 0
    for run in runs:
        grid: dict[int, tuple[int, int, int, int | None]] = {}
        for sample, _ in run:
            k = int(round((sample.t_ms - start) / 1000.0 * hz))
            u, v = game_map.to_uv(sample.x, sample.y)
            # the last sample on a grid point wins; z in decimetres of world z, as the parser gave it
            grid[k] = (u, v, game_map.yaw_to_map(sample.yaw), None if sample.z is None else int(round(sample.z / 10.0)))
        keys = sorted(grid)
        # A segment has a height for every sample or for none (revision 11). A run with any sample
        # missing one stores none: splitting it would move the positions revision 10 stored.
        has_z = all(sample.z is not None for sample, _ in run)
        u_list, v_list, yaw_list, z_list = [], [], [], []
        for a, b in zip(keys, keys[1:] + [None]):
            ua, va, ya, za = grid[a]
            u_list.append(ua), v_list.append(va), yaw_list.append(ya), z_list.append(za)
            if b is None:
                continue
            # Grid points the source skipped inside a segment (< SEGMENT_GAP_S)
            # are filled linearly, yaw along the shortest arc; never beyond the source's rate.
            ub, vb, yb, zb = grid[b]
            turn = (yb - ya + 180) % 360 - 180
            for step in range(1, b - a):
                f = step / (b - a)
                u_list.append(int(round(ua + (ub - ua) * f)))
                v_list.append(int(round(va + (vb - va) * f)))
                yaw_list.append(int(round(ya + turn * f)) % 360)
                z_list.append(int(round(za + (zb - za) * f)) if has_z else None)
        covered_points += len(u_list)
        segments.append(fmt.encode_segment(keys[0] / hz, u_list, v_list, yaw_list, z_list if has_z else None))

    alive_s = sum(_uncovered(iv[0], iv[1] if iv[1] is not None else end, unobserved) / 1000.0 for iv in intervals)
    max_gap = 0.0
    for interval in intervals:
        lo, hi = interval[0], interval[1] if interval[1] is not None else end
        bounds = [lo, *(s.t_ms for s in samples if lo <= s.t_ms <= hi), hi]
        max_gap = max(max_gap, *(_uncovered(a, b, unobserved) / 1000.0 for a, b in zip(bounds, bounds[1:])))
    coverage = min(1.0, covered_points / hz / alive_s) if alive_s > 0 else 1.0
    return segments, {"coverage": round(coverage, 3), "max_gap_s": round(max_gap, 3)}


def _util_entry(cast: UtilCast, start: int, game_map: MapInfo) -> dict:
    entry = {"k": cast.kind, "t": _seconds(cast.t_ms, start), "by": cast.by, "ability": cast.ability,
             "targets": list(cast.targets),
             "hits": [[slot, _seconds(t_ms, start), duration] for slot, t_ms, duration in cast.hits]}
    if cast.x is not None and cast.y is not None:
        entry["u"], entry["v"] = game_map.to_uv(float(cast.x), float(cast.y))
    if cast.activation is None:
        return entry                       # a nearsight: as before
    # A flash (format.py, "Flash rows"): its identity, height, explosion, flight and activation evidence.
    entry["id"] = cast.actor
    if cast.z is not None:
        entry["z"] = int(round(cast.z / 10.0))
    entry["activation"] = cast.activation
    if cast.pop is not None:
        pop = {"t": _seconds(cast.pop["t_ms"], start)}
        if "x" in cast.pop:
            pop["u"], pop["v"] = game_map.to_uv(cast.pop["x"], cast.pop["y"])
            if cast.pop["z"] is not None:
                pop["z"] = int(round(cast.pop["z"] / 10.0))
            pop["src"] = cast.pop["src"]
        else:
            pop["place"] = cast.pop["place"]
        if cast.pop.get("age_ms"):
            pop["age"] = round(cast.pop["age_ms"] / 1000.0, 3)
        entry["pop"] = pop
    if len(cast.path) > 1:
        entry["path"] = [[_seconds(t_ms, start), *game_map.to_uv(x, y), *([] if z is None else [int(round(z / 10.0))])]
                         for t_ms, x, y, z in cast.path]
    return entry


def _last_position(samples: list[Sample], t_ms: int, start: int) -> Sample | None:
    times = [s.t_ms for s in samples]
    i = bisect.bisect_right(times, t_ms) - 1
    if i >= 0 and samples[i].t_ms >= start:
        return samples[i]
    return None


def two_clusters(points: dict[int, tuple[float, float]]) -> tuple[list[int], list[int], float]:
    """Deterministic 2-means over start positions: (cluster, cluster, max distance to a centroid)."""
    slots = sorted(points)
    if len(slots) < 2:
        return slots, [], 0.0
    a, b = max(((p, q) for i, p in enumerate(slots) for q in slots[i + 1:]),
               key=lambda pq: math.dist(points[pq[0]], points[pq[1]]))
    centres = [points[a], points[b]]
    groups: list[list[int]] = [[], []]
    for _ in range(20):
        groups = [[], []]
        for slot in slots:
            groups[0 if math.dist(points[slot], centres[0]) <= math.dist(points[slot], centres[1]) else 1].append(slot)
        new = [tuple(sum(points[s][d] for s in g) / len(g) for d in (0, 1)) if g else c
               for g, c in zip(groups, centres)]
        if new == centres:
            break
        centres = new
    radius = max((math.dist(points[s], centres[i]) for i, g in enumerate(groups) for s in g), default=0.0)
    return groups[0], groups[1], radius


def side_groups(kills: list[Kill], clusters: list[tuple[list[int], list[int]]]) -> list[str]:
    """Unlabelled sides A/B per slot: kills are cross-side, spawn clusters are same-side.

    Parity union-find over both kinds of evidence. It must be consistent, connected and 5/5.
    Group A is the one holding slot 0.
    """
    parent = list(range(10))
    parity = [0] * 10

    def find(x: int) -> tuple[int, int]:
        p = 0
        while parent[x] != x:
            p ^= parity[x]
            x = parent[x]
        return x, p

    def join(a: int, b: int, different: int, why: str) -> None:
        ra, pa = find(a)
        rb, pb = find(b)
        if ra == rb:
            if pa ^ pb != different:
                raise ContractError("side_groups", f"inconsistent evidence ({why})")
            return
        parent[rb] = ra
        parity[rb] = pa ^ pb ^ different

    for kill in kills:
        if kill.killer != kill.victim:
            join(kill.killer, kill.victim, 1, "kill")
    for first, second in clusters:
        if len(first) != 5 or len(second) != 5:
            continue
        for group in (first, second):
            for other in group[1:]:
                join(group[0], other, 0, "spawn cluster")
        join(first[0], second[0], 1, "spawn clusters")

    roots = {find(slot)[0] for slot in range(10)}
    if len(roots) != 1:
        raise ContractError("side_groups", f"evidence leaves {len(roots)} disconnected groups")
    base = find(0)[1]
    sides = ["A" if find(slot)[1] == base else "B" for slot in range(10)]
    if sides.count("A") != 5:
        raise ContractError("side_groups", f"side groups are {sides.count('A')}/{sides.count('B')}, expected 5/5")
    return sides


# ---------------------------------------------------------------- eligibility (P-f)


def channel_kinds(export: Export, players: PlayerTable, movement_channels: set) -> dict:
    """channel -> the evidence it carried: phase, lifecycle, kill, movement."""
    kinds: dict = defaultdict(set)
    for row in export.events:
        data = row.data
        kind = data.get("type")
        channel = data.get("channel")
        if kind == "rpc_received" and data.get("function_name") in (RPC_PHASE_BEGIN, RPC_PHASE_ENDED, RPC_SET_PHASE):
            kinds[channel].add("phase")
        elif kind == "export_group_received" and data.get("export_group_path") == GAME_STATE_PATH \
                and "Phase" in (data.get("payload") or {}):
            kinds[channel].add("phase")
        elif kind in ("actor_spawned", "actor_closed") and int(data.get("actor_net_guid") or 0) in players.pawn_slot:
            kinds[channel].add("lifecycle")
        elif kind == "rpc_received" and data.get("function_name") == RPC_KILLED_ENEMY:
            kinds[channel].add("kill")
    for channel in movement_channels:
        kinds[channel].add("movement")
    return kinds


def eligibility(diagnostics, game: GameState, coverage: dict, kinds: dict) -> dict:
    """The link-eligibility record carried in link_inputs and enforced by the linker and
    `--dry-run` (P-f). Partial-bunch errors are classed by the evidence on their channel, with
    precedence phase > lifecycle/kill > movement > other."""
    reasons: list[str] = []
    reasons += [f"blocking diagnostic: {item}" for item in diagnostics.blocking]
    link_blocking = list(diagnostics.link_blocking)
    if game.round_results_fallback:
        link_blocking.append("RoundResults fell back to a raw payload in the fold")
    reasons += [f"link-blocking: {item}" for item in link_blocking]
    min_coverage = min((c["min"] for c in coverage.values()), default=1.0)
    max_gap = max((c["max_gap_s"] for c in coverage.values()), default=0.0)
    low = sorted((s for s, c in coverage.items() if c["min"] < MIN_ALIVE_COVERAGE), key=int)
    gaps = sorted((s for s, c in coverage.items() if c["max_gap_s"] > MAX_TRACK_GAP_S), key=int)
    if low:
        reasons.append(f"coverage below {MIN_ALIVE_COVERAGE:.0%} for slots {low}")
    if gaps:
        reasons.append(f"a track gap over {MAX_TRACK_GAP_S:g} s for slots {gaps}")
    partial: dict[str, dict] = {}
    for channel, count in sorted(diagnostics.channels.items(), key=lambda kv: str(kv[0])):
        carried = kinds.get(channel, set())
        # The channel classes and their precedence.
        if "phase" in carried:
            cls = "phase_validated" if game.phase_ended_checked else "phase_unvalidated"
            if not game.phase_ended_checked:
                reasons.append(f"{count} partial-bunch errors on phase channel {channel} with no "
                               f"ClientGamePhaseEnded cross-check")
        elif carried & {"lifecycle", "kill"}:
            cls = "lifecycle_validated"
        elif "movement" in carried:
            cls = "movement_coverage"
        else:
            cls = "ignored"
        partial[str(channel)] = {"count": count, "class": cls}
    return {
        "eligible": not reasons, "reasons": reasons, "link_blocking": link_blocking,
        "coverage_ok": not low and not gaps, "min_coverage": min_coverage, "max_gap_s": max_gap,
        "limits": {"min_alive_coverage": MIN_ALIVE_COVERAGE, "max_track_gap_s": MAX_TRACK_GAP_S},
        # Condense refuses outright when these fail, so a record that exists says they passed.
        "phase_cycle_ok": True, "phase_ended_checked": game.phase_ended_checked, "lifecycle_ok": True,
        "kills_outside_rounds": 0, "partial_errors": partial,
    }


# ---------------------------------------------------------------- entry points


UUID_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def match_uuid_of(game: GameState, manifest: dict, header_uuid: str | None = None,
                  file_names: list[str] | None = None) -> tuple[str, str]:
    """(match UUID, where it came from).

    The game state's MatchID when decoded, else the `.vrf` header's FriendlyName (W-a). Every
    source present must agree, and so must `file_names` (the local file's name and the name
    the export was made from) when given: local ingest passes them, an upload doesn't, since
    an uploaded file's name means nothing. Without a header (a preview with no `.vrf` at
    hand) the export's source file name is the last resort, reported as such. The header is
    an uploader-controlled claim, like the name: only the linker's proof ties it to a match.
    """
    sources = {}
    if game.match_uuid is not None:
        sources["game_state"] = game.match_uuid
    if header_uuid is not None:
        sources["vrf_header"] = header_uuid
    if not sources:
        stem = Path(str(manifest.get("source_file") or "")).stem.lower()
        if UUID_SHAPE.match(stem):
            return stem, "vrf_file_name"
        raise ContractError("match_id", "no MatchID in the game state, no .vrf header, and the .vrf isn't named by "
                                        "a match UUID")
    if len(set(sources.values())) > 1:
        raise ContractError("match_id", "the game state's MatchID and the .vrf header's match UUID disagree")
    uuid = next(iter(sources.values()))
    for name in file_names or []:
        if Path(name).stem.lower() != uuid:
            raise ContractError("match_id", "the .vrf's file name disagrees with its match UUID (renamed?)")
    return uuid, next(iter(sources))


def condense(export: Export, *, maps: dict[str, MapInfo], agents_by_code: dict[str, str], recipe: str,
             map_override: str | None = None, vrf_map_codes: dict[str, int] | None = None,
             header: tuple[str, dict] | None = None, file_names: list[str] | None = None) -> CondensedReplay:
    diagnostics = classify_diagnostics(export.manifest)
    game_map, map_evidence = discover_map(export, maps, vrf_map_codes)
    if game_map is None:
        if map_override is None:
            raise ContractError("map", "map not recognised from actor paths or the .vrf")
        if map_override not in maps:
            raise ContractError("map", f"--map {map_override!r} is not in maps.json")
        game_map = maps[map_override]
        map_evidence["source"] = "override"
    elif map_override is not None and map_override != game_map.name:
        raise ContractError("map", f"--map {map_override!r} disagrees with the discovered {game_map.name!r}")

    players = build_players(export, agents_by_code)
    game = read_game_state(export)
    match_uuid, match_uuid_source = match_uuid_of(game, export.manifest, header[0] if header else None,
                                                  file_names)
    if not game.windows:
        raise ContractError("rounds", "no round has both InRound and RoundEnding")
    by_slot, movement_counts, movement_channels = read_movement(export, players)
    hz = native_hz(by_slot)
    kills, kill_counts = read_kills(export, players)
    revives = read_revives(export, players)
    lifecycle = read_lifecycle(export, players, game.windows[-1][1], max(export.end_ms, game.windows[-1][2]))
    players.gone_ms = lifecycle.left
    players.away = lifecycle.away
    for kill in kills:
        for role, slot in (("by", kill.killer), ("of", kill.victim)):
            gone = players.gone_ms.get(slot)
            if gone is not None and kill.t_ms >= gone:
                raise ContractError("lifecycle", f"a kill {role} slot {slot} at {kill.t_ms} ms, after that player "
                                                 f"left at {gone} ms")
            away = players.away_at(slot, kill.t_ms)
            if away is not None:
                raise ContractError("lifecycle", f"a kill {role} slot {slot} at {kill.t_ms} ms, while that player "
                                                 f"was disconnected ({away[0]}-{away[1]} ms)")
    pawn_times = {slot: [t for t, _ in changes[1:]] for slot, changes in players.pawn_changes.items()}

    def present(slot: int, start: int) -> bool:
        gone = players.gone_ms.get(slot)
        return (gone is None or gone > start) and players.away_at(slot, start) is None

    rounds: dict[int, dict] = {}
    link_kills: dict[str, list] = {}
    start_positions: dict[str, dict] = {}
    spawn_checks: dict[str, dict] = {}
    clusters: list[tuple[list[int], list[int]]] = []
    coverage: dict[int, list[dict]] = defaultdict(list)
    round_kills: dict[int, list[Kill]] = {}
    absent: dict[str, list[int]] = {}
    assigned = 0
    for n, (start, _, end) in enumerate(game.windows, 1):
        in_round = [k for k in kills if start <= k.t_ms <= end]
        round_kills[n] = in_round
        assigned += len(in_round)
        missing = [slot for slot in range(10) if not present(slot, start)]
        if missing:
            absent[str(n)] = missing
        world_starts: dict[int, tuple[float, float]] = {}
        uv_starts: dict[str, list[int]] = {}
        for slot, rows in by_slot.items():
            if not present(slot, start):
                continue
            first = next((s for s in rows if start <= s.t_ms <= start + START_POSITION_WINDOW_S * 1000), None)
            if first is not None:
                world_starts[slot] = (first.x, first.y)
                uv_starts[str(slot)] = list(game_map.to_uv(first.x, first.y))
        first, second, radius = two_clusters(world_starts)
        sizes = sorted([len(first), len(second)])
        # Only tight clusters are side evidence. In the first real export one side roamed a
        # wide area through the buy phase, and a loose 2-means split put players on the wrong
        # side; kills alone connected all ten.
        # The linker re-checks the same rounds' start positions against the sides, so it gets
        # only the rounds used here: a loose round would make it refuse a correct link.
        if sizes == [5, 5] and radius <= SPAWN_CLUSTER_RADIUS:
            clusters.append((first, second))
            start_positions[str(n)] = uv_starts
        # A round with a player gone can't show two 5/5 clusters; it's reported, not failed.
        spawn_checks[str(n)] = {"sizes": sizes, "max_radius": round(radius, 1), "short_handed": bool(missing),
                                "ok": (sizes == [5, 5] or bool(missing)) and radius <= SPAWN_CLUSTER_RADIUS}
        link_kills[str(n)] = [[_seconds(k.t_ms, start), k.killer, k.victim] for k in in_round]

    # The pawns' own spawn points at match start: in the first real export the two teams
    # spawned in two tight lines, the one round-start signal that stayed clean.
    first, second, radius = two_clusters(players.spawn_xy)
    match_spawn = {"sizes": sorted([len(first), len(second)]), "max_radius": round(radius, 1)}
    match_spawn["ok"] = match_spawn["sizes"] == [5, 5] and radius <= SPAWN_CLUSTER_RADIUS
    if match_spawn["ok"]:
        clusters.append((first, second))
    util_casts, util_counts = read_util(export, players)
    util_counts["outside_rounds"] = sum(1 for c in util_casts
                                        if not any(s <= c.t_ms <= e for s, _, e in game.windows))
    damage_runs, damage_counts = read_damage(export, players)
    # P-a: every kill falls in one playback window, or in the dropped final round (excluded
    # with its reason); any other kill refuses.
    excluded = {"dropped_final_round": 0}
    if game.dropped_final_start is not None:
        excluded["dropped_final_round"] = sum(
            1 for k in kills if game.dropped_final_start <= k.t_ms <= game.dropped_final_end)
    outside = len(kills) - assigned - excluded["dropped_final_round"]
    if outside:
        raise ContractError("kills_outside_rounds", f"{outside} kills fall in no round window")
    # P-d: sides are for display only. If the evidence can't give one consistent, connected
    # 5/5 partition, every side is null: the replay still stores and plays, with neutral
    # colours, and the linker never needed them.
    try:
        sides: list[str | None] = side_groups(kills, clusters)
        sides_report = {"resolved": True}
    except ContractError as unresolved:
        sides = [None] * 10
        sides_report = {"resolved": False, "reason": unresolved.detail}
    player_rows = [{"slot": slot, "agent": players.agents[slot], "side": sides[slot]} for slot in range(10)]

    uncertain_lives = 0
    for n, (start, decided, end) in enumerate(game.windows, 1):
        tracks: dict[str, list] = {}
        alive: dict[str, list] = {}
        for slot in range(10):
            deaths = [k.t_ms for k in round_kills[n] if k.victim == slot]
            self_kills = [k.t_ms for k in round_kills[n] if k.victim == slot and k.killer == slot]
            revived = [t for t, s in revives if s == slot and start <= t <= end]
            pawns = [t for t in pawn_times.get(slot, []) if start < t <= end]
            spans = lifecycle.unobserved.get(slot, [])
            intervals = alive_intervals(start, end, deaths, revived, pawns, players.gone_ms.get(slot),
                                        self_kills, spans, players.away.get(slot, []))
            uncertain_lives += sum(1 for iv in intervals if "uncertain" in (iv[3] if len(iv) > 3 else []))
            alive[str(slot)] = [[_seconds(iv[0], start), None if iv[1] is None else _seconds(iv[1], start), iv[2],
                                 *iv[3:]] for iv in intervals]
            segments, stats = build_segments(by_slot.get(slot, []), intervals, start, end, hz, game_map, spans)
            if segments:
                tracks[str(slot)] = segments
            if intervals:
                coverage[slot].append({"round": n, **stats})
        kill_rows = []
        for i, k in enumerate(round_kills[n]):
            where = _last_position(by_slot.get(k.victim, []), k.t_ms, start)
            u, v = game_map.to_uv(where.x, where.y) if where else (None, None)
            kill_rows.append({"i": i, "t": _seconds(k.t_ms, start), "killer": k.killer, "victim": k.victim,
                              "u": u, "v": v})
        rounds[n] = {
            "v": fmt.FORMAT_VERSION, "round": n, "map": game_map.name, "hz": hz,
            "t_start": 0.0, "t_decided": _seconds(decided, start), "t_end": _seconds(end, start),
            "players": player_rows, "tracks": tracks, "alive": alive, "kills": kill_rows,
            # No plant/defuse source yet; the first export's TimedBomb wasn't decoded.
            "plant": None, "defuse": None,
            # W-e: flash and nearsight casts in this window; a new `k` needs no `v` bump. Map control
            # (revision 10): the damage runs that start in it.
            "util": [_util_entry(c, start, game_map) for c in util_casts if start <= c.t_ms <= end]
                    + [_damage_entry(r, start) for r in damage_runs if start <= r.t_ms <= end],
        }

    encoded = {n: fmt.encode_blob(blob) for n, blob in rounds.items()}
    # RoundResults index i (0-based, the decoder's encodedIndex - 1) is played round i + 1.
    # The first export decoded no RoundResults at all: the linker then checks no winners.
    round_results = {str(i + 1): fields for i, fields in sorted(game.round_results.items())}
    report = {
        "diagnostics": diagnostics.as_dict(),
        "map": {"name": game_map.name, **map_evidence},
        "match_uuid_source": match_uuid_source,
        "vrf_header": header[1] if header else None,
        "players": {"subjects_decoded": sum(1 for s in players.subjects if s),
                    "left": {str(slot): _seconds(t, 0) for slot, t in sorted(players.gone_ms.items())
                             if t < game.windows[-1][1]},
                    "absent_by_round": absent},
        "hz": hz,
        "rounds": len(rounds),
        "dropped_final_round": game.dropped_final_start is not None,
        "round_number_at_start": game.round_number_at_start,
        "round_results_decoded": len(game.round_results),
        "round_results_fallback": game.round_results_fallback,
        "kills": len(kills),
        "kill_sources": kill_counts,
        "kills_outside_rounds": outside,
        "kills_excluded": excluded,
        "ownership": players.ownership,
        "util": util_counts,
        "damage": damage_counts,
        "sides": sides_report,
        "lifecycle": {**lifecycle.report, "uncertain_lives": uncertain_lives, "contradictions": 0},
        "phase_ended_checked": game.phase_ended_checked,
        "movement": movement_counts,
        "spawn_checks": spawn_checks,
        "match_spawn_check": match_spawn,
        "coverage": {str(slot): {"min": min(r["coverage"] for r in rows),
                                 "max_gap_s": max(r["max_gap_s"] for r in rows)}
                     for slot, rows in sorted(coverage.items())},
        "sizes": fmt.size_report(encoded),
        "limits": {"min_alive_coverage": MIN_ALIVE_COVERAGE, "max_track_gap_s": MAX_TRACK_GAP_S,
                   "spawn_cluster_radius": SPAWN_CLUSTER_RADIUS},
    }
    link_inputs = {
        "round_results": round_results,
        "round_number_at_start": game.round_number_at_start,
        "start_positions": start_positions,
        # P-d: each slot's match-start spawn point in world units (the cluster radius's units);
        # the linker checks its assignment against them and never removes a candidate by them.
        "spawn_points": ({str(slot): [round(x, 1), round(y, 1)] for slot, (x, y) in sorted(players.spawn_xy.items())}
                         if len(players.spawn_xy) == 10 else {}),
        "kills": link_kills,
        "round_results_fallback": game.round_results_fallback,
        "eligibility": eligibility(diagnostics, game, report["coverage"],
                                   channel_kinds(export, players, movement_channels)),
    }
    private_players = [{"slot": slot, "subject": players.subjects[slot], "agent": players.agents[slot],
                        "side_group": sides[slot]} for slot in range(10)]
    return CondensedReplay(
        match_uuid=match_uuid, map_name=game_map.name, game_branch=str(export.manifest.get("replay_build")),
        source_sha256=str(export.manifest.get("source_sha256")), recipe=recipe, hz=hz,
        rounds=rounds, players=private_players, link_inputs=link_inputs, report=report,
    )


def condense_export_dir(export_dir: Path, *, source_sha256: str | None, build: dict | None = None,
                        map_override: str | None = None, allow_blocking: bool = False,
                        pin: ParserPin | None = None, static_dir: Path = fmt.STATIC_DIR,
                        vrf_path: Path | None = None, check_file_name: bool = True,
                        streaming: bool = True, with_extras: bool = True) -> CondensedReplay:
    """Checks the contract, then condenses. Blocking diagnostics refuse unless `allow_blocking`
    (the preview shows them instead). `vrf_path` gives the match UUID (its header, W-a) and
    the map when the export doesn't name it. `check_file_name` (local ingest) also requires the
    file's own name and the export's source name to agree with the header; the upload worker
    turns it off. `streaming` (the default, W-b) reads only the rows the condenser needs and
    streams movement; `streaming=False` loads everything, for the parity test. `with_extras` (the
    default, Stage 2) also stores the round's abilities and shots in `util` (`attach_extras`)."""
    pin = pin or load_pin()
    export = load_export_streaming(export_dir) if streaming else load_export(export_dir)
    check_manifest(export.manifest, pin, source_sha256, build)
    blocking = classify_diagnostics(export.manifest).blocking
    if blocking and not allow_blocking:
        raise ContractError("blocking_diagnostics", "; ".join(blocking))
    have_vrf = vrf_path is not None and vrf_path.is_file()
    vrf_map_codes = map_codes_in_file(vrf_path) if have_vrf else None
    header = header_match_uuid(vrf_path) if have_vrf else None
    file_names = [vrf_path.name, str(export.manifest.get("source_file") or "")] if have_vrf and check_file_name else None
    maps, agents = load_maps(static_dir), load_agents(static_dir)
    replay = condense(export, maps=maps, agents_by_code=agents,
                      recipe=fmt.recipe(pin.commit, fmt.assets_revision(static_dir)), map_override=map_override,
                      vrf_map_codes=vrf_map_codes, header=header, file_names=file_names)
    if with_extras:
        attach_extras(replay, export, export_dir / "events.ndjson", maps[replay.map_name], agents)
    return replay


def attach_extras(replay: CondensedReplay, export: Export, events_path: Path, game_map: MapInfo,
                  agents_by_code: dict[str, str]) -> None:
    """Adds each round's ability objects and shots (app/replays/extras.py) to its blob's `util`
    as the `ability` and `shot` kinds, after the kinds `condense()` wrote, and re-measures the
    sizes. Another pass over the events file and one over movement, both streamed (W-b)."""
    from app.replays.extras import WorldPositions, build_extras, util_entries  # extras imports this module

    players = build_players(export, agents_by_code)
    windows = read_game_state(export).windows
    positions = WorldPositions(export.movement, players)
    first = replay.rounds[min(replay.rounds)] if replay.rounds else {"players": []}
    teams = {row["slot"]: row.get("side") for row in first.get("players", [])}
    extras = build_extras(events_path, players, windows, game_map, agents_by_code, positions.at, positions.path,
                          teams=teams, pawn_yaws=positions.yaws)
    for n, blob in replay.rounds.items():
        blob["util"] = blob["util"] + util_entries(extras.rounds.get(n, {}))
        blob["movement_casts"] = 1      # revision 14: casts were looked for, so a round with none had none
    replay.report["extras"] = extras.report
    replay.report["sizes"] = fmt.size_report(replay.encoded_rounds())
