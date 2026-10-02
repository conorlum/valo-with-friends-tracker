"""The operator scripts' HTTP client for the site's /admin/replays routes (app/routers/replay_admin.py).

Standard library only. The token comes from the REPLAY_ADMIN_TOKEN environment variable, never an
argument (it would land in shell history) or a file in the repo (which is public).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_SITE = "https://valowithfriendstracker.onrender.com"


class AdminError(Exception):
    pass


def token() -> str:
    value = os.environ.get("REPLAY_ADMIN_TOKEN", "").strip()
    if not value:
        raise AdminError("set REPLAY_ADMIN_TOKEN in the environment (the value from the Render dashboard)")
    return value


def call(site: str, method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{site.rstrip('/')}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {token()}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        if error.code == 404 and not detail:
            raise AdminError("404: the admin routes are off (no REPLAY_ADMIN_TOKEN on the site) or the token "
                             "is wrong") from error
        raise AdminError(f"{error.code}: {detail or error.reason}") from error
    except (urllib.error.URLError, OSError) as error:
        raise AdminError(f"the site is unreachable: {error}") from error
