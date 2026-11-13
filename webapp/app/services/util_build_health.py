"""Queued health events and process-local build state for Critical Util.

Keep imports light: the Site health card must not load the remote builder or the
Critical Util engine just to check whether a build is in flight.
"""

from app.services import site_health, util_rebuild


KEYS = ("dispatch", "push", "run", "collect", "store", "gave_up", "digest")

_in_flight = False


def record(key, error, context=None) -> None:
    """Queue a remote build failure under one of the fixed health keys."""
    if key not in KEYS:
        raise ValueError(f"unknown Critical Util build health key: {key!r}")
    site_health.record("util_build_failed", f"remote:{key}", error, context)


def clear_all() -> None:
    """A successful build clears remote failures and the local child's failure."""
    for key in KEYS:
        site_health.resolve("util_build_failed", f"remote:{key}")
    site_health.resolve("util_build_failed", "build")


def set_in_flight(value: bool) -> None:
    """Set whether this process has a remote build in flight."""
    global _in_flight
    _in_flight = value


def in_flight() -> bool:
    """Whether a remote build or this process's local child is still building."""
    return _in_flight or util_rebuild.running()
