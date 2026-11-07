"""The operator scripts' HTTP client for the site's /admin/replays routes (app/routers/replay_admin.py).

Standard library only. The token comes from the REPLAY_ADMIN_TOKEN environment variable, never an
argument (it would land in shell history) or a file in the repo (which is public).
"""

from __future__ import annotations

import json
import os
from http.client import HTTPResponse
import urllib.error
import urllib.request

DEFAULT_SITE = "https://valowithfriendstracker.onrender.com"


class AdminError(Exception):
    pass


class ArchiveNotFound(AdminError):
    """A coded archive 404, distinct from the deliberately hidden admin gate."""

    def __init__(self, code: str, reason: str):
        self.code = code
        self.reason = reason
        super().__init__(f"404: {code}: {reason}")


def redact(message: str) -> str:
    value = os.environ.get("REPLAY_ADMIN_TOKEN", "").strip()
    return message.replace(value, "[redacted]") if value else message


def token() -> str:
    value = os.environ.get("REPLAY_ADMIN_TOKEN", "").strip()
    if not value:
        raise AdminError("set REPLAY_ADMIN_TOKEN in the environment (the value from the Render dashboard)")
    return value


def _open(site: str, method: str, path: str, body: dict | None, timeout: float) -> HTTPResponse:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{site.rstrip('/')}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {token()}",
                                              "Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        try:
            with error:
                payload = json.loads(error.read() or b"{}")
            detail = payload.get("detail") if isinstance(payload, dict) else None
        except ValueError:
            detail = None
        if error.code == 404 and isinstance(detail, dict) and detail.get("code"):
            raise ArchiveNotFound(redact(str(detail["code"])), redact(str(detail.get("reason") or ""))) from error
        if error.code == 404 and (not detail or detail == "Not Found"):
            raise AdminError("404: the admin routes are off or the token is wrong "
                             "(or the site is an older build)") from error
        raise AdminError(redact(f"{error.code}: {detail or error.reason}")) from error
    except (urllib.error.URLError, OSError) as error:
        raise AdminError(redact(f"the site is unreachable: {error}")) from error


def call(site: str, method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    try:
        with _open(site, method, path, body, timeout) as response:
            return json.loads(response.read())
    except ValueError as error:
        raise AdminError(redact(f"the site gave a non-JSON answer for {path}")) from error
    except (urllib.error.URLError, OSError) as error:
        raise AdminError(redact(f"the site is unreachable: {error}")) from error


def download(site: str, path: str, timeout: float = 60) -> HTTPResponse:
    """Open an authenticated GET stream; the caller must close the response."""
    return _open(site, "GET", path, None, timeout)
