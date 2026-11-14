import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_db
from app.maintenance import maintenance_middleware
from app.routers import (accounts, auth, coach, deploy_admin, friends, gap_patterns, matches, players, replay_admin,
                         replays, sessions, site_stats, squad, util_page)
from app.services.auth import get_current_player
from app.services.site_health_web import SlowPageMiddleware, page_error_handler
from app.services.upload_activity import UploadActivityMiddleware
from app.templates import templates

# INFO-level timing logs (see app.routers.sessions / app.services.sessions /
# app.services.session_stats) otherwise wouldn't reach Render's log stream --
# uvicorn configures its own loggers, but the root logger defaults to WARNING.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Map control for new replays on the replay worker: off unless REPLAY_CONTROL_REMOTE is set
    # (app/services/replay_control_remote.py; the engine is never imported by the web app).
    # The same thread runs the automatic re-parse queue's pass (app/services/replay_reparse_auto.py):
    # with REPLAY_REPARSE_AUTO off it starts nothing new, but still settles an attempt the worker
    # already accepted, so switching off never strands one.
    # The .vrf archive's sync (acks re-sent, tombstones pushed): on whenever uploads are
    # (app/services/replay_archive_sync.py); it does nothing while the worker's archive is off.
    from app.db import SessionLocal
    # The Competitive map pool's daily patch-notes check (app/services/map_pool_refresh.py); off unless MAP_POOL_AUTO.
    # The per-match state table the match lists read (app/services/replay_match_states.py): on whenever not in demo mode.
    # Missing per-kill Impact splits (app/services/kill_impact_sweep.py): on whenever not in demo mode.
    from app.services import (kill_impact_sweep, map_pool_refresh, replay_archive_sync, replay_control_remote, replay_match_states,
                              util_build_remote)
    # numpy for the map-mask decode every replay freshness check needs (app/services/mask_accel.py): app/replays
    # stays standard library only, so the web app hands it the fast path.
    from app.services import mask_accel

    from datetime import datetime, timezone

    from app.services import site_health

    started_at = datetime.now(timezone.utc)
    mask_accel.register()
    stops = []
    health_stop = None
    try:
        for service in (replay_control_remote, replay_archive_sync, map_pool_refresh, replay_match_states,
                        util_build_remote, kill_impact_sweep):
            stops.append(service.start(SessionLocal))
        # The flush thread writes what the site recorded and applies app/data/site_health_resolved.json (once
        # each); startup itself never waits on the database for it. None in demo mode.
        health_stop = site_health.start(SessionLocal, started_at=started_at)
        yield
    finally:
        for stop in stops:
            if stop is not None:
                stop.set()
        # A loop in the middle of a pass finishes it (up to 10 s), so the final flush takes what it recorded.
        site_health.join_loops(10)
        site_health.stop(health_stop)


app = FastAPI(title=settings.site_name, lifespan=lifespan)
# A page that crashes is recorded as a page_error event (in memory only), then gets the usual plain 500.
app.add_exception_handler(Exception, page_error_handler)
# Blocks every page but /health while MAINTENANCE_MODE is set, so a rollback can
# stop traffic without depending on how the platform behaves while suspended.
app.middleware("http")(maintenance_middleware)
app.add_middleware(SlowPageMiddleware)
# Counts POST /replays/upload requests in flight, for the deploy drain (app/services/upload_activity.py).
app.add_middleware(UploadActivityMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.session_cookie_https_only,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
app.include_router(accounts.router)
app.include_router(auth.router)
app.include_router(friends.router)
app.include_router(matches.router)
app.include_router(players.router)
# Replays: every route 404s in demo mode (docs/replay-viewer-plan.md, decision 4).
app.include_router(replay_admin.router)
app.include_router(deploy_admin.router)
app.include_router(coach.router)
app.include_router(replays.router)
app.include_router(gap_patterns.router)
app.include_router(util_page.router)
app.include_router(sessions.router)
app.include_router(site_stats.router)
app.include_router(squad.router)


@app.get("/")
def root(request: Request, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is not None:
        return RedirectResponse(url=f"/players/{quote(current_player.display_name)}")
    return templates.TemplateResponse(request, "landing.html", {})


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT")}


# Checked per request rather than at import, so the setting can be toggled in tests.
@app.get("/riot.txt")
@app.get("//riot.txt")
def riot_verification():
    if not settings.enable_riot_txt:
        raise HTTPException(status_code=404)
    return PlainTextResponse("f212a992-ace0-402a-838d-cad406c48fe2")
