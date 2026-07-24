from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.session_stats import get_session_stats
from app.services.sessions import get_session_or_404, list_sessions
from app.templates import templates

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
def session_list(request: Request, db: Session = Depends(get_db)):
    sessions = [s for s in reversed(list_sessions(db)) if s.is_multi_match]
    return templates.TemplateResponse(request, "sessions/list.html", {"sessions": sessions})


@router.get("/{session_index}")
def session_detail(request: Request, session_index: int, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_index)
    current_player = get_current_player(request, db)
    stats = get_session_stats(db, session, current_player.id if current_player else None)
    matches_by_id = {m.id: m for m in session.matches}
    return templates.TemplateResponse(
        request,
        "sessions/detail.html",
        {"session": session, "stats": stats, "matches_by_id": matches_by_id},
    )
