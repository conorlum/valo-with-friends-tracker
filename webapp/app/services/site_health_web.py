"""Records pages that crash as `page_error` site events (W8).

A plain `def` handler (Starlette runs an `async` one on the event loop). It only enqueues in memory
(`site_health.enqueue` never touches the database) and then returns the same 500 ServerErrorMiddleware sends.
"""

import logging
import traceback

from fastapi import Request
from fastapi.responses import PlainTextResponse

from app.services import site_health

TRACEBACK_LINES = 15  # PROVISIONAL(D1)
log = logging.getLogger(__name__)


def _key(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return f"{request.method} {path}" if path else f"{request.method} (unrouted)"


def page_error_handler(request: Request, exc: Exception) -> PlainTextResponse:
    try:
        lines = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).splitlines()
        context = {
            "type": type(exc).__name__,
            "traceback": "\n".join(lines[-TRACEBACK_LINES:]),
            "path": request.url.path,
            "logged_in": bool("session" in request.scope and request.session.get("player_id")),
        }
        site_health.enqueue("page_error", _key(request), f"{type(exc).__name__}: {exc}", context)
    except Exception:  # a handler must never raise
        log.exception("could not record a page error")
    return PlainTextResponse("Internal Server Error", status_code=500)
