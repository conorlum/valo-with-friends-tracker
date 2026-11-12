"""Read-only inventory of stored replay problems, across the friends site's corpus.

No persistent state or dismissal: once a recompute agrees, a problem disappears. `cached` keeps all evidence
for prompts; `page` caps the rows shown. Feature checks run serially, outside request threads.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import select

from app.config import settings
from app.models.site_events import SiteEvent
from app.replays import control_format as cf
from app.models.replay import (Replay, ReplayPlayer, ReplayRoundControl, ReplayRoundGapRun, ReplayRoundMoments,
                               ReplayRoundUtil, ReplayUpload, UtilModel)
from app.services import control_heights, replay_progress, site_health
from app.services.replay_gaps import Freshness

SHOW = 50
TTL_S = 55
SITE = "https://valowithfriendstracker.onrender.com"
KINDS = (
    ("box_conflict", "Box height conflicts"),
    ("control_unsupported", "Map control: not supported in this round"),
    ("control_failed", "Map control failed"),
    ("gaps_failed", "Timing gaps failed"),
    ("moments_failed", "Critical Moments failed"),
    ("util_failed", "Critical Util failed"),
    ("heights_rejected", "Height builds rejected"),
    ("heights_out_of_tries", "Height rebuilds out of tries"),
    ("given_up", "Rounds given up on"),
    ("held", "Replays held for a re-parse"),
    ("upload_failed", "Uploads failed"),
    ("upload_held", "Uploads held"),
    ("store_failed", "Replay storage failed"),
    ("ack_missing", "Archive acknowledgement missing"),
    ("stuck", "Uploads stuck"),
    ("unlinked", "Replays unlinked"),
    ("needs_a_person", "Needs a person"),
    ("feature_failed", "Map features failed to verify"),
    ("trip_too_long", "Cypher trips longer than 16 m"),
    ("page_error", "Pages that crashed"),
    ("slow_page", "Slow page loads"),
    ("loop_failed", "Background loops that failed"),
    ("loop_quiet", "Background loops gone quiet"),
    ("util_build_failed", "Critical Util build failed"),
    ("util_build_stale", "Critical Util build out of date"),
    ("worker_overrun", "Worker jobs that ran over"),
    ("worker_refused", "Worker refusals and failures"),
    ("warm_vanished", "Warm cache files deleted before use"),
    ("unscored_match", "Matches with no Impact scores"),
    ("kill_impact_missing", "Replays missing per-kill Impact"),
    ("map_pool_note", "Map-pool patch notes not read"),
    ("cache_fallback", "Cache fallbacks"),
)
GROUP_ORDER = ("Pages", "Background work", "Data", "Replays")
GROUPS = {**{key: "Replays" for key, _ in KINDS}, "page_error": "Pages", "slow_page": "Pages",
          "loop_failed": "Background work", "loop_quiet": "Background work",
          "util_build_failed": "Background work", "util_build_stale": "Background work",
          "worker_overrun": "Background work", "worker_refused": "Background work", "warm_vanished": "Background work",
          "unscored_match": "Data", "kill_impact_missing": "Data", "map_pool_note": "Data", "cache_fallback": "Data"}
EVENT_KINDS = frozenset({"page_error", "slow_page", "loop_failed", "loop_quiet", "util_build_failed", "util_build_stale",
                         "worker_overrun", "worker_refused", "warm_vanished", "cache_fallback",
                         "trip_too_long", "unscored_match", "kill_impact_missing", "map_pool_note"})
LIVE_KINDS = frozenset({"loop_quiet", "util_build_stale", "unscored_match", "kill_impact_missing", "map_pool_note"})
# read when the card is computed; the rest come from site_events
KILL_IMPACT_RECHECK_S = 600  # How often the background pass compares stored per-kill fingerprints
log = logging.getLogger(__name__)
_lock = threading.Lock()
_cached: tuple[float, dict] | None = None
_verification_lock = threading.Lock()
_verification_results: dict = {}
_verification_started = False
_verification_pending = False   # the last compute saw a current artifact with no result
_verification_running = False
_verification_progress = (0, 0)
_kill_impact_results: dict = {}     # replay id -> item dict, for linked replays whose stored fingerprint is stale
_kill_impact_checked_at: datetime | None = None
_util_values_current: bool | None = None   # the last pass's answer, None = never or failed
_util_checked_at: datetime | None = None
# the last pass's (build key, oldest current util row newer than that build or None): the age the out-of-date
# warning uses, since the card's own cheap query also counts rows a newer input superseded
_util_oldest_current: tuple | None = None
_digests: dict = {}    # (replay id, round, fingerprint) -> the round's feature artifact digest, or None


def now() -> datetime:
    """UTC clock, replaceable in tests."""
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


LIVE_NOTE = "live: checked when the card is computed"


def _parse_iso(value) -> datetime | None:
    try:
        return _utc(datetime.fromisoformat(value)) if value else None
    except (TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _inventory_command() -> str:
    return ('scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only python -c '
            '"import json; from app.db import SessionLocal; from app.services.replay_errors import compute; '
            'print(json.dumps(compute(SessionLocal(), limit=None), indent=2))"')


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


def _event_row(*, kind, key, count, first_seen, last_seen, error, context, link=None, reproduce=None) -> dict:
    return {"kind": kind, "key": key, "count": count, "first_seen": _iso(first_seen),
            "last_seen": _iso(last_seen), "error": error,
            "context": copy.deepcopy(context) if context is not None else {},
            "link": link or f"{SITE}/replays/upload", "reproduce": reproduce or _inventory_command()}


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


def _feature_representatives(db, collected=None):
    """Current rounds grouped by immutable artifact, lowest replay/round first."""
    global _digests
    collected = replay_progress.round_states(db) if collected is None else collected
    groups, unavailable = {}, 0
    # Never the `data` blob (one round's map control is large), and a summary only for a current round not read
    # before: a round's summary is fixed by its fingerprint, and this runs on every refresh of the card.
    current = []
    for row in db.query(ReplayRoundControl.replay_id, ReplayRoundControl.round_number,
                        ReplayRoundControl.fingerprint, ReplayRoundControl.control_revision,
                        ReplayRoundControl.computed_at).filter(
            ReplayRoundControl.status == "ok").order_by(
            ReplayRoundControl.replay_id, ReplayRoundControl.round_number):
        step = collected["states"].get((row.replay_id, row.round_number), "")
        if step == "done" or step.startswith("gaps_"):
            current.append(row)
    known = {key: digest for key, digest in _digests.items()}
    unread = sorted({row.replay_id for row in current
                     if (row.replay_id, row.round_number, row.fingerprint) not in known})
    for i in range(0, len(unread), 200):
        for rid, n, fp, summary in db.query(
                ReplayRoundControl.replay_id, ReplayRoundControl.round_number, ReplayRoundControl.fingerprint,
                ReplayRoundControl.summary).filter(ReplayRoundControl.status == "ok",
                                                   ReplayRoundControl.replay_id.in_(unread[i:i + 200])):
            try:
                digest = cf.unpack_summary(summary)["provenance"]["inputs"]["geometry"]["features"]
                if not isinstance(digest, str) or not digest:
                    raise ValueError("missing artifact digest")
            except (KeyError, TypeError, ValueError, OSError, EOFError):
                digest = None
            known[rid, n, fp] = digest
    # This call's own snapshot: another reader (a request, the verifier) may replace the shared cache meanwhile.
    digests = {key: known.get(key) for key in ((r.replay_id, r.round_number, r.fingerprint) for r in current)}
    _digests = digests
    for row in current:
        digest = digests[row.replay_id, row.round_number, row.fingerprint]
        if digest is None:
            unavailable += 1
            continue
        if digest in groups:
            representative, replay, count = groups[digest]
            groups[digest] = (representative, replay, count + 1)
        else:
            groups[digest] = (row, collected["replays"][row.replay_id], 1)
    return groups, unavailable


def _feature_command(row):
    return ('scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only python -c '
            '"from app.db import SessionLocal; from app.models.replay import ReplayRoundControl; '
            'from app.services.control_feature_verification import verify_stored_round; '
            f'db = SessionLocal(); print(verify_stored_round(db, db.get(ReplayRoundControl, '
            f'({int(row.replay_id)}, {int(row.round_number)})))); db.close()"')


def _load_round(db, row):
    """The whole stored row for one representative (the verifier reads its summary); a test seam."""
    full = db.get(ReplayRoundControl, (row.replay_id, row.round_number))
    if full is None:
        raise LookupError("round no longer stored")
    return full


def _verification_changed():
    global _cached
    with _lock:
        _cached = None


def _verify_kill_impact(db) -> dict:
    """Linked replays on a scored match whose stored per-kill split was made from other Impact rows than the
    match has now: {replay id: item dict}."""
    from app.replays.db import impact_fingerprint
    from app.services import site_health_checks

    # only the stored split's fingerprint, read in the database: never the splits themselves
    found = db.execute(select(Replay.id, Replay.match_id, Replay.map_name, Replay.match_uuid, Replay.linked_at,
                              Replay.created_at, Replay.kill_impact["fingerprint"].as_string())
                       .where(Replay.link_status == "linked", Replay.match_id.is_not(None),
                              Replay.kill_impact.is_not(None), site_health_checks._scored(Replay.match_id))
                       .order_by(Replay.id)).all()
    mismatched = {}
    for rid, match_id, map_name, match_uuid, linked_at, created_at, stored in found:
        if stored == impact_fingerprint(db, match_id):
            continue
        mismatched[rid] = {
            "key": str(rid), "first_seen": linked_at or created_at,
            "error": "stored per-kill Impact fingerprint no longer matches the match's Impact rows",
            "context": {"match_id": match_id, "map_name": map_name},
            "reproduce": site_health_checks.kill_impact_reproduce(match_uuid)}
    return mismatched


def _verify_data(db):
    """The data checks that are too slow to run when the card is computed. Each keeps its previous result when it
    fails."""
    global _kill_impact_results, _kill_impact_checked_at, _util_values_current, _util_checked_at, \
        _util_oldest_current
    try:
        results = _verify_kill_impact(db)
        with _verification_lock:
            _kill_impact_results = results
            _kill_impact_checked_at = now()
        _verification_changed()
    except Exception:
        log.exception("the per-kill Impact fingerprint check could not finish")
        with _verification_lock:
            _kill_impact_checked_at = now()   # an attempt counts: a failing check waits its 600 s like a finished one
        try:
            db.rollback()
        except Exception:
            log.warning("rollback after the per-kill Impact check failed", exc_info=True)
    try:
        from app.services import replay_util   # pulls numpy: only the background pass imports it

        rows = replay_util.current_rows(db, with_data=False)
        build = db.execute(select(UtilModel.key, UtilModel.computed_at).where(UtilModel.name == "values")).first()
        current = bool(replay_util.values_current(db, rows=rows))
        newer = [_utc(r[3]) for r in rows if r[3] is not None
                 and (build is None or _utc(r[3]) > _utc(build.computed_at))]
        with _verification_lock:
            _util_values_current, _util_checked_at = current, now()
            _util_oldest_current = (build.key if build else None, min(newer) if newer else None)
        _verification_changed()
    except Exception:
        log.exception("the Critical Util build comparison could not finish")
        with _verification_lock:
            _util_values_current = _util_oldest_current = None
        try:
            db.rollback()
        except Exception:
            log.warning("rollback after the util comparison failed", exc_info=True)


def _verify_features(session_factory=None, verifier=None):
    """Background body, injectable for offline tests. One session and one verifier call at a time."""
    global _verification_started, _verification_running, _verification_progress
    if settings.demo_mode:
        return
    from app.services.control_feature_verification import VerificationResult, verify_stored_round

    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    verifier = verify_stored_round if verifier is None else verifier
    with _verification_lock:
        _verification_started = _verification_running = True
    try:
        with session_factory() as db:
            representatives, _ = _feature_representatives(db)
            with _verification_lock:
                done = sum(digest in _verification_results for digest in representatives)
                _verification_progress = (done, len(representatives))
            _verification_changed()
            for digest, (row, _, _) in representatives.items():
                with _verification_lock:
                    if digest in _verification_results:
                        continue
                try:
                    result = verifier(db, _load_round(db, row))
                except Exception as exc:  # keep the inventory available if a check cannot run
                    result = VerificationResult("unavailable", "unavailable", "unavailable", (str(exc),))
                with _verification_lock:
                    _verification_results[digest] = result
                    done += 1
                    _verification_progress = (done, len(representatives))
                _verification_changed()
            _verify_data(db)
    except Exception:
        log.exception("replay error feature verification could not finish")
    finally:
        with _verification_lock:
            _verification_running = False
        _verification_changed()


def _start_verification():
    """First card open starts one daemon for this process, and a later open starts another once the card has seen
    current artifacts with no result (a recompute published them after the last pass). Never two at once; compute
    never calls this."""
    global _verification_started, _verification_running, _verification_pending
    if settings.demo_mode:
        return
    with _verification_lock:
        if _verification_running:
            return
        checked = _kill_impact_checked_at
        recheck = checked is None or (now() - _utc(checked)).total_seconds() > KILL_IMPACT_RECHECK_S
        if _verification_started and not _verification_pending and not recheck:
            return
        _verification_started = _verification_running = True
        _verification_pending = False
    threading.Thread(target=_verify_features, name="replay-error-features", daemon=True).start()


def collect(db, live=None, *, stale: Counter | None = None, notes: dict | None = None) -> dict[str, list[dict]]:
    """All stored rows, sorted within each kind. Optional `stale` receives obsolete failure counts.

    No query at all in demo mode. Only agents are selected for slot annotations.
    """
    rows = {key: [] for key, _ in KINDS}
    if settings.demo_mode:
        return rows
    stale = Counter() if stale is None else stale
    notes = {} if notes is None else notes
    collected = replay_progress.round_states(db)
    states, replays = collected["states"], collected["replays"]
    if live is None:
        for key in ("heights_out_of_tries", "warm_vanished", "given_up", "held"):
            notes[key] = "not known in this process (no dispatcher here)"
    else:
        from app.services.replay_control_remote import MAX_TRIES

        for map_name in live.heights_out_of_tries:
            rows["heights_out_of_tries"].append(_row(
                map_name=map_name, figures={"map": map_name}, reproduce=_height_command(map_name)))
        for identity, count in live.warm_vanished:
            # D3: counted toward the identity's warm-build fallback; this row is the alarm that it happened.
            row = _row(
                map_name=identity.split(":", 1)[0], figures={"identity": identity, "times": count},
                error=(f"a warm build's visibility cache file left the replay worker's disk {count} time(s) before "
                       "a round leased it (the worker's cache cleanup: 3 newest files per map, or the cache "
                       "budget). Each loss counts toward this identity's fallback to one warming round. Fixes to "
                       "weigh: lease the file from the warm build until its rounds are done, or keep more files "
                       "per map (replay_worker/server.py _next and control_cache). Evidence: the worker's /health "
                       "(control.warm_builds.vanished; control.cache maps, evictions, kept_pruned, reserve) and its "
                       "log lines 'warm identity ... vanished'."))
            row.update(_event_row(kind="warm_vanished", key=identity, count=count, first_seen=None,
                                  last_seen=None, error=row["error"],
                                  context={"times": count, "map": row["map"]}, reproduce=row["reproduce"]))
            rows["warm_vanished"].append(row)
        older = 0
        for rid, n, work, fp in live.given_up:
            if (states.get((rid, n)) not in {f"{work}_missing", f"{work}_stale"}
                    or collected.get("fingerprints", {}).get((rid, n)) != fp):
                older += 1
                continue
            rows["given_up"].append(_row(
                replay=replays[rid], n=n, figures={"kind": work, "tries": MAX_TRIES},
                revision={"fingerprint": fp}, reproduce=_round_command(replays[rid], n)))
        if older:
            notes["given_up"] = f"{older} older give-ups no longer apply"
        round_counts = Counter(rid for rid, _ in states)
        for rid in live.held:
            if rid in replays:
                rows["held"].append(_row(replay=replays[rid], figures={"rounds": round_counts[rid]}))
        for rid, n, sent_at, work in live.in_flight:
            if live.at - sent_at > 3600 and rid in replays:
                rows["stuck"].append(_row(
                    replay=replays[rid], n=n, figures={"kind": work, "minutes": (live.at - sent_at) / 60},
                    reproduce=_round_command(replays[rid], n)))
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

    waiting, unsupported_behind = Counter(), Counter()

    def behind(key, state):
        """Count a failed row that isn't its round's current step, by what its round waits on."""
        if state in replay_progress.UNSUPPORTED_STEPS:
            # Behind a round control doesn't support: listed under that kind; nobody, person or worker, moves it.
            unsupported_behind[key] += 1
        elif replay_progress.needs_person(state):
            # Behind an earlier step only a person moves (a local re-ingest): no worker retry is coming.
            waiting[key] += 1
        else:
            # Stale, or behind an earlier step the worker runs (a re-parse): it gets to it on its own.
            stale[key] += 1

    for key, model, revision in (("control_unsupported", ReplayRoundControl, "control_revision"),
                                 ("control_failed", ReplayRoundControl, "control_revision"),
                                 ("gaps_failed", ReplayRoundGapRun, "gaps_revision")):
        for stored in db.query(model.replay_id, model.round_number, model.error, model.computed_at,
                               getattr(model, revision).label("revision")).filter(model.status == "failed"):
            if model is ReplayRoundControl:
                if replay_progress.is_unsupported(stored.error) != (key == "control_unsupported"):
                    continue
            state = states.get((stored.replay_id, stored.round_number))
            if state != key:
                behind(key, state)
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
                behind(key, states.get((stored.replay_id, stored.round_number)))
                continue
            rows[key].append(_row(replay=replay, n=stored.round_number, error=stored.error,
                                  revision={"gaps_revision": stored.gaps_revision,
                                            "computed_at": _iso(stored.computed_at)},
                                  reproduce=_round_command(replay, stored.round_number)))

    for key in waiting.keys() | unsupported_behind.keys():
        notes[key] = "; ".join(n for n in (
            f"{waiting[key]} failed rows wait behind an earlier step that needs a person" if waiting[key] else None,
            f"{unsupported_behind[key]} older failed rows sit behind a round map control doesn't support"
            if unsupported_behind[key] else None) if n)

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
              if not automatic and key not in ({"control_failed", "gaps_failed"} | replay_progress.UNSUPPORTED_STEPS)}
    counts = Counter((rid, step) for (rid, _), step in states.items() if step in manual)
    for (rid, step), count in counts.items():
        replay = replays[rid]
        rows["needs_a_person"].append(_row(replay=replay, figures={"rounds": count, "step": manual[step]},
                                            revision={"recipe": replay.recipe}))
    representatives, missing = _feature_representatives(db, collected)
    global _verification_pending
    with _verification_lock:
        results = dict(_verification_results)
        started, running = _verification_started, _verification_running
        done, total = _verification_progress
        _verification_pending = any(digest not in results for digest in representatives)
    unavailable = missing
    verified = 0
    for digest, (stored, replay, count) in representatives.items():
        result = results.get(digest)
        if result is None:
            unavailable += 1
            continue
        failed = result.integrity == "failed" or result.recompilation == "failed"
        if not failed and (result.integrity in ("unavailable", "unsupported")
                           or result.recompilation in ("unavailable", "unsupported")):
            unavailable += 1
        else:
            verified += 1
        if failed:
            rows["feature_failed"].append(_row(
                replay=replay, n=stored.round_number,
                figures={"digest": digest, "integrity": result.integrity, "recompilation": result.recompilation,
                         "freshness": result.freshness, "rounds": count}, error="; ".join(result.reasons),
                revision={"fingerprint": stored.fingerprint, "control_revision": stored.control_revision,
                          "computed_at": _iso(stored.computed_at)}, reproduce=_feature_command(stored)))
    notes["feature_failed"] = (f"checking {done} of {total}" if running else
                               f"{verified} verified, {unavailable} unavailable or unsupported" if started else
                               "not checked yet")
    try:
        stored = db.execute(select(SiteEvent.kind, SiteEvent.key, SiteEvent.count, SiteEvent.first_seen,
                                   SiteEvent.last_seen, SiteEvent.error, SiteEvent.context)).all()
    except Exception:
        log.warning("site_events could not be read; showing replay evidence only", exc_info=True)
        try:
            db.rollback()
        except Exception:
            log.warning("rollback after the site_events read failed", exc_info=True)
        stored = []
    for kind, key, count, first_seen, last_seen, error, context in stored:
        # a warm_vanished identity can show twice: the live row (this worker process's count) and the stored
        # history (every process, with first and last seen)
        if kind in EVENT_KINDS and kind not in LIVE_KINDS and kind in rows:
            rows[kind].append(_event_row(kind=kind, key=key, count=count, first_seen=first_seen,
                                         last_seen=last_seen, error=error, context=context))
    if "loop_quiet" in rows:
        moment = now()
        for item in site_health.quiet_loops(moment):
            since = item["last_end"] or item["registered"]
            error = (f"last finished a pass at {_iso(item['last_end'])}" if item["last_end"]
                     else f"never finished a pass since {_iso(item['registered'])}")
            if item["in_pass_since"]:
                error += f"; in a pass since {_iso(item['in_pass_since'])}"
            rows["loop_quiet"].append(_event_row(
                kind="loop_quiet", key=item["name"], count=1, first_seen=since, last_seen=moment, error=error,
                context={"limit_s": item["limit_s"]}, reproduce="live: read in this process, not stored"))
    from app.services import site_health_checks

    moment = now()
    cap = site_health_checks.UNSCORED_CAP
    cut_note = f"more than {cap}: the first {cap} are counted here; the rest query lists them all"
    if "unscored_match" in rows:
        found = site_health_checks.unscored_matches(db, moment)
        if len(found) > cap:
            found, notes["unscored_match"] = found[:cap], cut_note
        for item in found:
            rows["unscored_match"].append(_event_row(
                kind="unscored_match", key=item["key"], count=1, first_seen=item["first_seen"], last_seen=moment,
                error=item["error"], context=item["context"], link=item.get("link"),
                reproduce=item.get("reproduce") or LIVE_NOTE))
    if "kill_impact_missing" in rows:
        found = site_health_checks.kill_impact_null(db)
        if len(found) > cap:
            found, notes["kill_impact_missing"] = found[:cap], cut_note
        items = {item["key"]: item for item in found}
        with _verification_lock:
            mismatched = dict(_kill_impact_results)
        for item in mismatched.values():
            items.setdefault(item["key"], item)
        for item in items.values():
            rows["kill_impact_missing"].append(_event_row(
                kind="kill_impact_missing", key=item["key"], count=1, first_seen=item["first_seen"],
                last_seen=moment, error=item["error"], context=item["context"], link=item.get("link"),
                reproduce=item.get("reproduce") or LIVE_NOTE))
    if "map_pool_note" in rows:
        for item in site_health_checks.map_pool_notes(db):
            rows["map_pool_note"].append(_event_row(
                kind="map_pool_note", key=item["key"], count=item["context"].get("miss_count") or 1,
                first_seen=item["first_seen"], last_seen=moment, error=item["error"], context=item["context"],
                link=item.get("link"), reproduce=item.get("reproduce") or LIVE_NOTE))
    if "util_build_stale" in rows:
        with _verification_lock:
            util_current, util_checked = _util_values_current, _util_checked_at
            oldest_current = _util_oldest_current
        for item in site_health_checks.util_build_stale(db, moment):
            if util_current is True and util_checked is not None:
                newer = _parse_iso(item["context"].get("oldest_newer_row"))
                if newer is not None and _utc(util_checked) > newer:
                    continue   # a pass after the newer rows found the build current: they were already covered
            if util_current is False and oldest_current is not None \
                    and oldest_current[0] == item["context"].get("build_key"):
                # the pass read which rows are current: superseded ones don't make the build overdue
                if oldest_current[1] is None or \
                        (_utc(moment) - oldest_current[1]).total_seconds() <= site_health_checks.UTIL_STALE_S:
                    continue
            rows["util_build_stale"].append(_event_row(
                kind="util_build_stale", key=item["key"], count=1, first_seen=item["first_seen"], last_seen=moment,
                error=item["error"], context=item["context"],
                reproduce="live: checked when the card is computed"))
    for key, values in rows.items():
        if key not in EVENT_KINDS:
            values.sort(key=_sort_key)
    for key in EVENT_KINDS & rows.keys():
        rows[key].sort(key=lambda row: row["key"])
        rows[key].sort(key=lambda row: row["last_seen"] or "", reverse=True)
    return rows


def compute(db, live=None, *, limit=SHOW) -> dict:
    """Read stored evidence and finished verification results; never start work here."""
    stale = Counter()
    notes = {}
    rows = collect(db, live=live, stale=stale, notes=notes)
    ordered = sorted(KINDS, key=lambda pair: GROUP_ORDER.index(GROUPS[pair[0]]))
    return {"kinds": [{"key": key, "label": label, "group": GROUPS[key],
                       "shape": "event" if key in EVENT_KINDS else "replay", "count": len(rows[key]),
                       "note": "; ".join(n for n in (
                           f"{stale[key]} failed rows from older rules: the worker will retry them" if stale[key]
                           else None, notes.get(key)) if n) or None, "rows": rows[key][:limit]}
                      for key, label in ordered],
            "checked_at": _iso(now())}


def cached(db, now: float | None = None) -> dict:
    """One computation shared by every viewer for TTL_S."""
    global _cached
    if settings.demo_mode:
        return compute(db, limit=None)
    from app.services import replay_control_remote

    _start_verification()
    stamp = time.monotonic() if now is None else now
    with _lock:
        if _cached is None or stamp - _cached[0] >= TTL_S:
            _cached = (stamp, compute(db, live=replay_control_remote.LIVE, limit=None))
        return _cached[1]


def page(result):
    """An independent display copy; counts and the cached prompt evidence stay intact."""
    shown = {**result, "kinds": [{**kind, "rows": kind["rows"][:SHOW]} for kind in result["kinds"]]}
    return copy.deepcopy(shown)


def _sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def _rest_query(key, rows):
    """Select exactly the remaining stored identities, with only diagnostic columns."""
    if key == "loop_quiet":
        return "(live rows, not stored: read the site's loop state in the running process)"
    checked = {"util_build_stale": "SELECT name, key, computed_at FROM util_model WHERE name = 'values';",
               "unscored_match": "SELECT m.id, m.external_id, m.map_name, m.created_at FROM matches m WHERE "
                                 "m.created_at < now() - interval '1 hour' AND NOT EXISTS (SELECT 1 FROM rounds r "
                                 "JOIN impact_scores i ON i.round_id = r.id WHERE r.match_id = m.id) ORDER BY m.id;",
               "kill_impact_missing": "SELECT id, match_id, map_name, match_uuid, kill_impact->>'fingerprint' "
                                      "AS stored_fingerprint FROM replays WHERE link_status = 'linked' AND "
                                      "match_id IS NOT NULL ORDER BY id;  -- compare each with "
                                      "app.replays.db.impact_fingerprint",
               "map_pool_note": "SELECT url, patch, miss_count, last_error FROM map_pool_patch_notes "
                                "WHERE last_error IS NOT NULL ORDER BY url;"}
    if key in checked:
        return f"(checked live, not stored in site_events; read-only) {checked[key]}"
    if key in EVENT_KINDS:
        keys =", ".join(_sql_string(r["key"]) for r in rows)
        return ("SELECT kind, key, count, first_seen, last_seen, error, context FROM site_events "
                f"WHERE kind = {_sql_string(key)} AND key IN ({keys}) ORDER BY last_seen DESC;")
    models = {"control_unsupported": ReplayRoundControl, "control_failed": ReplayRoundControl,
              "gaps_failed": ReplayRoundGapRun, "moments_failed": ReplayRoundMoments, "util_failed": ReplayRoundUtil}
    if key in models:
        identities = ", ".join(f"({_sql_string(r['match'])}, {int(r['round'])})" for r in rows)
        return (f"SELECT r.map_name, r.match_uuid, e.round_number, e.error, e.computed_at "
                f"FROM {models[key].__tablename__} e JOIN replays r ON r.id = e.replay_id "
                f"WHERE e.status = 'failed' AND (r.match_uuid, e.round_number) IN ({identities}) "
                "ORDER BY r.map_name, r.match_uuid, e.round_number;")
    if key == "heights_rejected":
        maps = ", ".join(sorted({_sql_string(r["map"]) for r in rows}))
        return ("SELECT h.map_name, h.digest, h.report, h.rules, h.built_at FROM control_heights h "
                f"WHERE h.status = 'rejected' AND h.map_name IN ({maps}) "
                "AND h.id = (SELECT x.id FROM control_heights x WHERE x.map_name = h.map_name "
                "ORDER BY x.built_at DESC, x.id DESC LIMIT 1) ORDER BY h.map_name;")
    filters = {"upload_failed": "u.status = 'failed'", "upload_held": "u.status = 'held'",
               "store_failed": "u.store_outcome = 'failed'", "ack_missing": "u.archive_ack IS NULL",
               "stuck": "u.status IN ('queued', 'parsing')"}
    if key in filters and all("upload_id" in r["figures"] for r in rows):
        ids = ", ".join(_sql_string(r["figures"]["upload_id"]) for r in rows)
        return ("SELECT u.id, u.replay_id, u.status, u.error, u.created_at, u.finished_at, "
                "u.worker_job_id, u.store_outcome, u.archive_ack FROM replay_uploads u "
                f"WHERE {filters[key]} AND u.id IN ({ids}) ORDER BY u.id;")
    if key in ("unlinked", "needs_a_person"):
        matches = ", ".join(sorted({_sql_string(r["match"]) for r in rows}))
        status = " AND link_status IN ('unlinked', 'refused')" if key == "unlinked" else ""
        return ("SELECT map_name, match_uuid, recipe, link_status, link_report FROM replays "
                f"WHERE match_uuid IN ({matches}){status} ORDER BY map_name, match_uuid;")
    return "\n".join(dict.fromkeys(r["reproduce"] for r in rows))


def prompt(result, kind=None) -> str:
    """Plain-text read-only investigation prompt, sharing 200 evidence blocks across kinds."""
    if kind is not None and kind not in dict(KINDS):
        raise KeyError(kind)
    by_key = {entry["key"]: entry for entry in result["kinds"]}
    chosen = [by_key[key] for key, _ in KINDS if key in by_key and (kind is None or kind == key)]
    if kind is not None and not any(entry["rows"] for entry in chosen):
        return f"{dict(KINDS)[kind]}: nothing to fix."
    lines = ["Investigate site health problems that need a human.",
             "Repo: conorlum/valo-with-friends-tracker on GitHub; code is under webapp/.",
             "Investigate read-only: read the code, and access the DB only from webapp/ through",
             "scripts/with_friends_db.py --expect-database valowithfriendsdb --read-only",
             "For each item, name the likely cause and propose a fix. Change nothing until the owner approves.",
             f"Inventory checked at: {result.get('checked_at')}"]
    remaining = 200
    for entry in chosen:
        if not entry["rows"]:
            continue
        lines.extend(["", f"{entry['label']} ({entry['count']})", f"note: {entry['note'] or ''}"])
        shown = entry["rows"][:remaining]
        for row in shown:
            lines.append("")
            if entry.get("shape") == "event":
                lines.extend([f"kind: {row['kind']}", f"key: {row['key']}", f"count: {row['count']}",
                              f"first_seen: {row['first_seen']}", f"last_seen: {row['last_seen']}",
                              f"error: {row['error']}",
                              f"context: {json.dumps(row['context'], ensure_ascii=False, sort_keys=True)}",
                              f"link: {row['link']}", f"reproduce: {row['reproduce']}"])
                continue
            for field in ("map", "match", "round", "t", "agent"):
                lines.append(f"{field}: {row[field]}")
            for name, value in row["figures"].items():
                lines.append(f"{name}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
            lines.extend([f"link: {row['link']}", f"error: {row['error']}",
                          f"revision: {json.dumps(row['revision'], ensure_ascii=False, sort_keys=True)}",
                          f"reproduce: {row['reproduce']}"])
        remaining -= len(shown)
        rest = entry["rows"][len(shown):]
        if rest:
            lines.extend(["", f"{len(rest)} more. List the rest read-only:", _rest_query(entry["key"], rest)])
    return "\n".join(lines)
