"""Map control for new replays, computed on the replay worker (docs/map-control-worker-plan.md).

The worker has no database, so the web app drives it: a daemon thread runs `cycle` every CYCLE_S.

1. **Collect** each job in flight. `done` -> stored (app/services/replay_control_store.py, with
   `require_current`: nothing is stored if the round's inputs moved meanwhile), but only when the
   result's CONTROL_REVISION, DATA_VERSION and geometry match this deploy's (the two services deploy
   separately). A failure that is the round's own (`engine`) is stored `failed`, as the local command
   does; the machine's (`infra`), a job the worker forgot (404) or one seen running for STALE_JOB_S is asked
   again, at most MAX_TRIES times per round and inputs, with BACKOFF_S between.
2. **Submit** every round `plan()` lists as `missing` or `stale`
   (docs/superpowers/plans/2026-10-05-control-idle-queue.md, D1-D3), from linked and unlinked replays
   alike: linked ones by their match's played_at, newest first, then the rest by upload time. A failure
   under the current inputs is left alone. Up to IN_FLIGHT at a time; the worker runs them only while it
   isn't parsing, so a job may sit `queued` there for a long time, and that never counts as a failure
   (D8). Only a job seen `running` for STALE_JOB_S does.

**Timing gaps** (docs/superpowers/plans/2026-10-07-worker-gaps-and-kill-one-impl.md). Each planning pass reads
the worker's `/health` once. Only a worker whose control is on and that names `control.gaps_protocol` 1 is
sent a `gaps` block (with the gap fingerprint and the tick cache key, which the worker cannot compute: its
interpreter has no SQLAlchemy); an older image, an unknown protocol or a failed read gets the plain task it
always got, because a gaps block fails the whole task there. A capable task has its own key, so the worker's
dedupe never hands back a plain task's result for it. Its result must also name this deploy's game figures.
Control is stored first; the gaps then go through the guarded writer (app/services/replay_gaps_store.py),
which checks the round's control again under the replay's lock and stores nothing stale.

A round whose control is current but whose gap run is missing or stale (new gap rules, an edited choke asset,
control stored by a plain task) is sent as a **gaps-only** task, again under its own key: after every
sendable control task of the pass, and only to a capable worker. Without the capability that work just
waits; nothing is planned for it and no try is used. Its result changes no control row. A stale one is
skipped by the writer and is not a failure; one from another deploy (revisions, geometry, figures, gap
keys) or a machine failure is asked again like control's, MAX_TRIES times; the detector's own failure under
the current keys is stored as the round's and left.

**Automatic re-parses** (docs/superpowers/plans/2026-10-07-auto-reparse-queue-impl.md, task 6). Between
collecting and submitting, the cycle runs one pass of `replay_reparse_auto.step` with the upload client:
it settles any unfinished attempt (also with REPLAY_REPARSE_AUTO off, so an accepted one is never left
behind) and, with the switch on, may reserve the next. The replays it returns are waiting for their
re-parse; none of their rounds is sent, for control or for gaps alone, since the result would be thrown
away. A failure in that pass is logged and holds nothing back: control goes on. When an attempt finishes
or the held set changes, the planning throttle is reset so the new replay's rounds go out at once.

`plan()` runs when there is room in flight; after a plan that left nothing sendable it waits
PLAN_IDLE_S. On PostgreSQL it holds `pg_try_advisory_xact_lock` for the cycle, so one
instance dispatches when Render overlaps two during a deploy; the worker's key dedupe makes a
duplicate harmless anyway. The lock has a session of its own that does nothing else: collecting, the
automatic pass and planning each use another, so nothing that commits or fails can end the lock's
transaction early. In-flight jobs live in memory: a restart just asks again.

Off unless REPLAY_CONTROL_REMOTE is set and REPLAY_WORKER_URL is, and never in demo mode. Standard
library and the DB only: the engine is never imported here.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from sqlalchemy import text

from app.config import settings
from app.models.match import Match
from app.models.replay import Replay, ReplayRound
from app.replays import choke_assets
from app.replays import control_format as cf
from app.services import control_heights, replay_control, replay_gaps
from app.services import replay_gaps_store as gaps_store
from app.services import replay_reparse_auto as reparse_auto
from app.services import replay_upload
from app.services.replay_control_store import ALREADY, STORED, store_round

log = logging.getLogger(__name__)

CYCLE_S = 20
IN_FLIGHT = 8
STALE_JOB_S = 1800
MAX_TRIES = 3
BACKOFF_S = 300
PLAN_IDLE_S = 120               # after a plan that found nothing to send, wait this long before planning again
LOCK_ID = 7_346_120_117          # pg_try_advisory_xact_lock's key for the dispatcher
TIMEOUT_S = 30
GAPS_PROTOCOL = 1               # the worker's control.gaps_protocol this deploy can talk to (its /health)


def enabled() -> bool:
    return bool(settings.replay_control_remote and settings.replay_worker_url and not settings.demo_mode)


class WorkerGone(Exception):
    """The worker doesn't know the job (it restarted) or has control off (404)."""


