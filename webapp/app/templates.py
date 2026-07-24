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
    played = played.astimezone()
    date_str = played.strftime("%m/%d/%y")
    hour = played.strftime("%I").lstrip("0") or "12"
    time_str = f"{hour}:{played.strftime('%M %p')}"
    return f"{name} {date_str} {time_str}"


templates.env.globals["match_label"] = match_label


def local_strftime(dt, fmt: str) -> str:
    """Formats a tz-aware datetime in the local system timezone -- stored
    timestamps are UTC, and this app runs local-only, so "local" is
    unambiguous."""
    if dt is None:
        return ""
    return dt.astimezone().strftime(fmt)


templates.env.filters["local_strftime"] = local_strftime


def strip_tag(display_name: str | None) -> str:
    """Drops the "#Tag" suffix from a Riot ID for display -- the full name
    (used for player lookups/links) is kept wherever it's needed for that."""
    if not display_name:
        return display_name
    return display_name.split("#", 1)[0]


templates.env.filters["strip_tag"] = strip_tag


def balanced_rows(items: list, max_per_row: int = 4) -> list[list]:
    """Splits items into the fewest rows possible without exceeding
    max_per_row, with row sizes as even as possible (extra items go to the
    earliest rows) -- so a card grid's last row is never a lonely leftover,
    e.g. 7 items become rows of [4, 3] rather than [4, 4, ...-1] or a
    half-empty final row of [4, 4, 4] padding.
    """
    n = len(items)
    if n == 0:
        return []
    num_rows = -(-n // max_per_row)  # ceil division
    base, remainder = divmod(n, num_rows)
    rows = []
    index = 0
    for row_index in range(num_rows):
        size = base + 1 if row_index < remainder else base
        rows.append(items[index : index + size])
        index += size
    return rows


templates.env.filters["balanced_rows"] = balanced_rows

_STYLE_CSS_PATH = Path(__file__).resolve().parent / "static" / "css" / "style.css"


def static_version() -> int:
    """Cache-busting token for style.css, derived from its mtime so edits take effect immediately."""
    try:
        return int(_STYLE_CSS_PATH.stat().st_mtime)
    except OSError:
        return 0


templates.env.globals["static_version"] = static_version
