"""Height rebuilds on the replay worker, driven from the web app
(docs/superpowers/specs/2026-10-05-height-auto-rebuild-design.md, sections 3 and 4). Called once per cycle by
the map-control dispatcher (app/services/replay_control_remote.py `cycle`), under its lock.

- **Due** (`plan_maps`). A map's inputs are its manifest (app/replays/height_inputs.py): its valid replays at
  condenser revision MIN_CONDENSE_REVISION or later, its masks, the rule revisions, the check set. Against the
  map's last build, whatever that build's status:
  - no build yet: due with FIRST_BUILD_MATCHES matches;
  - the same inputs: not due (a rejected build is not built again until something changes);
  - evidence gone (a match deleted, or its blobs replaced), or the masks, rules or check set changed: due at
    once;
  - only matches added: due with HEIGHT_REBUILD_EVERY of them.
  A map left with fewer than FIRST_BUILD_MATCHES matches whose active heights were built from evidence that is
  gone has its heights turned off: a deletion means the data goes. A map with a published feature generation
  is never rebuilt automatically (its generation names the height digest and would stop verifying).
- **Held.** While a map's rebuild is due or running, its rounds are not sent for recomputing: new heights
  would make them out of date at once. Other maps' rounds keep flowing. The page keeps showing a held round's
  stored control, marked out of date.
- **Sent, resumably.** One build at a time, opened only while the worker reports idle. Every cycle starts by
  asking the worker what state the build is in (its job when the dispatcher knows one; else by opening the key,
  which answers the build there is) and acts on that: `collecting` sends rounds and then starts it; `queued`
  and `running` wait; `done` is collected; `failed` is a failed try. So a restart of the web app adopts the
  build, a lost response is found on the next cycle, and a worker that forgot the build is sent it again from
  the first round. A started build is never sent to.
- **Trusted, then gated** (`_finish`). A result must be this build's (key, map, input digest, round count),
  its bytes must be the asset it names (app/services/control_heights.py `verify_asset`, `integrity`), and the
  map's inputs must still be the ones it was built from; then `store_build` applies the gate: active, or
  rejected with the old heights kept. A rejected rebuild of a map whose active heights rest on evidence that is
  gone turns them off.
- **Deploy gap.** A worker that says it is another deploy's (its health names another recipe, or has no
  `heights` field: an image from before height builds) is waited for at no cost, holding nothing, so the flag
  can be on from the merge while the worker's image is still building.
- **Bounded.** Every failure either costs nothing (the worker is away, or parsing), or spends one of MAX_TRIES
  tries for that input manifest (the module's tests pin which). When they are spent, active heights whose
  evidence is gone are turned off; otherwise they stay. The map's rounds are released and no more tries are
  made until its inputs change. The budget is in memory: a restart of the web
  app starts it again.

Off unless REPLAY_HEIGHTS_AUTO is set (and the control dispatcher is on), and never in demo mode. Standard
library and the DB only; the asset is opened by a child process.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import load_only

from app.config import settings
from app.models.replay import Replay, ReplayRound
from app.replays import control_format as cf
from app.replays import db as replay_db
from app.replays import height_inputs as hi
from app.services import control_heights, replay_control

log = logging.getLogger(__name__)

HEIGHT_REBUILD_EVERY = 5
FIRST_BUILD_MATCHES = 2
BATCH_BYTES = 2_000_000          # base64 of rounds per request (the worker's request cap is 4 MB)
BATCHES_PER_CYCLE = 8
COLLECT_STALE_S = 1800           # a build whose rounds aren't all sent this long after it was opened
QUEUED_STALE_S = 900             # a build queued this long on a worker that says it is idle
STALE_BUILD_S = 7200             # a build seen running this long
RESENDS_MAX = 2                  # times a build may be sent again from the first round (a forgotten build, lost rounds)
MAX_TRIES = 3
BACKOFF_S = 300
COUNTS = ("heights_live", "heights_rejected", "heights_failed", "heights_stale", "heights_off", "heights_sent")


def enabled() -> bool:
    return bool(settings.replay_heights_auto and settings.replay_control_remote and settings.replay_worker_url
                and not settings.demo_mode)


class BuildFailed(Exception):
    """This try at the build is over, and it spends one of the manifest's tries."""


class _Stale(Exception):
    """The map's inputs are no longer the build's: drop the build, at no cost."""


@dataclass
class Due:
    map_name: str
    manifest: dict
    replays: list          # [(replay id, match uuid, round count)], in replay order
    why: str


