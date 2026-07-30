from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.squad import get_squad_overview
from app.templates import templates

router = APIRouter(prefix="/squad", tags=["squad"])

RECENT_MATCH_LIMIT = 30


@router.get("")
def squad_page(request: Request, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)
    overview = get_squad_overview(db, current_player.id, match_limit=RECENT_MATCH_LIMIT)
    return templates.TemplateResponse(
        request, "squad/detail.html", {"overview": overview, "scope": "recent"}
    )


@router.get("/career")
def squad_career_fragment(request: Request, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)
    overview = get_squad_overview(db, current_player.id, match_limit=None)
    return templates.TemplateResponse(
        request, "squad/_squad_sections.html", {"overview": overview, "scope": "career"}
    )
