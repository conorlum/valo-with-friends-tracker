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
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import func
from sqlalchemy.orm import load_only

from app.models.replay import Replay, ReplayPlayer, ReplayRound, ReplayRoundControl
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.replays import format as fmt
from app.scoring.plant_window import attacking_team

CONTROL_DIR = fmt.STATIC_DIR / "data" / "control"
MAPS_JSON = fmt.STATIC_DIR / "data" / "maps.json"
# Blobs from before condenser revision 10 lack control's inputs (flash hits, possession, yaws,
# damage runs). A capability floor, so it orders revisions, unlike the recipe's freshness test.
MIN_CONDENSE_REVISION = 10


def _stamp() -> tuple:
    """The three asset files' (mtime, size): a new asset generation (a rebuilt index.json, an edited
    tags.json) changes it, so a long-running process rereads them instead of planning against stale inputs."""
    out = []
    for path in (CONTROL_DIR / "index.json", CONTROL_DIR / "tags.json", MAPS_JSON):
        try:
            st = path.stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


@lru_cache(maxsize=1)
def _load_assets(stamp: tuple) -> tuple[dict, dict, dict]:
    index = json.loads((CONTROL_DIR / "index.json").read_text(encoding="utf-8")).get("maps", {})
    tags = json.loads((CONTROL_DIR / "tags.json").read_text(encoding="utf-8")).get("maps", {})
    return index, tags, json.loads(MAPS_JSON.read_text(encoding="utf-8"))


def _assets() -> tuple[dict, dict, dict]:
    """(index.json maps, tags.json maps, maps.json): what the engine's geometry is built from. Cached while
    the files are unchanged."""
    return _load_assets(_stamp())


_assets.cache_clear = _load_assets.cache_clear


def map_layer(map_name: str) -> dict | None:
    """{"cover_reviewed": bool} when the map has the layer, else None."""
    entry = _assets()[0].get(map_name)
    if not entry or not (entry.get("kill_lines") or {}).get("passes"):
        return None
    return {"cover_reviewed": bool(entry.get("cover_reviewed"))}


def geometry_inputs(map_name: str) -> dict | None:
    """Everything app/control/geometry.py's `load_geometry` reads for a map: the built masks (by
    index.json's hashes; `barrier` is None for a map with no barrier paint), the specials from
    tags.json, the scale from maps.json, and the map's heights by their digest (`height`, only on a
    map that has them, so a flat map's inputs and its rounds' fingerprints are what they were).

    Map features (docs/superpowers/specs/2026-10-04-map-features-contract.md, section 8): `features`, the
    consumed-input manifest digest of the map's active feature generation (index.json `features_sha`), only on
    a map that has enabled features. No map has any in this build, so every input is what it was
    (tests/fixtures/control/map_features/legacy_inputs.json)."""
    index, tags, maps = _assets()
    entry = index.get(map_name)
    if entry is None:
        return None
    inputs = {"sight": entry.get("sight_sha"), "walk": entry.get("walk_sha"), "barrier": entry.get("barrier_sha"),
              "specials": (tags.get(map_name) or {}).get("specials") or [],
              "scale": (maps.get(map_name) or {}).get("xMultiplier")}
    if entry.get("height_sha"):
        inputs["height"] = entry["height_sha"]
    if entry.get("features_sha"):
        inputs["features"] = entry["features_sha"]
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


def round_fingerprint(replay: Replay, groups: dict[int, str], n: int) -> str | None:
    geometry = geometry_inputs(replay.map_name)
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
    if map_layer(replay.map_name) is None:
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
    stale = row.fingerprint != round_fingerprint(replay, side_groups(db, replay), n)
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

    @property
    def computable(self) -> bool:
        return self.reason not in ("no_map", "old_blob")


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
    for replay in replays:
        if replay.id not in valid:
            continue
        uuid = str(replay.match_uuid).lower()
        wanted = [n for n in range(1, replay.round_count + 1) if rounds is None or n in rounds]
        if map_layer(replay.map_name) is None or blob_too_old(replay):
            reason = "no_map" if map_layer(replay.map_name) is None else "old_blob"
            out += [PlannedRound(replay.id, uuid, replay.map_name, n, None, reason) for n in wanted]
            continue
        geometry = geometry_inputs(replay.map_name)
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
            out.append(PlannedRound(replay.id, uuid, replay.map_name, n, current, reason, link))
    return out


def nudge(db) -> str | None:
    """One line for the replay scripts to print when rounds need `compute_control.py`, else None."""
    todo = [p for p in plan(db) if p.computable]
    if not todo:
        return None
    replays = len({p.replay_id for p in todo})
    return (f"Map control: {len(todo)} round(s) of {replays} replay(s) need computing: run scripts\\compute_control.py "
            f"through with_friends_db.py (see its docstring)")
