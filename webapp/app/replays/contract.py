"""What the condenser relies on from the pinned parser's export (schema 8).

An export directory holds `manifest.json`, `events.ndjson` and `movement.ndjson`
(upstream `CliReader export`). This module:

- checks the manifest against `webapp/replay_parser.json` (schema version, source hash,
  game build, parser commit) and, when given, the build's `BUILD.json`;
- classifies the manifest's diagnostics as blocking, link-blocking, coverage or ignored;
- reads the NDJSON rows in contract order: sorted by `time_ms`, equal times keep file
  order;
- folds partial export-group updates per actor, in stream order.

Field names and paths below were first read from the parser's source (upstream `2b66c65`)
and then checked against the first real export (a Swiftplay match on 13.06, 2026-09-25).
What that export showed:

- players are character pawns (`actor_spawned`, archetype `Default__<Code>_PC_C`), each
  pointing at its player state through the character's `PlayerState` property. The mode's
  player state (`Swiftplay_EoRCredits_PlayerState_C`) and game state aren't decoded, so
  there are no Subjects, no `RoundResults` and no `MatchID` in that export. The legacy
  `BombPlayerState`/`BombGameState` shapes are still read when a replay has them;
- phases arrive as `ClientGamePhaseBegin` RPCs on the recording's own controller;
- the map is not named anywhere in the export, only in the `.vrf` (`map_codes_in_file`).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

PIN_FILE = Path(__file__).resolve().parents[2] / "replay_parser.json"

# Legacy shapes from the parser source; the first real export had neither (see above).
PLAYER_STATE_PATH = "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C"
GAME_STATE_PATH = "/Game/GameModes/Bomb/BombGameState.BombGameState_C"
# Confirmed by the first real export.
RPC_KILLED_ENEMY = "MulticastNotifyKilledEnemy"
RPC_PHASE_BEGIN = "ClientGamePhaseBegin"
RPC_PHASE_ENDED = "ClientGamePhaseEnded"
CHARACTER_PLAYER_STATE = "PlayerState"
# Not seen in the first export (no revive happened); the name is from the parser source.
RPC_SET_PHASE = "MulticastSetPhase"
RPC_RESURRECT = "MulticastReceivePlayerResurrectEvent"
# Confirmed by the Abyss export (81e38a00): a lethal hit's damage notify, which names the victim's
# pawn (`Character`) and the killer's (`EventInstigatorPawn`). Three of its kills (all Gekko's) had
# this and no MulticastNotifyKilledEnemy; every other player kill in it and in the Ascent export had both.
RPC_DAMAGE = frozenset({"MulticastNotifyDamage_Point", "MulticastNotifyDamage_Base"})

# A malformed packet blocks. Partial-bunch errors don't: they drop data on
# one channel, and the condenser's per-player coverage and gap checks measure what that
# costs a track. The first export had 76, across the recorder's controller and object
# channels, with every player's track intact.
MAX_MALFORMED_PACKETS = 0

KNOWN_DIAGNOSTIC_CODES = frozenset({"partial_sequence_error", "incomplete_partial_bunch", "raw_payload_fallback"})
GOOD_PARSE_STATUSES = frozenset({"completed", "completed_with_warnings"})


class ContractError(ValueError):
    """The export can't be condensed. `reason` is a short, user-facing code."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ParserPin:
    commit: str
    schema_version: int
    supported_builds: frozenset[str]
    patches: tuple[dict, ...]
    source_patches: tuple[dict, ...] = ()

    @property
    def patch_hash(self) -> str:
        """The same hash `scripts/build_replay_parser.ps1` writes into BUILD.json."""
        text = "".join(f"{p['file']}\n{p['find']}\n{p['replace']}\n" for p in self.patches)
        text += "".join(f"{p['file']}\n{p['sha256']}\n" for p in self.source_patches)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_pin(path: Path = PIN_FILE) -> ParserPin:
    data = json.loads(path.read_text(encoding="utf-8"))
    for patch in data.get('source_patches', []):
        source = path.parent / patch['file']
        if hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != patch['sha256']:
            raise ContractError('parser_patch', 'source patch digest differs from the pin')
    return ParserPin(
        commit=data["commit"],
        schema_version=int(data["export_schema_version"]),
        supported_builds=frozenset(data["supported_replay_builds"]),
        patches=tuple(data["patches"]),
        source_patches=tuple(data.get('source_patches', [])),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def current_recipe(pin: ParserPin | None = None, static_dir: Path | None = None) -> str:
    """The recipe a parse on this deploy would stamp (condense_export_dir builds the same string): what the
    worker reports in /health and the site compares a stored replay against."""
    from app.replays import format as fmt

    return fmt.recipe((pin or load_pin()).commit, fmt.assets_revision(static_dir or fmt.STATIC_DIR))


def check_manifest(manifest: dict, pin: ParserPin, source_sha256: str | None,
                   build: dict | None = None) -> None:
    """Refuses (ContractError) unless the export came from the pinned parser and this file.

    `source_sha256` is the hash of the `.vrf` that was exported; None skips that check
    (only for a preview of an export whose source isn't at hand, and the caller says so).
    """
    if manifest.get("schema_version") != pin.schema_version:
        raise ContractError("schema_version", f"export schema {manifest.get('schema_version')!r}, "
                                              f"expected {pin.schema_version}")
    if source_sha256 is not None and manifest.get("source_sha256") != source_sha256:
        raise ContractError("source_sha256", "the export is not of this .vrf file")
    if manifest.get("replay_build") not in pin.supported_builds:
        raise ContractError("replay_build", f"game build {manifest.get('replay_build')!r} is not supported "
                                            f"by the pinned parser (replay_parser.json)")
    # A 12-character prefix of the pinned commit must appear in parser_version.
    if pin.commit[:12] not in str(manifest.get("parser_version", "")):
        raise ContractError("parser_version", f"export made by {manifest.get('parser_version')!r}, "
                                              f"not the pinned commit {pin.commit[:12]}")
    if build is not None:
        if build.get("commit") != pin.commit:
            raise ContractError("parser_build", "BUILD.json is from a different commit; rebuild the parser")
        if build.get("patch_hash") != pin.patch_hash:
            raise ContractError("parser_build", "BUILD.json's patches differ from replay_parser.json; rebuild")


@dataclass
class DiagnosticReport:
    blocking: list[str] = field(default_factory=list)
    link_blocking: list[str] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    ignored: dict[str, int] = field(default_factory=dict)
    channels: dict[int | None, int] = field(default_factory=dict)  # partial-bunch errors per channel

    def as_dict(self) -> dict:
        return {"blocking": self.blocking, "link_blocking": self.link_blocking,
                "coverage": self.coverage, "ignored": dict(sorted(self.ignored.items())),
                "partial_error_channels": {str(k): n for k, n in sorted(self.channels.items(), key=lambda kv: str(kv[0]))}}


def classify_diagnostics(manifest: dict) -> DiagnosticReport:
    report = DiagnosticReport()
    status = manifest.get("parse_status")
    if status not in GOOD_PARSE_STATUSES:
        report.blocking.append(f"parse_status {status!r}")
    stats = manifest.get("stats") or {}
    if stats.get("malformed_packet_count", 0) > MAX_MALFORMED_PACKETS:
        report.blocking.append(f"malformed_packet_count {stats['malformed_packet_count']} > {MAX_MALFORMED_PACKETS}")
    if manifest.get("suppressed_diagnostic_count", 0):
        # Suppressed diagnostics can't be classified, so they can't be ruled harmless.
        report.blocking.append(f"{manifest['suppressed_diagnostic_count']} suppressed diagnostics")
    for diagnostic in manifest.get("diagnostics", []):
        code = diagnostic.get("code")
        group = diagnostic.get("export_group_path") or ""
        name = diagnostic.get("field_name") or ""
        where = f"{code} on {group or '?'}{'.' + name if name else ''}"
        if code not in KNOWN_DIAGNOSTIC_CODES:
            report.blocking.append(f"unclassified diagnostic {where}")
        elif group == PLAYER_STATE_PATH:
            report.blocking.append(where)
        elif code == "raw_payload_fallback" and group == GAME_STATE_PATH and name == "RoundResults":
            # A replay with no DB match can still play; it just can't link.
            report.link_blocking.append(where)
        elif code in ("partial_sequence_error", "incomplete_partial_bunch"):
            report.ignored[code] = report.ignored.get(code, 0) + 1
            channel = diagnostic.get("channel_index")
            report.channels[channel] = report.channels.get(channel, 0) + 1
        else:
            report.ignored[code] = report.ignored.get(code, 0) + 1
    if report.channels:
        report.coverage.append(f"{sum(report.channels.values())} partial-bunch errors on "
                               f"{len(report.channels)} channels (see the coverage checks)")
    return report


MAP_IN_FILE = re.compile(rb"/Game/Maps/([A-Za-z0-9_]+)/\1\b")


def map_codes_in_file(path: Path) -> dict[str, int]:
    """Map code -> hits of `/Game/Maps/<code>/<code>` in the `.vrf`'s bytes.

    The export doesn't name the map; the replay's header and level references do.
    """
    counts: dict[str, int] = {}
    with path.open("rb") as handle:
        for code in MAP_IN_FILE.findall(handle.read()):
            name = code.decode("ascii")
            counts[name] = counts.get(name, 0) + 1
    return counts


@dataclass(frozen=True)
class Row:
    index: int  # position in its file
    time_ms: int
    data: dict


def read_ndjson(path: Path) -> list[Row]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            data = json.loads(line)
            rows.append(Row(index, int(data.get("time_ms", 0)), data))
    # Contract order: time_ms, then file order (sorted() is stable).
    return sorted(rows, key=lambda row: row.time_ms)


@dataclass
class Export:
    manifest: dict
    events: list[Row]
    movement: list[Row]
    # The last event's time: the recording's end, where the final round's playback stops.
    end_ms: int = 0

    def of_type(self, row_type: str) -> Iterator[Row]:
        return (row for row in self.events if row.data.get("type") == row_type)

    def export_groups(self, path: str) -> Iterator[Row]:
        return (row for row in self.events
                if row.data.get("type") == "export_group_received" and row.data.get("export_group_path") == path)

    def rpcs(self, function_name: str) -> Iterator[Row]:
        return (row for row in self.events
                if row.data.get("type") == "rpc_received" and row.data.get("function_name") == function_name)


def _require_files(export_dir: Path) -> None:
    names = ("manifest.json", "events.ndjson", "movement.ndjson")
    missing = [name for name in names if not (export_dir / name).exists()]
    if missing:
        raise ContractError("incomplete_export", f"{export_dir} lacks {', '.join(missing)}")


def load_manifest(export_dir: Path) -> dict:
    _require_files(export_dir)
    return json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))


