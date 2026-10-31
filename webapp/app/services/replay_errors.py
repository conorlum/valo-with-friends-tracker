"""Read-only inventory of stored replay problems, across the friends site's corpus.

No persistent state or dismissal: once a recompute agrees, a problem disappears. `collect` keeps all rows
for callers that need the full evidence; `compute` caps only the rows shown on the page.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from app.config import settings
from app.models.replay import (ReplayPlayer, ReplayRoundControl, ReplayRoundGapRun, ReplayRoundMoments,
                               ReplayRoundUtil, ReplayUpload)
from app.services import control_heights, replay_progress
from app.services.replay_gaps import Freshness

SHOW = 50
TTL_S = 55
SITE = "https://valowithfriendstracker.onrender.com"
KINDS = (
    ("box_conflict", "Box height conflicts"),
    ("control_failed", "Map control failed"),
    ("gaps_failed", "Timing gaps failed"),
    ("moments_failed", "Critical Moments failed"),
    ("util_failed", "Critical Util failed"),
    ("heights_rejected", "Height builds rejected"),
    ("upload_failed", "Uploads failed"),
    ("upload_held", "Uploads held"),
    ("store_failed", "Replay storage failed"),
    ("ack_missing", "Archive acknowledgement missing"),
    ("stuck", "Uploads stuck"),
    ("unlinked", "Replays unlinked"),
    ("needs_a_person", "Needs a person"),
)
_lock = threading.Lock()
_cached: tuple[float, dict] | None = None


def now() -> datetime:
    """UTC clock, replaceable in tests."""
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _inventory_command() -> str:
    return ('scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only python -c '
            '"import json; from app.db import SessionLocal; from app.services.replay_errors import compute; '
            'print(json.dumps(compute(SessionLocal()), indent=2))"')


def _row(*, replay=None, map_name=None, match=None, n=None, t=None, agent=None, figures=None,
         error=None, revision=None, reproduce=None) -> dict:
    match = str(replay.match_uuid) if replay is not None else (str(match) if match is not None else None)
    map_name = replay.map_name if replay is not None else map_name
    params = {}
    if n is not None:
        params["round"] = n
    if t is not None:
        params["t"] = t
    link = f"{SITE}/replays/{match}" if match is not None else f"{SITE}/replays/upload"
    if params:
        link += "?" + urlencode(params)
    return {"map": map_name, "match": match, "round": n, "t": t, "agent": agent,
            "figures": copy.deepcopy(figures) if figures is not None else {}, "link": link,
            "error": error, "revision": revision, "reproduce": reproduce or _inventory_command()}


def _round_command(replay, n) -> str:
    return ("scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only "
            f"scripts/compute_control.py --match {replay.match_uuid} --round {n} --retry-failed --dry-run")


def _height_command(map_name) -> str:
    # The export CLI requires a directory outside any repository; the wrapper runs from webapp/.
    return ("scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only "
            f'scripts/control_heights.py export --map {map_name} --out "$TEMP/{map_name}_heights.npz"')


def _sort_key(row):
    return (row["map"] or "", row["match"] or "", row["round"] if row["round"] is not None else -1,
            row["t"] if row["t"] is not None else -1)


def collect(db, live=None, *, stale: Counter | None = None) -> dict[str, list[dict]]:
    """All stored rows, sorted within each kind. Optional `stale` receives obsolete failure counts.

    No query at all in demo mode. Only agents are selected for slot annotations.
    """
    rows = {key: [] for key, _ in KINDS}
    if settings.demo_mode:
        return rows
    stale = Counter() if stale is None else stale
    collected = replay_progress.round_states(db)
    states, replays = collected["states"], collected["replays"]
    by_match = {str(r.match_uuid).lower(): r for r in replays.values()}
    agents = {(rid, slot): agent for rid, slot, agent in db.query(
        ReplayPlayer.replay_id, ReplayPlayer.slot, ReplayPlayer.agent)}

    for map_name, rules, built_at, record in control_heights.all_box_conflicts(db):
        replay = by_match.get(str(record.get("match")).lower())
        candidates = [{"piece": c[0], "tops": c[1], "dist_m": c[2] if len(c) > 2 else None}
                      for c in record.get("candidates", [])]
        rows["box_conflict"].append(_row(
            map_name=map_name, match=record.get("match"), n=record.get("round"), t=record.get("t0"),
            agent=agents.get((replay.id, record.get("slot"))) if replay is not None else None,
            figures={"z": record.get("z"), "candidates": candidates, "px": record.get("px"),
                     "slot": record.get("slot"), "t1": record.get("t1")},
            revision={"heights_revision": (rules or {}).get("revision"), "built_at": _iso(built_at)},
            reproduce=_height_command(map_name)))

    for key, model, revision in (("control_failed", ReplayRoundControl, "control_revision"),
                                 ("gaps_failed", ReplayRoundGapRun, "gaps_revision")):
        for stored in db.query(model.replay_id, model.round_number, model.error, model.computed_at,
                               getattr(model, revision).label("revision")).filter(model.status == "failed"):
            state = states.get((stored.replay_id, stored.round_number))
            if state != key:
                # Stale, or behind an earlier step (a re-parse): the worker gets to it on its own.
                stale[key] += 1
            else:
                replay = replays[stored.replay_id]
                rows[key].append(_row(replay=replay, n=stored.round_number, error=stored.error,
                                      revision={revision: stored.revision, "computed_at": _iso(stored.computed_at)},
                                      reproduce=_round_command(replay, stored.round_number)))

    fresh = Freshness(db)
    for key, model in (("moments_failed", ReplayRoundMoments), ("util_failed", ReplayRoundUtil)):
        for stored in db.query(model.replay_id, model.round_number, model.error, model.computed_at,
                               model.gaps_revision, model.fingerprint).filter(model.status == "failed"):
            replay = replays.get(stored.replay_id)
            if replay is None:
                continue
            if not fresh.current(replay, stored.round_number, stored.gaps_revision, stored.fingerprint):
                stale[key] += 1
                continue
            rows[key].append(_row(replay=replay, n=stored.round_number, error=stored.error,
                                  revision={"gaps_revision": stored.gaps_revision,
                                            "computed_at": _iso(stored.computed_at)},
                                  reproduce=_round_command(replay, stored.round_number)))

    for map_name, height in control_heights.last_builds(db).items():
        if height.status == control_heights.REJECTED:
            rows["heights_rejected"].append(_row(
                map_name=map_name, figures={"reasons": control_heights.gate(height.report),
                                           "digest": height.digest, "inputs_sha": height.inputs_sha},
                revision={"heights_revision": (height.rules or {}).get("revision"),
                          "built_at": _iso(height.built_at)},
                reproduce=_height_command(map_name)))

    uploads = list(db.query(ReplayUpload.id, ReplayUpload.status, ReplayUpload.error, ReplayUpload.source_sha256,
                            ReplayUpload.size_bytes, ReplayUpload.created_at, ReplayUpload.finished_at,
                            ReplayUpload.replay_id, ReplayUpload.worker_job_id, ReplayUpload.store_outcome,
                            ReplayUpload.archive_ack))
    latest_stored = {}
    for upload in uploads:
        if upload.status == "stored" and upload.source_sha256 is not None:
            sha, created = upload.source_sha256, _utc(upload.created_at)
            if sha not in latest_stored or created > latest_stored[sha]:
                latest_stored[sha] = created
    cutoff = _utc(now()) - timedelta(hours=1)
    for upload in uploads:
        kinds = []
        superseded = (upload.source_sha256 is not None and upload.source_sha256 in latest_stored
                      and latest_stored[upload.source_sha256] > _utc(upload.created_at))
        if upload.status == "failed" and not superseded:
            kinds.append("upload_failed")
        if upload.status == "held":
            kinds.append("upload_held")
        if upload.store_outcome == "failed":
            kinds.append("store_failed")
        if (upload.status == "stored" and upload.archive_ack is None
                and _utc(upload.finished_at or upload.created_at) < cutoff):
            kinds.append("ack_missing")
        if upload.status in ("queued", "parsing") and _utc(upload.created_at) < cutoff:
            kinds.append("stuck")
        for key in kinds:
            rows[key].append(_row(
                replay=replays.get(upload.replay_id), error=upload.error,
                figures={"upload_id": str(upload.id), "size_bytes": upload.size_bytes,
                         "worker_job_id": upload.worker_job_id},
                revision={"created_at": _iso(upload.created_at), "finished_at": _iso(upload.finished_at)}))

    for replay in replays.values():
        if replay.link_status in ("unlinked", "refused"):
            rows["unlinked"].append(_row(replay=replay, figures=replay.link_report,
                                          revision={"recipe": replay.recipe}))
    manual = {key: label for key, label, automatic in replay_progress.STEPS
              if not automatic and key not in ("control_failed", "gaps_failed")}
    counts = Counter((rid, step) for (rid, _), step in states.items() if step in manual)
    for (rid, step), count in counts.items():
        replay = replays[rid]
        rows["needs_a_person"].append(_row(replay=replay, figures={"rounds": count, "step": manual[step]},
                                            revision={"recipe": replay.recipe}))
    for values in rows.values():
        values.sort(key=_sort_key)
    return rows


def compute(db, live=None) -> dict:
    """Fixed kind order, total counts, and the first SHOW rows per kind. `live` is reserved for W9b."""
    stale = Counter()
    rows = collect(db, live=live, stale=stale)
    return {"kinds": [{"key": key, "label": label, "count": len(rows[key]),
                       "note": (f"{stale[key]} failed rows from older rules: the worker will retry them"
                                if stale[key] else None), "rows": rows[key][:SHOW]}
                      for key, label in KINDS],
            "checked_at": _iso(now())}


def cached(db, now: float | None = None) -> dict:
    """One computation shared by every viewer for TTL_S."""
    global _cached
    if settings.demo_mode:
        return compute(db)
    stamp = time.monotonic() if now is None else now
    with _lock:
        if _cached is None or stamp - _cached[0] >= TTL_S:
            _cached = (stamp, compute(db))
        return _cached[1]
