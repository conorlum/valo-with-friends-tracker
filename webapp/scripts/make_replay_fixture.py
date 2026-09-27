"""Builds a trimmed, identity-free test fixture from a parser export.

    .\\.venv313\\Scripts\\python.exe scripts\\make_replay_fixture.py --export-dir %TEMP%\\valo-replay\\<uuid> --name swiftplay --rounds 2 --vrf <archive .vrf> --movement-step 8

This repository is public, so a fixture is built from an allowlist, never by redaction:

- only the rows and fields the condenser reads (map and character spawns with their
  spawn points, character closes, each
  character's `PlayerState` pointer, the player and game state groups, the kill, phase and
  revive RPCs, movement), within the first `--rounds` rounds. When the export names no map
  (the first real one didn't), `--vrf` supplies its code as one synthetic map row;
- `--movement-step N` keeps every Nth movement row per pawn: a full round at the native
  125 Hz is tens of MB, too much to commit. The fixture's `hz` is lower by that factor;
- every identity is replaced consistently with a synthetic one: each Subject by
  `00000000-0000-4000-8000-0000000000NN` (in sorted order), the MatchID and the source file
  name by `00000000-0000-4000-8000-00000000f1c5`;
- the manifest keeps only the contract fields; `source_sha256` becomes the hash of
  `FIXTURE_SOURCE_BYTES`, and the original diagnostics are dropped (their messages can hold
  paths);
- (pass 6, P-h) every kept value is checked against its expected shape (`SHAPES`), not just
  its field name: ints for GUIDs, numbers for positions, short letters-only enum strings,
  synthetic UUIDs. An opaque `RoundResults` value (a raw fallback payload, or items whose
  values don't fit) becomes the synthetic marker `{"fallback": true}`; any other value that
  fails its shape refuses. A pass-5 probe hid an encoded identity in a fallback payload;
- the output is then scanned for every identity in the original export (Subjects, the
  MatchID, the source name and hash, and every UUID in the original rows), for user paths,
  for `Name#TAG` patterns and against the shapes again. Any hit deletes the output and
  exits 3.

Writes `webapp/tests/fixtures/replay/<name>/` (`--out` to override).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.replays import contract  # noqa: E402
from app.replays.condense import character_code, read_game_state  # noqa: E402

FIXTURES = WEBAPP_ROOT / "tests" / "fixtures" / "replay"
SYNTHETIC_PREFIX = "00000000-0000-4000-8000-"
SYNTHETIC_MATCH = SYNTHETIC_PREFIX + "00000000f1c5"
FIXTURE_SOURCE_BYTES = b"synthetic replay fixture"

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Any number of backslashes: a path may be JSON-escaped once or more.
USER_PATH = re.compile(r"[A-Za-z]:(?:\\+|/)Users(?:\\+|/)|%USERPROFILE%|/Users/|/home/", re.IGNORECASE)
RIOT_ID = re.compile(r"[^\s\"'#{}\[\],:]{3,16}#[A-Za-z0-9]{3,5}\b")

KEEP_RPCS = {contract.RPC_KILLED_ENEMY, contract.RPC_SET_PHASE, contract.RPC_PHASE_BEGIN, contract.RPC_PHASE_ENDED,
             contract.RPC_RESURRECT}
KEEP_GROUPS = {contract.PLAYER_STATE_PATH, contract.GAME_STATE_PATH}
PLAYER_FIELDS = {"Subject", "SpawnedCharacter", "PossessedCharacter"}
GAME_FIELDS = {"MatchID", "Phase", "RoundNumber", "RoundResults"}
RPC_FIELDS = {"KillerCharacter", "KilledCharacter", "MultikillLevel", "NewPhase", "OldPhase", "NewRoundNumber",
              "ResurrectorPlayer", "ResurrectedPlayer"}
MOVEMENT_FIELDS = ("type", "time_ms", "actor_net_guid", "channel", "shooter_character_net_guid", "position", "yaw",
                   "timestamp", "error_sentinel")
MANIFEST_FIELDS = ("schema_version", "replay_build", "replay_version", "parser_version", "parse_status", "stats",
                   "total_diagnostic_count", "suppressed_diagnostic_count")
MAP_PATH = re.compile(r"(/Game/Maps/[^/.]+/)")
FALLBACK_MARKER = {"fallback": True}
FIXTURE_NOTE = "synthesised by scripts/make_replay_fixture.py"

# ---------------------------------------------------------------- value shapes (P-h)
# A shape is a callable (value -> bool), a dict of key -> shape (keys optional, no others
# allowed), or ("list", shape). `_null(s)` also allows None.
_INT = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
_NUMBER = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
_BOOL = lambda v: isinstance(v, bool)  # noqa: E731
# Enum-like strings: letters only, at most 32. Base64 and hex almost always carry digits or
# punctuation; an identifier that fails this is replaced (RoundResults) or refused.
_WORD = re.compile(r"^[A-Za-z]{1,32}$").match
_SYNTHETIC_UUID = re.compile(r"^" + re.escape(SYNTHETIC_PREFIX) + r"[0-9a-f]{12}$", re.IGNORECASE).match
_CLOSE_REASON = re.compile(r"^(?:[a-z_]{1,32}|[0-9]{1,2})$").match
_PATH = re.compile(r"^(?:/Game/Maps/[A-Za-z0-9_]{1,32}/Level|Default__[A-Za-z0-9]{1,32}_PC_C)$").match
_VERSION = re.compile(r"^[0-9A-Za-z.+_-]{1,80}$").match
_BUILD = re.compile(r"^\+\+Ares-Core\+release-[0-9]{1,3}\.[0-9]{1,3}$").match


def _null(shape):
    return lambda v: v is None or check_shape(v, shape) == []


def _string(match):
    return lambda v: isinstance(v, str) and bool(match(v))


def _one_of(*values):
    return lambda v: v in values


POSITION = {"x": _NUMBER, "y": _NUMBER, "z": _null(_NUMBER)}
ROUND_RESULT_ITEM = {"RoundNumber": _INT, "WinningTeam": _null(_string(_WORD)),
                     "WinningTeamRole": _null(_string(_WORD)), "RoundResult": _null(_string(_WORD))}
PAYLOAD = {
    "PlayerState": _INT, "SpawnedCharacter": _null(_INT), "PossessedCharacter": _null(_INT),
    "Subject": _null(_string(_SYNTHETIC_UUID)), "MatchID": _null(_string(_SYNTHETIC_UUID)),
    "Phase": _INT, "RoundNumber": _null(_INT),
    "RoundResults": lambda v: v == FALLBACK_MARKER or check_shape(v, ("list", ROUND_RESULT_ITEM)) == [],
    "KillerCharacter": _INT, "KilledCharacter": _INT, "MultikillLevel": _INT, "NewPhase": _INT, "OldPhase": _INT,
    "NewRoundNumber": _INT, "ResurrectorPlayer": _null(_INT), "ResurrectedPlayer": _null(_INT),
}
EVENT = {
    "type": _one_of("actor_spawned", "actor_closed", "export_group_received", "rpc_received"),
    "time_ms": _INT, "actor_net_guid": _null(_INT), "channel": _null(_INT),
    "actor_path": _null(_string(_PATH)), "archetype_path": _null(_string(_PATH)), "location": _null(POSITION),
    "reason": _string(_CLOSE_REASON),
    "export_group_path": _one_of("Character", contract.PLAYER_STATE_PATH, contract.GAME_STATE_PATH),
    "function_name": lambda v: v in KEEP_RPCS,
    "payload": PAYLOAD,
}
MOVEMENT = {
    "type": _one_of("remote_character_movement"), "time_ms": _INT, "actor_net_guid": _null(_INT),
    "channel": _null(_INT), "shooter_character_net_guid": _null(_INT), "position": _null(POSITION),
    "yaw": _null(_NUMBER), "timestamp": _null(_NUMBER), "error_sentinel": _null(_BOOL),
}
MANIFEST = {
    "schema_version": _INT, "replay_build": _string(_BUILD), "replay_version": _null(_string(_VERSION)),
    "parser_version": _string(_VERSION), "parse_status": _string(re.compile(r"^[a-z_]{1,40}$").match),
    "stats": lambda v: isinstance(v, dict) and all(_string(re.compile(r"^[a-z_]{1,40}$").match)(k) and _INT(n)
                                                   for k, n in v.items()),
    "total_diagnostic_count": _null(_INT), "suppressed_diagnostic_count": _null(_INT),
    "source_file": _one_of(f"{SYNTHETIC_MATCH}.vrf"),
    "source_sha256": _one_of(hashlib.sha256(FIXTURE_SOURCE_BYTES).hexdigest()),
    "diagnostics": _one_of([]),
    "fixture": {"rounds": _INT, "note": _one_of(FIXTURE_NOTE)},
}


def check_shape(value, shape, where: str = "") -> list[str]:
    """Every place `value` departs from `shape`, recursively; [] when it fits."""
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            return [f"{where or 'value'}: expected an object"]
        errors = [f"{where}.{key}: unexpected key" for key in value if key not in shape]
        for key, item in value.items():
            if key in shape:
                errors += check_shape(item, shape[key], f"{where}.{key}")
        return errors
    if isinstance(shape, tuple) and shape[0] == "list":
        if not isinstance(value, list):
            return [f"{where or 'value'}: expected a list"]
        return [error for i, item in enumerate(value) for error in check_shape(item, shape[1], f"{where}[{i}]")]
    return [] if shape(value) else [f"{where or 'value'}: unexpected value shape"]


def shape_hits(directory: Path) -> list[str]:
    """The shape check over a written fixture's three files."""
    hits = []
    manifest = directory / "manifest.json"
    if manifest.exists():
        hits += [f"manifest.json{e}" for e in check_shape(json.loads(manifest.read_text(encoding="utf-8")), MANIFEST)]
    for name, shape in (("events.ndjson", EVENT), ("movement.ndjson", MOVEMENT)):
        path = directory / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for i, line in enumerate(handle, 1):
                if line.strip():
                    hits += [f"{name}:{i}{e}" for e in check_shape(json.loads(line), shape)]
    return hits