def load_export(export_dir: Path) -> Export:
    """The whole export in memory, in contract order (the reference the streaming loader must
    match byte for byte; about 1.5 GB of rows for a 14-minute match)."""
    manifest = load_manifest(export_dir)
    events = read_ndjson(export_dir / "events.ndjson")
    return Export(manifest, events, read_ndjson(export_dir / "movement.ndjson"), events[-1].time_ms if events else 0)


# ---------------------------------------------------------------- streaming (W-b)

# The event rows the condenser reads. Everything else (shots, utility paths, the other RPCs:
# 115k of the first real export's 142k rows, most of its 814 MB) is skipped unparsed.
# W-e: the typed utility rows (casts and hits); their Subjects are null, casters resolve through pawns.
# 2026-10-05: a flash's explosion and its path samples too (condense.read_util: where and when it really
# popped, and whether it is proven to have gone off at all).
UTIL_EVENT_TYPES = frozenset({"valorant_flash_cast", "valorant_flash_player_hit", "valorant_nearsight_cast",
                              "valorant_nearsight_player_hit", "valorant_flash_exploded",
                              "valorant_flash_path_updated"})
STREAM_EVENT_TYPES = frozenset({"actor_spawned", "actor_closed", *UTIL_EVENT_TYPES})
STREAM_PAYLOAD_KEYS = frozenset({CHARACTER_PLAYER_STATE, "Subject", "SpawnedCharacter", "PossessedCharacter"})
STREAM_RPCS = frozenset({RPC_KILLED_ENEMY, RPC_PHASE_BEGIN, RPC_PHASE_ENDED, RPC_SET_PHASE, RPC_RESURRECT})
STREAM_GROUP_PATHS = frozenset({PLAYER_STATE_PATH, GAME_STATE_PATH})
PATH_KEYS = ("actor_path", "archetype_path", "object_path", "outer_path")
# Substrings that every kept row's line contains; a line with none of them can't be kept, so it
# is never parsed. The exact test (`keep_event`) runs on the parsed rows.
_NEEDLES = tuple(f'"{name}"' for name in (*STREAM_EVENT_TYPES, *STREAM_PAYLOAD_KEYS, *STREAM_RPCS)) + (
    "BombPlayerState", "BombGameState", "Maps", '"DamageKilledTarget"')


