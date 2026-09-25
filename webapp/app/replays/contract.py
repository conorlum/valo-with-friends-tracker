"""What the condenser relies on from the pinned parser's export (schema 8).

An export directory holds `manifest.json`, `events.ndjson` and `movement.ndjson`
(upstream `CliReader export`). This module:

- checks the manifest against `webapp/replay_parser.json` (schema version, source hash,
  game build, parser commit) and, when given, the build's `BUILD.json`;
- classifies the manifest's diagnostics as blocking, link-blocking, coverage or ignored;
- reads the NDJSON rows in contract order: sorted by `time_ms`, equal times keep file
  order;
- folds partial export-group updates per actor, in stream order.

Field names and paths below were read from the parser's source (upstream `2b66c65`),
not yet from a real export: they are PROVISIONAL(D4) until Stage 1a confirms them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

PIN_FILE = Path(__file__).resolve().parents[2] / "replay_parser.json"

# PROVISIONAL(D4): names read from the parser source, confirmed by the first export.
PLAYER_STATE_PATH = "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C"
GAME_STATE_PATH = "/Game/GameModes/Bomb/BombGameState.BombGameState_C"
RPC_KILLED_ENEMY = "MulticastNotifyKilledEnemy"
RPC_SET_PHASE = "MulticastSetPhase"
RPC_END_ROUND = "MulticastEndRound"
RPC_RESURRECT = "MulticastReceivePlayerResurrectEvent"

# PROVISIONAL(D6): diagnostic thresholds, frozen at Stage 1a/1b. Zero is the strictest
# choice; the first real export shows the counts a good parse produces.
MAX_MALFORMED_PACKETS = 0
MAX_PARTIAL_ERRORS = 0

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

    @property
    def patch_hash(self) -> str:
        """The same hash `scripts/build_replay_parser.ps1` writes into BUILD.json."""
        text = "".join(f"{p['file']}\n{p['find']}\n{p['replace']}\n" for p in self.patches)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_pin(path: Path = PIN_FILE) -> ParserPin:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ParserPin(
        commit=data["commit"],
        schema_version=int(data["export_schema_version"]),
        supported_builds=frozenset(data["supported_replay_builds"]),
        patches=tuple(data["patches"]),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


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
    # PROVISIONAL(D7): a 12-character prefix of the pinned commit must appear in parser_version.
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

    def as_dict(self) -> dict:
        return {"blocking": self.blocking, "link_blocking": self.link_blocking,
                "coverage": self.coverage, "ignored": dict(sorted(self.ignored.items()))}


def classify_diagnostics(manifest: dict) -> DiagnosticReport:
    report = DiagnosticReport()
    status = manifest.get("parse_status")
    if status not in GOOD_PARSE_STATUSES:
        report.blocking.append(f"parse_status {status!r}")
    stats = manifest.get("stats") or {}
    if stats.get("malformed_packet_count", 0) > MAX_MALFORMED_PACKETS:
        report.blocking.append(f"malformed_packet_count {stats['malformed_packet_count']} > {MAX_MALFORMED_PACKETS}")
    if stats.get("partial_error_count", 0) > MAX_PARTIAL_ERRORS:
        report.blocking.append(f"partial_error_count {stats['partial_error_count']} > {MAX_PARTIAL_ERRORS}")
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
        else:
            report.ignored[code] = report.ignored.get(code, 0) + 1
    return report


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

    def of_type(self, row_type: str) -> Iterator[Row]:
        return (row for row in self.events if row.data.get("type") == row_type)

    def export_groups(self, path: str) -> Iterator[Row]:
        return (row for row in self.events
                if row.data.get("type") == "export_group_received" and row.data.get("export_group_path") == path)

    def rpcs(self, function_name: str) -> Iterator[Row]:
        return (row for row in self.events
                if row.data.get("type") == "rpc_received" and row.data.get("function_name") == function_name)


def load_export(export_dir: Path) -> Export:
    names = ("manifest.json", "events.ndjson", "movement.ndjson")
    missing = [name for name in names if not (export_dir / name).exists()]
    if missing:
        raise ContractError("incomplete_export", f"{export_dir} lacks {', '.join(missing)}")
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    return Export(manifest, read_ndjson(export_dir / "events.ndjson"), read_ndjson(export_dir / "movement.ndjson"))


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