class WorkerBusy(Exception):
    """The worker's control queue is full (503)."""


class Unreachable(Exception):
    pass


class ControlClient:
    """The worker's control endpoints over urllib (the same private address as uploads)."""

    def __init__(self, base_url: str, timeout_s: float = TIMEOUT_S):
        self.base = (base_url if "://" in base_url else f"http://{base_url}").rstrip("/")
        self.timeout = timeout_s

    def submit(self, task: dict) -> dict:
        body = json.dumps(task).encode("utf-8")
        return self._call(urllib.request.Request(f"{self.base}/control", data=body, method="POST",
                                                 headers={"Content-Type": "application/json"}))

    def job(self, job_id: str) -> dict:
        return self._call(urllib.request.Request(f"{self.base}/control/{job_id}"))

    def health(self) -> dict:
        return self._call(urllib.request.Request(f"{self.base}/health"))

    def _call(self, request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise WorkerGone(str(error.code)) from error
            if error.code == 503:
                raise WorkerBusy(str(error.code)) from error
            raise Unreachable(f"worker answered {error.code}") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise Unreachable("the replay worker is unreachable") from error


@dataclass
class InFlight:
    job_id: str
    replay_id: int
    round_number: int
    fingerprint: str
    map_name: str
    sent_at: float
    running_since: float | None = None   # first seen running, without a `queued` since (D8)
    expect_gaps: bool = False            # sent with a gaps block: the capability read when it was submitted
    gaps_only: bool = False              # the round's control is current; only its gaps were asked for


@dataclass
class State:
    in_flight: dict[str, InFlight] = field(default_factory=dict)
    tries: dict[str, tuple[int, float]] = field(default_factory=dict)   # key -> (failures, last)
    last_planned: float | None = None   # when plan() last ran
    last_found: bool = True             # whether it left anything sendable unsent
    reparse: reparse_auto.Memo = field(default_factory=reparse_auto.Memo)   # the automatic queue's archive read
    held: frozenset = frozenset()       # the replays the last cycle held back for a re-parse

    def failed(self, key: str, now: float) -> None:
        count, _ = self.tries.get(key, (0, 0.0))
        self.tries[key] = (count + 1, now)

    def may_try(self, key: str, now: float) -> bool:
        count, last = self.tries.get(key, (0, 0.0))
        return count < MAX_TRIES and (count == 0 or now - last >= BACKOFF_S)


def task_key(replay_id: int, round_number: int, fingerprint: str) -> str:
    return f"{replay_id}:{round_number}:{fingerprint}"


def full_key(replay_id: int, round_number: int, fingerprint: str, gap_print: str) -> str:
    """A control task that carries a gaps block. The plain key comes first, so its fields keep their places;
    the suffix keeps the worker's dedupe from answering it with a plain task's result."""
    return f"{task_key(replay_id, round_number, fingerprint)}:g{GAPS_PROTOCOL}:{gap_print}"


def gaps_key(replay_id: int, round_number: int, fingerprint: str, gap_print: str) -> str:
    """A gaps-only task: another suffix again, so it is never answered with a control task's result."""
    return f"{task_key(replay_id, round_number, fingerprint)}:go{GAPS_PROTOCOL}:{gap_print}"


def supports_gaps(health) -> bool:
    """Whether the worker that gave this /health may be sent a gaps block: its control is on and it names
    exactly the protocol this deploy speaks. Anything missing, unknown or malformed does not qualify."""
    control = health.get("control") if isinstance(health, dict) else None
    if not isinstance(control, dict):
        return False
    protocol = control.get("gaps_protocol")
    return control.get("enabled") is True and type(protocol) is int and protocol == GAPS_PROTOCOL


def _gaps_capable(client) -> bool:
    try:
        return supports_gaps(client.health())
    except (WorkerGone, WorkerBusy, Unreachable):
        return False


def gaps_job(replay_id: int, round_number: int, fingerprint: str, map_name: str) -> dict:
    """A task's `gaps` block: app/control/task.py's three fields, plus the two keys the worker's interpreter
    cannot compute for itself."""
    return {"replay_id": replay_id, "round": round_number, "fingerprint": fingerprint,
            "gap_fingerprint": replay_gaps.gap_fingerprint(fingerprint, map_name),
            "engine_key": replay_gaps.engine_key(fingerprint, map_name)}


def _trusted_gaps(result: dict, f: InFlight) -> dict | None:
    """The result's gaps when they were computed under this deploy's gap rules and assets for this round's
    control, else None. A run that failed under those keys is the round's own gap failure and is trusted."""
    gaps = result.get("gaps")
    run = gaps.get("run") if isinstance(gaps, dict) else None
    if not isinstance(run, dict) or not isinstance(gaps.get("rows"), list) or run.get("status") not in ("ok", "failed"):
        return None
    if run.get("fingerprint") != replay_gaps.gap_fingerprint(f.fingerprint, f.map_name) \
            or run.get("gaps_revision") != replay_gaps.GAPS_REVISION \
            or run.get("chokes_hash") != choke_assets.asset_hash(f.map_name):
        return None
    return gaps


def _keep_gaps(session_factory, f: InFlight, result: dict, counts: dict) -> bool:
    """Stores a result's gaps through the guarded writer. False when they can't be trusted (another deploy's
    rules or assets, or malformed): dropped, and for a gaps-only job worth asking again."""
    gaps = _trusted_gaps(result, f)
    if gaps is None:
        counts["gaps_dropped"] += 1
        return False
    try:
        outcome = gaps_store.store_gaps(session_factory, f.replay_id, f.round_number, gaps["run"], gaps["rows"],
                                        expected_control_fingerprint=f.fingerprint)
    except Exception:  # noqa: BLE001 - rows the model refuses: the gaps are dropped, control stays stored
        log.exception("timing gaps of replay %s round %s could not be stored", f.replay_id, f.round_number)
        counts["gaps_dropped"] += 1
        return False
    counts["gaps_stored" if outcome == gaps_store.STORED else "gaps_skipped"] += 1     # skipped: stale, no failure
    return True


def _matches_geometry(result: dict, map_name: str, heights: dict) -> bool:
    mine = replay_control.geometry_inputs(map_name, heights)
    used = result.get("geometry") or {}
    return mine is not None and all(used.get(k) == mine.get(k)
                                    for k in ("sight", "walk", "barrier", "specials", "scale", "height", "features"))


def _collect(session_factory, session, client, state: State, now: float, counts: dict) -> None:
    heights = control_heights.active_digests(session)
    for key, f in list(state.in_flight.items()):
        try:
            job = client.job(f.job_id)
        except WorkerGone:
            del state.in_flight[key]
            counts["forgotten"] += 1
            continue
        except (Unreachable, WorkerBusy):
            counts["unreachable"] += 1
            continue
        status = job.get("status")
        if status == "queued":
            f.running_since = None       # D8: waiting behind parses is not a failure; only unbroken running counts
            continue
        if status == "running":
            if f.running_since is None:
                f.running_since = now
            elif now - f.running_since > STALE_JOB_S:
                del state.in_flight[key]
                state.failed(key, now)
                counts["timed_out"] += 1
            continue
        del state.in_flight[key]
        if status == "done":
            result = job.get("result") or {}
            if result.get("revision") != cf.CONTROL_REVISION or result.get("data_version") != cf.DATA_VERSION:
                state.failed(key, now)
                counts["dropped_revision"] += 1
                continue
            if not _matches_geometry(result, f.map_name, heights):
                state.failed(key, now)
                counts["dropped_geometry"] += 1
                continue
            # A capable worker names the game figures it read; they are in the fingerprint the row is stored
            # under, so a result computed with other figures is another deploy's.
            if (f.expect_gaps or "figures" in result) and result.get("figures") != cf.figures_hash():
                state.failed(key, now)
                counts["dropped_figures"] += 1
                continue
            if f.gaps_only:
                # No control row is read or written here: the writer checks the round's control inside its
                # own locked transaction, and a result it finds stale is skipped, not failed.
                if not _keep_gaps(session_factory, f, result, counts):
                    state.failed(key, now)
                continue
            row = {"status": "ok", "data": base64.b64decode(result["data"]),
                   "summary": base64.b64decode(result["summary"])}
        elif status == "failed" and job.get("error_kind") == "engine" and not f.gaps_only:
            # (A gaps-only task that fails as a whole says nothing about control, which is stored and ok:
            # it is asked again like a machine failure. The detector's own failure comes back inside an ok
            # result and is stored above.)
            row = {"status": "failed", "error": job.get("error") or "failed on the replay worker"}
        else:
            state.failed(key, now)
            counts["infra_failed"] += 1
            continue
        outcome = store_round(session_factory, f.replay_id, f.round_number, f.fingerprint, row, require_current=True)
        counts["stored" if outcome == STORED else "skipped"] += 1
        if row["status"] == "failed" and outcome == STORED:
            counts["stored_failed"] += 1
        if f.expect_gaps and row["status"] == "ok":
            if outcome in (STORED, ALREADY):     # control is there: the writer checks it again under the lock
                _keep_gaps(session_factory, f, result, counts)
            else:
                counts["gaps_skipped"] += 1


def _rank(todo: list, played: dict, created: dict) -> list:
    """D2: linked replays by their match's played_at, newest first; then the rest by upload time, newest
    first; rounds in order. Ties fall back to the replay id, newest first: rough order is enough."""
    def key(p):
        if p.replay_id in played:
            return (0, -played[p.replay_id].timestamp(), -p.replay_id, p.round_number)
        when = created.get(p.replay_id)
        return (1, -(when.timestamp() if when else 0.0), -p.replay_id, p.round_number)
    return sorted(todo, key=key)


def _order(session, todo: list) -> list:
    ids = {p.replay_id for p in todo}
    if not ids:
        return []
    played, created = {}, {}
    for rid, status, made, at in (session.query(Replay.id, Replay.link_status, Replay.created_at, Match.played_at)
                                  .outerjoin(Match, Match.id == Replay.match_id)
                                  .filter(Replay.id.in_(ids))):
        if status == "linked" and at is not None:
            played[rid] = at
        else:
            created[rid] = made
    return _rank(todo, played, created)


def _sendable(state: State, now: float, rounds: list, *, kind: str, capable: bool = False,
              held: frozenset = frozenset()) -> list:
    """Of the planned `rounds`, those that may be sent now, each with its worker key: not held back, no job
    for the round in flight (whatever its kind, so a change of capability never makes a second one), and
    tries left for that key. `kind` is "control" (with `capable` the task will carry a gaps block) or
    "gaps" (gaps alone, for a round whose control is current)."""
    flying = {(f.replay_id, f.round_number) for f in state.in_flight.values()}
    out = []
    for p in rounds:
        if p.replay_id in held or (p.replay_id, p.round_number) in flying:
            continue
        key = task_key(p.replay_id, p.round_number, p.fingerprint)
        if kind == "gaps":
            key = gaps_key(p.replay_id, p.round_number, p.fingerprint,
                           replay_gaps.gap_fingerprint(p.fingerprint, p.map_name))
        elif capable:
            key = full_key(p.replay_id, p.round_number, p.fingerprint,
                           replay_gaps.gap_fingerprint(p.fingerprint, p.map_name))
        if state.may_try(key, now):
            out.append((key, p))
    return out


def _send(session, client, state: State, now: float, counts: dict, todo: list, *, kind: str) -> bool:
    """Submits `todo` (from `_sendable`) in D2's order until the pool is full or the worker stops taking
    tasks. Returns whether sendable work was left."""
    keys = {(p.replay_id, p.round_number): key for key, p in todo}
    heights = control_heights.active_digests(session)
    for p in _order(session, [p for _, p in todo]):
        if len(state.in_flight) >= IN_FLIGHT:
            return True                      # sendable work is left: plan again next cycle
        key = keys[p.replay_id, p.round_number]
        gaps_only = kind == "gaps"
        expect_gaps = gaps_only or key != task_key(p.replay_id, p.round_number, p.fingerprint)
        row = session.get(ReplayRound, (p.replay_id, p.round_number))
        if row is None:
            continue
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]}}
        features = (replay_control.geometry_inputs(p.map_name, heights) or {}).get("features")
        if features:       # only a map with enabled features: an absent key means no verification
            task["features"] = features
        if expect_gaps:
            task["gaps"] = gaps_job(p.replay_id, p.round_number, p.fingerprint, p.map_name)
        if gaps_only:
            task["gaps_only"] = True
        try:
            answer = client.submit(task)
        except WorkerBusy:
            counts["busy"] += 1
            return True
        except (WorkerGone, Unreachable):
            counts["unreachable"] += 1
            return True
        state.in_flight[key] = InFlight(answer["id"], p.replay_id, p.round_number, p.fingerprint, p.map_name, now,
                                        expect_gaps=expect_gaps, gaps_only=gaps_only)
        counts["gaps_sent" if gaps_only else "sent"] += 1
        if not expect_gaps:
            counts["gaps_unavailable"] += 1
    return False


