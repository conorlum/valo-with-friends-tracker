"""Operator-only routes for the .vrf archive (docs/superpowers/specs/2026-10-01-control-heights-design.md,
part 1), called by scripts/delete_replay_data.py and scripts/reparse_archive.py.

Every route answers 404 unless REPLAY_ADMIN_TOKEN is set (and not in demo mode) and the request carries
`Authorization: Bearer <that token>`: to anyone else they don't exist.

    POST /admin/replays/delete       {"match_uuid", "reason"?}: tombstone the match, delete its stored
                                     replay, rounds, players and control rows, and tell the worker to
                                     delete its pending and archived files. Idempotent.
    GET  /admin/replays/archive      the worker's archive index.
    POST /admin/replays/reparse      {"match_uuid"}: parse the archived file again; {"upload_id"}.
    GET  /admin/replays/uploads/{id} one poll of that reparse (the same refresh_job as an upload).
    GET  /admin/replays/reparse/status  where the automatic re-parse queue stands
                                     (app/services/replay_reparse_auto.py, `status`). Read only.
"""

from __future__ import annotations

import hmac
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.replay import Replay, ReplayDeletion, ReplayUpload
from app.replays import db as replay_db
from app.replays import store
from app.services import replay_reparse_auto as reparse_auto
from app.services import replay_upload as uploads

router = APIRouter(prefix="/admin/replays", tags=["replay-admin"])

UUID_SHAPE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REPARSE_SESSION = "admin-reparse"


def require_admin(request: Request) -> None:
    token = settings.replay_admin_token
    given = request.headers.get("authorization", "")
    if not token or settings.demo_mode or not given.startswith("Bearer "):
        raise HTTPException(status_code=404)
    if not hmac.compare_digest(given[len("Bearer "):].encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(status_code=404)


def _match_uuid(body: dict) -> str:
    value = str(body.get("match_uuid") or "").strip().lower()
    if not UUID_SHAPE.match(value):
        raise HTTPException(status_code=400, detail="match_uuid must be a match UUID")
    return value


def _worker() -> uploads.WorkerClient:
    if not settings.replay_worker_url:
        raise HTTPException(status_code=503, detail="REPLAY_WORKER_URL is not set")
    return uploads.client()


@router.post("/delete", dependencies=[Depends(require_admin)])
def delete_match(body: dict = Body(...), db: Session = Depends(get_db)):
    match_uuid = _match_uuid(body)
    replay_db.advisory_lock(db, match_uuid)
    tombstone = db.get(ReplayDeletion, match_uuid)
    if tombstone is None:
        db.add(ReplayDeletion(match_uuid=match_uuid, reason=str(body.get("reason") or "")[:500] or None,
                              deleted_at=datetime.now(timezone.utc)))
    replay = db.query(Replay).filter(Replay.match_uuid == match_uuid).one_or_none()
    replay_deleted = replay is not None
    if replay is not None:
        db.query(ReplayUpload).filter(ReplayUpload.replay_id == replay.id).update(
            {ReplayUpload.replay_id: None}, synchronize_session=False)
        store._delete(db, replay)
    db.commit()
    worker = {"told": False}
    if settings.replay_worker_url:
        try:
            worker = {"told": True, "answer": uploads.client().delete(match_uuid)}
        except uploads.WorkerRefused as refused:  # 404: the archive is off, so there's nothing to delete
            worker = {"told": True, "answer": {"error": str(refused)}}
        except uploads.WorkerError as error:  # the sync thread pushes the full list on the next new boot
            worker = {"told": False, "error": str(error)}
    return {"match_uuid": match_uuid, "tombstoned": True, "already_tombstoned": tombstone is not None,
            "replay_deleted": replay_deleted, "worker": worker}


@router.get("/archive", dependencies=[Depends(require_admin)])
def archive_index():
    try:
        return _worker().archive()
    except uploads.WorkerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/reparse", dependencies=[Depends(require_admin)])
def reparse(body: dict = Body(...), db: Session = Depends(get_db)):
    match_uuid = _match_uuid(body)
    if db.get(ReplayDeletion, match_uuid) is not None:
        raise HTTPException(status_code=409, detail="this match was deleted on request")
    try:
        job = _worker().reparse(match_uuid)
    except uploads.WorkerRefused as refused:
        raise HTTPException(status_code=refused.status, detail=str(refused)) from refused
    except uploads.WorkerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    upload = ReplayUpload(id=str(uuid.uuid4()), status="parsing", source_sha256=job.get("sha256"),
                          size_bytes=job.get("size"), session_key=REPARSE_SESSION, client_ip=None,
                          created_at=datetime.now(timezone.utc), worker_job_id=job.get("id"))
    db.add(upload)
    db.commit()
    return {"upload_id": upload.id, "job_id": job.get("id")}


@router.get("/reparse/status", dependencies=[Depends(require_admin)])
def reparse_auto_status(db: Session = Depends(get_db)):
    # No worker URL is a stopped reason in the answer, not an error: the counts still come from the database.
    return reparse_auto.status(db, uploads.client() if settings.replay_worker_url else None)


@router.get("/uploads/{upload_id}", dependencies=[Depends(require_admin)])
def reparse_status(upload_id: str, db: Session = Depends(get_db)):
    upload = db.get(ReplayUpload, upload_id.lower())
    if upload is None or upload.session_key != REPARSE_SESSION:
        raise HTTPException(status_code=404)
    upload = uploads.refresh_job(db, upload, _worker())
    return {"status": upload.status, "error": upload.error, "store_outcome": upload.store_outcome,
            "archive_ack": upload.archive_ack, "replay_id": upload.replay_id}
