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
from dataclasses import dataclass


@dataclass(frozen=True)
class HeightSelection:
    mode: str
    digest: str | None = None

from app.replays import control_format as cf
from app.replays import format as fmt
from app.replays import layouts

CONTROL_DIR = fmt.STATIC_DIR / "data" / "control"
MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
MUST_BLOCK = CONTROL_DIR / "must_block.json"
MIN_CONDENSE_REVISION = 11       # blobs from before it carry no heights
MANIFEST_VERSION = 2


class CheckSetError(ValueError):
    """A required check set or region mask can't be read: the build must wait."""


def must_block_path(key: str | None) -> Path:
    """Base layouts share the original check set; later layouts have their own."""
    if key is None or layouts.map_of(key) == key:
        return MUST_BLOCK
    return CONTROL_DIR / f"must_block.{key}.json"


def must_block_set(path: Path | None = None, *, key: str | None = None) -> tuple[list, str]:
    """(the lines, 12 hex of the file's bytes). Raises CheckSetError for a missing or malformed file."""
    path = must_block_path(key) if path is None else Path(path)
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


def must_block_sha(path: Path | None = None, *, key: str | None = None) -> str | None:
    try:
        return must_block_set(path, key=key)[1]
    except CheckSetError:
        return None


def geometry_identity(map_name: str, control_dir: Path | None = None, maps_json: Path | None = None) -> dict | None:
    """The masks a build reads, by the hashes index.json records, and the map's scale. None without geometry."""
    try:
        entry = json.loads(((control_dir or CONTROL_DIR) / "index.json").read_text(encoding="utf-8")) \
            .get("maps", {}).get(map_name)
        scale = (json.loads((maps_json or MAPS_JSON).read_text(encoding="utf-8")).get(layouts.map_of(map_name)) or {}) \
            .get("xMultiplier")
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or not entry.get("sight_sha") or not entry.get("walk_sha"):
        return None
    out = {"sight": entry["sight_sha"], "walk": entry["walk_sha"], "scale": scale}
    if entry.get("cover_sha"):          # only a map with cover pieces: the others' manifests stay as they were
        out["cover"] = entry["cover_sha"]
    return out


def region_masks(map_name: str, registry=None, control_dir=None) -> list[tuple[str, str | None]]:
    """Region file identities in layout order; an unreadable mask blocks the build."""
    folder = CONTROL_DIR if control_dir is None else Path(control_dir)
    masks = []
    for layout in layouts.layouts_of(map_name, registry):
        sha = None
        if layout.region_mask is not None:
            path = folder / layout.region_mask
            try:
                raw = path.read_bytes()
            except OSError as error:
                raise CheckSetError(f"{path} is missing or unreadable: {error}") from error
            sha = hashlib.sha256(raw).hexdigest()[:12]
        masks.append((layout.asset_key, sha))
    return masks


def changed_between(map_name, key_a, key_b, registry=None) -> list[str]:
    """Masks after the earlier layout through the later layout, in either direction."""
    if key_a == key_b:
        return []
    ordered = layouts.layouts_of(map_name, registry)
    keys = [layout.asset_key for layout in ordered]
    first, last = sorted((keys.index(key_a), keys.index(key_b)))
    return [layout.asset_key for layout in ordered[first + 1:last + 1]
            if layout.region_mask is not None]


def regions_for(asset_key, pool, registry=None, control_dir=None) -> dict | None:
    """Record changed-region inputs and the replays borrowed from other layouts."""
    map_name = layouts.map_of(asset_key)
    if len(layouts.layouts_of(map_name, registry)) == 1:
        return None
    return {"masks": [[key, sha] for key, sha in region_masks(map_name, registry, control_dir)],
            "pool": sorted([str(uuid).lower(), from_key] for uuid, from_key in pool)}


def refinement_history(map_name, value=None):
    """Durable canonical input, validated without importing the numerical runtime."""
    empty = {'v': 1, 'map': map_name, 'child_px': 4, 'parents': []}
    if value is None:
        return empty
    if not isinstance(value, dict) or set(value) != set(empty) or value.get('v') != 1 \
            or value.get('map') != map_name or value.get('child_px') != 4 \
            or not isinstance(value.get('parents'), list) \
            or any(type(c) is not int or not 0 <= c < 128**2 for c in value['parents']) \
            or value['parents'] != sorted(set(value['parents'])):
        raise ValueError('invalid durable refinement history')
    return {**empty, 'parents': list(value['parents'])}


def history_identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def check_refinement_transition(map_name, prior, receipt):
    prior = refinement_history(map_name, prior)
    if not isinstance(receipt, dict) or receipt.get('v') != 1:
        raise ValueError('refinement receipt missing')
    current = refinement_history(map_name, receipt.get('history'))
    if receipt.get('previous_history_sha256') != history_identity(prior) \
            or receipt.get('history_sha256') != history_identity(current) \
            or not set(prior['parents']) <= set(current['parents']):
        raise ValueError('refinement receipt lost or changed durable history')


def manifest(map_name: str, replays: list, geometry: dict, must_block: str | None, *, history=None,
             regions=None) -> dict:
    """`replays` = [[match uuid, recipe, source sha256, round count], ...], in any order."""
    return {"v": MANIFEST_VERSION, "map": map_name, "height_version": cf.HEIGHT_VERSION,
            "height_rules": cf.HEIGHT_RULES_REVISION, "min_condense_revision": MIN_CONDENSE_REVISION,
            "geometry": {"sight": geometry.get("sight"), "walk": geometry.get("walk"), "scale": geometry.get("scale"),
                         **({"cover": geometry["cover"]} if geometry.get("cover") else {})},
            "must_block": must_block,
            "refinement_history": refinement_history(map_name, history),
            "replays": sorted([str(u).lower(), str(recipe), str(source), int(count)]
                              for u, recipe, source, count in replays),
            **({"regions": regions} if regions is not None else {})}


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
    rules, the check set). Pool membership is counted through added/gone replays, not as another input.
    With no old manifest (a row from before manifests) everything else counts as moved."""
    now = {row[0]: row for row in new["replays"]}
    if old is None:
        return {"added": sorted(now), "gone": [], "other": True}
    was = {row[0]: row for row in old.get("replays") or []}
    def rest(manifest):
        return {k: ({name: value for name, value in v.items() if name != "pool"} if k == "regions" else v)
                for k, v in manifest.items() if k != "replays"}

    return {"added": sorted(set(now) - set(was)),
            "gone": sorted(u for u, row in was.items() if now.get(u) != row),
            "other": rest(new) != rest(old)}
