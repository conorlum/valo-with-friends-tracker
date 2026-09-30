"""Replay pages (docs/replay-viewer-plan.md, "The viewer"): every route 404s in demo mode.

- `GET /replays/{match_uuid}?round=n`: the player. Linked: names, the kill feed with weapons and
  per-kill Impact, the round scoreboard. Unlinked: agents and side colours, and why it isn't
  linked.
- `GET /replays/{match_uuid}/{n}.json`: the stored gzip bytes as they are
  (`Content-Encoding: gzip`), with `ETag` = the first 16 hex of their sha256 and
  `Cache-Control: private, no-cache`, so a replacement is seen at once and an unchanged round
  is a cheap 304.
- `GET /replays/{match_uuid}/{n}/control.bin`: the round's stored map control
  (docs/replay-map-control-plan.md, "Delivery"; app/replays/control_format.py), with the same
  gzip, ETag and caching, plus `X-Control-Stale: 1` when its inputs have changed since it was
  computed. Otherwise JSON `{"status": ...}`: 202 `not_ready` (with `Retry-After`; it is computed
  by a local command, scripts/compute_control.py; also a row in an older byte format), 422
  `failed`, and 404 `no_map` when the map has no control layer yet or `old_blob` when the replay
  predates condenser revision 10 (re-ingest or re-upload it). The page's `match.control` says the last up front.
- `GET /replays/{match_uuid}/control/players.json`: the Control tab's numbers, per round and per
  match, from the stored summaries (never the ticks), with an ETag over the rows and which are
  stale; the same 404s (`no_map`, `old_blob`) as `control.bin`, and `unlinked`.
- `GET /matches/{external_id}/replay`: a redirect to the page when the match has a linked,
  valid replay.
- Stage 3, the friends-only upload (404 in demo mode and when no code or worker is configured):
  `GET /replays/upload` (the invite-code form, then the file form), `POST /replays/upload/code`,
  `POST /replays/upload` (size, magic and rate limits, then to the worker), `GET
  /replays/uploads/{id}` (the job page) and `GET /replays/uploads/{id}/status` (polled every 3 s;
  stores the result when the worker is done).

No scoring code. Only the upload routes write (an upload's row, and its replay through store.py).
"""

import hashlib
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.replay import ReplayUpload
from app.replays import db as replay_db
from app.services import replay_control as control_service
from app.services import replay_control_views as control_views
from app.services import replay_upload as uploads
from app.services import replays as replay_service
from app.services.matches import get_match_or_404
from app.templates import templates

router = APIRouter(tags=["replays"])

UUID_SHAPE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _replay_or_404(db: Session, match_uuid: str):
    if not replay_service.replays_enabled() or not UUID_SHAPE.match(match_uuid):
        raise HTTPException(status_code=404)
    replay = replay_service.find_replay(db, match_uuid)
    if replay is None:
        raise HTTPException(status_code=404)
    return replay


def etag_of(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:16] + '"'


# ---------------------------------------------------------------- Stage 3: upload (before /replays/{uuid})


def _upload_enabled_or_404() -> None:
    if not uploads.upload_enabled():
        raise HTTPException(status_code=404)


def _session_key(request: Request) -> str:
    key = request.session.get("replay_upload_sid")
    if not key:
        key = secrets.token_hex(16)
        request.session["replay_upload_sid"] = key
    return key


def _upload_page(request: Request, status_code: int = 200, error: str | None = None):
    return templates.TemplateResponse(request, "replays/upload.html", {
        "authorized": bool(request.session.get("replay_upload_ok")), "error": error,
        "max_mb": uploads.settings.replay_upload_max_bytes // 1_000_000, "per_hour": uploads.UPLOADS_PER_HOUR,
    }, status_code=status_code)


@router.get("/replays/upload")
def upload_form(request: Request):
    _upload_enabled_or_404()
    return _upload_page(request)


@router.post("/replays/upload/code")
def upload_code(request: Request, code: str = Form("")):
    _upload_enabled_or_404()
    if not uploads.code_matches(code):
        return _upload_page(request, 403, "That code isn't right.")
    request.session["replay_upload_ok"] = True
    return RedirectResponse("/replays/upload", status_code=303)


@router.post("/replays/upload")
def upload_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    _upload_enabled_or_404()
    if not request.session.get("replay_upload_ok"):
        return _upload_page(request, 403, "Enter the invite code first.")
    client_ip = request.client.host if request.client else None
    try:
        upload = uploads.create_upload(db, file.file, _session_key(request), client_ip, uploads.client())
    except ValueError as reason:
        return _upload_page(request, 400, str(reason).capitalize() + ".")
    except uploads.LimitExceeded as reason:
        return _upload_page(request, 429, str(reason).capitalize() + ".")
    except uploads.WorkerError as reason:
        return _upload_page(request, 503, f"The replay worker isn't available ({reason}). Please try again later.")
    finally:
        file.file.close()
    return RedirectResponse(f"/replays/uploads/{upload.id}", status_code=303)


def _own_upload_or_404(request: Request, db: Session, upload_id: str) -> ReplayUpload:
    _upload_enabled_or_404()
    if not UUID_SHAPE.match(upload_id):
        raise HTTPException(status_code=404)
    query = db.query(ReplayUpload).filter(ReplayUpload.id == upload_id.lower())
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    upload = query.one_or_none()
    if upload is None or upload.session_key != request.session.get("replay_upload_sid"):
        raise HTTPException(status_code=404)
    return upload


