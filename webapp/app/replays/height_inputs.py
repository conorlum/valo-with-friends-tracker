"""What a map's height build is made from, as both services say it
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 2 to 4).

The **input manifest** names everything a build reads: each replay's stored blobs (its match, the recipe they
were condensed under, the source file's hash and how many rounds), the map's sight and walk masks and its
scale, the asset format and the build rules' revision, and the must-block check set. Its digest:

- names the build on the replay worker, so a result is never reused for other inputs;
- decides when a rebuild is due (app/services/replay_heights_remote.py), by comparing it with the last
  build's;
- is what the worker's child recomputes from its own files and echoes, and what the web app compares again
  just before it activates a result.

The **check set** is the hand-kept list of sightlines that are impossible in game (`must_block.json`). A file
that is missing or unreadable is an error here, never an empty list: an unattended gate must not pass because
it had nothing to check. A readable file with no line for a map is a real answer.

Standard library only: the web app, the worker's server and its children all import this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.replays import control_format as cf
from app.replays import format as fmt

CONTROL_DIR = fmt.STATIC_DIR / "data" / "control"
MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
MUST_BLOCK = CONTROL_DIR / "must_block.json"
MIN_CONDENSE_REVISION = 11       # blobs from before it carry no heights
MANIFEST_VERSION = 1


class CheckSetError(ValueError):
    """The must-block check set can't be read: nothing may pass the check."""


def must_block_set(path: Path | None = None) -> tuple[list, str]:
    """(the lines, 12 hex of the file's bytes). Raises CheckSetError for a missing or malformed file."""
    path = MUST_BLOCK if path is None else Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CheckSetError(f"{path.name} is missing or unreadable: {error}") from error
    try:
        lines = json.loads(raw.decode("utf-8"))["lines"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as error:
        raise CheckSetError(f"{path.name} is not a check set: {type(error).__name__}") from error
    if not isinstance(lines, list):
        raise CheckSetError(f"{path.name} is not a check set: `lines` is not a list")
    return lines, hashlib.sha256(raw).hexdigest()[:12]


def must_block_sha(path: Path | None = None) -> str | None:
    try:
        return must_block_set(path)[1]
    except CheckSetError:
        return None


def geometry_identity(map_name: str, control_dir: Path | None = None, maps_json: Path | None = None) -> dict | None:
    """The masks a build reads, by the hashes index.json records, and the map's scale. None without geometry."""
    try:
        entry = json.loads(((control_dir or CONTROL_DIR) / "index.json").read_text(encoding="utf-8")) \
            .get("maps", {}).get(map_name)
        scale = (json.loads((maps_json or MAPS_JSON).read_text(encoding="utf-8")).get(map_name) or {}) \
            .get("xMultiplier")
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or not entry.get("sight_sha") or not entry.get("walk_sha"):
        return None
    return {"sight": entry["sight_sha"], "walk": entry["walk_sha"], "scale": scale}


def manifest(map_name: str, replays: list, geometry: dict, must_block: str | None) -> dict:
    """`replays` = [[match uuid, recipe, source sha256, round count], ...], in any order."""
    return {"v": MANIFEST_VERSION, "map": map_name, "height_version": cf.HEIGHT_VERSION,
            "height_rules": cf.HEIGHT_RULES_REVISION, "min_condense_revision": MIN_CONDENSE_REVISION,
            "geometry": {"sight": geometry.get("sight"), "walk": geometry.get("walk"), "scale": geometry.get("scale")},
            "must_block": must_block,
            "replays": sorted([str(u).lower(), str(recipe), str(source), int(count)]
                              for u, recipe, source, count in replays)}


def digest(m: dict) -> str:
    """16 hex over the whole manifest."""
    return hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def key(m: dict) -> str:
    return f"heights:{m['map']}:{digest(m)}"


def rounds_expected(m: dict) -> int:
    return sum(int(row[3]) for row in m["replays"])


def matches(m: dict) -> list[str]:
    return sorted(row[0] for row in m["replays"])


def changes(old: dict | None, new: dict) -> dict:
    """How `new` differs from the last build's manifest: the matches added, the ones whose evidence is gone
    (deleted, or replaced by other blobs under the same match), and whether anything else moved (the masks, the
    rules, the check set). With no old manifest (a row from before manifests) everything else counts as moved."""
    now = {row[0]: row for row in new["replays"]}
    if old is None:
        return {"added": sorted(now), "gone": [], "other": True}
    was = {row[0]: row for row in old.get("replays") or []}
    rest = {k: v for k, v in new.items() if k != "replays"}
    return {"added": sorted(set(now) - set(was)),
            "gone": sorted(u for u, row in was.items() if now.get(u) != row),
            "other": rest != {k: v for k, v in old.items() if k != "replays"}}
