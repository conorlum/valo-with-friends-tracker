"""The automatic re-parse queue: the site's half (docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md,
tasks 4-7).

A stored replay is stale when its `recipe` is not the one this deploy stamps (inequality: recipes are never
ordered). A stale *uploaded* replay whose recording the worker has archived is parsed again from that file,
one at a time, behind every upload. A stale local ingest is counted and left to `scripts/reingest_replays.py`,
even when the same file happens to be archived.

- `off_reason`: this site's own settings. The worker's half is `replay_upload.worker_off_reason`; nothing is
  started unless both say None. `REPLAY_REPARSE_AUTO` is off by default.
- `classify`: every stored replay with its state, from the database and an archive index the caller already
  read. It makes no worker request and writes nothing.
- `unfinished_attempts`: every automatic attempt not yet settled, under any recipe. One is enough to stop a
  new one site-wide: an attempt whose acceptance is unknown is still unfinished.

An attempt is a `replay_uploads` row (`replay_upload.AUTO_PREFIX`, `auto_context`). Only an attempt the worker
accepted counts as a try: a refusal before acceptance used no parse. A new recipe is a new tag, so finished
tries start again from zero, but an unfinished attempt of an older recipe still blocks.

- `step`: one pass, called by the control dispatcher while it holds its advisory lock (so one site process at
  a time). It first settles what is unfinished, then, with the switch on and everything quiet, reserves one
  attempt, commits it, and only then asks the worker. The reservation's id is the worker's idempotency key:
  whatever is lost afterwards (the reply, this process, the commit that records the acceptance), the same id
  is looked up or sent again and can only ever have one job. A second row is never the answer to doubt.
- `status`: the same checks and counts, read only, for `GET /admin/replays/reparse/status`.

`now` is float epoch seconds everywhere here; a naive `finished_at` (sqlite) is read as UTC.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import not_, or_
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.models.match import Match
from app.models.replay import Replay, ReplayDeletion, ReplayUpload
from app.replays import contract
from app.replays import db as replay_db
from app.services import replay_control, replay_gaps
from app.services import replay_upload as uploads

log = logging.getLogger(__name__)

MAX_TRIES = 2       # accepted attempts per match, file and target recipe
BACKOFF_S = 3600    # between those two
SETTLE_S = 180      # after an accepted attempt settles, before the next is reserved
INDEX_S = 120       # the archive index is read at most this often
TAG_MAX = 64        # replay_uploads.session_key holds 64 characters

STATES = ("deleted", "local", "current", "resolving", "in_flight", "gave_up", "unsupported_build", "unknown",
          "no_archive", "other_recording", "backoff", "eligible")
# Uploaded replays that are waiting for a re-parse: computing their map control now would be thrown away.
HELD = ("eligible", "backoff", "resolving", "in_flight")
# The worker's contract refusal (replay_worker/server.py): the file can't be condensed under this recipe,
# however often it is tried.
FINAL_REFUSAL = "not condensable"


@dataclass(frozen=True)
class Entry:
    replay_id: int
    match_uuid: str
    map_name: str
    state: str
    rank: int               # the order attempts are made in, 0 first (the control queue's: newest match first)
    attempts: int           # accepted attempts that ended, for this file under the target recipe
    last_error: str | None  # of the latest of them


def off_reason(recipe: str | None = None) -> str | None:
    """Why this site starts no automatic re-parse, from its own settings; None when they allow it."""
    if settings.demo_mode:
        return "demo mode"
    if not settings.replay_reparse_auto:
        return "REPLAY_REPARSE_AUTO is off"
    if not settings.replay_control_remote:
        return "map control on the worker (REPLAY_CONTROL_REMOTE) is off"
    if not settings.replay_worker_url:
        return "no replay worker URL is set"
    if len(uploads.auto_tag(recipe if recipe is not None else uploads.site_recipe())) > TAG_MAX:
        return "this recipe is too long for an attempt's tag"
    return None


def index_of(archive: dict | None) -> dict[str, str] | None:
    """`GET /archive` as {match uuid: sha256 of the archived recording}. None in, None out: an index that
    couldn't be read is unknown, never an empty archive."""
    if not isinstance(archive, dict) or not isinstance(archive.get("files"), list):
        return None
    return {str(entry["match_uuid"]).lower(): entry.get("sha256") for entry in archive["files"]
            if isinstance(entry, dict) and entry.get("match_uuid")}