@dataclass
class Plan:
    due: list = field(default_factory=list)
    off: list = field(default_factory=list)         # maps whose heights are to be turned off
    skipped: dict = field(default_factory=dict)     # map -> why it is never rebuilt automatically


@dataclass
class Build:
    map_name: str
    key: str
    manifest: dict
    rounds: list                          # every (replay id, match uuid, round number) of the build
    opened_at: float
    previous: str | None = None
    todo: list = field(default_factory=list)        # the rounds not sent yet to the worker's current build
    job_id: str | None = None
    resends: int = 0                      # 404s and missing-round resends share one bounded allowance
    queued_since: float | None = None
    running_since: float | None = None


@dataclass
class HeightState:
    build: Build | None = None
    tries: dict = field(default_factory=dict)       # manifest key -> (failures, last)

    def failed(self, key: str, now: float) -> None:
        count, _ = self.tries.get(key, (0, 0.0))
        self.tries[key] = (count + 1, now)

    def may_try(self, key: str, now: float) -> bool:
        count, last = self.tries.get(key, (0, 0.0))
        return count < MAX_TRIES and (count == 0 or now - last >= BACKOFF_S)

    def spent(self, key: str) -> bool:
        return self.tries.get(key, (0, 0.0))[0] >= MAX_TRIES


def current_manifests(db) -> dict[str, tuple[dict, list]]:
    """{map: (its input manifest now, [(replay id, match uuid, round count)])} for every map with geometry and at
    least one valid replay new enough to carry heights."""
    replays = db.query(Replay).options(load_only(
        Replay.id, Replay.match_uuid, Replay.map_name, Replay.recipe, Replay.source_sha256, Replay.round_count,
        Replay.format_version)).order_by(Replay.id).all()
    valid = replay_control._valid_ids(db, replays)
    by_map: dict[str, list] = {}
    for replay in replays:
        if replay.id in valid and (replay_control.condense_revision(replay.recipe) or 0) >= hi.MIN_CONDENSE_REVISION:
            by_map.setdefault(replay.map_name, []).append(replay)
    must_block = hi.must_block_sha()
    out = {}
    for name, rows in by_map.items():
        geometry = hi.geometry_identity(name)
        if geometry is None:
            continue
        uuids = [str(r.match_uuid).lower() for r in rows]
        out[name] = (hi.manifest(name, [[u, r.recipe, r.source_sha256, r.round_count] for u, r in zip(uuids, rows)],
                                 geometry, must_block),
                     [(r.id, u, r.round_count) for u, r in zip(uuids, rows)])
    return out


def plan_maps(db) -> Plan:
    """What is due, what is to be turned off, and what is left alone (the module docstring's "Due")."""
    plan = Plan()
    now = current_manifests(db)
    last, active = control_heights.last_builds(db), control_heights.active_rows(db)
    for name in sorted(set(now) | set(active)):
        if (replay_control.geometry_inputs(name, heights=None) or {}).get("features"):
            plan.skipped[name] = "it has a published feature generation, which a rebuild would stop verifying"
            continue
        manifest, replays = now.get(name, (None, []))
        gone = bool(manifest is None or (name in active and hi.changes(active[name].inputs, manifest)["gone"]))
        if len(replays) < FIRST_BUILD_MATCHES:
            if name in active and gone:
                plan.off.append(name)          # too little left to build from, and what is active rests on what is gone
            continue
        row = last.get(name)
        if row is None:
            plan.due.append(Due(name, manifest, replays, "first build"))
        elif row.inputs_sha == hi.digest(manifest):
            if row.status == control_heights.REJECTED and gone:
                plan.off.append(name)          # the rebuild after a deletion was rejected: the old heights go
        else:
            moved = hi.changes(row.inputs, manifest)
            if moved["gone"] or gone:
                plan.due.append(Due(name, manifest, replays, "its evidence changed"))
            elif moved["other"]:
                plan.due.append(Due(name, manifest, replays, "its inputs changed"))
            elif len(moved["added"]) >= HEIGHT_REBUILD_EVERY:
                plan.due.append(Due(name, manifest, replays, f"{len(moved['added'])} new matches"))
    plan.due.sort(key=lambda d: (d.why != "its evidence changed", d.map_name))
    return plan


