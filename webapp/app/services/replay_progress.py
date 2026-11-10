"""How far the replay worker has got with the stored corpus: the upload page's "Show Replay Info" panel
(`GET /replays/upload/progress.json`).

A round is done when nothing is left to do for it: its replay carries this deploy's recipe, its map control is
ok and current, and its timing-gap run is ok and current. "Current" is the dispatcher's own test, not a revision
number read from the rows: the same planners it plans its work with (`replay_control.plan`,
`replay_gaps.plan_gaps`), so a revision bump, a height rebuild or an edited figures file counts as soon as it
is deployed. Every other round needs work, under the first step it is stuck at, and each step says whether the
worker gets to it on its own or a person has to.

Read only. One computation serves every viewer for `TTL_S`: it plans every round of the corpus."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from app.config import settings
from app.models.replay import Replay, ReplayRound, ReplayRoundControl, ReplayRoundGapRun
from app.replays import control_format as cf
from app.services import replay_control, replay_gaps
from app.services import replay_upload as uploads

TTL_S = 55   # under the page's one-minute poll, so each poll sees a fresh count

# (key, label, automatic): in the order a round is checked, so a round is counted once, at its first stuck step.
STEPS = (
    ("reparse", "Needs a re-parse (older condenser)", True),
    ("reparse_local", "Needs a re-ingest (older condenser, local file)", False),
    ("incomplete", "Replay incomplete or in an unsupported format", False),
    ("no_map", "Map has no control layer", False),
    ("old_blob", "Replay too old for map control", False),
    ("features_pending", "Waiting for the map's features to compile", True),
    ("source_error", "The map's features failed to compile", False),
    ("control_missing", "Map control never computed", True),
    ("control_stale", "Map control out of date", True),
    ("control_unsupported", "Map control: not supported in this round", False),
    ("control_failed", "Map control failed", False),
    ("gaps_missing", "Timing gaps never computed", True),
    ("gaps_stale", "Timing gaps out of date", True),
    ("gaps_failed", "Timing gaps failed", False),
)
UNSUPPORTED_STEPS = {"control_unsupported"}
_LABELS = {key: (label, automatic) for key, label, automatic in STEPS}


def is_unsupported(error: str | None) -> bool:
    """A failed control row a re-parse can't fix: a missing feature source. One rule for the progress totals and
    the errors card."""
    return (error or "").startswith("features_source_unavailable")


def needs_person(step: str | None) -> bool:
    """A step only a person moves a round past ("done", None and unknown steps: no)."""
    return step in _LABELS and not _LABELS[step][1]

log = logging.getLogger(__name__)
_lock = threading.Lock()
_cached: tuple[float, dict] | None = None


def round_states(db) -> dict:
    """Every stored round's first stuck step, with the replay rows and recipe used to judge it.

    Shared with the errors reader so its failures follow the dispatcher's freshness rules too.
    """
    recipe = uploads.site_recipe()
    replays = {r.id: r for r in db.query(Replay)}
    rounds = [(rid, n) for rid, n in db.query(ReplayRound.replay_id, ReplayRound.round_number)]

    every = {(p.replay_id, p.round_number): p for p in replay_control.plan(db, force=True)}
    control_todo = replay_control.plan(db, retry_failed=True)
    control = {(p.replay_id, p.round_number): p.reason for p in control_todo}
    unsupported = {(r.replay_id, r.round_number) for r in db.query(
        ReplayRoundControl.replay_id, ReplayRoundControl.round_number, ReplayRoundControl.error
        ).filter(ReplayRoundControl.status == "failed")
        if is_unsupported(r.error)}
    gaps_todo = replay_gaps.plan_gaps(db, [p for p in control_todo if p.computable], list(every.values()),
                                      retry_failed=True)
    gaps = {(p.replay_id, p.round_number): p for p in gaps_todo}
    runs = {}
    if gaps:
        runs = {(r.replay_id, r.round_number): r for r in db.query(
            ReplayRoundGapRun.replay_id, ReplayRoundGapRun.round_number, ReplayRoundGapRun.status,
            ReplayRoundGapRun.fingerprint)}
    wanted: dict[tuple, str] = {}

    def gaps_state(key) -> str:
        p, run = gaps[key], runs.get(key)
        if run is None:
            return "gaps_missing"
        if (p.fingerprint, p.map_name) not in wanted:
            wanted[p.fingerprint, p.map_name] = replay_gaps.gap_fingerprint(p.fingerprint, p.map_name)
        if run.fingerprint == wanted[p.fingerprint, p.map_name]:
            return "gaps_failed" if run.status == "failed" else "gaps_stale"
        return "gaps_stale"

    def state(key) -> str:
        replay = replays[key[0]]
        if replay.recipe != recipe:
            return "reparse_local" if replay.source == "local" else "reparse"
        planned = every.get(key)
        if planned is None:
            return "incomplete"
        if not planned.computable:
            return planned.reason if planned.reason in _LABELS else "incomplete"
        reason = control.get(key)
        if reason is not None:
            if reason not in ("missing", "stale") and key in unsupported:
                return "control_unsupported"
            return {"missing": "control_missing", "stale": "control_stale"}.get(reason, "control_failed")
        if key in gaps:
            return gaps_state(key)
        return "done"

    states = {key: state(key) for key in rounds}
    return {"states": states, "replays": replays, "recipe": recipe,
            "fingerprints": {key: planned.fingerprint for key, planned in every.items()}}


def compute(db) -> dict:
    """Every stored round under one state: "done" or the key of its first stuck step."""
    collected = round_states(db)
    states, replays, recipe = collected["states"], collected["replays"], collected["recipe"]
    order = {key: i for i, (key, _, _) in enumerate(STEPS)}
    matches: dict[str, dict] = {}
    manual: dict[str, str] = {}
    for (replay_id, _), found in states.items():
        uuid = str(replays[replay_id].match_uuid).lower()
        row = matches.setdefault(uuid, {"done": 0, "total": 0, "first": None})
        row["total"] += 1
        if found == "done":
            row["done"] += 1
        elif row["first"] is None or order[found] < order[row["first"]]:
            row["first"] = found
        if found != "done" and not _LABELS[found][1] and found not in UNSUPPORTED_STEPS:
            if uuid not in manual or order[found] < order[manual[uuid]]:
                manual[uuid] = found
    for uuid, row in matches.items():
        # The stored summary has one step: show work a person can do before an unsupported round.
        if row["first"] in UNSUPPORTED_STEPS and uuid in manual:
            row["first"] = manual[uuid]
    counts = Counter(states.values())
    total = len(states)
    done = counts.pop("done", 0)
    steps = [{"key": key, "label": label, "automatic": automatic, "rounds": counts[key],
              **({"unsupported": True} if key in UNSUPPORTED_STEPS else {})}
             for key, label, automatic in STEPS if counts.get(key)]
    return {
        "total_rounds": total,
        "total_replays": len(replays),
        "done": done,
        "needs_work": total - done,
        "worker_will_do": sum(s["rounds"] for s in steps if s["automatic"]),
        "needs_a_person": sum(s["rounds"] for s in steps
                              if not s["automatic"] and s["key"] not in UNSUPPORTED_STEPS),
        "unsupported": sum(s["rounds"] for s in steps if s["key"] in UNSUPPORTED_STEPS),
        "steps": steps,
        "matches": matches,
        "judged_against": {"recipe": recipe, "control_revision": cf.CONTROL_REVISION,
                           "gaps_revision": replay_gaps.GAPS_REVISION},
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def cached(db, now: float | None = None) -> dict:
    """`compute`, shared by every viewer for TTL_S."""
    global _cached
    now = time.monotonic() if now is None else now
    with _lock:
        if _cached is None or now - _cached[0] >= TTL_S:
            _cached = (now, compute(db))
        return _cached[1]


def seed(result: dict, started: float) -> None:
    """Hand `cached` a `compute()` result made elsewhere (the match-state refresher), stamped with the
    `time.monotonic()` at which that computation started."""
    global _cached
    with _lock:
        _cached = (started, result)


def match_states(db, external_ids) -> dict[str, dict]:
    """The match lists' "Up to date" column: per lower-cased external ID (= match UUID) with a stored replay,
    `{"done", "total", "label", "automatic"}`; `label` is its earliest stuck step, None when every round is
    done. Read from the stored `replay_match_states` rows (written by the refresher), never planned here; a row
    judged under another recipe or revision, or older than `STALE_S`, counts as absent. Best-effort: a list
    page never fails on it."""
    if settings.demo_mode:
        return {}
    from datetime import timedelta
    from uuid import UUID

    from app.models.replay import ReplayMatchState
    from app.services.replay_match_states import STALE_S

    wanted = set()
    for e in external_ids:
        try:
            wanted.add(str(UUID(str(e).strip())))
        except (ValueError, AttributeError):
            continue
    if not wanted:
        return {}
    recipe, control, gaps = uploads.site_recipe(), cf.CONTROL_REVISION, replay_gaps.GAPS_REVISION
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_S)
    ids = sorted(wanted)
    out = {}
    try:
        for i in range(0, len(ids), 900):
            query = db.query(ReplayMatchState).filter(
                ReplayMatchState.match_uuid.in_(ids[i:i + 900]),
                ReplayMatchState.recipe == recipe,
                ReplayMatchState.control_revision == control,
                ReplayMatchState.gaps_revision == gaps,
                ReplayMatchState.checked_at >= cutoff)
            for row in query:
                if row.first_step is not None and row.first_step not in _LABELS:
                    continue
                label, automatic = _LABELS.get(row.first_step, (None, None))
                out[str(row.match_uuid).lower()] = {
                    "done": row.done, "total": row.total, "label": label, "automatic": automatic,
                    **({"unsupported": True} if row.first_step in UNSUPPORTED_STEPS else {})}
    except Exception:
        log.exception("stored replay match states could not be read for a match list")
        db.rollback()
        return {}
    return out