def epoch(when: datetime | None) -> float | None:
    if when is None:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()


def unfinished_attempts(session) -> list[ReplayUpload]:
    """Every automatic attempt not yet settled, oldest first, whatever recipe it aims for and whether or not
    its context can be read."""
    return (session.query(ReplayUpload)
            .filter(ReplayUpload.status.in_(uploads.UNFINISHED),
                    ReplayUpload.session_key.startswith(uploads.AUTO_PREFIX, autoescape=True))
            .order_by(ReplayUpload.created_at, ReplayUpload.id).all())


def accepted_attempts(session, recipe: str) -> dict[tuple[str, str], list[ReplayUpload]]:
    """The settled attempts the worker accepted for `recipe`, oldest first, by (match uuid, file sha256).
    Keyed from the row's validated context: `replay_id` is null after a replacement, and a sha alone could be
    another match's."""
    found: dict[tuple[str, str], list[ReplayUpload]] = {}
    rows = (session.query(ReplayUpload)
            .filter(ReplayUpload.session_key == uploads.auto_tag(recipe),
                    ReplayUpload.status.notin_(uploads.UNFINISHED))
            .order_by(ReplayUpload.finished_at, ReplayUpload.created_at, ReplayUpload.id))
    for upload in rows:
        context = uploads.auto_context(upload)
        if context is None or context["phase"] != "accepted" or context["target_recipe"] != recipe:
            continue
        found.setdefault((context["match_uuid"], context["source_sha256"]), []).append(upload)
    return found


def classify(session, recipe: str, index: dict[str, str] | None, builds: frozenset[str], now: float) -> list[Entry]:
    """Every stored replay, in the order attempts are made. `index` is `index_of` the worker's archive (None:
    not known), `builds` the pin's supported builds."""
    deleted = {str(uuid).lower() for (uuid,) in session.query(ReplayDeletion.match_uuid)}
    waiting: dict[str, str] = {}
    for upload in unfinished_attempts(session):   # across every recipe: an old target's attempt still blocks
        context = uploads.auto_context(upload)
        if context is not None and waiting.get(context["match_uuid"]) != "in_flight":
            waiting[context["match_uuid"]] = "in_flight" if context["phase"] == "accepted" else "resolving"
    tried = accepted_attempts(session, recipe)
    rows = (session.query(Replay, Match.played_at).outerjoin(Match, Match.id == Replay.match_id).all())

    def order(row):  # the control queue's D2 order (replay_control_remote._rank)
        replay, played = row
        if replay.link_status == "linked" and played is not None:
            return (0, -epoch(played), -replay.id)
        return (1, -(epoch(replay.created_at) or 0.0), -replay.id)

    entries = []
    for rank, (replay, _) in enumerate(sorted(rows, key=order)):
        uuid = replay.match_uuid.lower()
        attempts = tried.get((uuid, replay.source_sha256), [])
        last = attempts[-1] if attempts else None
        if uuid in deleted:
            state = "deleted"
        elif replay.source != "upload":
            state = "local"
        elif replay.recipe == recipe:
            state = "current"
        elif uuid in waiting:
            state = waiting[uuid]
        elif replay.game_branch not in builds:
            state = "unsupported_build"
        elif index is None:
            state = "unknown"
        elif uuid not in index:
            state = "no_archive"
        elif index[uuid] != replay.source_sha256:
            state = "other_recording"
        elif len(attempts) >= MAX_TRIES or any((a.error or "").startswith(FINAL_REFUSAL) for a in attempts):
            state = "gave_up"
        elif last is not None and now - (epoch(last.finished_at) or now) < BACKOFF_S:
            state = "backoff"
        else:
            state = "eligible"
        entries.append(Entry(replay.id, uuid, replay.map_name, state, rank, len(attempts),
                             last.error if last is not None else None))
    return entries


