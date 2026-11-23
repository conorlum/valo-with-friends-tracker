"""One durable upload slot, driven by explicit clock ticks."""

import logging
import os
import threading
import time
from pathlib import Path

from .api import BadKey, NotFound, Offline, Refused, Rejected
from .constants import (DELETE_BATCH, FAILED_RETRY_S, IDLE_SCAN_S, REFUSED_RETRY_S, REFUSED_WINDOW_S,
                        STATUS_POLL_S)
from .files import Watcher, sha256_file
from .procs import valorant_running


logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, state, client, config, clock=time.time, sleep=time.sleep,
                 watcher=None, procs=valorant_running, deleter=os.remove):
        # Shared with tray preference changes; callbacks may read status while
        # already inside a tick, so this must be reentrant.
        self.lock = threading.RLock()
        self.state = state
        self.client = client
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self.watcher = Watcher() if watcher is None else watcher
        self.procs = procs
        self.deleter = deleter
        self.needs_key = False
        self._hashes = {}
        self._last_stored = []
        for _, row in sorted(state.items(), key=lambda pair: pair[1].get("stored_at") or 0):
            if row["status"] == "stored":
                self._remember(row["paths"])

    def tick(self, now):
        with self.lock:
            if self.config.paused:
                return
            self._scan(now)
            if not self.needs_key:
                self._poll_held(now)
            if not self.needs_key:
                rows = self.state.items()
                if any(row["status"] == "uploading" for _, row in rows):
                    # The one transmission slot belongs to the in-flight upload.
                    for sha, row in rows:
                        if row["status"] == "uploading" and row["next_try"] <= now:
                            self._guarded_step(sha, row, now)
                            break
                else:
                    for sha in self.state.due(now):
                        row = self.state.entry(sha)
                        if row["status"] == "held":
                            continue
                        # A file that was not available did not use the slot:
                        # look at the next due entry.
                        if self._guarded_step(sha, row, now):
                            break
            self._delete_stored(now)

    def _poll_held(self, now):
        """Held replays only poll the site; they never occupy the upload slot."""
        for sha, row in self.state.items():
            if self.needs_key:
                return
            if row["status"] == "held" and row["next_try"] <= now:
                self._guarded_step(sha, row, now)

    def _guarded_step(self, sha, row, now):
        """Run one entry's step; a bug for one file must never stop the app."""
        try:
            return self._step(sha, row, now)
        except Exception:
            logger.exception("Unexpected error while handling one replay; deferring it")
            try:
                if self.state.entry(sha) is not None:
                    self.state.mark(sha, next_try=now + REFUSED_RETRY_S)
            except Exception:
                logger.exception("Could not defer the replay")
            return True

    def _defer(self, sha, now):
        if self.state.entry(sha) is not None:
            self.state.mark(sha, next_try=now + STATUS_POLL_S)

    def _scan(self, now):
        for path in self.watcher.poll(now):
            try:
                info = path.stat()
                signature = (info.st_size, info.st_mtime_ns)
                cached = self._hashes.get(path)
                if cached is not None and cached[:2] == signature:
                    sha = cached[2]
                else:
                    sha = sha256_file(path)
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != signature:
                        self._hashes.pop(path, None)
                        self.watcher.forget(path)
                        continue
                    self._hashes[path] = (*signature, sha)
                row = self.state.entry(sha)
                if row is None:
                    self.state.mark(sha, paths=[str(path)], size=info.st_size,
                                    mtime=info.st_mtime, next_try=now)
                elif row["status"] != "rejected" and str(path) not in row["paths"]:
                    self.state.mark(sha, paths=[*row["paths"], str(path)])
                    if row["status"] == "stored":
                        self._remember([str(path)])
            except OSError:
                logger.warning("Replay could not be read during discovery")

    def _ready_path(self, sha):
        row = self.state.entry(sha)
        paths = list(row["paths"])
        selected = None
        for name in row["paths"]:
            path = Path(name)
            try:
                if not path.is_file():
                    paths.remove(name)
                    self._hashes.pop(path, None)
                elif self.watcher.exclusive_ok(path):
                    if sha256_file(path) != sha:
                        paths.remove(name)
                        self._hashes.pop(path, None)
                        self.watcher.forget(path)
                    elif selected is None:
                        selected = path
            except FileNotFoundError:
                paths.remove(name)
                self._hashes.pop(path, None)
            except OSError:
                logger.warning("Replay is unavailable; waiting for another tick")
        if not paths:
            self.state.drop(sha)
            return None
        if paths != row["paths"]:
            self.state.mark(sha, paths=paths)
        return selected

    def _step(self, sha, row, now):
        try:
            if row["status"] in ("uploading", "held"):
                # Status requests transmit no local bytes: a missing/locked file
                # must not release an in-flight upload's slot.
                result = self.client.status(row["upload_id"])
                if result["status"] == "stored":
                    self._stored(sha, now)
                elif result["status"] == "failed":
                    self._failed(sha, now)
                elif result["status"] == "held":
                    # Held for an unsupported build or agent, possibly for weeks:
                    # keep the upload id, free the slot, poll rarely.
                    self.state.mark(sha, status="held", first_refused_at=None,
                                    next_try=now + IDLE_SCAN_S)
                elif row["status"] == "held" and result["status"] not in ("parsing", "queued"):
                    self.state.mark(sha, next_try=now + IDLE_SCAN_S)
                elif row["status"] == "held":
                    self.state.mark(sha, status="uploading", first_refused_at=None,
                                    next_try=now + STATUS_POLL_S)
                else:
                    self.state.mark(sha, next_try=now + STATUS_POLL_S)
                return True
            path = self._ready_path(sha)
            if path is None:
                self._defer(sha, now)
                return False
            self.state.mark(sha, status="pending", attempts=row["attempts"] + 1)
            if self.client.check(sha):
                self._stored(sha, now)
                return True
            # /check may take time. Revalidate again before sending file bytes.
            path = self._ready_path(sha)
            if path is None:
                self._defer(sha, now)
                return False
            upload_id = self.client.upload(path)
            self.state.mark(sha, status="uploading", upload_id=upload_id,
                            next_try=now + STATUS_POLL_S)
            return True
        except (Refused, Offline):
            current = self.state.entry(sha)
            first = current.get("first_refused_at")
            if first is None:
                first = now
            if row["status"] == "held":
                # Held has no site timeout; an outage never fails it.
                self.state.mark(sha, next_try=now + REFUSED_RETRY_S)
            elif now - first >= REFUSED_WINDOW_S:
                self._failed(sha, now)
            else:
                self.state.mark(sha, first_refused_at=first, next_try=now + REFUSED_RETRY_S)
            return True
        except Rejected:
            if row["status"] == "held":
                # Held has its own upload id and no site timeout: keep it held.
                self.state.mark(sha, next_try=now + IDLE_SCAN_S)
                return True
            self.state.mark(sha, status="rejected", upload_id=None, first_refused_at=None,
                            next_try=now + FAILED_RETRY_S)
            return True
        except BadKey:
            # On restart the first due call discovers a revoked key again.
            self.needs_key = True
            return True
        except NotFound:
            if row["status"] in ("uploading", "held"):
                self._pending(sha, now)
            else:
                self._failed(sha, now)
            return True
        except OSError:
            logger.warning("Replay became unavailable before upload; waiting for another tick")
            self._defer(sha, now)
            return False

    def _stored(self, sha, now):
        self.state.mark(sha, status="stored", stored_at=now, first_refused_at=None)
        self._remember(self.state.entry(sha)["paths"])

    def _failed(self, sha, now):
        self.state.mark(sha, status="failed", upload_id=None, first_refused_at=None,
                        next_try=now + FAILED_RETRY_S)

    def _pending(self, sha, now):
        self.state.mark(sha, status="pending", upload_id=None, first_refused_at=None, next_try=now)

    def key_replaced(self, client):
        with self.lock:
            self.client = client
            self.needs_key = False
            now = self.clock()
            for sha, row in self.state.items():
                if row["status"] in ("uploading", "held"):
                    self._pending(sha, now)

    def _delete_stored(self, now):
        """Delete an old local copy only when the site holds that exact file's replay."""
        if self.needs_key:
            return
        budget = DELETE_BATCH
        for sha, row in self.state.items():
            if budget <= 0 or self.needs_key:
                return
            if row["status"] != "stored" or row.get("next_delete_check", 0.0) > now:
                continue
            paths = list(row["paths"])
            old = []
            retry = None
            for name in row["paths"]:
                path = Path(name)
                try:
                    if now - path.stat().st_mtime >= self.config.keep_days * 86400:
                        old.append(name)
                except FileNotFoundError:
                    paths.remove(name)
                    self._hashes.pop(path, None)
                except OSError:
                    logger.warning("Stored replay could not be read; keeping local file")
                    retry = REFUSED_RETRY_S
            verified = []
            if old:
                budget -= 1
            for name in old:
                path = Path(name)
                try:
                    if sha256_file(path) != sha:
                        self._hashes.pop(path, None)
                        self.watcher.forget(path)
                        logger.warning("Stored replay changed; keeping local file")
                        retry = REFUSED_RETRY_S
                    else:
                        verified.append(name)
                except FileNotFoundError:
                    paths.remove(name)
                    self._hashes.pop(path, None)
                except OSError:
                    logger.warning("Stored replay could not be read; keeping local file")
                    retry = REFUSED_RETRY_S
            deleted = False
            if verified:
                try:
                    kept = self.client.kept(sha)
                except BadKey:
                    self.needs_key = True
                    kept = None
                except Exception:
                    logger.warning("Could not ask the site about a stored replay; keeping local file")
                    kept, retry = None, REFUSED_RETRY_S
                if kept is None:
                    pass
                elif not kept:
                    # The site did not keep this file (a friend's copy of the match got there first):
                    # it stays on this PC for good.
                    retry = max(retry or 0, FAILED_RETRY_S)
                else:
                    for name in verified:
                        path = Path(name)
                        try:
                            self.deleter(path)
                            paths.remove(name)
                            self._hashes.pop(path, None)
                            deleted = True
                        except FileNotFoundError:
                            paths.remove(name)
                            self._hashes.pop(path, None)
                        except OSError:
                            logger.warning("Stored replay could not be deleted; keeping local file")
                            retry = REFUSED_RETRY_S
            changes = {}
            if paths != row["paths"]:
                changes["paths"] = paths
                if deleted:
                    changes["deleted_at"] = now
            if retry is not None:
                changes["next_delete_check"] = now + retry
            if changes:
                self.state.mark(sha, **changes)

    def _remember(self, paths):
        for path in paths:
            name = Path(path).name
            if name in self._last_stored:
                self._last_stored.remove(name)
            self._last_stored.append(name)
        self._last_stored = self._last_stored[-5:]

    def idle(self):
        """Daily failures wait for idle scans; only pending/in-flight work queues."""
        return self.status()["queue_length"] == 0

    def status(self):
        with self.lock:
            rows = [row for _, row in self.state.items()]
            queue_length = sum(row["status"] in ("pending", "uploading") for row in rows)
            if self.config.paused:
                activity = "paused"
            elif self.needs_key:
                activity = "needs_key"
            elif any(row["status"] == "uploading" for row in rows):
                activity = "uploading"
            else:
                activity = "queued" if queue_length else "idle"
            return dict(state=activity, queue_length=queue_length, last_stored=list(self._last_stored),
                        needs_key=self.needs_key, paused=self.config.paused)