def _status_body(db: Session, upload: ReplayUpload) -> dict:
    body = {"status": upload.status, "error": upload.error}
    if upload.status == "stored" and upload.replay_id is not None:
        from app.models.replay import Replay

        replay = db.get(Replay, upload.replay_id)
        if replay is not None:
            body["replay_url"] = f"/replays/{str(replay.match_uuid).lower()}"
            body["linked"] = replay.link_status == "linked"
    return body


@router.get("/replays/uploads/{upload_id}")
def upload_job(request: Request, upload_id: str, db: Session = Depends(get_db)):
    upload = _own_upload_or_404(request, db, upload_id)
    body = _status_body(db, upload)
    created = upload.created_at if upload.created_at.tzinfo else upload.created_at.replace(tzinfo=timezone.utc)
    elapsed = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
    db.rollback()
    return templates.TemplateResponse(request, "replays/upload_job.html",
                                      {"upload_id": upload.id, "job": body, "elapsed_seconds": elapsed})


@router.get("/replays/uploads/{upload_id}/status")
def upload_status(request: Request, upload_id: str, db: Session = Depends(get_db)):
    upload = _own_upload_or_404(request, db, upload_id)
    upload = uploads.refresh_job(db, upload, uploads.client())
    body = _status_body(db, upload)
    db.rollback()
    return JSONResponse(body)


# ---------------------------------------------------------------- replay pages


@router.get("/replays/{match_uuid}/{round_number}.json")
def replay_round(request: Request, match_uuid: str, round_number: int, db: Session = Depends(get_db)):
    replay = _replay_or_404(db, match_uuid)
    data = replay_service.round_blob(db, replay, round_number)
    if data is None:
        raise HTTPException(status_code=404)
    return _stored_gzip(request, data, "application/json")


def _stored_gzip(request: Request, data: bytes, media_type: str, headers: dict | None = None) -> Response:
    """Stored gzip bytes as they are, with an ETag of their sha256 and a 304 for a match."""
    etag = etag_of(data)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", **(headers or {})}
    if etag in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=media_type, headers={**headers, "Content-Encoding": "gzip"})


CONTROL_RETRY_AFTER_S = 600


@router.get("/replays/{match_uuid}/{round_number}/control.bin")
def replay_round_control(request: Request, match_uuid: str, round_number: int, db: Session = Depends(get_db)):
    replay = _replay_or_404(db, match_uuid)
    if not 1 <= round_number <= replay.round_count:
        raise HTTPException(status_code=404)
    answer = control_service.round_control(db, replay, round_number)
    if answer.status in ("no_map", "old_blob"):
        return JSONResponse({"status": answer.status}, status_code=404)
    if answer.status == "not_ready":
        return JSONResponse({"status": "not_ready"}, status_code=202,
                            headers={"Retry-After": str(CONTROL_RETRY_AFTER_S), "Cache-Control": "no-store"})
    if answer.status == "failed":
        return JSONResponse({"status": "failed"}, status_code=422, headers={"Cache-Control": "no-store"})
    return _stored_gzip(request, answer.row.data, "application/octet-stream",
                        {"X-Control-Stale": "1"} if answer.stale else None)


def _control_replay_or_404(db: Session, match_uuid: str):
    """The replay, when its map has the layer and its blob can have control; else a 404 that says why."""
    replay = _replay_or_404(db, match_uuid)
    if control_service.map_layer(replay.map_name) is None:
        return replay, JSONResponse({"status": "no_map"}, status_code=404)
    if control_service.blob_too_old(replay):
        return replay, JSONResponse({"status": "old_blob"}, status_code=404)
    if not replay_db.is_linked(replay):
        # Sides and teams come from the link: an unlinked replay's tables and heatmap can't name them.
        return replay, JSONResponse({"status": "unlinked"}, status_code=404)
    return replay, None


def _json_with_etag(request: Request, body: dict, etag: str) -> Response:
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if etag in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return JSONResponse(body, headers=headers)


@router.get("/replays/{match_uuid}/control/players.json")
def replay_control_players(request: Request, match_uuid: str, db: Session = Depends(get_db)):
    """Every round's per-player control table and the match's (app/services/replay_control_views.py)."""
    replay, refused = _control_replay_or_404(db, match_uuid)
    if refused is not None:
        return refused
    loaded = control_views.load_round_summaries(db, replay)
    return _json_with_etag(request, control_views.player_tables(replay, loaded), loaded.etag)


@router.get("/replays/{match_uuid}")
def replay_page(request: Request, match_uuid: str, db: Session = Depends(get_db)):
    replay = _replay_or_404(db, match_uuid)
    context = replay_service.page_context(db, replay)
    start_round = request.query_params.get("round")
    start = int(start_round) if start_round and start_round.isdigit() else 1
    if start not in context["match"]["rounds"]:
        start = 1
    return templates.TemplateResponse(request, "replays/replay.html", {
        "replay": {"map_name": replay.map_name, "round_numbers": context["match"]["rounds"],
                   "title_suffix": None, "linked": context["match"]["linked"],
                   "match_url": (f"/matches/{context['match']['external_id']}" if context["match"]["linked"] else None),
                   "reason": context["match"].get("reason")},
        "linked": context["match"]["linked"], "replay_data": context, "start_round": start,
        "control": context["match"]["control"] if context["match"]["linked"] else None})


@router.get("/matches/{external_id}/replay")
def match_replay(external_id: str, db: Session = Depends(get_db)):
    if not replay_service.replays_enabled():
        raise HTTPException(status_code=404)
    match = get_match_or_404(db, external_id)
    url = replay_service.replay_url_for_match(db, match)
    if url is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(url, status_code=302)
