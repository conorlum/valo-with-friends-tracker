"""Wait for the owner to clear tracker.gg's human verification, audio only."""

from pathlib import Path
import sys
import time


VERIFY_TIMEOUT_S = 900
BEEP_EVERY_S = 60


class TrackerVerificationTimeout(RuntimeError):
    """The owner did not clear human verification within the allowed wait."""


class TrackerMatchUnavailable(RuntimeError):
    """The match is permanently unavailable from tracker.gg (404 or 410)."""


def is_verification_page(page):
    """Only the page title identifies verification; unavailable titles are ignored."""
    try:
        return "just a moment" in page.title().lower()
    except Exception:
        return False


def beep():
    """Sound an alert on Windows unless the owner's shared mute flag exists."""
    if sys.platform != "win32" or (Path.home() / ".claude" / "sounds" / ".muted").exists():
        return
    try:
        import winsound

        winsound.Beep(880, 300)
    except (ImportError, OSError, RuntimeError):
        pass


def wait_for_owner(page, *, timeout_s=VERIFY_TIMEOUT_S, clock=None, pause=None, alert=None):
    """Poll once a second, keeping Playwright events pumping while the owner acts.

    `pause` takes milliseconds, like page.wait_for_timeout. The injectable clock
    measures elapsed seconds with the same monotonic semantics as time.monotonic.
    """
    if not is_verification_page(page):
        return 0
    clock = clock if clock is not None else time.monotonic
    pause = pause if pause is not None else page.wait_for_timeout
    alert = alert if alert is not None else beep
    started = clock()
    next_alert = 0
    print("tracker.gg human verification: clear the check in the browser; waiting up to 15 minutes.")
    while True:
        waited = clock() - started
        if waited >= timeout_s:
            raise TrackerVerificationTimeout(
                f"tracker.gg human verification was not cleared within {timeout_s}s"
            )
        if waited >= next_alert:
            alert()
            next_alert += BEEP_EVERY_S
        pause(min(1, timeout_s - waited) * 1000)
        if not is_verification_page(page):
            return clock() - started
