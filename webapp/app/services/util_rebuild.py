"""Critical Util's corpus build, off the request path.

The build (replay_util.ensure_values: the estimator if it is due, then every stored cast valued and compared with
the no-util baseline) takes minutes of CPU. A page or tab that finds the stored build stale or missing calls
`request()`, which starts `python -m app.services.util_rebuild` as a separate, lower-priority process, and shows
the last build meanwhile. A separate process keeps the build's CPU off the web server's interpreter lock, so the
rest of the site stays quick while it runs.

One build at a time: each process starts at most one child while its last is still running, and a child that
cannot take LOCK_KEY (a Postgres session lock: another process's child is building) exits at once. Waiting callers
(the coach, scripts/critical_util.py) still call ensure_values directly.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

WEBAPP = Path(__file__).resolve().parents[2]
LOCK_KEY = 0x7574696C03         # "util" + 3, beside util_model.LOCK_IDS
NICENESS = 10

_lock = threading.Lock()
_child: subprocess.Popen | None = None


def _spawn() -> subprocess.Popen:
    kwargs = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = lambda: os.nice(NICENESS)
    return subprocess.Popen([sys.executable, "-m", "app.services.util_rebuild"], cwd=WEBAPP, **kwargs)


def running() -> bool:
    """Whether this process's last child is still building."""
    with _lock:
        return _child is not None and _child.poll() is None


def request() -> bool:
    """Start a background build unless this process's last one is still running. True when one was started.
    Never raises: a build that cannot start leaves the stored one in place, and the next visit tries again."""
    global _child
    with _lock:
        if _child is not None and _child.poll() is None:
            return False
        try:
            _child = _spawn()
        except OSError:
            log.exception("could not start the Critical Util build")
            _child = None
            return False
        log.info("started the Critical Util build (pid %s)", _child.pid)
        return True


def main() -> int:
    from sqlalchemy import text

    from app.db import SessionLocal, engine
    from app.services import mask_accel, replay_util

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
        return _build(SessionLocal, replay_util)
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
