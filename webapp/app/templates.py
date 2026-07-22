from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.db import SessionLocal
from app.services.auth import get_current_player


def _inject_current_player(request: Request) -> dict:
    db = SessionLocal()
    try:
        return {"current_player": get_current_player(request, db)}
    finally:
        db.close()


templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates"),
    context_processors=[_inject_current_player],
)

_AGENT_ICON_SLUGS = {
    "brimstone", "chamber", "clove", "cypher", "deadlock", "fade", "gekko",
    "iso", "jett", "kayo", "killjoy", "neon", "omen", "phoenix", "raze",
    "reyna", "sova", "veto", "yoru",
}


def agent_icon_slug(agent: str | None) -> str | None:
    """Maps an agent name (e.g. "KAY/O") to its icon filename slug, or None if no icon exists."""
    if not agent:
        return None
    slug = agent.replace("/", "").lower()
    return slug if slug in _AGENT_ICON_SLUGS else None


templates.env.globals["agent_icon_slug"] = agent_icon_slug


def match_label(match) -> str:
    """Human-friendly label for a match link, e.g. "Haven 10/17/25 9:08 PM"."""
    name = match.map_name or match.external_id
    played = match.played_at
    if not played:
        return name
    date_str = played.strftime("%m/%d/%y")
    hour = played.strftime("%I").lstrip("0") or "12"
    time_str = f"{hour}:{played.strftime('%M %p')}"
    return f"{name} {date_str} {time_str}"


templates.env.globals["match_label"] = match_label


def strip_tag(display_name: str | None) -> str:
    """Drops the "#Tag" suffix from a Riot ID for display -- the full name
    (used for player lookups/links) is kept wherever it's needed for that."""
    if not display_name:
        return display_name
    return display_name.split("#", 1)[0]


templates.env.filters["strip_tag"] = strip_tag

_STYLE_CSS_PATH = Path(__file__).resolve().parent / "static" / "css" / "style.css"


def static_version() -> int:
    """Cache-busting token for style.css, derived from its mtime so edits take effect immediately."""
    try:
        return int(_STYLE_CSS_PATH.stat().st_mtime)
    except OSError:
        return 0


templates.env.globals["static_version"] = static_version
