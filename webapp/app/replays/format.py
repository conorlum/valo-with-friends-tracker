"""JSON format v1 for one condensed round, and the replay recipe.

A round blob is `gzip(json)` of:

    {"v": 1, "round": 7, "map": "Ascent", "hz": 16, "t_start": 0.0, "t_decided": 91.4, "t_end": 98.4,
     "players": [{"slot": 0, "agent": "Jett", "side": "A"}, ...],
     "tracks": {"0": [{"t0": 0.0, "u": [4121, 9, -3], "v": [...], "yaw": [...]}, ...]},
     "alive": {"0": [[0.0, 41.3, "kill"]]},
     "kills": [{"i": 0, "t": 41.3, "killer": 5, "victim": 0, "u": 4500, "v": 6012}],
     "plant": null, "defuse": null,
     "util": [{"k": "flash", "t": 12.4, "by": 3, "u": 5120, "v": 4410, "ability": "...", "targets": [6, 8]},
              {"k": "ability", "t": 3.1, "by": 3, "t1": 21.0, "kind": "Zone", "code": "Wraith", ...},
              {"k": "shot", "t": 40.2, "by": 5, "u": 4100, "v": 3300, "u1": 4390, "v1": 3310, ...},
              {"k": "damage", "t": 40.1, "t1": 40.3, "by": 5, "target": 1, "src": "gun", "wall": false, "n": 3}]}

A `flash`/`nearsight` row's `hits` is `[[target slot, hit t, duration s | null], ...]` (revision 10).

Every `t` is seconds since the round's `InRound` phase start on the replay's own clock.
`t_decided` is the `RoundEnding` phase (the round was decided); playback runs on to
`t_end`, the next phase, so post-decision kills stay in the blob. In a track segment the first value of each array is absolute and the rest are
differences from the previous sample, one sample per `1/hz` seconds from `t0`.
`u`/`v` are ints in 0..10000 of the square minimap; `yaw` is an int in degrees 0..359 in
minimap space (0 = +u, 90 = +v).

Heights (revision 11; docs/superpowers/specs/2026-10-01-control-heights-design.md, part 2) are optional
keys, all ints in decimetres of the game's world z (no map offset). A missing height is a missing
key, never 0. A track segment has `"z"` (delta-encoded like `u`) only when every one of its samples
had one. An `ability` row has `"z"` (its spawn, i.e. where a thrown one landed), `"z"` inside
`thrown` (the throw's own spawn), `"end_z"` beside a tripwire's `end`, and a fourth value on each
`path` point, `[t, u, v, z]`.

Movement-ability casts (revision 14; docs/superpowers/plans/2026-10-05-height-slopes.md, Task 1b): `util` has
`{"k": "cast", "t", "by", "code", "name"}` per use of a movement ability (app/replays/extras.py
MOVEMENT_EQUIPPABLES: Jett's and Waylay's dashes), and the blob has `"movement_casts": 1` when the condenser
looked for them, so a round with that key and no cast had none. A blob without the key was condensed before.

A Killjoy turret or alarmbot `ability` row has `"off"` (revision 12) when it was switched off (KJ walked out
of its range): `[[from, to | null], ...]` in round seconds, null meaning until the row's `t1`.

Flash rows (2026-10-05) also carry, all optional to a reader: `"id"` (the ability actor: one cast's
identity within the round), `"z"` (the cast's height), `"pop"` (the explosion: `{"t"}` plus either its place
`{"u", "v", ["z"], "src": "flash_source" | "replicated_movement", ["age"]}` or `{"place": "stale" |
"unverified", ["age"]}` when the export's position is the cast's or can't be tied to a unit: never a guessed
place), `"path"` (the flight, `[[t, u, v, z], ...]`, each sample read in its own source's unit) and
`"activation"`:

    {"state": "completed" | "unknown", "evidence": <the export's explosion evidence | null>,
     "targets_complete": true | false, "diagnostics": [...]}

`completed` needs an explosion row with proving evidence. `targets_complete` is true only for a completed
flash with no unresolved target and no unattributed hit near its explosion: only then does an empty `hits`
mean it hit nobody. A row without `activation` (stored before this) proves nothing either way. A hit is kept
whatever its duration, zero included. KAY/O's landed knife (`Grenadier_E_SuppressionPulse`, an `ability` row)
has the same `activation` and, when it pulsed, `"pulse": {"t", "hits": [slots], ["r": radius in uv units]}`.

`FORMAT_VERSION` changes only when the shape changes. `CONDENSE_REVISION` changes with
any condenser change. Staleness is inequality with the current recipe, never ordering.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

FORMAT_VERSION = 1
SUPPORTED_VERSIONS = frozenset({1})
CONDENSE_REVISION = 15   # 15: bounded Ascent map-message evidence and visible state timelines
                         # 12: Killjoy turret/alarmbot `off` spans (extras.device_off_spans);
                         # 13: utility kept in full (2026-10-05): Deadlock's actors, flash explosions, paths and
                         #     activation, knife pulses, projectile flights, Blaze/Sage/Vyse walls, Omen's ult
                         #     marker and its outcome, Waylay's recall;
                         # 14: movement-ability casts (`cast` util rows, `movement_casts`) for the height
                         #     build's blackout (2026-10-08)

UV_SCALE = 10000

# Size budgets: a reported check in the ingest preview, not a refusal. They keep scrubbing between
# rounds quick (the viewer fetches one round at a time) and flag a condenser change that bloats
# rounds. The per-round p95 was 60 KB at the Stage 1b freeze (D7) and 70 KB once rounds stored
# abilities and shots (R2, approved D9): the six competitive matches are 46.7-63.0 KB per round
# (Sunset over 60) and 0.65-1.03 MB per match (approved D7).
ROUND_BUDGET_P95_BYTES = 70_000
MATCH_BUDGET_BYTES = 1_500_000

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
ASSET_FILES = ("data/maps.json", "data/agents.json")


class FormatError(ValueError):
    pass


def delta_encode(values: list[int]) -> list[int]:
    if not values:
        return []
    return [values[0], *(b - a for a, b in zip(values, values[1:]))]


def delta_decode(values: list[int]) -> list[int]:
    out: list[int] = []
    total = 0
    for i, value in enumerate(values):
        total = value if i == 0 else total + value
        out.append(total)
    return out


def encode_yaw_deltas(yaws: list[int]) -> list[int]:
    """Delta-encodes yaw along the shortest arc, so 359 -> 1 is +2, not -358."""
    if not yaws:
        return []
    out = [yaws[0] % 360]
    for a, b in zip(yaws, yaws[1:]):
        out.append((b - a + 180) % 360 - 180)
    return out


def decode_yaw_deltas(values: list[int]) -> list[int]:
    return [yaw % 360 for yaw in delta_decode(values)]


def encode_segment(t0: float, u: list[int], v: list[int], yaw: list[int], z: list[int] | None = None) -> dict:
    """`z` (decimetres of world z) is all of the segment's samples or None: a segment never mixes."""
    if not (len(u) == len(v) == len(yaw)) or not u:
        raise FormatError("a segment needs equal, non-empty u/v/yaw arrays")
    segment = {"t0": round(t0, 3), "u": delta_encode(u), "v": delta_encode(v), "yaw": encode_yaw_deltas(yaw)}
    if z is not None:
        if len(z) != len(u):
            raise FormatError("a segment's z needs one value per sample")
        segment["z"] = delta_encode(z)
    return segment


