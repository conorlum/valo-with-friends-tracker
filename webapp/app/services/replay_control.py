"""Map control's reads (docs/replay-map-control-plan.md, "Storage" and "Delivery"; Stage 3).

- `map_layer`: whether a map has the control layer: only once one of its replays passed the
  kill-line test (Q59, Q67; `app/static/data/control/index.json`), and whether its cover is reviewed.
- `round_link` / `round_fingerprint`: what a round's control reads from the link (each slot's side
  this round, the DB-only deaths on the replay clock) and the hash of all its inputs
  (app/replays/control_format.py).
- `round_control`: the endpoint's answer for one round: `ok` (with the row and whether it's stale),
  `not_ready`, `failed`, `no_map` or `old_blob`.
- `plan`: every round of every valid replay that has no current control, for
  scripts/compute_control.py and the nudges in the replay scripts. A few queries in all, not per
  replay: the DB is ~65 ms a round trip away.

Standard library and the DB only: the engine (app/control) is never imported here.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import load_only

from app.models.replay import Replay, ReplayPlayer, ReplayRound, ReplayRoundControl
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.replays import format as fmt
from app.scoring.plant_window import attacking_team
from app.services import control_heights
from app.replays import map_feature_inputs as fi
from app.replays.height_inputs import HeightSelection
from app.replays.control_inputs import PinnedInputs, verify_result_inputs, InvalidControlInputs, StaleControlInputs
from app.services import control_feature_artifacts

CONTROL_DIR = fmt.STATIC_DIR / "data" / "control"
MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
# Blobs from before condenser revision 10 lack control's inputs (flash hits, possession, yaws,
# damage runs). A capability floor, so it orders revisions, unlike the recipe's freshness test.
MIN_CONDENSE_REVISION = 10


def _asset_paths() -> tuple:
    return CONTROL_DIR / "index.json", CONTROL_DIR / "tags.json", MAPS_JSON


def _stamp() -> tuple:
    """The three asset files' (file id, mtime, size): a new asset generation (a rebuilt index.json, an edited
    tags.json, either replaced atomically) changes it, so a long-running process rereads them instead of
    planning against stale inputs."""
    out = []
    for path in _asset_paths():
        try:
            st = path.stat()
            out.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


_ASSET_READS = 4            # attempts at a consistent snapshot before falling back to the last one
_ASSET_RETRY_S = 0.05
_asset_lock = threading.Lock()
_asset_cache: dict = {"stamp": None, "value": None, "path": None}


def _read_snapshot(stamp: tuple) -> tuple[dict, dict, dict] | None:
    """The three files parsed together, or None when one changed while they were read (the stamp after
    isn't `stamp`) or one isn't whole JSON yet (a writer mid-write). Raises OSError for a missing file."""
    raw = [path.read_bytes() for path in _asset_paths()]
    if _stamp() != stamp:
        return None
    try:
        index, tags, maps = (json.loads(b.decode("utf-8")) for b in raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not all(isinstance(v, dict) for v in (index, tags, maps)):
        return None
    return index.get("maps", {}), tags.get("maps", {}), maps


def _assets() -> tuple[dict, dict, dict]:
    """(index.json maps, tags.json maps, maps.json): what the engine's geometry is built from, as one
    consistent snapshot. Cached while the files are unchanged. The files are read together and kept only when
    none changed meanwhile, so an index.json and a tags.json from different generations never mix; a file
    caught mid-write, or one that keeps changing, leaves the last valid snapshot in use (and is retried on the
    next call). With no valid snapshot yet, the failure is raised."""
    with _asset_lock:
        paths = _asset_paths()
        if _asset_cache["path"] != paths:          # the asset folder moved (tests): nothing cached applies
            _asset_cache.update(stamp=None, value=None, path=paths)
        last_error: Exception | None = None
        for attempt in range(_ASSET_READS):
            stamp = _stamp()
            if stamp == _asset_cache["stamp"]:
                return _asset_cache["value"]
            try:
                snapshot = _read_snapshot(stamp)
            except OSError as exc:
                snapshot, last_error = None, exc
            if snapshot is not None:
                _asset_cache.update(stamp=stamp, value=snapshot)
                return snapshot
            if attempt + 1 < _ASSET_READS:
                time.sleep(_ASSET_RETRY_S)
        if _asset_cache["value"] is not None:
            return _asset_cache["value"]       # transient: keep planning against the last consistent inputs
        if last_error is not None:
            raise last_error
        raise ValueError(f"control assets in {CONTROL_DIR} are changing or not valid JSON; no snapshot to use")


def _clear_assets() -> None:
    with _asset_lock:
        _asset_cache.update(stamp=None, value=None, path=None)


_assets.cache_clear = _clear_assets


def map_layer(map_name: str) -> dict | None:
    """{"cover_reviewed": bool} when the map has the layer, else None."""
    entry = _assets()[0].get(map_name)
    if not entry or not (entry.get("kill_lines") or {}).get("passes"):
        return None
    return {"cover_reviewed": bool(entry.get("cover_reviewed"))}


@dataclass(frozen=True)
class CurrentGeometryContext:
    map_name: str
    state: str
    height: HeightSelection
    pinned: PinnedInputs | None
    feature_input: fi.FeatureInput | None = None


def _current_snapshot():
    for _ in range(_ASSET_READS):
        stamp = _stamp()
        raw = [path.read_bytes() for path in _asset_paths()]
        index, tags, maps = [fi.read_json(value) for value in raw]
        if _stamp() == stamp:
            return index.get('maps', {}), tags.get('maps', {}), maps, raw[1]
    raise fi.FeatureInputsError('current control source is changing')


def resolve_current_geometry(db, map_name):
    selection = HeightSelection('flat')
    try:
        index, tags, maps, raw = _current_snapshot()
        entry = index.get(map_name)
        if entry is None or not (entry.get('kill_lines') or {}).get('passes'):
            return CurrentGeometryContext(map_name, 'no_map', selection, None)
        if entry.get('features_sha'):
            return CurrentGeometryContext(map_name, 'conversion_required', selection, None)
        selection = control_heights.select_height(db, map_name, entry.get('height_sha'))
        source = fi.capture_source_snapshot(map_name, raw)
        source_entry = tags.get(map_name) or {}
        mf = source_entry.get('map_features') or {}
        intended = any(b.get('enabled') and b.get('runtime_consumer') in fi.CONSUMER_VERSIONS
                       for b in mf.get('bundles', []))
        base = {}
        if intended:
            from app.replays.map_feature_sources import base_snapshot
            base = base_snapshot(map_name, CONTROL_DIR, source_entry, maps[map_name]['xMultiplier'])
            if _current_snapshot() != (index, tags, maps, raw):
                raise fi.FeatureInputsError('source changed while reading masks')
        inp = fi.identify_features(map_name, selection.digest, source, base)
        geometry = {'sight': entry.get('sight_sha'), 'walk': entry.get('walk_sha'),
                    'barrier': entry.get('barrier_sha'), 'specials': source_entry.get('specials') or [],
                    'scale': (maps.get(map_name) or {}).get('xMultiplier')}
        if selection.digest:
            geometry['height'] = selection.digest
        if inp is not None:
            header = control_feature_artifacts.find_artifact_header(db, inp.key)
            if header is None:
                return CurrentGeometryContext(map_name, 'features_pending', selection, None, inp)
            geometry['features'] = header.digest
        pin = PinnedInputs(geometry, feature_key=inp.key if inp else None,
                           artifact_digest=geometry.get('features'), source_sha256=source.raw_sha256,
                           height_mode=selection.mode)
        return CurrentGeometryContext(map_name, 'ready', selection, pin, inp)
    except (OSError, ValueError, KeyError, TypeError):
        return CurrentGeometryContext(map_name, 'source_error', selection, None)


def exact_height_bytes(db, map_name, digest):
    if not digest:
        return None
    data = control_heights.asset_bytes(db, map_name, digest)
    if data is not None:
        return data
    # A selected committed fallback is possible only when no database history exists.
    selection = control_heights.select_height(db, map_name, digest)
    if selection.mode != 'asset' or selection.digest != digest:
        raise fi.FeatureInputsError('selected height is unavailable')
    return (CONTROL_DIR / f'{map_name}.height.npz').read_bytes()


def prepare_discovered(session_factory, map_names, retry_after, now):
    for name in sorted(set(map_names)):
        with session_factory() as db:
            inp = None
            try:
                context = resolve_current_geometry(db, name)
                inp = context.feature_input
                if context.state != 'features_pending' or inp is None or now < retry_after.get(inp.key, 0):
                    continue
                height = exact_height_bytes(db, name, context.height.digest)
                control_feature_artifacts.prepare_artifact(db, inp, height)
                db.commit()
                retry_after.pop(inp.key, None)
            except (control_feature_artifacts.FeaturePreparationPending, OSError, ValueError):
                db.rollback()
                if inp is not None:
                    retry_after[inp.key] = now + 300


def geometry_inputs(map_name: str, heights: dict | None = None, *, context=None) -> dict | None:
    """Everything app/control/geometry.py's `load_geometry` reads for a map: the built masks (by
    index.json's hashes; `barrier` is None for a map with no barrier paint), the specials from
    tags.json, the scale from maps.json, and the map's heights by their digest (`height`, only on a
    map that has them, so a flat map's inputs and its rounds' fingerprints are what they were).

    Exact feature artifacts are selected separately by resolve_current_geometry. This offline-compatible
    helper retains the legacy base/height inputs; an unexpected old pointer requires conversion.

    `heights` is `control_heights.active_digests(db)`: a map's active digest in the database is its heights
    (docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, section 1). The committed index.json
    `height_sha` is the fallback for a map with no active row, and all there is when `heights` is None (a
    checkout without a database, most tests). Every caller under app/ and scripts/ passes it
    (tests/replays/test_control_heights_db.py)."""
    if context is not None:
        return context.pinned.geometry if context.state == 'ready' else None
    index, tags, maps = _assets()
    entry = index.get(map_name)
    if entry is None:
        return None
    inputs = {"sight": entry.get("sight_sha"), "walk": entry.get("walk_sha"), "barrier": entry.get("barrier_sha"),
              "specials": (tags.get(map_name) or {}).get("specials") or [],
              "scale": (maps.get(map_name) or {}).get("xMultiplier")}
    height = (heights or {}).get(map_name) or entry.get("height_sha")
    if height:
        inputs["height"] = height
    if entry.get("features_sha"):
        raise fi.FeatureInputsError('legacy feature pointer requires archive conversion')
    return inputs


def condense_revision(recipe: str) -> int | None:
    """The condenser revision in a blob recipe (`<parser>.c<N>.f<V>.a<assets>`, format.recipe)."""
    parts = recipe.split(".")
    if len(parts) > 1 and parts[1].startswith("c") and parts[1][1:].isdigit():
        return int(parts[1][1:])
    return None


def blob_too_old(replay: Replay) -> bool:
    revision = condense_revision(replay.recipe)
    return revision is None or revision < MIN_CONDENSE_REVISION


def side_groups(db, replay: Replay) -> dict[int, str]:
    return {slot: group for slot, group in db.query(ReplayPlayer.slot, ReplayPlayer.side_group)
            .filter(ReplayPlayer.replay_id == replay.id) if group}


def round_link(replay: Replay, groups: dict[int, str], n: int) -> dict:
    """What control reads from the link for round n: JSON-ready, and part of the fingerprint."""
    if not replay_db.is_linked(replay):
        return {"linked": False, "sides": {}, "db_deaths": []}
    side_to_team = (replay.link_report or {}).get("side_to_team") or {}
    attacking = attacking_team(n)
    sides = {}
    for slot, group in sorted(groups.items()):
        team = side_to_team.get(group)
        if team is not None and attacking is not None:
            sides[str(slot)] = "attack" if team == attacking.value else "defense"
    offset = replay.clock_offset or 0.0
    deaths = [[d["slot"], round(float(d["t_db"]) - offset, 3)]
              for d in (replay.db_deaths or {}).get(str(n), []) if d.get("slot") is not None]
    return {"linked": True, "sides": sides, "db_deaths": sorted(deaths)}


def round_fingerprint(replay: Replay, groups: dict[int, str], n: int, heights: dict | None = None, *, context=None) -> str | None:
    geometry = geometry_inputs(replay.map_name, heights, context=context) if context is not None else geometry_inputs(replay.map_name, heights)
    if geometry is None:
        return None
    return cf.fingerprint(replay.recipe, replay.source_sha256, round_link(replay, groups, n), geometry)


@dataclass
class RoundControlAnswer:
    status: str                              # ok | not_ready | failed | no_map | old_blob
    row: ReplayRoundControl | None = None
    stale: bool = False


def round_control(db, replay: Replay, n: int) -> RoundControlAnswer:
    """The endpoint's answer. A row in an older byte format reads as not ready: a viewer that
    decodes the current format can't read it, unlike a row that is only stale."""
    context = resolve_current_geometry(db, replay.map_name)
    if context.state == 'no_map' or (context.state == 'ready' and map_layer(replay.map_name) is None):
        return RoundControlAnswer("no_map")
    if blob_too_old(replay):
        return RoundControlAnswer("old_blob")
    row = (db.query(ReplayRoundControl)
           .options(load_only(ReplayRoundControl.status, ReplayRoundControl.fingerprint,
                              ReplayRoundControl.data_version, ReplayRoundControl.data))
           .filter(ReplayRoundControl.replay_id == replay.id, ReplayRoundControl.round_number == n)
           .one_or_none())
    if row is None or (row.status == "ok" and row.data_version != cf.DATA_VERSION):
        return RoundControlAnswer("not_ready")
    if row.status != "ok":
        return RoundControlAnswer("failed", row)
    heights = control_heights.active_digests(db)
    stale = row.fingerprint != round_fingerprint(replay, side_groups(db, replay), n, heights,
                                               context=context)
    return RoundControlAnswer("ok", row, stale)


