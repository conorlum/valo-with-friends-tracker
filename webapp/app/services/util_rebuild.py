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
hands it to the next child (`--previous`, a list): that child writes them before anything else, so a failure and a
later success's clear come from one process, in order. The parent keeps every handed failure until a child's exit
code says it stored them (EXIT_STORED_PREVIOUS), and hands them on again otherwise; each carries an id the write
deduplicates on, so one handed twice (a child killed after its write committed) is stored once. A failure is timed
when its child exited (a watcher thread), not when the parent noticed. Only a child that can't be started leaves
them to the parent's queue, and a clear the child couldn't store (EXIT_UNCLEARED) is queued by the parent.
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
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

WEBAPP = Path(__file__).resolve().parents[2]
LOCK_KEY = 0x7574696C03         # "util" + 3, beside util_model.LOCK_IDS
NICENESS = 10

_lock = threading.Lock()
_child: subprocess.Popen | None = None
_child_started = 0.0
_pending: list = []     # failures handed to a child that hasn't confirmed storing them
PENDING_MAX = 5
SEEN_IDS = 50

EXIT_RECORDED = 3       # the build failed and the child stored it: the parent adds nothing
EXIT_PREVIOUS_UNSTORED = 4      # the handed failures could not be stored: nothing built, the parent keeps them
EXIT_UNCLEARED = 5      # the build is stored but its clear failed: the parent queues the clear
EXIT_UNRECORDED = 6     # the build failed and its record failed: the parent records the exit
# exit codes after which the handed failures are stored (any other code, a crash or a kill, may come before)
EXIT_STORED_PREVIOUS = frozenset({0, EXIT_RECORDED, EXIT_UNCLEARED, EXIT_UNRECORDED})


def _spawn(previous: list | None = None) -> subprocess.Popen:
    kwargs = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = lambda: os.nice(NICENESS)
    args = [sys.executable, "-m", "app.services.util_rebuild"]
    if previous:
        args += ["--previous", json.dumps(previous)]
    return subprocess.Popen(args, cwd=WEBAPP, **kwargs)


def _watch(child) -> None:
    """Note when the child exits (monotonic and UTC), so its failure is timed then, not when it is read."""
    def run():
        try:
            child.wait()
            from datetime import datetime, timezone

            child.exited = (time.monotonic(), datetime.now(timezone.utc))
        except Exception:
            pass

    threading.Thread(target=run, name="util-rebuild-watch", daemon=True).start()


def running() -> bool:
    """Whether this process's last child is still building."""
    with _lock:
        return _child is not None and _child.poll() is None


def _exited(child):
    from datetime import datetime, timezone

    exited = getattr(child, "exited", None)
    return exited if exited is not None else (time.monotonic(), datetime.now(timezone.utc))


def _read_finished(child, started) -> dict | None:
    """The failure of a child that died without recording it ({id, error, context, at}), else None. Never
    raises."""
    try:
        code = child.poll()
        if code is None or code in (0, EXIT_RECORDED, EXIT_UNCLEARED, EXIT_PREVIOUS_UNSTORED):
            return None
        mono, at = _exited(child)
        return {"id": uuid.uuid4().hex, "error": f"the build exited with code {code}",
                "context": {"exit_code": code, "pid": getattr(child, "pid", None),
                            "ran_s": round(max(0.0, mono - started))},
                "at": at.isoformat()}
    except Exception:
        log.exception("could not read the Critical Util build's exit code")
        return None


def _seen_ids(seen, context):
    """Site health's dedupe for a handed failure: None when its id is already stored, else the ids kept."""
    ids = list((seen or {}).get("ids") or []) if isinstance(seen, dict) else []
    failure_id = (context or {}).get("failure_id")
    if failure_id is not None and failure_id in ids:
        return None
    return {"ids": (ids + [failure_id])[-SEEN_IDS:]}


def _context(failure: dict) -> dict:
    return {**(failure.get("context") or {}), "failure_id": failure.get("id")}


def _queue_failure(failure: dict) -> None:
    """No child to hand a failure to: queue it in this process. Never raises."""
    try:
        from datetime import datetime

        from app.services import site_health

        site_health.enqueue("util_build_failed", "build", failure["error"], _context(failure),
                            at=datetime.fromisoformat(failure["at"]), dedupe=_seen_ids)
    except Exception:
        log.exception("could not queue the Critical Util build's failure")


def _settle(child, started) -> None:
    """Read a finished child once: drop the handed failures it stored, keep its own, queue a clear it couldn't
    store. Under _lock."""
    global _pending
    try:
        code = child.poll()
    except Exception:
        log.exception("could not read the Critical Util build's exit code")
        code = None
    if code in EXIT_STORED_PREVIOUS:
        _pending = []
    if code == EXIT_UNCLEARED:
        try:
            from app.services import site_health

            site_health.resolve("util_build_failed", "build", at=_exited(child)[1])
        except Exception:
            log.exception("could not queue the Critical Util build's clear")
    failure = _read_finished(child, started)
    if failure is not None:
        _pending.append(failure)
    if len(_pending) > PENDING_MAX:
        log.warning("dropping %s Critical Util build failures no child stored", len(_pending) - PENDING_MAX)
        _pending = _pending[-PENDING_MAX:]


def request() -> bool:
    """Start a background build unless this process's last one is still running. True when one was started.
    Never raises: a build that cannot start leaves the stored one in place, and the next visit tries again."""
    global _child, _child_started, _pending
    with _lock:
        if _child is not None and _child.poll() is None:
            return False
        if _child is not None:
            finished, _child = _child, None      # each finished child is read once
            _settle(finished, _child_started)
        try:
            _child = _spawn(list(_pending) or None)
            _child_started = time.monotonic()
        except OSError:
            log.exception("could not start the Critical Util build")
            _child = None
            for failure in _pending:
                _queue_failure(failure)
            _pending = []
            return False
        _watch(_child)
        log.info("started the Critical Util build (pid %s)", _child.pid)
        return True


def _previous(argv) -> list:
    """The failures the parent handed over (`--previous <json list>`; one object is read as a list of one)."""
    if len(argv) >= 2 and argv[0] == "--previous":
        try:
            value = json.loads(argv[1])
        except ValueError:
            log.warning("unreadable --previous: %r", argv[1])
            return []
        items = value if isinstance(value, list) else [value]
        return [v for v in items if isinstance(v, dict)]
    return []


def main(argv=None) -> int:
    from datetime import datetime

    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.services import mask_accel, replay_util, site_health

    # before anything else: a clear this build makes must come after them; nothing is built until they are stored
    for failure in _previous(sys.argv[1:] if argv is None else argv):
        if not site_health.record_now("util_build_failed", "build", failure.get("error"), _context(failure),
                                      session_factory=SessionLocal, at=datetime.fromisoformat(failure["at"]),
                                      dedupe=_seen_ids):
            return EXIT_PREVIOUS_UNSTORED
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
            return EXIT_RECORDED if recorded else EXIT_UNRECORDED     # not stored: the parent records the exit
        if not site_health.resolve_now("util_build_failed", "build", session_factory=SessionLocal):
            return EXIT_UNCLEARED
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