def held(entries: list[Entry]) -> frozenset[int]:
    """The replays whose map control and timing gaps wait for their re-parse. Never a local one: `classify`
    gives those `local` whatever else is true of them."""
    return frozenset(entry.replay_id for entry in entries if entry.state in HELD)


# ---------------------------------------------------------------- one pass: recover, then reserve and submit

COUNTS = ("reparse_reserved", "reparse_sent", "reparse_recovered", "reparse_finished", "reparse_refused",
          "reparse_deferred")
# The attempt routes' definite refusals (replay_worker/server.py): no job was made and none can be for this
# id, so the attempt ends without having used a parse.
REFUSALS = {"no_archived_file": "the worker has no archived file for this match",
            "sha_mismatch": "the worker's archived recording is another file",
            "deleted": "the match is deleted on the worker",
            "identity_conflict": "the worker knows this attempt under another match or file",
            "bad_request": "the worker refused the request as malformed",
            "closed": "closed before the worker accepted it"}


@dataclass
class Memo:
    """What one site process remembers between passes: only the archive index, which is a read. Everything
    correctness depends on is in `replay_uploads` and the worker's receipts, so a restart loses nothing."""
    index: dict[str, str] | None = None
    index_at: float | None = None


_builds: frozenset[str] | None = None


def supported_builds() -> frozenset[str]:
    global _builds
    if _builds is None:
        _builds = contract.load_pin().supported_builds
    return _builds


def busy_reason(session, health: dict, control_in_flight) -> str | None:
    """Why no parse may be asked for right now though everything is switched on; None when all is quiet.
    A re-parse goes behind every upload and waits for map control to drain: a parse stops a control child."""
    ordinary = (session.query(ReplayUpload.id)
                .filter(ReplayUpload.status.in_(uploads.UNFINISHED),
                        or_(ReplayUpload.session_key.is_(None),
                            not_(ReplayUpload.session_key.startswith(uploads.AUTO_PREFIX, autoescape=True))))
                .first())
    if ordinary is not None:
        return "an upload is unfinished"
    if type(health.get("queued")) is not int or health["queued"]:
        return "the worker has uploads queued"
    if control_in_flight:
        return "map control tasks are in flight"
    control = health.get("control") or {}
    if any(type(control.get(key)) is not int or control[key] for key in ("queued", "running")):
        return "the worker is computing map control"
    return None


def _tombstoned(session, match_uuid: str) -> bool:
    return match_uuid in {str(found).lower() for (found,) in session.query(ReplayDeletion.match_uuid)}


def _settled(session):
    """Finished automatic attempts, latest first."""
    return (session.query(ReplayUpload)
            .filter(ReplayUpload.status.notin_(uploads.UNFINISHED),
                    ReplayUpload.session_key.startswith(uploads.AUTO_PREFIX, autoescape=True))
            .order_by(ReplayUpload.finished_at.desc(), ReplayUpload.created_at.desc(), ReplayUpload.id))


def settling(session, now: float) -> bool:
    """Whether the last accepted attempt, under any recipe, settled less than SETTLE_S ago."""
    for upload in _settled(session):
        context = uploads.auto_context(upload)
        if context is not None and context["phase"] == "accepted":
            return now - (epoch(upload.finished_at) or now) < SETTLE_S
    return False


def recently_refused(session, recipe: str, now: float) -> set[tuple[str, str]]:
    """(match uuid, file sha256) of attempts for `recipe` the worker refused before acceptance in the last
    BACKOFF_S. They used no parse and don't count as tries, but the same replay isn't picked again at once:
    an archive index that disagrees with the worker would otherwise add a refused row every pass."""
    out = set()
    for upload in _settled(session).filter(ReplayUpload.session_key == uploads.auto_tag(recipe)):
        context = uploads.auto_context(upload)
        if (context is not None and context["phase"] == "refused_before_acceptance"
                and now - (epoch(upload.finished_at) or now) < BACKOFF_S):
            out.add((context["match_uuid"], context["source_sha256"]))
    return out


