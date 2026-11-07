"""Critical Util's corpus build, off the request path.

The build (replay_util.ensure_values: the estimator if it is due, then every stored cast valued and compared with
the no-util baseline) takes minutes of CPU. A page or tab that finds the stored build stale or missing calls
`request()`, which starts `python -m app.services.util_rebuild` as a separate, lower-priority process, and shows
the last build meanwhile. A separate process keeps the build's CPU off the web server's interpreter lock, so the
rest of the site stays quick while it runs.

One build at a time: each process starts at most one child while its last is still running, and a child that
cannot take LOCK_KEY (a Postgres session lock: another process's child is building) exits at once. Waiting callers
(the coach, scripts/critical_util.py) still call ensure_values directly.

Site health: the child records its own failure, or clears the stored one after a build, at once (it exits). A child
that died without recording (killed, or its write failed) is found by the parent at its next request(), which
hands it to the next child (`--previous`): that child writes it before anything else, so the failure and a later
success's clear come from one process, in order. Only a child that can't be started leaves it to the parent's queue.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

log = logging.getLogger(__name__)

WEBAPP = Path(__file__).resolve().parents[2]
LOCK_KEY = 0x7574696C03         # "util" + 3, beside util_model.LOCK_IDS
NICENESS = 10

_lock = threading.Lock()
_child: subprocess.Popen | None = None
_child_started = 0.0
EXIT_RECORDED = 3       # the child recorded its own failure in site_events: the parent adds nothing


def _spawn(previous: dict | None = None) -> subprocess.Popen:
    kwargs = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = lambda: os.nice(NICENESS)
    args = [sys.executable, "-m", "app.services.util_rebuild"]
    if previous is not None:
        args += ["--previous", json.dumps(previous)]
    return subprocess.Popen(args, cwd=WEBAPP, **kwargs)


def running() -> bool:
    """Whether this process's last child is still building."""
    with _lock:
        return _child is not None and _child.poll() is None


def _read_finished(child, started) -> dict | None:
    """The failure of a child that died without recording it ({error, context, at}), else None. Never raises."""
    try:
        code = child.poll()
        if code is None or code in (0, EXIT_RECORDED):
            return None
        from datetime import datetime, timezone

        return {"error": f"the build exited with code {code}",
                "context": {"exit_code": code, "pid": getattr(child, "pid", None),
                            "ran_s": round(time.monotonic() - started)},
                "at": datetime.now(timezone.utc).isoformat()}
    except Exception:
        log.exception("could not read the Critical Util build's exit code")
        return None


def _queue_failure(previous: dict) -> None:
    """No child to hand a failure to: queue it in this process. Never raises."""
    try:
        from datetime import datetime

        from app.services import site_health

        site_health.enqueue("util_build_failed", "build", previous["error"], previous["context"],
                            at=datetime.fromisoformat(previous["at"]))
    except Exception:
        log.exception("could not queue the Critical Util build's failure")


def request() -> bool:
    """Start a background build unless this process's last one is still running. True when one was started.
    Never raises: a build that cannot start leaves the stored one in place, and the next visit tries again."""
    global _child, _child_started
    with _lock:
        if _child is not None and _child.poll() is None:
            return False
        previous = None
        if _child is not None:
            finished, _child = _child, None      # each finished child is read once
            previous = _read_finished(finished, _child_started)
        try:
            _child = _spawn(previous)
            _child_started = time.monotonic()
        except OSError:
            log.exception("could not start the Critical Util build")
            _child = None
            if previous is not None:
                _queue_failure(previous)
            return False
        log.info("started the Critical Util build (pid %s)", _child.pid)
        return True


def _previous(argv) -> dict | None:
    """The failed child the parent handed over (`--previous <json>`), or None."""
    if len(argv) >= 2 and argv[0] == "--previous":
        try:
            return json.loads(argv[1])
        except ValueError:
            log.warning("unreadable --previous: %r", argv[1])
    return None


def main(argv=None) -> int:
    from datetime import datetime

    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.services import mask_accel, replay_util, site_health

    previous = _previous(sys.argv[1:] if argv is None else argv)
    if previous is not None:      # before anything else: a clear this build makes must come after it
        site_health.record_now("util_build_failed", "build", previous.get("error"), previous.get("context"),
                               session_factory=SessionLocal, at=datetime.fromisoformat(previous["at"]))
    mask_accel.register()

    holder = None
    if engine.dialect.name == "postgresql":
        # a session-level lock on a connection of its own, held for the whole build across the build's commits
        holder = engine.connect()
        got = holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        holder.commit()
        if not got:
            holder.close()
            print("[util_rebuild] another process is building; nothing to do", flush=True)
            return 0
    try:
        try:
            code = _build(SessionLocal, replay_util)
        except Exception as exc:
            log.exception("the Critical Util build failed")
            tb = traceback.format_exc().splitlines()[-15:]
            recorded = site_health.record_now("util_build_failed", "build", f"{type(exc).__name__}: {exc}",
                                              {"traceback": tb}, session_factory=SessionLocal)
            return EXIT_RECORDED if recorded else 1     # not stored: the parent hands it to the next child
        site_health.resolve_now("util_build_failed", "build", session_factory=SessionLocal)
        return code
    finally:
        if holder is not None:      # close() only returns it to the pool: unlock first (the exit would too)
            try:
                holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                holder.commit()
            finally:
                holder.close()


def _build(SessionLocal, replay_util) -> int:
    with SessionLocal() as db:
        t0 = time.monotonic()
        data = replay_util.ensure_values(db)
        print(f"[util_rebuild] stored {len((data or {}).get('rows') or ())} valued casts "
              f"in {time.monotonic() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