def keep_event(data: dict) -> bool:
    """Whether the condenser reads this event row (the streaming loader's exact filter)."""
    kind = data.get("type")
    if kind in STREAM_EVENT_TYPES:
        return True
    if kind == "rpc_received" and data.get("function_name") in STREAM_RPCS:
        return True
    if kind == "rpc_received" and data.get("function_name") in RPC_DAMAGE:
        # Every damage notify: lethal ones are kills (read_kills), and all of them are the blob's
        # `damage` runs (read_damage; about 2,300 rows a match).
        return isinstance(data.get("payload"), dict)
    if kind == "export_group_received":
        payload = data.get("payload")
        if data.get("export_group_path") in STREAM_GROUP_PATHS:
            return True
        if isinstance(payload, dict) and STREAM_PAYLOAD_KEYS.intersection(payload):
            return True
    return any(isinstance(data.get(key), str) and "/Game/Maps/" in data[key] for key in PATH_KEYS)


class MovementStream:
    """`movement.ndjson` as a re-iterable stream of Rows, in file order, never held.

    Contract order is by `time_ms` with ties in file order. The condenser only reads movement
    per slot, and `read_movement` stable-sorts each slot's samples by time, which gives the
    same lists as a global stable sort would (the first real export is already sorted).
    """

    def __init__(self, path: Path):
        self.path = path

    def __iter__(self) -> Iterator[Row]:
        with self.path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if line.strip():
                    data = json.loads(line)
                    yield Row(index, int(data.get("time_ms", 0)), data)