def restoration(session, recipe: str) -> dict | None:
    """The replay the last successful automatic replacement stored, while its map control and timing gaps
    are not yet back: `{"match_uuid", "replay_id", "control_rounds", "gap_rounds"}` (rounds still to
    compute), else None. Read from the database, never inferred from an empty worker queue: rounds whose
    machine retries ran out are still missing here. A round whose own computation failed under the current
    inputs is settled. A replay that is gone, superseded, deleted or stale again no longer holds the queue."""
    for upload in _settled(session).filter(ReplayUpload.status == "stored",
                                           ReplayUpload.store_outcome == "replaced"):
        context = uploads.auto_context(upload)
        if context is None:
            continue
        replay = session.get(Replay, upload.replay_id) if upload.replay_id is not None else None
        if (replay is None or replay.source != "upload" or replay.recipe != recipe
                or replay.recipe != context["target_recipe"] or replay.source_sha256 != context["source_sha256"]
                or _tombstoned(session, context["match_uuid"])):
            return None
        todo = [p for p in replay_control.plan(session, match_uuid=context["match_uuid"])
                if p.computable and p.reason in ("missing", "stale")]
        gaps = replay_gaps.plan_gaps(session, todo,
                                     replay_control.plan(session, match_uuid=context["match_uuid"], force=True))
        if not todo and not gaps:
            return None
        return {"match_uuid": context["match_uuid"], "replay_id": replay.id, "control_rounds": len(todo),
                "gap_rounds": len(gaps)}
    return None


def _selection_problem(session, context: dict, recipe: str) -> str | None:
    """Why a reservation must not be sent (again): what it was selected for no longer holds. Called with the
    match's lock held. Not `classify`: the reservation itself makes its replay `resolving` there."""
    if context["target_recipe"] != recipe:
        return "the site is on another recipe now"
    if _tombstoned(session, context["match_uuid"]):
        return "the match was deleted"
    replay = session.get(Replay, context["selected_replay_id"], populate_existing=True)
    if replay is None or replay.match_uuid.lower() != context["match_uuid"]:
        return "the selected replay is gone"
    if replay.source != "upload":
        return "the replay is a local ingest now"
    if (replay.recipe, replay.source_sha256) != (context["source_recipe"], context["source_sha256"]):
        return "the replay changed since it was selected"
    if replay.recipe == recipe:
        return "the replay is already current"
    tried = accepted_attempts(session, recipe).get((context["match_uuid"], context["source_sha256"]), [])
    if len(tried) >= MAX_TRIES or any((a.error or "").startswith(FINAL_REFUSAL) for a in tried):
        return "its attempts are used up"
    return None


def _refuse(session, memo: Memo, upload_id: str, error: str, when: datetime, counts: dict) -> None:
    if uploads.settle_auto(session, upload_id, "failed", error, phase="refused_before_acceptance", now=when):
        counts["reparse_refused"] += 1
    memo.index = None   # the archive is not what the index said: read it again


def _bind(session, memo: Memo, upload_id: str, context: dict, receipt: dict, when: datetime, counts: dict,
          counted: str) -> None:
    """The worker says the attempt has its job. If this commit is lost the row stays reserved, and the next
    pass finds the same receipt by its id."""
    if not uploads.receipt_matches(receipt, upload_id, context):
        _refuse(session, memo, upload_id, "the worker's receipt for this attempt names another file", when, counts)
        return
    try:
        if uploads.bind_accepted(session, upload_id, receipt):
            counts[counted] += 1
    except SQLAlchemyError:
        session.rollback()
        log.exception("automatic re-parse %s: the acceptance was not recorded; it is looked up again", upload_id)


