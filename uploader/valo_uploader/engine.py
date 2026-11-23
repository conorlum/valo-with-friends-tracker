"""One durable upload slot, driven by explicit clock ticks."""

import logging
import os
import threading
import time
from pathlib import Path

from .api import BadKey, NotFound, Offline, Refused, Rejected
from .constants import (DELETE_BATCH, FAILED_RETRY_CAP_S, FAILED_RETRY_S, HELD_POLL_S,
                        REFUSED_GAP_S, REFUSED_RETRY_S, REFUSED_WINDOW_S, RETRY_GENERATION,
                        STATUS_POLL_S)
from .files import Watcher, sha256_file
from .procs import valorant_running


logger = logging.getLogger(__name__)
MAX_NETWORK_WAIT_S = 3600  # the longest a site's Retry-After can hold the whole app back


def retry_delay(failures):
    """Wait after the nth failure of one file: 1, 2, 4, 8 days, then every 14."""
    return min(FAILED_RETRY_S * 2 ** min(max(failures, 1) - 1, 30), FAILED_RETRY_CAP_S)


def _deletion_due(row, now):
    """A candidate needs a later file-age check; no replay-file access here."""
    return (row["status"] == "stored" and bool(row["paths"])
            and row.get("next_delete_check", 0.0) <= now)


