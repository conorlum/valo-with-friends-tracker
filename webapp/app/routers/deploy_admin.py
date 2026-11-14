"""Owner-fenced deploy drains and detached site/worker readiness snapshots."""

from datetime import datetime, timezone
import logging
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, get_db
from app.models.site_events import SiteEvent
from app.routers.replay_admin import require_admin
from app.services import deploy_drain, replay_control_remote, replay_heights_remote
from app.services import site_health, upload_activity, util_build_remote, util_rebuild


router = APIRouter(prefix="/admin/deploy", dependencies=[Depends(require_admin)])
log = logging.getLogger(__name__)
_session_factory = SessionLocal
_tasks_lock = threading.Lock()
_tasks_thread: threading.Thread | None = None
_tasks_result: dict | None = None


class OwnerBody(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)


class DrainBody(OwnerBody):
    sha: str = Field(min_length=1, max_length=64)
    skip_drain: bool = False


class PollBody(OwnerBody):
    tasks: bool = False


class ResumeBody(OwnerBody):
    outcome: Literal["ok", "failed", "dry_run"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _worker() -> replay_control_remote.ControlClient:
    if not settings.replay_worker_url:
        raise replay_control_remote.Unreachable("replay worker is not configured")
    # Health plus a hold/cancel/release (each capped at 30 s) stays below 60 s.
    return replay_control_remote.ControlClient(settings.replay_worker_url, timeout_s=5)


def _worker_call(method, *args, **kwargs):
    try:
        return getattr(_worker(), method)(*args, **kwargs)
    except Exception as error:
        log.warning("deploy worker %s failed (%s)", method, type(error).__name__)
        return None


def _active(db, now):
    row = deploy_drain.active(db, now)
    if row is not None:
        db.expunge(row)
    db.commit()
    return row


def _owned(db, run_id, now):
    row = _active(db, now)
    if row is None or row.run_id != run_id:
        raise HTTPException(status_code=409, detail="run does not own an active deploy drain")
    return row


def _owner_write(operation, *args):
    try:
        return operation(*args)
    except deploy_drain.NotOwner as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _ping_tasks(started_at):
    global _tasks_result
    result = _worker_call("ping", timeout=330)
    answer = result.get("answer") if result and result.get("ok") else None
    commit = answer.get("commit") if isinstance(answer, dict) else None
    with _tasks_lock:
        _tasks_result = {"commit": commit, "started_at": started_at}


def _tasks(start=False):
    global _tasks_thread
    with _tasks_lock:
        result = dict(_tasks_result) if _tasks_result is not None else None
        if start and (_tasks_thread is None or not _tasks_thread.is_alive()):
            _tasks_thread = threading.Thread(target=_ping_tasks, args=(_now().timestamp(),),
                                             name="deploy-tasks-ping", daemon=True)
            _tasks_thread.start()
        return result


def _status(row, now):
    health = _worker_call("health")
    live = replay_control_remote.LIVE
    facts = deploy_drain.SiteFacts(
        cycle_at=live.at if live is not None else None,
        control_in_flight=len(live.in_flight) if live is not None else 0,
        heights_open=replay_heights_remote.open_builds(),
        util_phase=util_build_remote.STATE.phase,
        util_child_running=util_rebuild.running(),
        uploads_receiving=upload_activity.receiving(),
        uploads_last_end_age_s=upload_activity.last_end_age_s(),
    )
    return {"drain": deploy_drain.to_json(row), "status": deploy_drain.evaluate(health, facts, row, now),
            "commits": {"worker": health.get("commit") if health is not None else None, "tasks": _tasks()},
            "limit_s": deploy_drain.LIMIT_S, "now": now.timestamp()}


@router.post("/drain")
def request_drain(body: DrainBody, db: Session = Depends(get_db)):
    now = _now()
    result = deploy_drain.request(db, body.run_id, body.sha, now, skip_drain=body.skip_drain)
    row = result["drain"]
    # The request contract returns the initial row, while the stored row gains readiness below.
    answer = {**result, "drain": deploy_drain.to_json(row)}
    db.expunge(row)
    db.commit()
    if result["result"] != "busy":
        _worker_call("drain_hold", row.id, deploy_drain._utc(row.expires_at).timestamp())
        status = _status(row, now)["status"]
        try:
            deploy_drain.owner_poll(db, body.run_id, now, status["waiting_total"])
        except deploy_drain.NotOwner:
            # A takeover while the worker answered is observed by the next owner poll.
            pass
    return answer


@router.post("/poll")
def poll(body: PollBody, db: Session = Depends(get_db)):
    now = _now()
    row = _owned(db, body.run_id, now)
    answer = _status(row, now)
    row = _owner_write(deploy_drain.owner_poll, db, body.run_id, now, answer["status"]["waiting_total"])
    answer["drain"] = deploy_drain.to_json(row)
    answer["commits"]["tasks"] = _tasks(start=body.tasks)
    return answer


@router.get("/status")
def status(db: Session = Depends(get_db)):
    now = _now()
    return _status(_active(db, now), now)


@router.post("/deploying")
def deploying(body: OwnerBody, db: Session = Depends(get_db)):
    row = _owner_write(deploy_drain.mark_deploying, db, body.run_id, _now())
    return {"drain": deploy_drain.to_json(row)}


@router.post("/force")
def force(body: OwnerBody, db: Session = Depends(get_db)):
    now = _now()
    row = _owned(db, body.run_id, now)
    waiting = _status(row, now)["status"]["waiting"]
    result = _worker_call("drain_cancel", row.id)
    cancelled = result.get("cancelled", []) if result is not None else []
    context = {"cancelled": cancelled, "waiting": waiting}
    if result is None:
        context["worker_unreachable"] = True
    site_health.record_now("forced_deploy", str(row.id), "deploy forced after 90 min", context,
                           session_factory=_session_factory)
    _owner_write(deploy_drain.mark_forced, db, body.run_id)
    return {"cancelled": cancelled}


@router.post("/resume")
def resume(body: ResumeBody, db: Session = Depends(get_db)):
    now = _now()
    row = _owner_write(deploy_drain.finish, db, body.run_id, body.outcome, now)
    answer = deploy_drain.to_json(row)
    keys = []
    if body.outcome == "ok" and not answer["forced"]:
        keys = db.scalars(select(SiteEvent.key).where(SiteEvent.kind == "forced_deploy")).all()
    db.commit()
    _worker_call("drain_release", answer["id"])
    # A newer drain may start during release. This success clears only earlier occurrences.
    for key in keys:
        site_health.resolve_now("forced_deploy", key, session_factory=_session_factory, at=now)
    return {"drain": answer}
