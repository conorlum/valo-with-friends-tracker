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

Rules marked PROVISIONAL(D<n>) were chosen before any real export existed and are frozen
at the Stage 1b gate (docs/replay-viewer-plan.md).
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
from app.replays.contract import (
    CHARACTER_PLAYER_STATE,
    GAME_STATE_PATH,
    PLAYER_STATE_PATH,
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
    load_pin,
    map_codes_in_file,
)

# EAresGamePhase (parser source): RoundStarting=3, InRound=4, RoundEnding=5.
PHASE_IN_ROUND = 4
PHASE_ROUND_ENDING = 5

# PROVISIONAL(D5): segment rules from the plan, frozen at Stage 1a/1b.
SEGMENT_GAP_S = 1.0
TELEPORT_UNITS = 600.0
# PROVISIONAL(D5): 1a gate limits, reported (not enforced) by the condenser.
MIN_ALIVE_COVERAGE = 0.90
MAX_TRACK_GAP_S = 3.0
SPAWN_CLUSTER_RADIUS = 1500.0
START_POSITION_WINDOW_S = 2.0
# PROVISIONAL(D15): pass-6 rules the 1b gate freezes. A ClientGamePhaseEnded must sit within
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
    z: float
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
    for row in export.of_type("actor_spawned"):
        code = character_code(row.data)
        if code is None:
            continue
        if code not in agents_by_code:
            raise ContractError("unknown_agent", f"agent code name {code!r} is not in agents.json")
        guid = int(row.data["actor_net_guid"])
        codes[guid] = code
        spawned_at.setdefault(guid, row.time_ms)
        location = row.data.get("location") or {}
        if location.get("x") is not None and location.get("y") is not None:
            spawned_xy.setdefault(guid, (float(location["x"]), float(location["y"])))
    closed_at = {int(row.data["actor_net_guid"]): row.time_ms for row in export.of_type("actor_closed")
                 if int(row.data.get("actor_net_guid", 0)) in codes}

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

    # PROVISIONAL(D10): slots in order of each player's first pawn (spawn time, then guid):
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
        if pawns[-1] in closed_at:
            gone_ms[slot] = closed_at[pawns[-1]]
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
            # PROVISIONAL(D15): the final InRound (no later 4) may end in another phase, a
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


def read_movement(export: Export, players: PlayerTable) -> tuple[dict[int, list[Sample]], dict]:
    by_slot: dict[int, list[Sample]] = defaultdict(list)
    counts = Counter()
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
        by_slot[slot].append(Sample(row.time_ms, float(position["x"]), float(position["y"]),
                                    float(position.get("z") or 0.0), float(data.get("yaw") or 0.0), pawn))
    return by_slot, dict(counts)


def native_hz(samples: dict[int, list[Sample]]) -> int:
    """PROVISIONAL(D5): the exported rate, as the median interval between a player's samples."""
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


def read_kills(export: Export, players: PlayerTable) -> list[Kill]:
    kills = []
    for row in export.rpcs(RPC_KILLED_ENEMY):
        payload = row.data.get("payload") or {}
        killer = players.resolve(int(payload.get("KillerCharacter") or 0), row.time_ms)
        victim = players.resolve(int(payload.get("KilledCharacter") or 0), row.time_ms)
        if killer is None or victim is None:
            raise ContractError("unresolved_kill", f"a kill at {row.time_ms} ms has no player for the "
                                                   f"{'killer' if killer is None else 'victim'}")
        kills.append(Kill(row.time_ms, killer, victim))
    return kills


def read_revives(export: Export, players: PlayerTable) -> list[tuple[int, int]]:
    revives = []
    for row in export.rpcs(RPC_RESURRECT):
        payload = row.data.get("payload") or {}
        slot = players.resolve(int(payload.get("ResurrectedPlayer") or 0), row.time_ms)
        if slot is not None:
            revives.append((row.time_ms, slot))
    return revives


def _seconds(t_ms: int, start_ms: int) -> float:
    return round((t_ms - start_ms) / 1000.0, 3)