def _batch(session, build: Build) -> list:
    """The next rounds to send, up to BATCH_BYTES of base64; they stay in `todo` until the worker has them."""
    out, size = [], 0
    for replay_id, uuid, n in build.todo:
        row = session.get(ReplayRound, (replay_id, n))
        if row is None:
            raise _Stale(f"round {n} of {uuid} is gone")
        blob = base64.b64encode(row.data).decode("ascii")
        if len(blob) > BATCH_BYTES:
            raise BuildFailed(f"round {n} of {uuid} is {len(blob)} bytes as base64: too large to send")
        if out and size + len(blob) > BATCH_BYTES:
            break
        out.append({"match": uuid, "n": n, "blob": blob})
        size += len(blob)
    return out


def _turn_off(session_factory, map_name: str, *, removed_from: dict | None = None) -> bool:
    """Leaves the map without active heights, through a session of its own. The cycle's session holds the
    dispatcher's transaction-scoped lock, and `deactivate` commits: on that session it would let a second
    dispatcher in for the rest of the cycle."""
    writer = session_factory()
    try:
        if removed_from is not None:
            # Recheck the active row under the map lock: an operator may have replaced the
            # obsolete asset while the failed build was running. Keep that replacement.
            replay_db.advisory_lock(writer, control_heights.lock_name(map_name))
            active = control_heights.active_rows(writer).get(map_name)
            if active is None or not hi.changes(active.inputs, removed_from)["gone"]:
                return False
        return control_heights.deactivate(
            writer, map_name, generation=(replay_control.geometry_inputs(map_name, heights=None) or {}).get("features"))
    except control_heights.HasGeneration as guarded:
        log.info("heights: %s", guarded)
        return False
    finally:
        writer.rollback()
        writer.close()


def _retire_spent_evidence(session_factory, due: Due, counts: dict) -> None:
    if _turn_off(session_factory, due.map_name, removed_from=due.manifest):
        counts["heights_off"] += 1
        log.info("heights: %s off: rebuild retries exhausted and its evidence is gone", due.map_name)


def _cancel(client, build: Build) -> None:
    """Best effort: a build still collecting on the worker is dropped there."""
    from app.services.replay_control_remote import Conflict, Rejected, Unreachable, WorkerBusy, WorkerGone

    if build.job_id:
        try:
            client.cancel_build(build.job_id)
        except (Conflict, Rejected, Unreachable, WorkerBusy, WorkerGone):
            pass


def _finish(session_factory, session, build: Build, job: dict, counts: dict) -> None:
    """A finished build: trusted, still wanted, then stored behind the gate."""
    if (replay_control.geometry_inputs(build.map_name, heights=None) or {}).get("features"):
        raise _Stale("a feature generation was published while this build was pending")
    result = job.get("result")
    try:
        if not isinstance(result, dict):
            raise ValueError("it has no result")
        if result.get("key") != build.key or result.get("map") != build.map_name:
            raise ValueError("it is another build's")
        if result.get("inputs_sha") != hi.digest(build.manifest):
            raise ValueError("it was built from other inputs")
        if result.get("rounds") != hi.rounds_expected(build.manifest):
            raise ValueError(f"it read {result.get('rounds')} rounds, the manifest has {hi.rounds_expected(build.manifest)}")
        digest, report, rules = result["digest"], result["report"], result["rules"]
        if not isinstance(digest, str) or len(digest) != 12 or not isinstance(rules, dict):
            raise ValueError("its digest or its rules are malformed")
        if (rules.get("version"), rules.get("revision")) != (cf.HEIGHT_VERSION, cf.HEIGHT_RULES_REVISION):
            # `rules` is what the row records and what `active_digests` reads to tell a loadable asset: a row
            # stored with another deploy's would be active, never used, and never due again.
            raise ValueError(f"it was built under height version {rules.get('version')}, rules "
                             f"{rules.get('revision')}: not this deploy's")
        asset = base64.b64decode(result["asset"], validate=True)
    except (KeyError, TypeError, ValueError) as error:              # binascii.Error is a ValueError
        raise BuildFailed(f"the worker's result can't be used: {error}") from error
    why = control_heights.integrity(digest, report, control_heights.verify_asset(asset, build.map_name), hi.must_block_sha())
    if why:
        raise BuildFailed("the worker's result can't be trusted: " + "; ".join(why))
    current = current_manifests(session).get(build.map_name)
    if current is None or hi.digest(current[0]) != hi.digest(build.manifest):
        raise _Stale("the map's inputs changed while it was built")
    writer = session_factory()
    try:
        status, reasons = control_heights.store_build(
            writer, map_name=build.map_name, digest=digest, asset=asset, inputs=build.manifest, rules=rules,
            report={**report, "seconds": result.get("seconds"), "peak": result.get("peak")})
        if status == control_heights.REJECTED:
            active = control_heights.active_rows(writer).get(build.map_name)
            if active is not None and hi.changes(active.inputs, build.manifest)["gone"]:
                control_heights.deactivate(writer, build.map_name, generation=(
                    replay_control.geometry_inputs(build.map_name, heights=None) or {}).get("features"))
                counts["heights_off"] += 1
    except control_heights.HasGeneration as guarded:
        raise _Stale(str(guarded)) from guarded
    except control_heights.Busy as busy:
        raise BuildFailed("another change to the map's heights got there first") from busy
    finally:
        writer.rollback()
        writer.close()
    counts["heights_live" if status == control_heights.ACTIVE else "heights_rejected"] += 1
    log.info("heights: %s %s %s%s", build.map_name, digest, status, f" ({'; '.join(reasons)})" if reasons else "")


