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
- the output is then scanned for every identity in the original export (Subjects, the
  MatchID, the source name and hash, and every UUID in the original rows), for user paths
  and for `Name#TAG` patterns. Any hit deletes the output and exits 3.

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

KEEP_RPCS = {contract.RPC_KILLED_ENEMY, contract.RPC_SET_PHASE, contract.RPC_PHASE_BEGIN, contract.RPC_RESURRECT}
KEEP_GROUPS = {contract.PLAYER_STATE_PATH, contract.GAME_STATE_PATH}
PLAYER_FIELDS = {"Subject", "SpawnedCharacter", "PossessedCharacter"}
GAME_FIELDS = {"MatchID", "Phase", "RoundNumber", "RoundResults"}
RPC_FIELDS = {"KillerCharacter", "KilledCharacter", "MultikillLevel", "NewPhase", "NewRoundNumber",
              "ResurrectorPlayer", "ResurrectedPlayer"}
MOVEMENT_FIELDS = ("type", "time_ms", "actor_net_guid", "shooter_character_net_guid", "position", "yaw",
                   "timestamp", "error_sentinel")
MANIFEST_FIELDS = ("schema_version", "replay_build", "replay_version", "parser_version", "parse_status", "stats",
                   "total_diagnostic_count", "suppressed_diagnostic_count")
MAP_PATH = re.compile(r"(/Game/Maps/[^/.]+/)")


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
    return hits


def _window_ms(export: contract.Export, rounds: int) -> tuple[int, int]:
    """(where kept movement starts, where every kept row ends)."""
    game = read_game_state(export)
    if len(game.windows) < rounds:
        raise FixtureRefused(f"the export has {len(game.windows)} complete rounds, fewer than {rounds}")
    # Movement from just before the first round (the buy phase before it is dead weight);
    # everything up to a little past the last kept RoundEnding, so its RoundResults update is included.
    return game.windows[0][0] - 3000, game.windows[rounds - 1][1] + 2000


def _synthesise_payload(payload: dict, allowed: set[str], subjects: dict[str, str]) -> dict:
    out = {}
    for key, value in payload.items():
        if key not in allowed:
            continue
        if key == "Subject":
            value = subjects[str(value).lower()] if value else value
        elif key == "MatchID":
            value = SYNTHETIC_MATCH if value else value
        elif key == "RoundResults" and isinstance(value, list):
            value = [{k: item.get(k) for k in ("RoundNumber", "WinningTeam", "WinningTeamRole", "RoundResult")}
                     for item in value if isinstance(item, dict)]
        out[key] = value
    return out


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
        base = {"type": kind, "time_ms": row.time_ms, "actor_net_guid": data.get("actor_net_guid")}
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
            events.append(base)
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
        "fixture": {"rounds": rounds, "note": "synthesised by scripts/make_replay_fixture.py"},
    })
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
    export = contract.load_export(export_dir)
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
