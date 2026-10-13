"""The coaching page: the replay viewer with a coach panel (docs/superpowers/specs/2026-10-12-ai-coach-design.md).

    GET /replays/{match_uuid}/coach?player=<player id>&version=<n>

A linked replay only; 404 in demo mode. A match with no report renders the viewer with an empty panel."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.replays import UUID_SHAPE, viewer_context
from app.services import coach as coach_service
from app.services import replays as replay_service
from app.templates import templates

router = APIRouter(tags=["coach"])


def _whole(value) -> int | None:
    return int(value) if isinstance(value, str) and value.isdigit() else None


def linked_replay_or_404(db: Session, match_uuid: str):
    if not coach_service.coach_enabled() or not UUID_SHAPE.match(match_uuid):
        raise HTTPException(status_code=404)
    replay = replay_service.find_replay(db, match_uuid)
    if replay is None or replay.match_id is None or replay.link_status != "linked":
        raise HTTPException(status_code=404)
    return replay


@router.get("/replays/{match_uuid}/coach")
def coach_page(request: Request, match_uuid: str, player: str | None = None, version: str | None = None,
               db: Session = Depends(get_db)):
    replay = linked_replay_or_404(db, match_uuid)
    context = viewer_context(request, db, replay)
    context["coach"] = coach_service.page_data(db, replay, _whole(player), _whole(version))
    return templates.TemplateResponse(request, "replays/replay.html", context)
