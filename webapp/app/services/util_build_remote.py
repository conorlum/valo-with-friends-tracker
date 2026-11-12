"""Prepare and push frozen Critical Util inputs, without building on the site.

The loop's cheap signature gates the full freshness/key check. A preparation keeps
metadata and encoded context, and reads only worker-missing blobs in bounded chunks.
One UtilState admits one build, followed and collected outside the control lock.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from sqlalchemy import case, func, text, tuple_

from app.config import settings
from app.critutil import build_digest, corpus
from app.models.friendship import Friendship
from app.models.kill_event import KillEvent
from app.models.match import Match
from app.models.replay import Replay, ReplayRoundUtil, UtilModel
from app.models.round import Round
from app.services import replay_util, util_build_health, util_model, util_values


log = logging.getLogger(__name__)
WEBAPP = Path(__file__).resolve().parents[2]
OBS_VERSION = hashlib.sha256(b"".join(
    (WEBAPP / path).read_bytes().replace(b"\r\n", b"\n") for path in (
        "app/services/util_model.py", "app/services/state_replay.py", "app/scoring/plant_window.py"
    ))).hexdigest()[:8]
BATCH_BYTES = 2_000_000
BATCHES_PER_CYCLE = 8
ROW_CHUNK = 200
# Measured 2026-11-12 (friends DB): all 3,909 matches' observations load in 19.6 s (~5 ms a match in one batch;
# a lone match is 0.16 s, mostly three round trips), so 200 a cycle is ~1-1.3 s, under the 5 s budget.
OBS_MATCHES_PER_CYCLE = 200
FULL_CHECK_S = 600
QUIET_S = 120
DIGEST_GRACE_S = 1800
MAX_TRIES = 3
BACKOFF_S = 300
STALE_BUILD_S = 3600  # seen running: > 2 x the worker's 1500 s task limit


class Signature(NamedTuple):
    util: tuple
    last_computed: object
    replay_matches: int
    friends: tuple
    offsets: tuple
    models: tuple


@dataclass
class UtilState:
    phase: str = "idle"
    key: str | None = None
    build_id: str | None = None
    ref: str | None = None
    tries: dict[str, int] = field(default_factory=dict)
    backoff: dict[str, float] = field(default_factory=dict)
    asked: bool = False
    digest_mismatch_since: float | None = None
    digest_reported: bool = False
    deploy_mismatch: bool = False
    last_signature: Signature | None = None
    last_full_check: float | None = None
    stale: bool = False
    checked_rows: tuple = ()
    values_key_now: str | None = None
    win_key: str | None = None
    refit: bool = False
    excluded: frozenset = frozenset()
    prepared_signature: Signature | None = None
    rows: tuple = ()
    row_keys: dict = field(default_factory=dict)
    obs_keys: dict = field(default_factory=dict)
    context: bytes = b""
    digest: str | None = None
    missing_rows: list = field(default_factory=list)
    missing_obs: list = field(default_factory=list)
    have_pending: bool = False
    resends: int = 0
    gave_up: set = field(default_factory=set)
    opened_at: float | None = None
    running_since: float | None = None
    last_status: str | None = None


STATE = UtilState()


def enabled() -> bool:
    return bool(getattr(settings, "util_build_remote", False) and not settings.demo_mode)


def in_flight(state: UtilState | None = None) -> bool:
    return (STATE if state is None else state).phase != "idle"


def obs_key(match_id, count, max_kill_id) -> str:
    return f"{match_id}-{count}-{max_kill_id}-{OBS_VERSION}"


def request_now(state: UtilState | None = None) -> None:
    state = STATE if state is None else state
    if enabled():
        state.asked = True
        state.last_signature = None


def _signature(db) -> Signature:
    ok = ReplayRoundUtil.status == "ok"
    count, latest_ok, latest = db.query(
        func.count(case((ok, 1))), func.max(case((ok, ReplayRoundUtil.computed_at))),
        func.max(ReplayRoundUtil.computed_at)).one()
    replay_count = db.query(func.count(func.distinct(Replay.match_id))).scalar()
    friends = tuple(db.query(func.count(Friendship.id), func.max(Friendship.id)).one())
    offsets = tuple(db.query(func.count(Replay.clock_offset), func.sum(Replay.clock_offset)).one())
    models = tuple(tuple(r) for r in db.query(UtilModel.name, UtilModel.key, UtilModel.version)
                   .filter(UtilModel.name.in_(("win", "values"))).order_by(UtilModel.name))
    return Signature((count, latest_ok), latest, replay_count, friends, offsets, models)


def _epoch(at) -> float:
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def due(db, state: UtilState, now: float) -> bool:
    """One aggregate signature per pass; scan metadata only on change or after ten minutes.

    The estimator's excluded JSON field is projected separately during full checks.
    No values data or fitted estimator cells are read to decide whether work is due.
    """
    if not enabled() or state.phase != "idle":
        return False
    signature = _signature(db)
    if (signature != state.last_signature or state.last_full_check is None
            or now - state.last_full_check >= FULL_CHECK_S):
        products = {name: (key, version) for name, key, version in signature.models}
        win = products.get("win")
        state.win_key = win[0] if win and win[1] == util_model.VERSION else None
        # Only the exclusion list, never the stored build, is needed for covers().
        excluded = (db.query(UtilModel.data["excluded"]).filter(UtilModel.name == "win").scalar()
                    if state.win_key is not None else None)
        state.excluded = frozenset(util_model.replay_match_ids(db))
        state.refit = not util_model.covers(
            {"excluded": excluded} if state.win_key is not None else None, state.excluded)
        state.checked_rows = tuple(replay_util.current_rows(db, with_data=False))
        state.values_key_now = replay_util.corpus_key(
            db, state.checked_rows, {"key": state.win_key} if state.win_key is not None else None)
        values = products.get("values")
        state.stale = state.refit or values != (state.values_key_now, util_values.VERSION)
        state.last_signature = signature
        state.last_full_check = now
    quiet = signature.last_computed is None or now - _epoch(signature.last_computed) >= QUIET_S
    return state.stale and (quiet or state.asked)


def note_digest_mismatch(state: UtilState, now: float, error="worker digest differs") -> None:
    """Also used by a task-side deploy refusal; it spends no build try."""
    if state.digest_mismatch_since is None:
        state.digest_mismatch_since = now
    if now - state.digest_mismatch_since > DIGEST_GRACE_S and not state.digest_reported:
        util_build_health.record("digest", error, {"key": state.key, "ref": state.ref})
        state.digest_reported = True


class _Changed(Exception):
    """Prepared inputs moved; decide again next cycle, with a new dispatch key."""


def _abandon(state: UtilState) -> None:
    state.phase = "idle"
    state.build_id = None
    state.rows = ()
    state.row_keys.clear()
    state.obs_keys.clear()
    state.context = b""
    state.missing_rows.clear()
    state.missing_obs.clear()
    state.have_pending = False
    state.prepared_signature = None
    state.last_signature = None
    state.opened_at = state.running_since = state.last_status = None
    util_build_health.set_in_flight(False)


def _observation_keys(db, excluded, match_ids=None) -> dict:
    # One aggregate includes empty matches too, so their empty shard is cached.
    rows = (db.query(Match.id, func.count(KillEvent.id), func.coalesce(func.max(KillEvent.id), 0))
            .outerjoin(Round, Round.match_id == Match.id).outerjoin(KillEvent, KillEvent.round_id == Round.id)
            .filter(Match.id.notin_(excluded)).group_by(Match.id).order_by(Match.id))
    if match_ids is not None:
        rows = rows.filter(Match.id.in_(match_ids))
    return {obs_key(mid, count, maximum): mid for mid, count, maximum in rows}


def _prepare(db, state: UtilState, digest: str, now: float) -> bool:
    observations = _observation_keys(db, state.excluded) if state.refit else {}
    key = corpus.dispatch_key(state.values_key_now, state.refit, state.excluded, observations, digest)
    if state.tries.get(key, 0) >= MAX_TRIES:
        if key not in state.gave_up:
            util_build_health.record("gave_up", "three build tries exhausted", {"key": key, "ref": state.ref})
            state.gave_up.add(key)
        return False
    if now < state.backoff.get(key, 0):
        return False
    state.ref, state.key = uuid4().hex, key
    state.rows = state.checked_rows
    state.row_keys = {corpus.row_key(*row): row for row in state.rows}
    state.obs_keys = observations
    state.digest = digest
    state.prepared_signature = state.last_signature
    parts = replay_util.corpus_key_parts(db, state.rows)
    model = None if state.refit else util_model.load(db)
    if not state.refit and (model or {}).get("key") != state.win_key:
        raise _Changed("estimator changed while preparing")
    ctx = replay_util._replay_context(db, {r[0] for r in state.rows})
    friends = replay_util.friend_players(db)
    # Bytes detach every nested context value from the session and future DB edits.
    state.context = corpus.encode_context(ctx, friends, model, state.rows, state.excluded, state.refit, parts)
    state.have_pending = True
    state.resends = 0
    state.build_id = None
    state.phase = "pushing"
    state.asked = False
    state.tries[key] = state.tries.get(key, 0) + 1
    util_build_health.set_in_flight(True)
    log.info("util decision key=%s ref=%s refit=%s rows=%s obs=%s", key, state.ref,
             state.refit, len(state.rows), len(observations))
    return True


def _missing(state: UtilState, answer: dict) -> None:
    rows, obs = set(answer.get("missing_rows", [])), set(answer.get("missing_obs", []))
    if not rows <= state.row_keys.keys() or not obs <= state.obs_keys.keys():
        raise ValueError("worker requested inputs outside the frozen preparation")
    state.missing_rows = [key for key in state.row_keys if key in rows]
    state.missing_obs = [key for key in state.obs_keys if key in obs]


def _round_entries(db, state: UtilState, keys) -> list:
    pairs = [state.row_keys[key][:2] for key in keys]
    rows = db.query(ReplayRoundUtil.replay_id, ReplayRoundUtil.round_number, ReplayRoundUtil.fingerprint,
                    ReplayRoundUtil.computed_at, ReplayRoundUtil.data).filter(
        tuple_(ReplayRoundUtil.replay_id, ReplayRoundUtil.round_number).in_(pairs), ReplayRoundUtil.status == "ok")
    found = {(r[0], r[1]): tuple(r) for r in rows}
    entries = []
    for key in keys:
        expected = state.row_keys[key]
        row = found.get(expected[:2])
        if row is None or row[:4] != expected or row[4] is None:
            raise _Changed("util row changed while pushing")
        entries.append({"key": key, "data": base64.b64encode(corpus.encode_round(row[4])).decode("ascii")})
    return entries


def _send_entries(client, state: UtilState, kind: str, entries: list, budget: int) -> int:
    """Cap the actual JSON request bytes (including keys, ref, and base64)."""
    sender = client.util_rounds if kind == "rows" else client.util_obs
    remaining = state.missing_rows if kind == "rows" else state.missing_obs
    base_size = len(json.dumps({kind: [], "ref": state.ref}).encode())
    batch, size = [], base_size

    def send():
        sender(batch, ref=state.ref)
        del remaining[:len(batch)]
        log.info("util push key=%s ref=%s kind=%s count=%s", state.key, state.ref, kind, len(batch))

    for entry in entries:
        entry_size = len(json.dumps(entry).encode())
        if base_size + entry_size > BATCH_BYTES:
            raise ValueError("one util input exceeds the batch byte limit")
        extra = entry_size + (2 if batch else 0)
        if size + extra > BATCH_BYTES:
            send()
            budget -= 1
            if not budget:
                return 0
            batch, size = [], base_size
            extra = entry_size
        batch.append(entry)
        size += extra
    if batch:
        send()
        budget -= 1
    return budget


def push(db, client, state: UtilState, health: dict, now: float | None = None) -> None:
    """Decide, push at most eight batches, and open; keep unfinished preparation for next cycle."""
    if not enabled() or state.phase == "open":
        return
    now = time.time() if now is None else now
    stage = "push"
    try:
        digest = build_digest.digest(WEBAPP)
        if (health.get("util") or {}).get("digest") != digest:
            note_digest_mismatch(state, now)
            return
        if not state.deploy_mismatch:
            state.digest_mismatch_since = None
            state.digest_reported = False
        if state.phase == "idle":
            if not due(db, state, now) or not _prepare(db, state, digest, now):
                return
        elif _signature(db) != state.prepared_signature or digest != state.digest:
            raise _Changed("prepared signature changed while pushing")
        if state.have_pending:
            db.rollback()  # detached preparation; no connection through worker I/O
            answer = client.util_have(list(state.row_keys), list(state.obs_keys), prune=True, ref=state.ref)
            _missing(state, answer)
            state.have_pending = False
            log.info("util have key=%s ref=%s rows=%s obs=%s", state.key, state.ref,
                     len(state.missing_rows), len(state.missing_obs))
        budget = BATCHES_PER_CYCLE
        while state.missing_rows and budget:
            entries = _round_entries(db, state, state.missing_rows[:ROW_CHUNK])
            db.rollback()
            budget = _send_entries(client, state, "rows", entries, budget)
        if state.missing_obs and budget:
            keys = state.missing_obs[:OBS_MATCHES_PER_CYCLE]
            ids = {state.obs_keys[key] for key in keys}
            if _observation_keys(db, state.excluded, ids) != {key: state.obs_keys[key] for key in keys}:
                raise _Changed("observation match changed while pushing")
            observations = util_model.load_observations(db, set(state.excluded), by_match=True, match_ids=ids)
            entries = [{"key": key, "data": base64.b64encode(corpus.encode_obs(
                [[state.obs_keys[key], observations.get(state.obs_keys[key], [])]])).decode("ascii")} for key in keys]
            db.rollback()
            _send_entries(client, state, "obs", entries, budget)
        if state.missing_rows or state.missing_obs:
            return
        stage = "dispatch"
        db.rollback()
        answer = client.util_open(key=state.key, digest=state.digest, rows=list(state.row_keys),
                                  obs=list(state.obs_keys), refit=state.refit,
                                  context=base64.b64encode(state.context).decode("ascii"), ref=state.ref)
        if "missing_rows" in answer or "missing_obs" in answer:
            _missing(state, answer)
            state.resends += 1
            if state.resends > 2:
                raise ValueError("worker repeatedly lost prepared inputs")
            log.info("util open missing key=%s ref=%s", state.key, state.ref)
            return
        if not answer.get("id") or answer.get("key") != state.key:
            raise ValueError("worker returned an invalid build identity")
        state.build_id, state.phase = answer["id"], "open"
        state.opened_at = now
        state.running_since = state.last_status = None
        log.info("util open key=%s ref=%s id=%s bytes=0 seconds=0.0", state.key, state.ref, state.build_id)
    except _Changed as error:
        log.info("util abandon key=%s ref=%s reason=%s", state.key, state.ref, error)
        _abandon(state)
    except Exception as error:
        _failure(state, stage, error, now)


def _failure(state: UtilState, stage: str, error, now: float) -> None:
    context = {"key": state.key, "ref": state.ref, "id": state.build_id}
    util_build_health.record(stage, str(error), context)
    log.warning("util %s failed key=%s ref=%s id=%s error=%s", stage, state.key, state.ref, state.build_id, error)
    if state.key:
        state.backoff[state.key] = now + BACKOFF_S
        if state.tries.get(state.key, 0) >= MAX_TRIES and state.key not in state.gave_up:
            util_build_health.record("gave_up", "three build tries exhausted", context)
            state.gave_up.add(state.key)
    _abandon(state)


def _store(session_factory, state: UtilState, answer: dict, data: str) -> None:
    """Extract both products in SQL from one bound text value; never decode the build."""
    with session_factory() as db:
        with util_model.rebuild_lock(db, "values"):
            source = "CAST(:data AS JSONB)" if db.get_bind().dialect.name == "postgresql" else ":data"
            products = [f"('values', :values_version, :values_key, {source} -> 'values', CURRENT_TIMESTAMP)"]
            if state.refit:
                products.append(f"('win', :win_version, ({source} -> 'win' ->> 'key'), "
                                f"{source} -> 'win', CURRENT_TIMESTAMP)")
            db.execute(text("INSERT INTO util_model (name, version, key, data, computed_at) VALUES "
                            + ", ".join(products) + " ON CONFLICT (name) DO UPDATE SET "
                            "version = excluded.version, key = excluded.key, data = excluded.data, "
                            "computed_at = excluded.computed_at"),
                       {"data": data, "values_version": util_values.VERSION, "values_key": answer["values_key"],
                        "win_version": util_model.VERSION})
            db.commit()


def _follow(session_factory, client, state: UtilState, now: float) -> None:
    stage = "run"
    try:
        answer = client.util_build(state.build_id)
        if answer.get("id") != state.build_id or answer.get("key") != state.key:
            raise ValueError("worker returned another build's status")
        status = answer.get("status")
        if status != state.last_status:
            log.info("util poll key=%s ref=%s id=%s status=%s bytes=%s seconds=%.1f", state.key, state.ref,
                     state.build_id, status, answer.get("bytes") or 0, now - state.opened_at)
            state.last_status = status
        if status == "queued":
            state.running_since = None
            return
        if status == "running":
            if state.running_since is None:
                state.running_since = now
            if now - state.running_since > STALE_BUILD_S:
                raise ValueError("build ran longer than 3600 seconds")
            return
        if status == "failed" and answer.get("error_kind") == "deploy":
            state.deploy_mismatch = True
            note_digest_mismatch(state, now, answer.get("error", "task deploy mismatch"))
            state.tries[state.key] = max(0, state.tries.get(state.key, 1) - 1)
            state.backoff[state.key] = now + BACKOFF_S
            log.info("util deploy retry key=%s ref=%s id=%s", state.key, state.ref, state.build_id)
            _abandon(state)
            return
        if status != "done":
            raise ValueError(answer.get("error") or f"build ended in {status!r}")
        stage = "collect"
        if (answer.get("version") != util_values.VERSION or answer.get("digest") != state.digest
                or answer.get("digest") != build_digest.digest(WEBAPP) or answer.get("refit") != state.refit
                or not isinstance(answer.get("values_key"), str) or not answer["values_key"]):
            raise ValueError("build version, digest, refit or values key differs")
        with tempfile.TemporaryDirectory(prefix="util-result-") as folder:
            path = Path(folder) / "result.json"
            client.util_result(state.build_id, path)
            with path.open("rb") as source:
                sha = hashlib.file_digest(source, "sha256").hexdigest()
            size = path.stat().st_size
            if sha != answer.get("sha256") or size != answer.get("bytes"):
                raise ValueError("build result sha256 or byte count differs")
            data = path.read_text(encoding="utf-8")
            log.info("util collect key=%s ref=%s id=%s bytes=%s seconds=%.1f", state.key, state.ref,
                     state.build_id, size, now - state.opened_at)
            stage = "store"
            _store(session_factory, state, answer, data)
        log.info("util store key=%s ref=%s id=%s bytes=%s seconds=%.1f", state.key, state.ref,
                 state.build_id, size, now - state.opened_at)
        state.deploy_mismatch = False
        state.digest_mismatch_since = None
        state.digest_reported = False
        _abandon(state)
        util_build_health.clear_all()
    except Exception as error:
        _failure(state, stage, error, now)


def step(session_factory, client, state: UtilState | None = None, now: float | None = None) -> None:
    """Advance one build after the control cycle has released its transaction and lock."""
    if not enabled():
        return
    state = STATE if state is None else state
    now = time.time() if now is None else now
    if state.phase == "open":
        _follow(session_factory, client, state, now)
        return
    try:
        health = client.health()
    except Exception as error:
        _failure(state, "dispatch", error, now)
        return
    with session_factory() as db:
        push(db, client, state, health, now)