class FixtureRefused(Exception):
    pass


def synthetic_subject(i: int) -> str:
    return f"{SYNTHETIC_PREFIX}{i:012d}"


def original_identities(export: contract.Export) -> set[str]:
    found = {str(export.manifest.get("source_sha256") or ""), str(export.manifest.get("source_file") or "")}
    found.add(Path(str(export.manifest.get("source_file") or "")).stem)
    for row in [*export.events, *export.movement]:
        found.update(match.lower() for match in UUID.findall(json.dumps(row.data)))
    return {item.lower() for item in found if item and not item.lower().startswith(SYNTHETIC_PREFIX)}


def scan_text(text: str, forbidden: set[str] = frozenset()) -> list[str]:
    """Every identity-like hit in `text`: a forbidden string, a non-synthetic UUID, a user path, a Name#TAG."""
    hits = []
    lowered = text.lower()
    hits += [f"original identity {item[:8]}..." for item in sorted(forbidden) if item in lowered]
    hits += [f"non-synthetic UUID {u[:8]}..." for u in sorted(set(UUID.findall(text)))
             if not u.lower().startswith(SYNTHETIC_PREFIX)]
    hits += [f"user path {m!r}" for m in sorted(set(USER_PATH.findall(text)))]
    hits += [f"Riot ID-like {m!r}" for m in sorted(set(RIOT_ID.findall(text)))]
    return hits


