from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.friends import list_friend_ids
from app.services.session_stats import get_session_stats
from app.services.sessions import find_session_index_containing_match, get_session_or_404, list_sessions
from app.templates import templates

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _scoped_player_ids(db: Session, current_player_id: int, include_friends: bool) -> list[int]:
    if not include_friends:
        return [current_player_id]
    return [current_player_id, *list_friend_ids(db, current_player_id)]


@router.get("")
def session_list(request: Request, friends: bool = True, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)
    player_ids = _scoped_player_ids(db, current_player.id, friends)
    sessions = [s for s in reversed(list_sessions(db, player_ids)) if s.is_multi_match]
    return templates.TemplateResponse(
        request, "sessions/list.html", {"sessions": sessions, "friends_enabled": friends}
    )


@router.get("/{session_index}")
def session_detail(
    request: Request, session_index: int, friends: bool = True, db: Session = Depends(get_db)
):
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)
    player_ids = _scoped_player_ids(db, current_player.id, friends)
    session = get_session_or_404(db, session_index, player_ids)
    stats = get_session_stats(db, session, current_player.id)
    matches_by_id = {m.id: m for m in session.matches}

    # The friends-scope toggle links to this same session under the opposite
    # scope -- its index there isn't the same number (see
    # find_session_index_containing_match), so resolve it here rather than
    # reusing session_index and risking a 404 or the wrong session.
    other_player_ids = _scoped_player_ids(db, current_player.id, not friends)
    other_sessions = list_sessions(db, other_player_ids)
    other_session_index = (
        find_session_index_containing_match(other_sessions, session.matches[0].id) if session.matches else None
    )

    return templates.TemplateResponse(
        request,
        "sessions/detail.html",
        {
            "session": session,
            "stats": stats,
            "matches_by_id": matches_by_id,
            "friends_enabled": friends,
            "other_session_index": other_session_index,
        },
    )
