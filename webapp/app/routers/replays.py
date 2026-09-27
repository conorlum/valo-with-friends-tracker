"""Replay pages (docs/replay-viewer-plan.md, "The viewer"): every route 404s in demo mode.

- `GET /replays/{match_uuid}?round=n`: the player. Linked: names, the kill feed with weapons and
  per-kill Impact, the round scoreboard. Unlinked: agents and side colours, and why it isn't
  linked.
- `GET /replays/{match_uuid}/{n}.json`: the stored gzip bytes as they are
  (`Content-Encoding: gzip`), with `ETag` = the first 16 hex of their sha256 and
  `Cache-Control: private, no-cache`, so a replacement is seen at once and an unchanged round
  is a cheap 304.
- `GET /matches/{external_id}/replay`: a redirect to the page when the match has a linked,
  valid replay.

No scoring code and no writes.
"""

import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
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


@router.get("/replays/{match_uuid}/{round_number}.json")
def replay_round(request: Request, match_uuid: str, round_number: int, db: Session = Depends(get_db)):
    replay = _replay_or_404(db, match_uuid)
    data = replay_service.round_blob(db, replay, round_number)
    if data is None:
        raise HTTPException(status_code=404)
    etag = etag_of(data)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if etag in [tag.strip() for tag in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})


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
        "linked": context["match"]["linked"], "replay_data": context, "start_round": start})


@router.get("/matches/{external_id}/replay")
def match_replay(external_id: str, db: Session = Depends(get_db)):
    if not replay_service.replays_enabled():
        raise HTTPException(status_code=404)
    match = get_match_or_404(db, external_id)
    url = replay_service.replay_url_for_match(db, match)
    if url is None:
        raise HTTPException(status_code=404)
    return RedirectResponse(url, status_code=302)
