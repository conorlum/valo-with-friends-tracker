"""A maintenance switch that blocks traffic without suspending the service.

Rolling back the PR #67 merge means dropping columns the running code maps, so
the site must stop serving BEFORE the downgrade and start again only once the
reverted code is live. Suspending the Render service was the obvious way, but
its deploy behaviour while suspended is undocumented for this account, and a
recovery path must not rest on a guess.

So the site blocks itself: set MAINTENANCE_MODE=1 on the service, wait for the
deploy to go live, confirm pages return 503, and only then downgrade.

`/health` keeps answering, so the platform's own health check still passes and
an operator can tell "blocked on purpose" from "fallen over".

The reverted code will not contain this middleware. That is deliberate: its
deploy is what reopens the site, so there is no switch left to forget to turn
off.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import settings

#: Paths that answer even while blocked.
ALWAYS_OPEN = ("/health",)

MAINTENANCE_ENV = "MAINTENANCE_MODE"
MESSAGE = (f"{settings.site_name} is briefly down for a scoring update. "
           "Nothing is lost; matches are still being recorded.")


def maintenance_mode_enabled() -> bool:
    return os.environ.get(MAINTENANCE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


async def maintenance_middleware(request: Request, call_next):
    if not maintenance_mode_enabled() or request.url.path in ALWAYS_OPEN:
        return await call_next(request)
    if request.url.path.startswith("/static/"):
        return await call_next(request)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "maintenance", "detail": MESSAGE}, status_code=503)
    return PlainTextResponse(MESSAGE, status_code=503,
                             headers={"Retry-After": "900", "Cache-Control": "no-store"})