@dataclass
class PlannedRound:
    replay_id: int
    match_uuid: str
    map_name: str
    round_number: int
    fingerprint: str | None
    reason: str       # missing | stale | failed | retry_failed | forced | no_map | old_blob
    link: dict | None = None
    inputs: PinnedInputs | None = None

    @property
    def computable(self) -> bool:
        return self.reason not in ("no_map", "old_blob", 'features_pending', 'source_error')


def _valid_ids(db, replays: list[Replay]) -> set[int]:
    """replays.db.is_valid for many replays in one query: rounds 1..round_count, a supported `v`. Only these
    replays' rounds are counted."""
    ids = sorted({r.id for r in replays})
    if not ids:
        return set()
    counts = {rid: (n, lo, hi) for rid, n, lo, hi in db.query(
        ReplayRound.replay_id, func.count(), func.min(ReplayRound.round_number), func.max(ReplayRound.round_number))
        .filter(ReplayRound.replay_id.in_(ids)).group_by(ReplayRound.replay_id)}
    return {r.id for r in replays if r.format_version in fmt.SUPPORTED_VERSIONS
            and counts.get(r.id) == (r.round_count, 1, r.round_count)}


def plan(db, *, match_uuid: str | None = None, rounds: set[int] | None = None, force: bool = False,
         retry_failed: bool = False) -> list[PlannedRound]:
    """Rounds without current control, in replay then round order. A failed row with the current
    fingerprint is skipped (its failure would repeat) unless `retry_failed`; `force` takes every
    round. Rounds that can't be computed (`no_map`, `old_blob`) are listed so they can be reported."""
    query = db.query(Replay).order_by(Replay.id)
    if match_uuid:
        query = query.filter(Replay.match_uuid == match_uuid.lower())
    replays = query.all()
    if not replays:
        return []
    valid = _valid_ids(db, replays)
    heights = control_heights.active_digests(db)
    groups: dict[int, dict[int, str]] = defaultdict(dict)
    for rid, slot, group in db.query(ReplayPlayer.replay_id, ReplayPlayer.slot, ReplayPlayer.side_group):
        if group:
            groups[rid][slot] = group
    stored: dict[int, dict[int, ReplayRoundControl]] = defaultdict(dict)
    for row in db.query(ReplayRoundControl).options(load_only(
            ReplayRoundControl.replay_id, ReplayRoundControl.round_number, ReplayRoundControl.status,
            ReplayRoundControl.fingerprint, ReplayRoundControl.data_version)):
        stored[row.replay_id][row.round_number] = row
    out: list[PlannedRound] = []
    contexts = {}
    for replay in replays:
        if replay.id not in valid:
            continue
        uuid = str(replay.match_uuid).lower()
        wanted = [n for n in range(1, replay.round_count + 1) if rounds is None or n in rounds]
        if replay.map_name not in contexts:
            contexts[replay.map_name] = resolve_current_geometry(db, replay.map_name)
        context = contexts[replay.map_name]
        if context.state != 'ready':
            out += [PlannedRound(replay.id, uuid, replay.map_name, n, None, context.state) for n in wanted]
            continue
        if map_layer(replay.map_name) is None or blob_too_old(replay):
            reason = 'no_map' if map_layer(replay.map_name) is None else 'old_blob'
            out += [PlannedRound(replay.id, uuid, replay.map_name, n, None, reason) for n in wanted]
            continue
        geometry = geometry_inputs(replay.map_name, heights, context=context)
        for n in wanted:
            link = round_link(replay, groups[replay.id], n)
            current = cf.fingerprint(replay.recipe, replay.source_sha256, link, geometry)
            row = stored[replay.id].get(n)
            if force:
                reason = "forced"
            elif row is None:
                reason = "missing"
            elif row.fingerprint != current or (row.status == "ok" and row.data_version != cf.DATA_VERSION):
                reason = "stale"
            elif row.status != "ok":
                if not retry_failed:
                    continue
                reason = "retry_failed"
            else:
                continue
            pin = context.pinned.for_round(cf.input_envelope(replay.recipe, replay.source_sha256, link, geometry))
            out.append(PlannedRound(replay.id, uuid, replay.map_name, n, current, reason, link, pin))
    return out


def nudge(db) -> str | None:
    """One line for the replay scripts to print when rounds need `compute_control.py`, else None."""
    todo = [p for p in plan(db) if p.computable]
    if not todo:
        return None
    replays = len({p.replay_id for p in todo})
    return (f"Map control: {len(todo)} round(s) of {replays} replay(s) need computing: run scripts\\compute_control.py "
            f"through with_friends_db.py (see its docstring)")
