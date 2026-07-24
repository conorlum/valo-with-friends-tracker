from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_db
from app.routers import auth, friends, matches, players, sessions
from app.services.auth import get_current_player
from app.templates import templates

app = FastAPI(title="ValoMaths")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
app.include_router(auth.router)
app.include_router(friends.router)
app.include_router(matches.router)
app.include_router(players.router)
app.include_router(sessions.router)


@app.get("/")
def root(request: Request, db: Session = Depends(get_db)):
    current_player = get_current_player(request, db)
    if current_player is not None:
        return RedirectResponse(url=f"/players/{quote(current_player.display_name)}")
    return templates.TemplateResponse(request, "landing.html", {})


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/riot.txt")
@app.get("//riot.txt")
def riot_verification():
    return PlainTextResponse("f212a992-ace0-402a-838d-cad406c48fe2")