def _submit(session, client, state: State, now: float, counts: dict, held: frozenset = frozenset()) -> None:
    if len(state.in_flight) >= IN_FLIGHT:
        return
    if state.last_planned is not None and not state.last_found and now - state.last_planned < PLAN_IDLE_S:
        return
    # D1/D3: never computed, or out of date, linked or not. A failure under the current inputs stays put.
    planned = [p for p in replay_control.plan(session) if p.computable and p.reason in ("missing", "stale")]
    # Read afresh every pass and never remembered: the worker deploys on its own, in either direction.
    capable = _gaps_capable(client)
    state.last_planned = now
    state.last_found = _send(session, client, state, now, counts,
                             _sendable(state, now, planned, kind="control", capable=capable, held=held),
                             kind="control")
    if state.last_found or not capable:
        return          # control first; and without the capability gaps-only work waits, unplanned and uncounted
    # As the local command does: every round with its current fingerprint, then those whose control is ok and
    # current and whose gap run is missing or stale. A gap failure under the current keys stays put.
    wanted = replay_gaps.plan_gaps(session, planned, replay_control.plan(session, force=True))
    state.last_found = _send(session, client, state, now, counts,
                             _sendable(state, now, wanted, kind="gaps", held=held), kind="gaps")


def _reparse(session_factory, worker, state: State, now: float, counts: dict) -> frozenset:
    """One pass of the automatic re-parse queue; the replays to hold back. It runs in sessions of its own,
    and whatever goes wrong in it, map control goes on with nothing held; the attempts it left are in the
    database for the next cycle."""
    held: frozenset = frozenset()
    if worker is not None:
        try:
            held = reparse_auto.step(session_factory, worker, state.reparse, len(state.in_flight), now, counts)
        except Exception:  # noqa: BLE001 - never the dispatcher's failure
            log.exception("the automatic re-parse pass failed; map control goes on with nothing held back")
    if counts["reparse_finished"] or held != state.held:
        # A replacement just landed, or a replay was released: plan now, not after PLAN_IDLE_S.
        state.last_planned, state.last_found = None, True
    state.held = held
    counts["held_back"] = len(held)
    return held