def scan_dir(directory: Path, forbidden: set[str] = frozenset()) -> list[str]:
    hits = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        hits += [f"{path.name}: {hit}" for hit in scan_text(path.read_text(encoding="utf-8"), forbidden)]
    for folder in sorted({p.parent for p in directory.rglob("manifest.json")}):
        hits += [f"shape: {hit}" for hit in shape_hits(folder)]
    return hits


def _window_ms(export: contract.Export, rounds: int) -> tuple[int, int]:
    """(where kept movement starts, where every kept row ends)."""
    game = read_game_state(export)
    if len(game.windows) < rounds:
        raise FixtureRefused(f"the export has {len(game.windows)} complete rounds, fewer than {rounds}")
    # Movement from just before the first round (the buy phase before it is dead weight);
    # everything up to a little past the last kept round's playback end (the next phase after
    # its RoundEnding), so its post-decision period and RoundResults update are included.
    return game.windows[0][0] - 3000, game.windows[rounds - 1][2] + 2000


def _synthesise_payload(payload: dict, allowed: set[str], subjects: dict[str, str]) -> dict:
    out = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key == "Subject":
            value = subjects[str(value).lower()] if value else value
        elif key == "MatchID":
            value = SYNTHETIC_MATCH if value else value
        elif key == "RoundResults":
            value = _round_results(value)
        out[key] = value
    return out


