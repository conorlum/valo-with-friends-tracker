from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.friends import add_friend, list_acquaintances, list_friends, remove_friend
from app.services.players import get_player_or_404, list_player_display_names
from app.templates import templates

router = APIRouter(prefix="/friends", tags=["friends"])


@router.get("")
def friends_page(request: Request, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    friends = list_friends(db, current_player.id)
    friend_names = {f.display_name for f in friends}
    friend_ids = {f.id for f in friends}
    acquaintances = [a for a in list_acquaintances(db, current_player.id) if a.player.id not in friend_ids]
    all_players = [
        name
        for name in list_player_display_names(db)
        if name != current_player.display_name and name not in friend_names
    ]
    return templates.TemplateResponse(
        request,
        "friends.html",
        {"friends": friends, "acquaintances": acquaintances, "all_players": all_players},
    )


def _safe_next(next: str | None) -> str:
    """Only allow redirecting back to a local path (never an absolute URL,
    to avoid becoming an open redirect)."""
    if next and next.startswith("/") and not next.startswith("//"):
        return next
    return "/friends"


@router.post("/add")
def add_friend_route(
    request: Request, display_name: str = Form(...), next: str | None = Form(None), db: Session = Depends(get_db)
):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    friend = get_player_or_404(db, display_name)
    add_friend(db, current_player.id, friend.id)
    return RedirectResponse(url=_safe_next(next), status_code=303)


@router.post("/remove")
def remove_friend_route(request: Request, display_name: str = Form(...), db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    friend = get_player_or_404(db, display_name)
    remove_friend(db, current_player.id, friend.id)
    return RedirectResponse(url="/friends", status_code=303)