def load_export_streaming(export_dir: Path) -> Export:
    """The export with only the event rows the condenser reads, and movement as a stream (W-b).

    One pass over `events.ndjson` keeps the rows `keep_event` accepts, then stable-sorts that
    small subset by `time_ms`. Stable sorting commutes with filtering, so the subset is in the
    same order as in the full contract-ordered list, and every condenser pass over it sees
    exactly what it would see in memory. `end_ms` is the last event's time over all rows.
    """
    manifest = load_manifest(export_dir)
    rows = []
    end_ms = 0
    with (export_dir / "events.ndjson").open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            if not any(needle in line for needle in _NEEDLES):
                # The time of every row still counts for end_ms: read just that field.
                end_ms = max(end_ms, _time_of(line))
                continue
            data = json.loads(line)
            time_ms = int(data.get("time_ms", 0))
            end_ms = max(end_ms, time_ms)
            if keep_event(data):
                rows.append(Row(index, time_ms, data))
    return Export(manifest, sorted(rows, key=lambda row: row.time_ms), MovementStream(export_dir / "movement.ndjson"),
                  end_ms)


_TIME = re.compile(r'"time_ms"\s*:\s*(-?\d+)')


def _time_of(line: str) -> int:
    """A row's `time_ms` without parsing the row (a skipped row can still end the recording)."""
    match = _TIME.search(line)
    if match is None:
        return int(json.loads(line).get("time_ms", 0))
    return int(match.group(1))


@dataclass
class ActorState:
    """The fold of one actor's partial export-group updates, with each property's history."""
    values: dict[str, Any] = field(default_factory=dict)
    history: dict[str, list[tuple[int, Any]]] = field(default_factory=dict)


def fold_export_groups(rows: Iterator[Row]) -> dict[int, ActorState]:
    """Folds partial updates per `actor_net_guid` in contract order.

    An update carries only the properties decoded in it; a property absent from an update
    keeps its earlier value.
    """
    actors: dict[int, ActorState] = {}
    for row in rows:
        payload = row.data.get("payload")
        if not isinstance(payload, dict):
            continue
        state = actors.setdefault(int(row.data["actor_net_guid"]), ActorState())
        for key, value in payload.items():
            state.values[key] = value
            state.history.setdefault(key, []).append((row.time_ms, value))
    return actors
