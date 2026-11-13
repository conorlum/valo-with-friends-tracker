"""The site's upload counter (deploy drain, R7): how many `POST /replays/upload` requests are streaming in right
now, and how long ago the last one ended. In-process, in memory: a restart starts at zero, which is true (a
restart drops every open request). An ASGI middleware, so it counts from the first byte of the request to the end
of the response, before any route code runs."""

import threading
import time

_lock = threading.Lock()
_receiving = 0
_last_end: float | None = None      # time.monotonic() of the last upload's end


def receiving() -> int:
    with _lock:
        return _receiving


def last_end_age_s() -> float | None:
    """Seconds since the last upload ended; None when none has ended since this process started."""
    with _lock:
        return None if _last_end is None else max(0.0, time.monotonic() - _last_end)


def reset() -> None:
    """Tests only."""
    global _receiving, _last_end
    with _lock:
        _receiving, _last_end = 0, None


def _begin() -> None:
    global _receiving
    with _lock:
        _receiving += 1


def _end() -> None:
    global _receiving, _last_end
    with _lock:
        _receiving = max(0, _receiving - 1)
        _last_end = time.monotonic()


class UploadActivityMiddleware:
    PATH = "/replays/upload"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope.get("path") != self.PATH:
            await self.app(scope, receive, send)
            return
        _begin()
        try:
            await self.app(scope, receive, send)
        finally:
            _end()