def _send(session, worker, memo: Memo, upload_id: str, context: dict, when: datetime, counts: dict,
          counted: str) -> None:
    """Asks the worker for the attempt's one job. Only called for a committed reservation."""
    try:
        answer = worker.reparse(context["match_uuid"], attempt_id=upload_id,
                                expected_sha256=context["source_sha256"])
    except uploads.WorkerError:
        return   # nothing is known: the reservation stays, and the same id is looked up or sent again
    code = answer.get("code")
    if code == "accepted":
        _bind(session, memo, upload_id, context, answer, when, counts, counted)
    elif code in REFUSALS:
        _refuse(session, memo, upload_id, REFUSALS[code], when, counts)
    # queue_full, archive_off or anything else: neither accepted nor refused for good, so it waits as it is


def _resolve(session, worker, memo: Memo, upload_id: str, recipe: str, site_off: str | None, may_send, when,
             counts: dict) -> None:
    """A reservation the worker doesn't know as accepted, with this site and the worker agreeing: send the
    same identity again, wait, or fence it so that no late request can start its job."""
    session.rollback()
    upload = session.get(ReplayUpload, upload_id, populate_existing=True)
    context = uploads.auto_context(upload) if upload is not None else None
    if context is None or upload.status not in uploads.UNFINISHED or context["phase"] != "reserved":
        return
    replay_db.advisory_lock(session, context["match_uuid"])
    why = site_off or _selection_problem(session, context, recipe)
    session.rollback()   # the selection was read under the lock; the store checks it again under the same lock
    if why is None:
        if may_send():
            _send(session, worker, memo, upload_id, context, when, counts, "reparse_recovered")
        return
    try:
        answer = worker.close_reparse_attempt(upload_id, context["match_uuid"], context["source_sha256"])
    except uploads.WorkerError:
        return   # a timeout is not evidence: unresolved until the worker answers
    if answer.get("code") == "accepted":
        _bind(session, memo, upload_id, context, answer, when, counts, "reparse_recovered")   # collected next pass
    elif answer.get("code") == "closed":
        _refuse(session, memo, upload_id, f"closed before the worker accepted it: {why}", when, counts)


def _phase(session, upload_id: str) -> tuple[str | None, str | None]:
    session.rollback()
    upload = session.get(ReplayUpload, upload_id, populate_existing=True)
    context = uploads.auto_context(upload) if upload is not None else None
    return (upload.status if upload is not None else None), (context["phase"] if context else None)


def _recover(session, worker, memo: Memo, upload_id: str, recipe: str, site_off: str | None, may_send, when,
             counts: dict) -> bool:
    """One unfinished attempt; returns whether it is still unfinished."""
    _, before = _phase(session, upload_id)
    try:
        outcome = uploads.collect_auto(session, upload_id, worker, when)
    except SQLAlchemyError:
        session.rollback()   # nothing was settled: the attempt is as it was, and still blocks a new one
        log.exception("automatic re-parse %s: could not be collected; it is looked at again", upload_id)
        return True
    if outcome.startswith("deferred"):
        counts["reparse_deferred"] += 1
        return True
    status, after = _phase(session, upload_id)
    if before == "reserved" and after == "accepted":
        counts["reparse_recovered"] += 1
    if outcome == "reserved":
        _resolve(session, worker, memo, upload_id, recipe, site_off, may_send, when, counts)
        status, after = _phase(session, upload_id)
        if status in uploads.UNFINISHED:
            return True
        outcome = "refused"   # `_refuse` counted it
    if outcome == "finished":
        if after == "refused_before_acceptance":
            counts["reparse_refused"] += 1
            memo.index = None
        else:
            counts["reparse_finished"] += 1
    return status in uploads.UNFINISHED