def _other_deploy(answer) -> bool:
    """The worker answered as a well-formed worker of another deploy: another recipe, or no height builds."""
    from app.services import replay_upload

    return isinstance(answer, dict) and "recipe" in answer and (
        answer["recipe"] != replay_upload.site_recipe() or not isinstance(answer.get("heights"), dict))


def _idle(client, answer=None) -> bool:
    answer = client.health() if answer is None else answer
    if not isinstance(answer, dict) or type(answer.get("idle")) is not bool:
        raise BuildFailed("the worker's health has no boolean idle field")
    return answer["idle"]


def _resend(build: Build, why: str) -> None:
    build.resends += 1
    if build.resends > RESENDS_MAX:
        raise BuildFailed(why)


def _advance(session_factory, session, client, build: Build, now: float, counts: dict) -> bool:
    """Asks the worker what state the build is in and does the one thing that state allows. True when the build
    is over (its result stored)."""
    from app.services.replay_control_remote import Conflict, Rejected, WorkerGone

    try:
        if build.job_id is None:
            # Opening a key answers the build the worker has for it, in whatever state (a restart of the web app
            # adopts it); only when there is none, or it failed, is this a fresh one to send rounds to.
            from app.services import control_feature_artifacts
            options = {}
            health = client.health()
            if (health.get('heights') or {}).get('diagnostics_protocol') == 1:
                options['diagnostic'] = control_feature_artifacts.diagnostic_snapshot(session, build.map_name)
            state = client.open_build(build.key, build.map_name, len(build.rounds), build.manifest, **options)
            build.job_id = state.get("id")
            build.queued_since = build.running_since = None
            if state.get("status") == "collecting":
                build.todo = list(build.rounds)       # immutable uploads: what it already has is acknowledged for free
            else:
                build.todo = []
        else:
            state = client.build(build.job_id)        # 404: the worker forgot it (WorkerGone, bounded here before `step` spends a try)
        status = state.get("status")
        if status == "collecting":
            if now - build.opened_at > COLLECT_STALE_S:
                raise BuildFailed("its rounds could not all be sent in time")
            if not _idle(client):
                return False                          # a parse is running: sending waits, like everything else
            for _ in range(BATCHES_PER_CYCLE):
                if not build.todo:
                    break
                batch = _batch(session, build)
                try:
                    client.send_rounds(build.job_id, batch)
                except Conflict as conflict:
                    if conflict.body.get("error") == "not collecting":
                        return False                  # it was started (a start whose answer was lost): ask again
                    raise BuildFailed(f"the worker refused a round: {conflict.body.get('error')}") from conflict
                build.todo = build.todo[len(batch):]
                counts["heights_sent"] += len(batch)
            if build.todo:
                return False
            if build.previous:
                asset = control_heights.asset_bytes(session, build.map_name, build.previous)
                if asset is not None:
                    client.push_height(build.map_name, build.previous, asset)
            try:
                client.start_build(build.job_id, build.previous)
            except Conflict as conflict:
                if conflict.body.get("error") != "rounds are missing":
                    raise BuildFailed(f"the worker refused to start it: {conflict.body.get('error')}") from conflict
                _resend(build, "the worker keeps losing rounds")
                build.todo = list(build.rounds)       # immutable uploads: what it has is acknowledged for free
            return False
        if status == "queued":
            build.running_since = None
            if _idle(client):
                build.queued_since = now if build.queued_since is None else build.queued_since
                if now - build.queued_since > QUEUED_STALE_S:
                    raise BuildFailed("it sat queued on an idle worker and never started")
            else:
                build.queued_since = None             # waiting behind a parse is never a failure
            return False
        if status == "running":
            build.queued_since = None
            build.running_since = now if build.running_since is None else build.running_since
            if now - build.running_since > STALE_BUILD_S:
                raise BuildFailed("it ran too long")
            return False
        if status == "done":
            _finish(session_factory, session, build, state if "result" in state else client.build(build.job_id), counts)
            return True
        raise BuildFailed(str(state.get("error") or f"the worker says the build is {status!r}"))
    except WorkerGone:
        # Includes 404 while opening, before there is a job id. Reopening after this 404 is already charged;
        # a successful collecting response must not charge a second time for the same lost build.
        _resend(build, "the worker keeps forgetting or refusing the height-build endpoint")
        build.job_id = None
        build.queued_since = build.running_since = None
        return False
    except Rejected as refused:
        raise BuildFailed(f"the worker refused the request ({refused})") from refused