def _round_results(value):
    """RoundResults items with only their fixed keys, or the synthetic fallback marker when the
    value is opaque (not a list of dicts whose values fit ROUND_RESULT_ITEM)."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return dict(FALLBACK_MARKER)
    items = [{k: item.get(k) for k in ROUND_RESULT_ITEM} for item in value]
    if any(set(item) - set(ROUND_RESULT_ITEM) for item in value) or check_shape(items, ("list", ROUND_RESULT_ITEM)):
        return dict(FALLBACK_MARKER)
    return items


def build_fixture(export: contract.Export, rounds: int, map_code: str | None = None,
                  movement_step: int = 1) -> tuple[dict, list[dict], list[dict]]:
    movement_from, cut = _window_ms(export, rounds)
    pawns = {int(row.data["actor_net_guid"]) for row in export.of_type("actor_spawned") if character_code(row.data)}
    states = contract.fold_export_groups(export.export_groups(contract.PLAYER_STATE_PATH))
    originals = sorted({str(s.values["Subject"]).lower() for s in states.values() if s.values.get("Subject")})
    subjects = {subject: synthetic_subject(i) for i, subject in enumerate(originals)}

    events = []
    map_kept = False
    for row in export.events:
        data = row.data
        if row.time_ms > cut:
            continue
        kind = data.get("type")
        base = {"type": kind, "time_ms": row.time_ms, "actor_net_guid": data.get("actor_net_guid"),
                "channel": data.get("channel")}
        if kind == "actor_spawned":
            paths = [data.get("actor_path") or "", data.get("archetype_path") or ""]
            code = character_code(data)
            game_map = next((m.group(1) for p in paths if (m := MAP_PATH.search(p))), None)
            if code:
                events.append({**base, "actor_path": None, "archetype_path": f"Default__{code}_PC_C",
                               "location": data.get("location")})
            elif game_map and not map_kept:
                events.append({**base, "actor_path": game_map + "Level", "archetype_path": None})
                map_kept = True
        elif kind == "actor_closed" and data.get("actor_net_guid") in pawns:
            events.append({**base, "reason": data.get("reason")})
        elif (kind == "export_group_received" and data.get("actor_net_guid") in pawns
              and (data.get("payload") or {}).get(contract.CHARACTER_PLAYER_STATE)):
            events.append({**base, "export_group_path": "Character",
                           "payload": {contract.CHARACTER_PLAYER_STATE: data["payload"][contract.CHARACTER_PLAYER_STATE]}})
        elif kind == "export_group_received" and data.get("export_group_path") in KEEP_GROUPS:
            allowed = PLAYER_FIELDS if data["export_group_path"] == contract.PLAYER_STATE_PATH else GAME_FIELDS
            payload = _synthesise_payload(data.get("payload") or {}, allowed, subjects)
            if payload:
                events.append({**base, "export_group_path": data["export_group_path"], "payload": payload})
        elif kind == "rpc_received" and data.get("function_name") in KEEP_RPCS:
            payload = {k: v for k, v in (data.get("payload") or {}).items() if k in RPC_FIELDS}
            events.append({**base, "function_name": data["function_name"], "payload": payload})

    if not map_kept and map_code:
        events.insert(0, {"type": "actor_spawned", "time_ms": 0, "actor_net_guid": 0,
                          "actor_path": f"/Game/Maps/{map_code}/Level", "archetype_path": None})
    seen: dict[object, int] = {}
    movement = []
    for row in export.movement:
        pawn = row.data.get("shooter_character_net_guid")
        if not movement_from <= row.time_ms <= cut or pawn not in pawns:
            continue
        seen[pawn] = seen.get(pawn, -1) + 1
        if seen[pawn] % movement_step == 0:
            movement.append({k: row.data.get(k) for k in MOVEMENT_FIELDS})
    manifest = {k: export.manifest.get(k) for k in MANIFEST_FIELDS}
    manifest.update({
        "source_file": f"{SYNTHETIC_MATCH}.vrf",
        "source_sha256": hashlib.sha256(FIXTURE_SOURCE_BYTES).hexdigest(),
        "diagnostics": [],
        "fixture": {"rounds": rounds, "note": FIXTURE_NOTE},
    })
    hits = check_shape(manifest, MANIFEST, "manifest")
    hits += [e for row in events for e in check_shape(row, EVENT, f"event@{row['time_ms']}")]
    hits += [e for row in movement for e in check_shape(row, MOVEMENT, f"movement@{row['time_ms']}")]
    if hits:
        raise FixtureRefused("a kept value fails its shape check:\n  " + "\n  ".join(hits[:20]))
    return manifest, events, movement


def write_fixture(out: Path, manifest: dict, events: list[dict], movement: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for name, rows in (("events.ndjson", events), ("movement.ndjson", movement)):
        with (out / name).open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def make_fixture(export_dir: Path, out: Path, rounds: int, vrf: Path | None = None,
                 movement_step: int = 1) -> list[str]:
    # Streaming (W-b): a competitive export is ~5 GB of NDJSON, too much to hold. The kept event
    # rows are every row the condenser reads, a superset of what the fixture copies, so the
    # identity scan still sees every row an output value could come from.
    export = contract.load_export_streaming(export_dir)
    forbidden = original_identities(export)
    map_code = None
    if vrf is not None:
        codes = contract.map_codes_in_file(vrf)
        map_code = max(codes, key=codes.get) if codes else None
    manifest, events, movement = build_fixture(export, rounds, map_code, movement_step)
    if out.exists():
        raise FixtureRefused(f"{out} exists; remove it first")
    write_fixture(out, manifest, events, movement)
    hits = scan_dir(out, forbidden)
    if hits:
        shutil.rmtree(out)
        raise FixtureRefused("identity scan failed, output deleted:\n  " + "\n  ".join(hits))
    return [f"{p.name}: {p.stat().st_size} bytes" for p in sorted(out.iterdir())]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--name", required=True, help="fixture folder name, e.g. swiftplay")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--vrf", type=Path, help="the exported .vrf, for the map when the export names none")
    parser.add_argument("--movement-step", type=int, default=1, help="keep every Nth movement row per pawn")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or FIXTURES / args.name
    try:
        for line in make_fixture(args.export_dir, out, args.rounds, args.vrf, args.movement_step):
            print(line)
    except (FixtureRefused, contract.ContractError) as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 3
    print(f"fixture written to {out}; scan passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
