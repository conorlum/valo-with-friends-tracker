"""The coaching page: the replay viewer with a coach panel (docs/superpowers/specs/2026-10-12-ai-coach-design.md).

    GET /replays/{match_uuid}/coach?player=<player id>&version=<n>

A linked replay only; 404 in demo mode. A match with no report renders the viewer with an empty panel."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.coach import prompt as coach_prompt

from app.db import get_db
from app.routers.replays import UUID_SHAPE, viewer_context
from app.services import coach as coach_service
from app.services.auth import get_current_player
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
    context["coach"]["can_feedback"] = get_current_player(request, db) is not None
    return templates.TemplateResponse(request, "replays/replay.html", context)


@router.get("/replays/{match_uuid}/coach/prompt")
def prompt_out(match_uuid: str, report: str | None = None, db: Session = Depends(get_db)):
    replay = linked_replay_or_404(db, match_uuid)
    if report is None:      # no report named: the whole match, every report with open feedback
        reports, quiet = coach_service.match_prompt_data(db, replay)
        return PlainTextResponse(coach_prompt.prompt_out_match(str(replay.match_uuid).lower(), reports, quiet))
    report_id = _whole(report)
    found = coach_service.report_page_data(db, report_id) if report_id is not None else None
    if found is None or found[0] != str(replay.match_uuid).lower():
        raise HTTPException(status_code=404)
    return PlainTextResponse(coach_prompt.prompt_out(found[0], found[1]))


@router.post("/replays/{match_uuid}/coach/points/{point_id}/feedback")
def post_feedback(request: Request, match_uuid: str, point_id: int, verdict: str = Form(...), note: str = Form(""),
                  db: Session = Depends(get_db)):
    replay = linked_replay_or_404(db, match_uuid)
    viewer = get_current_player(request, db)
    if viewer is None:
        raise HTTPException(status_code=403, detail="Log in to give feedback")
    try:
        coach_service.add_feedback(db, replay.match_id, point_id, viewer.id, verdict, note)
    except LookupError:
        raise HTTPException(status_code=404) from None
    except coach_service.ReportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    report = coach_service.report_of_point(db, point_id)
    return RedirectResponse(f"/replays/{str(replay.match_uuid).lower()}/coach?player={report.player_id}"
                            f"&version={report.version}", status_code=303)
