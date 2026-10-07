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
from app.replays import choke_assets
from app.replays import control_format as cf
from app.services import replay_control, replay_gaps
from app.services import replay_gaps_store as gaps_store
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


def full_key(replay_id: int, round_number: int, fingerprint: str, gap_print: str) -> str:
    """A control task that carries a gaps block. The plain key comes first, so its fields keep their places;
    the suffix keeps the worker's dedupe from answering it with a plain task's result."""
    return f"{task_key(replay_id, round_number, fingerprint)}:g{GAPS_PROTOCOL}:{gap_print}"


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


def _keep_gaps(session_factory, f: InFlight, result: dict, counts: dict) -> None:
    gaps = _trusted_gaps(result, f)
    if gaps is None:
        counts["gaps_dropped"] += 1
        return
    try:
        outcome = gaps_store.store_gaps(session_factory, f.replay_id, f.round_number, gaps["run"], gaps["rows"],
                                        expected_control_fingerprint=f.fingerprint)
    except Exception:  # noqa: BLE001 - rows the model refuses: the gaps are dropped, control stays stored
        log.exception("timing gaps of replay %s round %s could not be stored", f.replay_id, f.round_number)
        counts["gaps_dropped"] += 1
        return
    counts["gaps_stored" if outcome == gaps_store.STORED else "gaps_skipped"] += 1


def _matches_geometry(result: dict, map_name: str) -> bool:
    mine = replay_control.geometry_inputs(map_name)
    used = result.get("geometry") or {}
    return mine is not None and all(used.get(k) == mine.get(k)
                                    for k in ("sight", "walk", "barrier", "specials", "scale", "height", "features"))


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
            # A capable worker names the game figures it read; they are in the fingerprint the row is stored
            # under, so a result computed with other figures is another deploy's.
            if (f.expect_gaps or "figures" in result) and result.get("figures") != cf.figures_hash():
                state.failed(key, now)
                counts["dropped_figures"] += 1
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
    tries left for that key. `kind` is "control"; with `capable` the task will carry a gaps block."""
    flying = {(f.replay_id, f.round_number) for f in state.in_flight.values()}
    out = []
    for p in rounds:
        if p.replay_id in held or (p.replay_id, p.round_number) in flying:
            continue
        key = task_key(p.replay_id, p.round_number, p.fingerprint)
        if capable:
            key = full_key(p.replay_id, p.round_number, p.fingerprint,
                           replay_gaps.gap_fingerprint(p.fingerprint, p.map_name))
        if state.may_try(key, now):
            out.append((key, p))
    return out


def _send(session, client, state: State, now: float, counts: dict, todo: list, *, kind: str) -> bool:
    """Submits `todo` (from `_sendable`) in D2's order until the pool is full or the worker stops taking
    tasks. Returns whether sendable work was left."""
    keys = {(p.replay_id, p.round_number): key for key, p in todo}
    for p in _order(session, [p for _, p in todo]):
        if len(state.in_flight) >= IN_FLIGHT:
            return True                      # sendable work is left: plan again next cycle
        key = keys[p.replay_id, p.round_number]
        expect_gaps = key != task_key(p.replay_id, p.round_number, p.fingerprint)
        row = session.get(ReplayRound, (p.replay_id, p.round_number))
        if row is None:
            continue
        task = {"key": key, "map": p.map_name, "blob": base64.b64encode(row.data).decode("ascii"),
                "link": {"sides": p.link["sides"], "db_deaths": p.link["db_deaths"]}}
        features = (replay_control.geometry_inputs(p.map_name) or {}).get("features")
        if features:       # only a map with enabled features: an absent key means no verification
            task["features"] = features
        if expect_gaps:
            task["gaps"] = gaps_job(p.replay_id, p.round_number, p.fingerprint, p.map_name)
        try:
            answer = client.submit(task)
        except WorkerBusy:
            counts["busy"] += 1
            return True
        except (WorkerGone, Unreachable):
            counts["unreachable"] += 1
            return True
        state.in_flight[key] = InFlight(answer["id"], p.replay_id, p.round_number, p.fingerprint, p.map_name, now,
                                        expect_gaps=expect_gaps)
        counts["sent"] += 1
        if not expect_gaps:
            counts["gaps_unavailable"] += 1
    return False


def _submit(session, client, state: State, now: float, counts: dict) -> None:
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
                             _sendable(state, now, planned, kind="control", capable=capable), kind="control")


def cycle(session_factory, client, state: State, now: float | None = None) -> dict:
    """One pass: collect, then submit. Returns what it did, by kind."""
    now = time.time() if now is None else now
    counts: dict[str, int] = {k: 0 for k in ("sent", "stored", "stored_failed", "skipped", "forgotten", "timed_out",
                                             "dropped_revision", "dropped_geometry", "dropped_figures", "infra_failed",
                                             "busy", "unreachable", "gaps_stored", "gaps_dropped",
                                             "gaps_skipped", "gaps_unavailable")}
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