def decode_segment(segment: dict, hz: int) -> list[tuple[float, int, int, int]]:
    """A segment's samples as (t, u, v, yaw)."""
    u = delta_decode(segment["u"])
    v = delta_decode(segment["v"])
    yaw = decode_yaw_deltas(segment["yaw"])
    if not (len(u) == len(v) == len(yaw)):
        raise FormatError("segment arrays differ in length")
    return [(round(segment["t0"] + i / hz, 6), u[i], v[i], yaw[i]) for i in range(len(u))]


def decode_segment_z(segment: dict, hz: int) -> list[tuple[float, int, int, int, int | None]]:
    """A segment's samples as (t, u, v, yaw, z): z in decimetres of world z, or None for every
    sample of a segment stored without heights (before revision 11, or the parser gave none)."""
    samples = decode_segment(segment, hz)
    if "z" not in segment:
        return [(*sample, None) for sample in samples]
    z = delta_decode(segment["z"])
    if len(z) != len(samples):
        raise FormatError("segment arrays differ in length")
    return [(*sample, z[i]) for i, sample in enumerate(samples)]


def encode_blob(blob: dict) -> bytes:
    """Deterministic bytes: the same blob always gzips to the same bytes (mtime 0, sorted keys)."""
    if blob.get("v") not in SUPPORTED_VERSIONS:
        raise FormatError(f"unsupported format version {blob.get('v')!r}")
    text = json.dumps(blob, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return gzip.compress(text.encode("ascii"), compresslevel=9, mtime=0)


def decode_blob(data: bytes) -> dict:
    blob = json.loads(gzip.decompress(data))
    if blob.get("v") not in SUPPORTED_VERSIONS:
        raise FormatError(f"unsupported format version {blob.get('v')!r}")
    return blob


def known_util(blob: dict, known_kinds: frozenset[str] = frozenset()) -> list[dict]:
    """The blob's util entries whose kind this reader knows; an unknown `k` is ignored."""
    return [entry for entry in blob.get("util", []) if entry.get("k") in known_kinds]


def assets_revision(static_dir: Path = STATIC_DIR) -> str:
    digest = hashlib.sha256()
    for name in ASSET_FILES:
        digest.update(name.encode("ascii") + b"\0")
        digest.update((static_dir / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def recipe(parser_commit: str, assets_rev: str) -> str:
    return f"{parser_commit[:12]}.c{CONDENSE_REVISION}.f{FORMAT_VERSION}.a{assets_rev[:8]}"


def size_report(encoded_rounds: dict[int, bytes]) -> dict:
    sizes = sorted(len(data) for data in encoded_rounds.values())
    if not sizes:
        return {"rounds": 0, "total_bytes": 0, "p95_bytes": 0, "max_bytes": 0, "fits_budget": True}
    p95 = sizes[min(len(sizes) - 1, max(0, -(-95 * len(sizes) // 100) - 1))]
    total = sum(sizes)
    return {
        "rounds": len(sizes),
        "total_bytes": total,
        "p95_bytes": p95,
        "max_bytes": sizes[-1],
        "fits_budget": p95 <= ROUND_BUDGET_P95_BYTES and total <= MATCH_BUDGET_BYTES,
    }