def alive_intervals(start: int, end: int, deaths: list[int], revives: list[int],
                    pawn_changes: list[int], gone_ms: int | None = None) -> list[list]:
    """[[from_ms, to_ms | None, cause]] for one slot in one round window (times absolute).

    `gone_ms` is when a player who left lost their last pawn: nothing after it, and an
    open interval then ends with cause "left". Gone before the round starts: no intervals.
    """
    if gone_ms is not None and gone_ms <= start:
        return []
    events = sorted([(t, 0, "death") for t in deaths] + [(t, 1, "revive") for t in revives + pawn_changes])
    intervals: list[list] = [[start, None, "round_end"]]
    for t, _, kind in events:
        current = intervals[-1]
        is_open = current[1] is None and current[2] == "round_end"
        if kind == "death" and is_open:
            current[1], current[2] = t, "kill"
        elif kind == "revive" and not is_open:
            intervals.append([t, None, "round_end"])
    if gone_ms is not None and gone_ms <= end:
        intervals = [iv for iv in intervals if iv[0] < gone_ms]
        last = intervals[-1]
        if last[1] is None or last[1] > gone_ms:
            last[1], last[2] = gone_ms, "left"
    return intervals


def _interval_index(intervals: list[list], t_ms: int, end: int) -> int | None:
    for i, (lo, hi, _) in enumerate(intervals):
        if lo <= t_ms <= (hi if hi is not None else end):
            return i
    return None


def build_segments(samples: list[Sample], intervals: list[list], start: int, end: int,
                   hz: int, game_map: MapInfo) -> tuple[list[dict], dict]:
    """Splits a slot's samples into grid-snapped segments; returns them and coverage stats."""
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
               or math.dist((sample.x, sample.y, sample.z),
                            (previous[0].x, previous[0].y, previous[0].z)) > TELEPORT_UNITS)
        if brk:
            runs.append([])
        runs[-1].append((sample, interval))
        previous = (sample, interval)

    segments = []
    covered_points = 0
    for run in runs:
        grid: dict[int, tuple[int, int, int]] = {}
        for sample, _ in run:
            k = int(round((sample.t_ms - start) / 1000.0 * hz))
            u, v = game_map.to_uv(sample.x, sample.y)
            grid[k] = (u, v, game_map.yaw_to_map(sample.yaw))  # the last sample on a grid point wins
        keys = sorted(grid)
        u_list, v_list, yaw_list = [], [], []
        for a, b in zip(keys, keys[1:] + [None]):
            ua, va, ya = grid[a]
            u_list.append(ua), v_list.append(va), yaw_list.append(ya)
            if b is None:
                continue
            # PROVISIONAL(D5): grid points the source skipped inside a segment (< SEGMENT_GAP_S)
            # are filled linearly, yaw along the shortest arc; never beyond the source's rate.
            ub, vb, yb = grid[b]
            turn = (yb - ya + 180) % 360 - 180
            for step in range(1, b - a):
                f = step / (b - a)
                u_list.append(int(round(ua + (ub - ua) * f)))
                v_list.append(int(round(va + (vb - va) * f)))
                yaw_list.append(int(round(ya + turn * f)) % 360)
        covered_points += len(u_list)
        segments.append(fmt.encode_segment(keys[0] / hz, u_list, v_list, yaw_list))

    alive_s = sum(((hi if hi is not None else end) - lo) / 1000.0 for lo, hi, _ in intervals)
    max_gap = 0.0
    for lo, hi, _ in intervals:
        hi = hi if hi is not None else end
        bounds = [lo, *(s.t_ms for s in samples if lo <= s.t_ms <= hi), hi]
        max_gap = max(max_gap, *((b - a) / 1000.0 for a, b in zip(bounds, bounds[1:])))
    coverage = min(1.0, covered_points / hz / alive_s) if alive_s > 0 else 1.0
    return segments, {"coverage": round(coverage, 3), "max_gap_s": round(max_gap, 3)}


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


# ---------------------------------------------------------------- entry points


UUID_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def match_uuid_of(game: GameState, manifest: dict) -> tuple[str, str]:
    """(match UUID, where it came from). The game state's MatchID when decoded, else the
    `.vrf`'s file name: the client names a replay after its match."""
    if game.match_uuid is not None:
        return game.match_uuid, "game_state"
    stem = Path(str(manifest.get("source_file") or "")).stem.lower()
    if UUID_SHAPE.match(stem):
        return stem, "vrf_file_name"
    raise ContractError("match_id", "no MatchID in the game state, and the .vrf isn't named by a match UUID")


