"""Records pages that crash or load slowly as in-memory Site health events.

A plain `def` handler (Starlette runs an `async` one on the event loop). It only enqueues in memory
(`site_health.enqueue` never touches the database) and then returns the same 500 ServerErrorMiddleware sends.
"""

import logging
import time
import traceback

from fastapi import Request
from fastapi.responses import PlainTextResponse

from app.services import site_health

TRACEBACK_LINES = 15  # traceback lines kept per crash
SLOW_PAGE_S = 5.0
_clock = time.perf_counter
log = logging.getLogger(__name__)


SECRET_PARAMS = ("key", "token", "code", "secret")    # a path holding one of these is stored as its template


def safe_path(scope) -> str:
    """The request path to store: the route template when the route takes a secret in its path (the upload
    guide's `{key}`), else the path itself."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template and any("{" + name + "}" in template or "{" + name + ":" in template for name in SECRET_PARAMS):
        return template
    return scope.get("path", "")


def _join_slowest(stored, batch):
    """A flush's slow-page context joined with the stored one: the slowest ever, the last 5, every load."""
    stored, batch = stored or {}, batch or {}
    a = {"s": stored.get("slowest_s", 0), "path": stored.get("slowest_path")}
    b = {"s": batch.get("slowest_s", 0), "path": batch.get("slowest_path")}
    best = b if b["s"] > a["s"] else a
    return {"slowest_s": best["s"], "slowest_path": best["path"],
            "last": (stored.get("last", []) + batch.get("last", []))[-5:],
            "n": stored.get("n", 0) + batch.get("n", 0)}


def _keep_slowest(old, item):
    old = old or {}
    slowest = {"s": old.get("slowest_s", 0), "path": old.get("slowest_path")}
    if item["s"] > slowest["s"]:
        slowest = item
    return {
        "slowest_s": slowest["s"],
        "slowest_path": slowest["path"],
        "last": (old.get("last", []) + [item])[-5:],
        "n": old.get("n", 0) + 1,
    }


class SlowPageMiddleware:
    """Time full-page GETs through their final response body, recording only in memory."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return

        started = None
        try:
            from app.config import settings

            if not settings.demo_mode:
                started = _clock()
        except Exception:
            log.exception("could not start slow page timing")
        if started is None:
            await self.app(scope, receive, send)
            return

        finished = None

        async def timed_send(message):
            nonlocal finished
            await send(message)
            try:
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    finished = _clock()
            except Exception:
                log.exception("could not finish slow page timing")

        await self.app(scope, receive, timed_send)
        try:
            route = scope.get("route")
            if finished is None or route is None or site_health.PAGE_ROUTES.get(route.path) != "page":
                return
            seconds = finished - started
            if seconds > SLOW_PAGE_S:
                site_health.count("slow_page", route.path, {"s": round(seconds, 2), "path": safe_path(scope)},
                                  merge=_keep_slowest, combine=_join_slowest)
        except Exception:
            log.exception("could not record a slow page load")


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
            "path": safe_path(request.scope),
            "logged_in": bool("session" in request.scope and request.session.get("player_id")),
        }
        site_health.enqueue("page_error", _key(request), f"{type(exc).__name__}: {exc}", context)
    except Exception:  # a handler must never raise
        log.exception("could not record a page error")
    return PlainTextResponse("Internal Server Error", status_code=500)