def _reserve(session, entry: Entry, recipe: str, index: dict[str, str], when: datetime):
    """Commits the reservation for `entry`, with its match's lock held and the selection read again under
    it; (attempt id, context), or None when it no longer holds. Nothing has been asked of the worker yet."""
    session.rollback()
    replay_db.advisory_lock(session, entry.match_uuid)
    replay = session.get(Replay, entry.replay_id, populate_existing=True)
    if (replay is None or replay.match_uuid.lower() != entry.match_uuid or replay.source != "upload"
            or replay.recipe == recipe or index.get(entry.match_uuid) != replay.source_sha256
            or _tombstoned(session, entry.match_uuid) or unfinished_attempts(session)):
        session.rollback()
        return None
    upload_id, context = str(uuid.uuid4()), uploads.new_auto_context(replay, recipe)
    session.add(ReplayUpload(id=upload_id, status="queued", source_sha256=replay.source_sha256,
                             session_key=uploads.auto_tag(recipe), created_at=when, auto_context=context))
    session.commit()   # before any worker request: if this fails, nothing was asked
    return upload_id, context


def step(session_factory, worker, memo: Memo, control_in_flight, now: float, counts: dict) -> frozenset[int]:
    """One pass of the automatic queue; returns the replays whose map control and timing gaps must wait.
    The caller holds the dispatcher's advisory lock in a session of its own; every read, reservation and
    settlement here uses another one, so a failure here never ends the caller's transaction.

    Off and with nothing unfinished, it asks the worker nothing at all. With an attempt unfinished it always
    looks: an accepted one is collected even with the switch off, a reserved one is sent again (switch on,
    selection still true, all quiet), left waiting, or closed. A worker this site doesn't match (another
    recipe during a deploy, a missing protocol, control off) leaves every attempt exactly as it is."""
    for name in COUNTS:
        counts.setdefault(name, 0)
    if worker is None or settings.demo_mode:
        return frozenset()
    session = session_factory()
    try:
        return _step(session, worker, memo, control_in_flight, now, counts)
    finally:
        session.rollback()
        session.close()


def _step(session, worker, memo: Memo, control_in_flight, now: float, counts: dict) -> frozenset[int]:
    site_off = off_reason()
    pending = [upload.id for upload in unfinished_attempts(session)]
    session.rollback()
    if site_off is not None and not pending:
        return frozenset()
    recipe = uploads.site_recipe()
    try:
        health = worker.health()
    except uploads.WorkerError:
        health = None
    if uploads.worker_off_reason(health, recipe) is not None:
        counts["reparse_deferred"] += len(pending)
        return frozenset()   # nothing speculative is held back, and no attempt is touched
    when = datetime.fromtimestamp(now, timezone.utc)

    def quiet() -> bool:
        return busy_reason(session, health, control_in_flight) is None

    oldest = True   # only the oldest unfinished attempt may be sent again: two can't wait on each other
    for upload_id in pending:
        still = _recover(session, worker, memo, upload_id, recipe, site_off,
                         quiet if oldest else (lambda: False), when, counts)
        oldest = oldest and not still
    if site_off is not None:
        return _owed(session)

    if memo.index is None or memo.index_at is None or now - memo.index_at >= INDEX_S:
        try:
            memo.index = index_of(worker.archive())
        except uploads.WorkerError:
            memo.index = None
        memo.index_at = now if memo.index is not None else None
    entries = classify(session, recipe, memo.index, supported_builds(), now)
    holding = held(entries)
    if (memo.index is None or unfinished_attempts(session) or not quiet() or settling(session, now)
            or restoration(session, recipe) is not None):
        return holding
    refused = recently_refused(session, recipe, now)
    by_id = {replay_id: sha for replay_id, sha in session.query(Replay.id, Replay.source_sha256)}
    chosen = next((entry for entry in entries if entry.state == "eligible"
                   and (entry.match_uuid, by_id.get(entry.replay_id)) not in refused), None)
    reserved = _reserve(session, chosen, recipe, memo.index, when) if chosen is not None else None
    if reserved is not None:
        counts["reparse_reserved"] += 1
        _send(session, worker, memo, reserved[0], reserved[1], when, counts, "reparse_sent")
    return holding


def _owed(session) -> frozenset[int]:
    """With the switch off, only replays whose attempt is still unfinished wait for it."""
    out = set()
    for upload in unfinished_attempts(session):
        context = uploads.auto_context(upload)
        replay = session.get(Replay, context["selected_replay_id"]) if context is not None else None
        if (replay is not None and replay.source == "upload"
                and (replay.recipe, replay.source_sha256) == (context["source_recipe"], context["source_sha256"])):
            out.add(replay.id)
    return frozenset(out)


