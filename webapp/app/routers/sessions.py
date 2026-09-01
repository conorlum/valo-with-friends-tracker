import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import get_current_player
from app.services.friends import list_friend_ids
from app.services.session_stats import get_session_stats
from app.services.sessions import find_session_index_for_matches, get_session_or_404, list_sessions
from app.templates import templates

router = APIRouter(prefix="/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


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
    t_start = time.perf_counter()
    current_player = get_current_player(request, db)
    if current_player is None:
        return RedirectResponse(url="/login", status_code=303)
    player_ids = _scoped_player_ids(db, current_player.id, friends)
    t0 = time.perf_counter()
    session = get_session_or_404(db, session_index, player_ids)
    t1 = time.perf_counter()
    stats = get_session_stats(db, session, current_player.id)
    t2 = time.perf_counter()
    matches_by_id = {m.id: m for m in session.matches}

    # The friends-scope toggle links to this same session under the opposite
    # scope -- its index there isn't the same number, and the two scopes don't
    # even hold the same matches (see find_session_index_for_matches), so
    # resolve it here rather than reusing session_index and risking a 404 or
    # the wrong session. None means the opposite scope has nothing to show --
    # under "just mine" that's a session the viewer sat out entirely -- and the
    # template drops the toggle rather than bouncing them to the session list.
    other_player_ids = _scoped_player_ids(db, current_player.id, not friends)
    other_sessions = list_sessions(db, other_player_ids)
    t3 = time.perf_counter()
    other_session_index = find_session_index_for_matches(
        other_sessions, [m.id for m in session.matches]
    )
    t4 = time.perf_counter()

    response = templates.TemplateResponse(
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
    t5 = time.perf_counter()
    logger.info(
        "session_detail session_index=%d friends=%s player_ids=%d get_session=%.3fs stats=%.3fs "
        "other_sessions=%.3fs find_index=%.3fs render=%.3fs total=%.3fs",
        session_index, friends, len(player_ids),
        t1 - t0, t2 - t1, t3 - t2, t4 - t3, t5 - t4, t5 - t_start,
    )
    return response
