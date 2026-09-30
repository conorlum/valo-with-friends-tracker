"""Map control's reads (docs/replay-map-control-plan.md, "Storage" and "Delivery"; Stage 3).

- `map_layer`: whether a map has the control layer: only once one of its replays passed the
  kill-line test (Q59, Q67; `app/static/data/control/index.json`), and whether its cover is reviewed.
- `round_link` / `round_fingerprint`: what a round's control reads from the link (each slot's side
  this round, the DB-only deaths on the replay clock) and the hash of all its inputs
  (app/replays/control_format.py).
- `round_control`: the endpoint's answer for one round: `ok` (with the row and whether it's stale),
  `not_ready`, `failed` or `no_map`.
- `plan`: every round of every valid replay that has no current control, for
  scripts/compute_control.py and the nudges in the replay scripts.

Standard library and the DB only: the engine (app/control) is never imported here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import load_only

from app.models.replay import Replay, ReplayPlayer, ReplayRound, ReplayRoundControl
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.replays import format as fmt
from app.scoring.plant_window import attacking_team

INDEX_JSON = fmt.STATIC_DIR / "data" / "control" / "index.json"
# Blobs from before condenser revision 10 lack control's inputs (flash hits, possession, yaws,
# damage runs); the engine's placeholders are for feasibility runs only.
MIN_CONDENSE_REVISION = 10


@lru_cache(maxsize=1)
def _index() -> dict:
    return json.loads(INDEX_JSON.read_text(encoding="utf-8")).get("maps", {})


def map_layer(map_name: str) -> dict | None:
    """{"cover_reviewed": bool} when the map has the layer, else None."""
    entry = _index().get(map_name)
    if not entry or not (entry.get("kill_lines") or {}).get("passes"):
        return None
    return {"cover_reviewed": bool(entry.get("cover_reviewed"))}


def geometry_inputs(map_name: str) -> dict | None:
    entry = _index().get(map_name)
    if entry is None:
        return None
    return {"sight": entry.get("sight_sha"), "walk": entry.get("walk_sha"), "specials": entry.get("specials") or []}


def condense_revision(recipe: str) -> int | None:
    """The `c<N>` part of a blob recipe (format.recipe), or None."""
    for part in recipe.split("."):
        if part.startswith("c") and part[1:].isdigit():
            return int(part[1:])
    return None


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
    status: str                              # ok | not_ready | failed | no_map
    row: ReplayRoundControl | None = None
    stale: bool = False


def round_control(db, replay: Replay, n: int) -> RoundControlAnswer:
    if map_layer(replay.map_name) is None:
        return RoundControlAnswer("no_map")
    row = db.get(ReplayRoundControl, (replay.id, n))
    if row is None:
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

    def as_dict(self) -> dict:
        return {"match_uuid": self.match_uuid, "map": self.map_name, "round": self.round_number,
                "reason": self.reason}


def plan(db, *, match_uuid: str | None = None, rounds: set[int] | None = None, force: bool = False,
         retry_failed: bool = False) -> list[PlannedRound]:
    """Rounds without current control, in replay then round order. A failed row with the current
    fingerprint is skipped (its failure would repeat) unless `retry_failed`; `force` takes every
    round. Rounds that can't be computed (`no_map`, `old_blob`) are listed so they can be reported."""
    query = db.query(Replay).order_by(Replay.id)
    if match_uuid:
        query = query.filter(Replay.match_uuid == match_uuid.lower())
    out: list[PlannedRound] = []
    for replay in query:
        if not replay_db.is_valid(db, replay):
            continue
        uuid = str(replay.match_uuid).lower()
        wanted = [n for n in range(1, replay.round_count + 1) if rounds is None or n in rounds]
        if map_layer(replay.map_name) is None:
            out += [PlannedRound(replay.id, uuid, replay.map_name, n, None, "no_map") for n in wanted]
            continue
        revision = condense_revision(replay.recipe)
        if revision is None or revision < MIN_CONDENSE_REVISION:
            out += [PlannedRound(replay.id, uuid, replay.map_name, n, None, "old_blob") for n in wanted]
            continue
        groups = side_groups(db, replay)
        stored = {row.round_number: row for row in db.query(ReplayRoundControl)
                  .options(load_only(ReplayRoundControl.round_number, ReplayRoundControl.status,
                                     ReplayRoundControl.fingerprint))
                  .filter(ReplayRoundControl.replay_id == replay.id)}
        for n in wanted:
            link = round_link(replay, groups, n)
            current = cf.fingerprint(replay.recipe, replay.source_sha256, link, geometry_inputs(replay.map_name))
            row = stored.get(n)
            if force:
                reason = "forced"
            elif row is None:
                reason = "missing"
            elif row.fingerprint != current:
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


def round_blob_bytes(db, replay_id: int, n: int) -> bytes | None:
    row = db.get(ReplayRound, (replay_id, n))
    return None if row is None else row.data
