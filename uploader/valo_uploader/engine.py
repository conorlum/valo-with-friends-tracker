"""One durable upload slot, driven by explicit clock ticks."""

import logging
import os
import threading
import time
from pathlib import Path

from .api import BadKey, NotFound, Offline, Refused, Rejected
from .constants import FAILED_RETRY_S, REFUSED_RETRY_S, REFUSED_WINDOW_S, STATUS_POLL_S
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
                active = next(((sha, row) for sha, row in self.state.items()
                               if row["status"] == "uploading"), None)
                if active is not None:
                    sha, row = active
                    if row["next_try"] <= now:
                        self._step(sha, row, now)
                else:
                    due = self.state.due(now)
                    if due:
                        sha = due[0]
                        self._step(sha, self.state.entry(sha), now)
            self._delete_stored(now)

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
            if row["status"] == "uploading":
                # Status requests transmit no local bytes: a missing/locked file
                # must not release an in-flight upload's slot.
                result = self.client.status(row["upload_id"])
                if result["status"] == "stored":
                    self._stored(sha, now)
                elif result["status"] == "failed":
                    self._failed(sha, now)
                else:
                    self.state.mark(sha, next_try=now + STATUS_POLL_S)
                return
            path = self._ready_path(sha)
            if path is None:
                return
            self.state.mark(sha, status="pending", attempts=row["attempts"] + 1)
            if self.client.check(sha):
                self._stored(sha, now)
                return
            # /check may take time. Revalidate again before sending file bytes.
            path = self._ready_path(sha)
            if path is None:
                return
            upload_id = self.client.upload(path)
            self.state.mark(sha, status="uploading", upload_id=upload_id,
                            next_try=now + STATUS_POLL_S)
        except (Refused, Offline):
            current = self.state.entry(sha)
            first = current.get("first_refused_at")
            if first is None:
                first = now
            if now - first >= REFUSED_WINDOW_S:
                self._failed(sha, now)
            else:
                self.state.mark(sha, first_refused_at=first, next_try=now + REFUSED_RETRY_S)
        except Rejected:
            self.state.mark(sha, status="rejected", upload_id=None, first_refused_at=None,
                            next_try=now + FAILED_RETRY_S)
        except BadKey:
            # On restart the first due call discovers a revoked key again.
            self.needs_key = True
        except NotFound:
            if row["status"] == "uploading":
                self._pending(sha, now)
            else:
                self._failed(sha, now)
        except OSError:
            logger.warning("Replay became unavailable before upload; waiting for another tick")

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
                if row["status"] == "uploading":
                    self._pending(sha, now)

    def _delete_stored(self, now):
        for sha, row in self.state.items():
            if row["status"] != "stored":
                continue
            paths = list(row["paths"])
            deleted = False
            for name in row["paths"]:
                path = Path(name)
                try:
                    if now - path.stat().st_mtime < self.config.keep_days * 86400:
                        continue
                    if sha256_file(path) != sha:
                        self._hashes.pop(path, None)
                        self.watcher.forget(path)
                        logger.warning("Stored replay changed; keeping local file")
                        continue
                    self.deleter(path)
                    paths.remove(name)
                    self._hashes.pop(path, None)
                    deleted = True
                except FileNotFoundError:
                    paths.remove(name)
                    self._hashes.pop(path, None)
                except OSError:
                    logger.warning("Stored replay could not be deleted; keeping local file")
            if paths != row["paths"]:
                self.state.mark(sha, paths=paths,
                                deleted_at=now if deleted else row.get("deleted_at"))

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
