from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.sessions import get_session_or_404, list_sessions
from app.templates import templates

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
def session_list(request: Request, db: Session = Depends(get_db)):
    sessions = list(reversed(list_sessions(db)))
    return templates.TemplateResponse(request, "sessions/list.html", {"sessions": sessions})


@router.get("/{session_index}")
def session_detail(request: Request, session_index: int, db: Session = Depends(get_db)):
    session = get_session_or_404(db, session_index)
    return templates.TemplateResponse(request, "sessions/detail.html", {"session": session})
