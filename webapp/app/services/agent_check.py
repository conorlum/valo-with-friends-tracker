"""Daily playable-agent inventory check, disabled in demo mode."""
import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from app.config import settings
from app.replays import condense
from app.services import deploy_drain, site_health

log = logging.getLogger(__name__)

API_URL = "https://valorant-api.com/v1/agents?isPlayableCharacter=true"
USER_AGENT = "valo-with-friends-tracker agent check (+https://valowithfriendstracker.onrender.com)"
LOOP_NAME = "agent-check"
CHECK_KEY = "agent_check:check"
MIN_GAP_S = 24 * 3600
ALLOWED_QUIET_S = 26 * 3600
DRAIN_RETRY_S = 300
KIND = "agent_unsupported"

_last_check: float | None = None


def enabled() -> bool:
    return bool(settings.agent_check_auto and not settings.demo_mode)


def http_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def check(fetch: Callable[[str], str], now: datetime | None = None, *, session_factory=None) -> dict:
    """Validate the whole inventory, then queue health updates. Sessions only read the drain."""
    counts = {"listed": 0, "missing": 0, "skipped": False}
    try:
        if session_factory is None:
            paused = deploy_drain.paused_now()
        else:
            with session_factory() as db:
                paused = deploy_drain.paused(db, now or datetime.now(timezone.utc))
        if paused:
            counts["skipped"] = True
            return counts

        data = json.loads(fetch(API_URL))["data"]
        if not isinstance(data, list):
            raise ValueError("agent data must be a list")
        agents = []
        for entry in data:
            if not isinstance(entry, dict):
                raise ValueError("agent entry must be an object")
            code, name = entry.get("developerName"), entry.get("displayName")
            if not isinstance(code, str) or not code.strip() or not isinstance(name, str) or not name.strip():
                raise ValueError("agent entry requires developerName and displayName")
            if entry.get("isPlayableCharacter"):
                role = entry.get("role") or {}
                agents.append((code, name, role.get("displayName")))
        supported = {code.casefold() for code in condense.load_agents()}
    except Exception as exc:  # noqa: BLE001
        site_health.loop_failed(CHECK_KEY, exc)
        return counts

    for code, name, role in agents:
        counts["listed"] += 1
        if code.casefold() in supported:
            site_health.resolve(KIND, code)
        else:
            counts["missing"] += 1
            site_health.record(KIND, code, f"{name} ({code}) is not supported yet",
                               {"display_name": name, "role": role})
    site_health.loop_ok(CHECK_KEY)
    return counts


def run_forever(session_factory, fetch, stop: threading.Event, every_s: float = 86400,
                first_delay_s: float = 60) -> None:
    global _last_check
    site_health.register(LOOP_NAME, every_s, ALLOWED_QUIET_S)
    try:
        if stop.wait(first_delay_s):
            return
        while True:
            delay = every_s
            if _last_check is None or time.monotonic() - _last_check >= MIN_GAP_S:
                site_health.beat(LOOP_NAME, "start")
                try:
                    counts = check(fetch, session_factory=session_factory)
                    if counts.get("skipped"):
                        delay = DRAIN_RETRY_S
                    else:
                        _last_check = time.monotonic()
                        log.info("agent check: %s", counts)
                        site_health.loop_ok(LOOP_NAME)
                except Exception as exc:  # noqa: BLE001
                    _last_check = time.monotonic()
                    log.exception("agent check failed")
                    site_health.loop_failed(LOOP_NAME, exc)
                site_health.beat(LOOP_NAME, "end")
            if stop.wait(delay):
                return
    finally:
        if stop.is_set():
            site_health.unregister(LOOP_NAME)


def start(session_factory) -> threading.Event | None:
    if not enabled():
        return None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(session_factory, http_fetch, stop), name=LOOP_NAME,
                     daemon=True).start()
    log.info("agent check on: daily")
    return stop
