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

`plan()` runs when there is room in flight; after a plan that left nothing sendable it waits
PLAN_IDLE_S. On PostgreSQL it holds `pg_try_advisory_xact_lock` for the cycle, so one
instance dispatches when Render overlaps two during a deploy; the worker's key dedupe makes a
duplicate harmless anyway. In-flight jobs live in memory: a restart just asks again.

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
from app.replays import control_format as cf
from app.services import replay_control
from app.services.replay_control_store import STORED, store_round

log = logging.getLogger(__name__)

CYCLE_S = 20
IN_FLIGHT = 8
STALE_JOB_S = 1800
MAX_TRIES = 3
BACKOFF_S = 300
PLAN_IDLE_S = 120               # after a plan that found nothing to send, wait this long before planning again
LOCK_ID = 7_346_120_117          # pg_try_advisory_xact_lock's key for the dispatcher
TIMEOUT_S = 30


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


@dataclass
class State:
    in_flight: dict[str, InFlight] = field(default_factory=dict)
    tries: dict[str, tuple[int, float]] = field(default_factory=dict)   # key -> (failures, last)
    last_planned: float | None = None   # when plan() last ran
    last_found: bool = True             # whether it left anything sendable unsent

    def failed(self, key: str, now: float) -> None:
        count, _ = self.tries.get(key, (0, 0.0))
        self.tries[key] = (count + 1, now)

    def may_try(self, key: str, now: float) -> bool:
        count, last = self.tries.get(key, (0, 0.0))
        return count < MAX_TRIES and (count == 0 or now - last >= BACKOFF_S)


def task_key(replay_id: int, round_number: int, fingerprint: str) -> str:
    return f"{replay_id}:{round_number}:{fingerprint}"


def _matches_geometry(result: dict, map_name: str) -> bool:
    mine = replay_control.geometry_inputs(map_name)
    used = result.get("geometry") or {}
    return mine is not None and all(used.get(k) == mine.get(k)
                                    for k in ("sight", "walk", "barrier", "specials", "scale", "height"))


def _collect(session_factory, client, state: State, now: float, counts: dict) -> None:
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
            if not _matches_geometry(result, f.map_name):
                state.failed(key, now)
                counts["dropped_geometry"] += 1
                continue
            row = {"status": "ok", "data": base64.b64decode(result["data"]),
                   "summary": base64.b64decode(result["summary"])}
        elif status == "failed" and job.get("error_kind") == "engine":
            row = {"status": "failed", "error": job.get("error") or "failed on the replay worker"}
        else:
            state.failed(key, now)
            counts["infra_failed"] += 1
            continue
        outcome = store_round(session_factory, f.replay_id, f.round_number, f.fingerprint, row, require_current=True)
        counts["stored" if outcome == STORED else "skipped"] += 1
        if row["status"] == "failed" and outcome == STORED:
            counts["stored_failed"] += 1


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


def _submit(session, client, state: State, now: float, counts: dict) -> None:
    if len(state.in_flight) >= IN_FLIGHT:
        return
    if state.last_planned is not None and not state.last_found and now - state.last_planned < PLAN_IDLE_S:
        return
    # D1/D3: never computed, or out of date, linked or not. A failure under the current inputs stays put.
    todo = [p for p in replay_control.plan(session) if p.computable and p.reason in ("missing", "stale")]
    todo = [p for p in todo if task_key(p.replay_id, p.round_number, p.fingerprint) not in state.in_flight
            and state.may_try(task_key(p.replay_id, p.round_number, p.fingerprint), now)]
    state.last_planned, state.last_found = now, False
    for p in _order(session, todo):
        if len(state.in_flight) >= IN_FLIGHT:
            state.last_found = True          # sendable work is left: plan again next cycle
            break
        key = task_key(p.replay_id, p.round_number, p.fingerprint)
        row = session.get(ReplayRound, (p.replay_id, p.round_number))
        if row is None:
            continue
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]}}
        try:
            answer = client.submit(task)
        except WorkerBusy:
            counts["busy"] += 1
            state.last_found = True
            break
        except (WorkerGone, Unreachable):
            counts["unreachable"] += 1
            state.last_found = True
            break
        state.in_flight[key] = InFlight(answer["id"], p.replay_id, p.round_number, p.fingerprint, p.map_name, now)
        counts["sent"] += 1


def cycle(session_factory, client, state: State, now: float | None = None) -> dict:
    """One pass: collect, then submit. Returns what it did, by kind."""
    now = time.time() if now is None else now
    counts: dict[str, int] = {k: 0 for k in ("sent", "stored", "stored_failed", "skipped", "forgotten", "timed_out",
                                             "dropped_revision", "dropped_geometry", "infra_failed", "busy",
                                             "unreachable")}
    session = session_factory()
    try:
        if session.get_bind().dialect.name == "postgresql":
            if not session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_ID}).scalar():
                counts["locked_out"] = 1
                return counts
        _collect(session_factory, client, state, now, counts)
        _submit(session, client, state, now, counts)
        return counts
    finally:
        session.rollback()
        session.close()


def run_forever(session_factory, client, stop: threading.Event, every_s: float = CYCLE_S) -> None:
    state = State()
    while not stop.wait(every_s):
        try:
            counts = cycle(session_factory, client, state)
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
    threading.Thread(target=run_forever, args=(session_factory, ControlClient(settings.replay_worker_url), stop),
                     name="control-dispatch", daemon=True).start()
    log.info("map control dispatch on: every %ss, up to %d rounds in flight", CYCLE_S, IN_FLIGHT)
    return stop