def cycle(session_factory, client, state: State, now: float | None = None, worker=None) -> dict:
    """One pass: collect, the automatic re-parse queue's pass (with `worker`, the upload client), then
    submit. Returns what it did, by kind."""
    now = time.time() if now is None else now
    counts: dict[str, int] = {k: 0 for k in ("sent", "stored", "stored_failed", "skipped", "forgotten", "timed_out",
                                             "dropped_revision", "dropped_geometry", "dropped_figures", "infra_failed",
                                             "busy", "unreachable", "gaps_sent", "gaps_stored",
                                             "gaps_dropped", "gaps_skipped", "gaps_unavailable", "held_back",
                                             *reparse_auto.COUNTS)}
    lock = session_factory()   # owns the advisory lock and nothing else
    try:
        if lock.get_bind().dialect.name == "postgresql":
            if not lock.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_ID}).scalar():
                counts["locked_out"] = 1
                return counts
        collect = session_factory()   # reads the active heights the results are checked against
        try:
            _collect(session_factory, collect, client, state, now, counts)
        finally:
            collect.rollback()
            collect.close()
        held = _reparse(session_factory, worker, state, now, counts)
        session = session_factory()   # opened after the pass: it reads the replay a settlement just committed
        try:
            _submit(session, client, state, now, counts, held)
        finally:
            session.rollback()
            session.close()
        return counts
    finally:
        lock.rollback()
        lock.close()


def run_forever(session_factory, client, stop: threading.Event, every_s: float = CYCLE_S, worker=None) -> None:
    state = State()
    while not stop.wait(every_s):
        try:
            counts = cycle(session_factory, client, state, worker=worker)
        except Exception:  # noqa: BLE001 - the dispatcher must never take the site down
            log.exception("map control dispatch failed")
            continue
        did = {k: v for k, v in counts.items() if v}
        if did:
            log.info("map control dispatch: %s; %d in flight", ", ".join(f"{k} {v}" for k, v in sorted(did.items())),
                     len(state.in_flight))


def start(session_factory) -> threading.Event | None:
    """Starts the dispatcher thread when it is enabled; returns its stop event."""
    if not enabled():
        return None
    stop = threading.Event()
    # The upload client too, whatever REPLAY_REPARSE_AUTO says: an attempt already accepted is still collected.
    threading.Thread(target=run_forever, args=(session_factory, ControlClient(settings.replay_worker_url), stop),
                     kwargs={"worker": replay_upload.client()}, name="control-dispatch", daemon=True).start()
    log.info("map control dispatch on: every %ss, up to %d rounds in flight", CYCLE_S, IN_FLIGHT)
    return stop
