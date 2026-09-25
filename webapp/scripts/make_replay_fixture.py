"""Builds a trimmed, identity-free test fixture from a parser export.

    .\\.venv313\\Scripts\\python.exe scripts\\make_replay_fixture.py --export-dir %TEMP%\\valo-replay\\<uuid> --name swiftplay --rounds 2

This repository is public, so a fixture is built from an allowlist, never by redaction:

- only the rows and fields the condenser reads (map and character spawns, the player and
  game state groups, four RPCs, movement), within the first `--rounds` rounds;
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
from app.replays.condense import PHASE_IN_ROUND, PHASE_ROUND_ENDING, read_game_state  # noqa: E402

FIXTURES = WEBAPP_ROOT / "tests" / "fixtures" / "replay"
SYNTHETIC_PREFIX = "00000000-0000-4000-8000-"
SYNTHETIC_MATCH = SYNTHETIC_PREFIX + "00000000f1c5"
FIXTURE_SOURCE_BYTES = b"synthetic replay fixture"

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Any number of backslashes: a path may be JSON-escaped once or more.
USER_PATH = re.compile(r"[A-Za-z]:(?:\\+|/)Users(?:\\+|/)|%USERPROFILE%|/Users/|/home/", re.IGNORECASE)
RIOT_ID = re.compile(r"[^\s\"'#{}\[\],:]{3,16}#[A-Za-z0-9]{3,5}\b")

KEEP_RPCS = {contract.RPC_KILLED_ENEMY, contract.RPC_SET_PHASE, contract.RPC_END_ROUND, contract.RPC_RESURRECT}
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
CHARACTER_PATH = re.compile(r"(/Game/Characters/[^/.]+/)")


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


def _cut_off_ms(export: contract.Export, rounds: int) -> int:
    game = read_game_state(export)
    if len(game.windows) < rounds:
        raise FixtureRefused(f"the export has {len(game.windows)} complete rounds, fewer than {rounds}")
    # Keep a little past the last kept RoundEnding so its RoundResults update is included.
    return game.windows[rounds - 1][1] + 2000


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


def build_fixture(export: contract.Export, rounds: int) -> tuple[dict, list[dict], list[dict]]:
    cut = _cut_off_ms(export, rounds)
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
            character = next((m.group(1) for p in paths if (m := CHARACTER_PATH.search(p))), None)
            game_map = next((m.group(1) for p in paths if (m := MAP_PATH.search(p))), None)
            if character:
                events.append({**base, "actor_path": None, "archetype_path": character + "Character"})
            elif game_map and not map_kept:
                events.append({**base, "actor_path": game_map + "Level", "archetype_path": None})
                map_kept = True
        elif kind == "export_group_received" and data.get("export_group_path") in KEEP_GROUPS:
            allowed = PLAYER_FIELDS if data["export_group_path"] == contract.PLAYER_STATE_PATH else GAME_FIELDS
            payload = _synthesise_payload(data.get("payload") or {}, allowed, subjects)
            if payload:
                events.append({**base, "export_group_path": data["export_group_path"], "payload": payload})
        elif kind == "rpc_received" and data.get("function_name") in KEEP_RPCS:
            payload = {k: v for k, v in (data.get("payload") or {}).items() if k in RPC_FIELDS}
            events.append({**base, "function_name": data["function_name"], "payload": payload})

    movement = [{k: row.data.get(k) for k in MOVEMENT_FIELDS} for row in export.movement if row.time_ms <= cut]
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


def make_fixture(export_dir: Path, out: Path, rounds: int) -> list[str]:
    export = contract.load_export(export_dir)
    forbidden = original_identities(export)
    manifest, events, movement = build_fixture(export, rounds)
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
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or FIXTURES / args.name
    try:
        for line in make_fixture(args.export_dir, out, args.rounds):
            print(line)
    except (FixtureRefused, contract.ContractError) as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 3
    print(f"fixture written to {out}; scan passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
