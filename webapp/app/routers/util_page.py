"""The cross-match Critical Util page (docs/superpowers/specs/2026-10-21-critical-util-design.md, section 2).

`GET /util`: for the logged-in viewer's circle (app/services/circle.py), which util wins rounds, a score and a
plain call per lineup, each friend's best and worst casts with links into the replay, and their patterns. 404 in
demo mode, like every replay route; logged out, it asks for a login (friends only, owner decision)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.player import Player
from app.services import circle
from app.services import replay_util
from app.services import replays as replay_service
from app.services.auth import get_current_player
from app.templates import templates

router = APIRouter(tags=["util"])

LOGIN_NOTE = "Critical Util shows you and your friends' utility, so it needs a login."
THIN_NOTE = ("These come from the uploaded replays only, so samples are small: every figure shows how many casts "
             "and matches it is out of. Values are the share of the change in your side's chance to win the round "
             "(percentage points) credited to each cast; scores compare what followed each cast with moments in the "
             "same state with no such util up: evidence, not proof.")
PHASE_WORDS = {"pre_contact": "before first contact", "contact": "after contact", "post_plant": "after the plant"}


@router.get("/util")
def util_page(request: Request, db: Session = Depends(get_db), mode: str = "yours"):
    mode = mode if mode in ("yours", "all") else "yours"
    if not replay_service.replays_enabled():
        raise HTTPException(status_code=404)
    viewer = get_current_player(request, db)
    if viewer is None:
        return templates.TemplateResponse(request, "util/page.html", {"login_note": LOGIN_NOTE, "page": None})
    corpus = replay_util.ensure_values(db)
    ids = circle.circle_ids(db, viewer.id) | {viewer.id}
    page = replay_util.page_for(corpus, ids, mode)
    names = {p.id: p.display_name for p in db.query(Player.id, Player.display_name).filter(Player.id.in_(list(ids)))}
    for c in page["lineups"]:
        c["name"] = names.get(c["player_id"], "?")
    players = sorted(({"name": names.get(pid, "?"), **block} for pid, block in page["players"].items()),
                     key=lambda p: (p["name"] != names.get(viewer.id), p["name"].lower()))
    return templates.TemplateResponse(request, "util/page.html", {
        "page": page, "players": players, "thin_note": THIN_NOTE, "phase_words": PHASE_WORDS, "login_note": None,
        "mode": mode,
    })
