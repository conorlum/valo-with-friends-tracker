"""Replay uploader API: token gates precede all body parsing."""
import re
from datetime import datetime, timezone
from typing import NamedTuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from app.db import get_db
from app.models.replay import Replay, ReplayUpload
from app.replays.store import DELETED_ON_REQUEST
from app.routers.replays import UUID_SHAPE, _status_body
from app.services import deploy_drain, replay_upload as uploads, upload_activity, upload_tokens

router = APIRouter(tags=["replay-api"])
SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _answer(body: dict, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", **(headers or {})})


def _error(status: int, message: str, headers: dict | None = None) -> JSONResponse:
    return _answer({"error": message}, status, headers)


class Caller(NamedTuple):
    """What a route needs from its token, copied out so no ORM row is read after the transaction ends."""
    id: int
    label: str


def _key_or_401(request: Request, db: Session):
    if not uploads.upload_enabled():
        return _error(404, "not found")
    parts = request.headers.get("authorization", "").split()
    token = upload_tokens.verify(db, parts[1]) if len(parts) == 2 and parts[0].lower() == "bearer" else None
    if token is None:
        return _error(401, "unknown or revoked key", {"WWW-Authenticate": "Bearer"})
    caller = Caller(token.id, token.label)
    # Verification began a transaction; end it so no pooled connection is held while a body is received.
    db.rollback()
    return caller


def _admit_upload(request: Request, db: Session):
    caller = _key_or_401(request, db)
    if isinstance(caller, JSONResponse):
        return caller
    paused = deploy_drain.paused(db, datetime.now(timezone.utc))
    db.rollback()
    if paused:
        return _answer({"error": "the site is deploying: try again later", "deploy": True},
                       503, {"Retry-After": "600"})
    return caller


def _create_upload(db: Session, stream, token, client_ip: str | None) -> dict:
    upload = uploads.create_upload(db, stream, upload_tokens.session_key(token), client_ip, uploads.client())
    # A commit expires ORM attributes; read them here too, away from the event loop.
    return {"upload_id": upload.id, "status_url": f"/api/replays/uploads/{upload.id}/status"}


@router.post("/api/replays/upload")
async def upload_file(request: Request, db: Session = Depends(get_db)):
    token = await run_in_threadpool(_admit_upload, request, db)
    if isinstance(token, JSONResponse):
        return token
    with upload_activity.admitted():
        try:
            form = await request.form()
        except ClientDisconnect:
            # The PC slept or the network dropped mid-body: not a crash, nothing to record.
            return _error(400, "upload interrupted")
        except (HTTPException, ValueError):
            return _error(400, "invalid multipart body")
        try:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                return _error(400, "file is required")
            client_ip = request.client.host if request.client else None
            try:
                body = await run_in_threadpool(_create_upload, db, file.file, token, client_ip)
            except ValueError as reason:
                return _error(400, str(reason).capitalize() + ".")
            except uploads.LimitExceeded as reason:
                return _error(429, str(reason).capitalize() + ".", {"Retry-After": "600"})
            except uploads.WorkerError as reason:
                return _error(503, f"The replay worker isn't available ({reason}). Please try again later.",
                              {"Retry-After": "600"})
        finally:
            await form.close()
    await run_in_threadpool(upload_tokens.touch, db, token, datetime.now(timezone.utc))
    return _answer(body)


@router.get("/api/replays/uploads/{upload_id}/status")
def upload_status(request: Request, upload_id: str, db: Session = Depends(get_db)):
    token = _key_or_401(request, db)
    if isinstance(token, JSONResponse):
        return token
    if not UUID_SHAPE.fullmatch(upload_id):
        return _error(404, "not found")
    query = db.query(ReplayUpload).filter(ReplayUpload.id == upload_id.lower())
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    upload = query.one_or_none()
    if upload is None or upload.session_key != upload_tokens.session_key(token):
        return _error(404, "not found")
    upload = uploads.refresh_job(db, upload, uploads.client())
    body = _status_body(db, upload)
    if upload.status == "failed" and DELETED_ON_REQUEST in (upload.error or ""):
        # For the app only (the browser's answer is unchanged): never send this match again.
        body = {**body, "deleted": True}
    db.rollback()  # Release the upload lock before the token's short touch transaction.
    upload_tokens.touch(db, token, datetime.now(timezone.utc))
    return _answer(body)


def _known_and_touch(db: Session, token, sha: str) -> JSONResponse:
    # kept: a Replay holds exactly this file now (the only answer the app may delete on).
    # known: also a stored upload of it, which may be a recording the site discarded for another copy.
    kept = db.query(Replay.id).filter(Replay.source_sha256 == sha).first() is not None
    known = kept or db.query(ReplayUpload.id).filter(ReplayUpload.source_sha256 == sha,
                                                    ReplayUpload.status == "stored").first() is not None
    upload_tokens.touch(db, token, datetime.now(timezone.utc))
    return _answer({"known": known, "kept": kept})


@router.post("/api/replays/check")
async def check(request: Request, db: Session = Depends(get_db)):
    token = await run_in_threadpool(_key_or_401, request, db)
    if isinstance(token, JSONResponse):
        return token
    try:
        body = await request.json()
    except ClientDisconnect:
        return _error(400, "request interrupted")
    except (ValueError, UnicodeError):
        return _error(400, "invalid JSON body")
    sha = body.get("sha256") if isinstance(body, dict) else None
    if not isinstance(sha, str) or not SHA256.fullmatch(sha):
        return _error(400, "sha256 must be 64 hexadecimal characters")
    return await run_in_threadpool(_known_and_touch, db, token, sha.lower())


@router.get("/api/replays/key")
def key(request: Request, db: Session = Depends(get_db)):
    token = _key_or_401(request, db)
    if isinstance(token, JSONResponse):
        return token
    body = {"ok": True, "label": token.label}
    upload_tokens.touch(db, token, datetime.now(timezone.utc))
    return _answer(body)
