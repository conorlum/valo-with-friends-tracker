from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services.account_groups import (
    link_account,
    linked_display_names,
    list_group_members,
    make_main_account,
    set_account_label,
    unlink_account,
)
from app.services.auth import get_current_player
from app.services.circle import circle_dependents
from app.services.player_view_cache import invalidate_player_cache
from app.services.players import get_player_or_404, list_player_display_names
from app.services.viewer_site_stats_cache import invalidate_viewer_site_stats
from app.templates import templates

router = APIRouter(prefix="/accounts", tags=["accounts"])

DEADLOCK_PGCODE = "40P01"
LOST_RACE_MESSAGE = "That account was just linked to another group."


def _reject_in_demo_mode() -> None:
    """The demo's sample players aren't anyone's accounts to group."""
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Accounts can't be grouped on the demo.")


def _invalidate_circles(db: Session, account_ids: set[int]) -> None:
    """Clears the cached pages of every account whose circle the link change
    can touch. Call it while one group holds both accounts (after a link,
    before an unlink): that group's dependents cover both groupings. Does NOT commit."""
    dependents = circle_dependents(db, account_ids)
    invalidate_player_cache(db, dependents)
    for player_id in dependents:
        invalidate_viewer_site_stats(db, player_id)


@router.get("")
def accounts_page(request: Request, error: str | None = None, db: Session = Depends(get_db)):
    _reject_in_demo_mode()
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    members = list_group_members(db, current_player.id)
    if len(members) < 2:   # a group left with only its main (a sub's player row was deleted) is no group
        members = []
    main = members[0] if members else None
    is_main = main is None or main.player_id == current_player.id
    # Accounts already linked anywhere can't be added, so they aren't offered.
    unavailable = linked_display_names(db) | {current_player.display_name}
    all_players = [name for name in list_player_display_names(db) if name not in unavailable]
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {"members": members, "main": main, "is_main": is_main, "all_players": all_players, "error": error},
    )


@router.post("/add")
def add_account_route(request: Request, display_name: str = Form(...), db: Session = Depends(get_db)):
    _reject_in_demo_mode()
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    other = get_player_or_404(db, display_name)
    try:
        error = link_account(db, current_player.id, other.id)
        if not error:
            # Linking joins two groups: the new group's dependents are both old ones'.
            _invalidate_circles(db, {current_player.id, other.id})
            db.commit()
    except (IntegrityError, OperationalError) as exc:
        # Two links at once: the loser's member row collides with the winner's
        # (unique violation), or Postgres picked it as a deadlock victim.
        if isinstance(exc, OperationalError) and getattr(exc.orig, "pgcode", None) != DEADLOCK_PGCODE:
            raise
        db.rollback()
        return accounts_page(request, error=LOST_RACE_MESSAGE, db=db)
    if error:
        db.rollback()
        return accounts_page(request, error=error, db=db)
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/remove")
def remove_account_route(request: Request, display_name: str = Form(...), db: Session = Depends(get_db)):
    _reject_in_demo_mode()
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    other = get_player_or_404(db, display_name)
    # Before the write, while the group still holds both accounts; after it,
    # the two are apart and their circles are a subset of that.
    _invalidate_circles(db, {current_player.id, other.id})
    unlink_account(db, current_player.id, other.id)
    db.commit()
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/make-main")
def make_main_route(request: Request, display_name: str = Form(...), db: Session = Depends(get_db)):
    _reject_in_demo_mode()
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    other = get_player_or_404(db, display_name)
    make_main_account(db, current_player.id, other.id)
    db.commit()
    return RedirectResponse(url="/accounts", status_code=303)


@router.post("/label")
def label_account_route(
    request: Request, display_name: str = Form(...), label: str = Form(""), db: Session = Depends(get_db)
):
    _reject_in_demo_mode()
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)

    other = get_player_or_404(db, display_name)
    set_account_label(db, current_player.id, other.id, label)
    db.commit()
    return RedirectResponse(url="/accounts", status_code=303)
