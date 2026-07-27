from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import SESSION_KEY
from app.services.players import find_player_by_search_query, get_player_or_404, list_player_display_names
from app.templates import templates

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    players = list_player_display_names(db)
    return templates.TemplateResponse(request, "login.html", {"players": players})


@router.post("/login")
def login_submit(request: Request, display_name: str = Form(...), db: Session = Depends(get_db)):
    player = get_player_or_404(db, display_name)
    request.session[SESSION_KEY] = player.id
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse(url="/", status_code=303)


@router.get("/search")
def search(request: Request, q: str = "", db: Session = Depends(get_db)):
    player = find_player_by_search_query(db, q) if q else None
    if player is not None:
        return RedirectResponse(url=f"/players/{player.display_name}", status_code=303)
    return templates.TemplateResponse(
        request, "landing.html", {"search_query": q, "not_found": bool(q)}
    )