# ---------------------------------------------------------------- status (read only)


def status(session, worker, now: float | None = None) -> dict:
    """Where the automatic queue stands, for the admin route and `reparse_archive.py --status`. It reads the
    database, the worker's /health and, when the worker matches this site, its archive index; it never
    submits, closes or collects an attempt, and changes nothing.

    `running` says the queue is switched on and the worker matches (the same two checks `step` makes): it
    does not say a parse is running now, nor that the pacing gates would let the next one start. `waiting_on`
    names the gate that holds the next reservation, as far as the route can see it (it can't see this
    process's map-control tasks in flight)."""
    now = time.time() if now is None else now
    recipe = uploads.site_recipe()
    site_off = off_reason(recipe)
    health = None
    if worker is not None and not settings.demo_mode:
        try:
            health = worker.health()
        except uploads.WorkerError:
            health = None
    worker_off = uploads.worker_off_reason(health, recipe) if worker is not None else "no replay worker URL is set"
    reason = site_off or worker_off
    index = None
    if worker_off is None:
        try:
            index = index_of(worker.archive())
        except uploads.WorkerError:
            index = None
    entries = classify(session, recipe, index, supported_builds(), now)
    counts = {state: 0 for state in STATES}
    for entry in entries:
        counts[entry.state] += 1
    unfinished = unfinished_attempts(session)
    attempts = []
    for upload in unfinished:
        context = uploads.auto_context(upload)
        attempts.append({
            "attempt_id": upload.id,
            "match_uuid": context["match_uuid"] if context else None,
            "target_recipe": uploads.auto_recipe(upload.session_key),
            # resolving: reserved, and not known to be accepted. in_flight: the worker has its one job.
            "state": "unreadable" if context is None else "in_flight" if context["phase"] == "accepted" else "resolving",
            "reserved_at": upload.created_at.isoformat() if upload.created_at else None,
            # While this site and the worker don't match, the attempt is left exactly as it is.
            "deferred": worker_off,
        })
    pending = restoration(session, recipe)
    holding = held(entries) if reason is None else _owed(session)
    todo = [p for p in replay_control.plan(session) if p.computable and p.reason in ("missing", "stale")]
    gaps = replay_gaps.plan_gaps(session, todo, replay_control.plan(session, force=True))
    waiting_on = None
    if reason is None:
        if unfinished:
            waiting_on = "an attempt is unfinished"
        elif index is None:
            waiting_on = "the worker's archive index could not be read"
        else:
            waiting_on = busy_reason(session, health, 0)
        if waiting_on is None and settling(session, now):
            waiting_on = f"the last attempt settled less than {SETTLE_S} s ago"
        if waiting_on is None and pending is not None:
            waiting_on = "the last replacement's map control and timing gaps are not back yet"
    archive, control = (health or {}).get("archive") or {}, (health or {}).get("control") or {}
    return {
        "running": reason is None,
        "reason": reason,
        "site_recipe": recipe,
        "worker_recipe": health.get("recipe") if isinstance(health, dict) else None,
        "worker": {"reachable": isinstance(health, dict), "archive_enabled": archive.get("enabled"),
                   "reparse_protocol": archive.get("reparse_protocol"), "control_enabled": control.get("enabled"),
                   "gaps_protocol": control.get("gaps_protocol")},
        "counts": counts,
        "gave_up": [{"match_uuid": e.match_uuid, "map": e.map_name, "attempts": e.attempts,
                     "last_error": e.last_error} for e in entries if e.state == "gave_up"],
        "attempts": attempts,
        "waiting_on": waiting_on,
        "restoration_pending": pending,
        "rounds": {"control_waiting": sum(p.replay_id not in holding for p in todo),
                   "gaps_waiting": sum(p.replay_id not in holding for p in gaps),
                   "held_back": sum(p.replay_id in holding for p in [*todo, *gaps])},
    }