def step(session_factory, session, client, hstate: HeightState, now: float, counts: dict) -> set[str]:
    """One cycle's work on height rebuilds. Returns the maps whose rounds are held."""
    from app.services.replay_control_remote import Conflict, Unreachable, WorkerBusy

    for key in COUNTS:
        counts.setdefault(key, 0)
    plan = plan_maps(session)
    for name in plan.off:
        if _turn_off(session_factory, name):          # never on `session`: it must not commit (the cycle's lock)
            counts["heights_off"] += 1
            log.info("heights: %s off: what its heights were built from is gone", name)
    due = {hi.key(d.manifest): d for d in plan.due}
    for key, d in due.items():
        if hstate.spent(key):
            # Also retry a retirement that failed to commit on the last failed build's cycle.
            _retire_spent_evidence(session_factory, d, counts)
    held = {d.map_name for key, d in due.items() if not hstate.spent(key)}
    build = hstate.build
    if build is not None and build.key not in due:
        _cancel(client, build)                        # its inputs are no longer the map's
        hstate.build = build = None
        counts["heights_stale"] += 1
    attempt_key = build.key if build is not None else None
    try:
        if build is None:
            ready = [d for key, d in due.items() if hstate.may_try(key, now)]
            if not ready:
                return held
            d = ready[0]
            attempt_key = hi.key(d.manifest)  # health validation can fail before a Build is created
            answer = client.health()
            if _other_deploy(answer):
                return set()                          # the deploy gap: wait for the worker, hold nothing, spend nothing
            if not _idle(client, answer):
                return held
            rounds = [(rid, uuid, n) for rid, uuid, count in d.replays for n in range(1, count + 1)]
            build = hstate.build = Build(d.map_name, hi.key(d.manifest), d.manifest, rounds, opened_at=now,
                                         previous=control_heights.active_digests(session).get(d.map_name))
            log.info("heights: %s rebuild (%s), %d rounds", d.map_name, d.why, len(rounds))
        held.add(build.map_name)
        if _advance(session_factory, session, client, build, now, counts):
            hstate.build = None
            held.discard(build.map_name)
    except (Unreachable, WorkerBusy):
        counts["unreachable"] = counts.get("unreachable", 0) + 1
    except _Stale as stale:
        log.info("heights: %s build dropped: %s", build.map_name, stale)
        _cancel(client, build)
        hstate.build = None
        counts["heights_stale"] += 1
    except (BuildFailed, Conflict, AttributeError, KeyError, TypeError, ValueError) as error:
        # The last four: a worker's answer that isn't shaped as the protocol says (`state.get` on a list, a
        # missing field). Handled here, where the build can be dropped; left to `cycle`'s catch-all the build
        # would stay and meet the same answer every cycle without spending a try. A database error is none of
        # these and still reaches `cycle`, which rolls back and takes its lock again.
        log.warning("heights: %s build failed: %s: %s", build.map_name if build else "?", type(error).__name__, error)
        if build is not None:
            _cancel(client, build)
            hstate.build = None
        if attempt_key is not None:
            hstate.failed(attempt_key, now)
            if hstate.spent(attempt_key):
                _retire_spent_evidence(session_factory, due[attempt_key], counts)
                held.discard(due[attempt_key].map_name)
        counts["heights_failed"] += 1
    return held