def condense(export: Export, *, maps: dict[str, MapInfo], agents_by_code: dict[str, str], recipe: str,
             map_override: str | None = None, vrf_map_codes: dict[str, int] | None = None) -> CondensedReplay:
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
    match_uuid, match_uuid_source = match_uuid_of(game, export.manifest)
    if not game.windows:
        raise ContractError("rounds", "no round has both InRound and RoundEnding")
    by_slot, movement_counts = read_movement(export, players)
    hz = native_hz(by_slot)
    kills = read_kills(export, players)
    revives = read_revives(export, players)
    pawn_times = {slot: [t for t, _ in changes[1:]] for slot, changes in players.pawn_changes.items()}

    def present(slot: int, start: int) -> bool:
        gone = players.gone_ms.get(slot)
        return gone is None or gone > start

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
    # P-a: every kill falls in one playback window, or in the dropped final round (excluded
    # with its reason); any other kill refuses.
    excluded = {"dropped_final_round": 0}
    if game.dropped_final_start is not None:
        excluded["dropped_final_round"] = sum(
            1 for k in kills if game.dropped_final_start <= k.t_ms <= game.dropped_final_end)
    outside = len(kills) - assigned - excluded["dropped_final_round"]
    if outside:
        raise ContractError("kills_outside_rounds", f"{outside} kills fall in no round window")
    sides = side_groups(kills, clusters)
    player_rows = [{"slot": slot, "agent": players.agents[slot], "side": sides[slot]} for slot in range(10)]

    for n, (start, decided, end) in enumerate(game.windows, 1):
        tracks: dict[str, list] = {}
        alive: dict[str, list] = {}
        for slot in range(10):
            deaths = [k.t_ms for k in round_kills[n] if k.victim == slot]
            revived = [t for t, s in revives if s == slot and start <= t <= end]
            pawns = [t for t in pawn_times.get(slot, []) if start < t <= end]
            intervals = alive_intervals(start, end, deaths, revived, pawns, players.gone_ms.get(slot))
            alive[str(slot)] = [[_seconds(lo, start), None if hi is None else _seconds(hi, start), cause]
                                for lo, hi, cause in intervals]
            segments, stats = build_segments(by_slot.get(slot, []), intervals, start, end, hz, game_map)
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
            # PROVISIONAL(D9): no plant/defuse source yet; the first export's TimedBomb wasn't decoded.
            "plant": None, "defuse": None,
            "util": [],
        }

    encoded = {n: fmt.encode_blob(blob) for n, blob in rounds.items()}
    # PROVISIONAL(D8): RoundResults index i (0-based, the decoder's encodedIndex - 1) is played round i + 1.
    # The first export decoded no RoundResults at all: the linker then checks no winners.
    round_results = {str(i + 1): fields for i, fields in sorted(game.round_results.items())}
    report = {
        "diagnostics": diagnostics.as_dict(),
        "map": {"name": game_map.name, **map_evidence},
        "match_uuid_source": match_uuid_source,
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
        "kills_outside_rounds": outside,
        "kills_excluded": excluded,
        "ownership": players.ownership,
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
        "kills": link_kills,
        "round_results_fallback": game.round_results_fallback,
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
                        vrf_path: Path | None = None) -> CondensedReplay:
    """Checks the contract, then condenses. Blocking diagnostics refuse unless `allow_blocking`
    (the preview shows them instead). `vrf_path` lets the map come from the replay file when
    the export doesn't name it."""
    pin = pin or load_pin()
    export = load_export(export_dir)
    check_manifest(export.manifest, pin, source_sha256, build)
    blocking = classify_diagnostics(export.manifest).blocking
    if blocking and not allow_blocking:
        raise ContractError("blocking_diagnostics", "; ".join(blocking))
    vrf_map_codes = map_codes_in_file(vrf_path) if vrf_path is not None and vrf_path.is_file() else None
    return condense(export, maps=load_maps(static_dir), agents_by_code=load_agents(static_dir),
                    recipe=fmt.recipe(pin.commit, fmt.assets_revision(static_dir)), map_override=map_override,
                    vrf_map_codes=vrf_map_codes)