class Engine:
    def __init__(self, state, client, config, clock=time.time, sleep=time.sleep,
                 watcher=None, procs=valorant_running, deleter=os.remove):
        # Serialises state changes: tick against key_replaced. Reads for the tray (status, idle) take
        # no lock: State replaces its rows whole on every change, so a read is one consistent
        # snapshot, and a tray click must never wait on an upload. Reentrant because callbacks may
        # read status while already inside a tick.
        self.lock = threading.RLock()
        # One in-memory time before which nothing talks to the site: set by a refusal or an outage.
        self.next_network = 0.0
        self.state = state
        self.client = client
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self.watcher = Watcher() if watcher is None else watcher
        self.procs = procs
        self._watcher_clock_offset = 0.0
        self.deleter = deleter
        self.needs_key = False
        self.deletion_pending = False
        self._hashes = {}
        self._last_stored = []
        for _, row in sorted(state.items(), key=lambda pair: pair[1].get("stored_at") or 0):
            if row["status"] == "stored":
                self._remember(row["paths"])

    def clock_rewound(self, difference):
        """Keep scheduling waits through a wall-clock rollback; leave history unchanged."""
        with self.lock:
            if self.next_network:
                self.next_network += difference
            # Watcher observation times measure stability, not file age.
            self._watcher_clock_offset -= difference
            for sha, row in self.state.items():
                changes = {field: row[field] + difference
                           for field in ("next_try", "next_delete_check",
                                         "first_refused_at", "last_refused_at")
                           if row.get(field)}
                if changes:
                    self.state.mark(sha, **changes)

    def _may_send(self, now):
        """The flag is read at every step boundary: Pause lands without the lock."""
        return not self.config.paused and not self.needs_key and now >= self.next_network

    def tick(self, now, scan=True):
        """Advance queued work and retention; discovery is optional."""
        with self.lock:
            if self.config.paused:
                return
            if scan:
                self._scan(now)
            self._poll_held(now)
            if self._may_send(now):
                rows = self.state.items()
                if any(row["status"] == "uploading" for _, row in rows):
                    # The one transmission slot belongs to the in-flight upload.
                    for sha, row in rows:
                        if row["status"] == "uploading" and row["next_try"] <= now:
                            self._guarded_step(sha, row, now)
                            break
                else:
                    for sha in self.state.due(now):
                        if not self._may_send(now):
                            break
                        row = self.state.entry(sha)
                        if row is None or row["status"] == "held":
                            continue
                        # A file that was not available did not use the slot:
                        # look at the next due entry.
                        if self._guarded_step(sha, row, now):
                            break
            self._delete_stored(now)

    def _poll_held(self, now):
        """Held replays only poll the site; they never occupy the upload slot."""
        for sha, row in self.state.items():
            if not self._may_send(now):
                return
            if row["status"] == "held" and row["next_try"] <= now:
                self._guarded_step(sha, row, now)

    def _back_off(self, error, now):
        """A refusal or an outage holds back every call, for every file, until this time."""
        wait = REFUSED_RETRY_S
        if isinstance(error, Refused):
            wait = max(REFUSED_RETRY_S, min(error.retry_after_s, MAX_NETWORK_WAIT_S))
        self.next_network = max(self.next_network, now + wait)

    def apply_retry_generation(self, save_config):
        """Once per RETRY_GENERATION bump (an app update): send what failed again, at once.

        Entries go out one at a time through the normal queue, /check first. A match the site deleted
        on request ("refused") is never reset. State is written first, then the config, so a crash
        in between repeats this rather than losing it.
        """
        if self.config.retry_generation >= RETRY_GENERATION:
            return
        now = self.clock()
        with self.lock:
            for sha, row in self.state.items():
                if row["status"] in ("failed", "rejected"):
                    self.state.mark(sha, failures=0, next_try=now)
            self.config.retry_generation = RETRY_GENERATION
            save_config()

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
        for path in self.watcher.poll(now + self._watcher_clock_offset):
            if self.config.paused:
                return
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
                    if result.get("deleted") is True:
                        self._refused(sha)
                    else:
                        self._failed(sha, now)
                elif result["status"] == "held":
                    # Held for an unsupported build or agent, possibly for weeks:
                    # keep the upload id, free the slot, poll rarely.
                    self.state.mark(sha, status="held", first_refused_at=None, last_refused_at=None,
                                    next_try=now + HELD_POLL_S)
                elif row["status"] == "held" and result["status"] not in ("parsing", "queued"):
                    self.state.mark(sha, next_try=now + HELD_POLL_S)
                elif row["status"] == "held":
                    self.state.mark(sha, status="uploading", first_refused_at=None, last_refused_at=None,
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
                            first_refused_at=None, last_refused_at=None,
                            next_try=now + STATUS_POLL_S)
            return True
        except (Refused, Offline) as error:
            failed_at = max(now, self.clock())
            self._back_off(error, failed_at)
            current = self.state.entry(sha)
            first = current.get("first_refused_at")
            last = current.get("last_refused_at")
            # A long break (including an old state row) starts a fresh run of attempts.
            if first is None or last is None or failed_at - last > REFUSED_GAP_S:
                first = failed_at
            if row["status"] == "held":
                # Held has no site timeout; an outage never fails it.
                self.state.mark(sha, first_refused_at=first, last_refused_at=failed_at,
                                next_try=failed_at + REFUSED_RETRY_S)
            elif failed_at - first >= REFUSED_WINDOW_S:
                self._failed(sha, failed_at)
            else:
                self.state.mark(sha, first_refused_at=first, last_refused_at=failed_at,
                                next_try=failed_at + REFUSED_RETRY_S)
            return True
        except Rejected:
            if row["status"] == "held":
                # Held has its own upload id and no site timeout: keep it held.
                self.state.mark(sha, next_try=now + HELD_POLL_S)
                return True
            failures = self.state.entry(sha).get("failures", 0) + 1
            self.state.mark(sha, status="rejected", upload_id=None, first_refused_at=None,
                            last_refused_at=None,
                            failures=failures, next_try=now + retry_delay(failures))
            return True
        except BadKey:
            # On restart the first due call discovers a revoked key again.
            self.needs_key = True
            return True
        except NotFound:
            if row["status"] in ("uploading", "held"):
                self._pending(sha, now)
            else:
                self._failed(sha, now, count=False)  # a 404 says nothing about the file itself
            return True
        except OSError:
            logger.warning("Replay became unavailable before upload; waiting for another tick")
            self._defer(sha, now)
            return False

    def _stored(self, sha, now):
        self.state.mark(sha, status="stored", stored_at=now, first_refused_at=None,
                        last_refused_at=None, failures=0)
        self._remember(self.state.entry(sha)["paths"])

    def _failed(self, sha, now, count=True):
        failures = self.state.entry(sha).get("failures", 0) + (1 if count else 0)
        self.state.mark(sha, status="failed", upload_id=None, first_refused_at=None,
                        last_refused_at=None,
                        failures=failures, next_try=now + retry_delay(failures))

    def _refused(self, sha):
        """The site deleted this match on request: never upload it again, but keep the file."""
        self.state.mark(sha, status="refused", upload_id=None, first_refused_at=None,
                        last_refused_at=None, next_try=0.0)

    def _pending(self, sha, now):
        self.state.mark(sha, status="pending", upload_id=None, first_refused_at=None,
                        last_refused_at=None, next_try=now)

    def key_replaced(self, client):
        with self.lock:
            self.client = client
            self.needs_key = False
            now = self.clock()
            for sha, row in self.state.items():
                if row["status"] in ("uploading", "held"):
                    self._pending(sha, now)
            self.deletion_pending = any(_deletion_due(row, now) for _, row in self.state.items())

    def _delete_stored(self, now):
        """Delete an old local copy only when the site holds that exact file's replay."""
        if self.needs_key:
            self.deletion_pending = False
            return
        if not self._may_send(now):
            self.deletion_pending = any(_deletion_due(row, now) for _, row in self.state.items())
            return
        self.deletion_pending = False
        budget = DELETE_BATCH
        for sha, row in self.state.items():
            if not _deletion_due(row, now):
                continue
            if budget <= 0 or not self._may_send(now):
                self.deletion_pending = not self.needs_key
                return
            paths = list(row["paths"])
            old = []
            retry = None
            retry_at = now
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
                except (Refused, Offline) as error:
                    logger.warning("Could not ask the site about a stored replay; keeping local file")
                    failed_at = max(now, self.clock())
                    self._back_off(error, failed_at)
                    retry_at = failed_at
                    kept, retry = None, REFUSED_RETRY_S
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
                changes["next_delete_check"] = retry_at + retry
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
        """Only pending/in-flight uploads occupy the transmission queue."""
        return self.status()["queue_length"] == 0

    def status(self):
        """Takes no lock: the tray reads this while a tick may be deep in an upload."""
        rows = [row for _, row in self.state.items()]
        queue_length = sum(row["status"] in ("pending", "uploading") for row in rows)
        paused = self.config.paused
        if paused:
            activity = "paused"
        elif self.needs_key:
            activity = "needs_key"
        elif any(row["status"] == "uploading" for row in rows):
            activity = "uploading"
        else:
            activity = "queued" if queue_length else "idle"
        return dict(state=activity, queue_length=queue_length, last_stored=list(self._last_stored),
                    needs_key=self.needs_key, paused=paused,
                    deletion_pending=self.deletion_pending)
