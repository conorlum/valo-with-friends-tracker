"""Durable per-content upload state, written by atomic replacement."""

import json
import os
import tempfile
import time

from .paths import app_dir

# "refused": the site deleted this match on request; it is never uploaded again.
STATUSES = frozenset({"pending", "uploading", "held", "stored", "failed", "rejected", "refused"})


class State:
    def __init__(self, *, env=None):
        self.path = app_dir(env) / "state.json"
        self._entries = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or any(
                not isinstance(row, dict) or row.get("status") not in STATUSES
                for row in data.values()
            ):
                raise ValueError("Invalid upload state")
            for row in data.values():
                row.setdefault("last_refused_at", None)
            self._entries = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            try:
                self.path.rename(self.path.with_name(self.path.name + f".corrupt-{time.time_ns()}"))
            except OSError:
                pass

    def entry(self, sha):
        row = self._entries.get(sha)
        return None if row is None else {**row, "paths": list(row.get("paths", []))}

    def mark(self, sha, **changes):
        defaults = dict(paths=[], size=0, mtime=0.0, status="pending", upload_id=None,
                        stored_at=None, first_refused_at=None, last_refused_at=None,
                        attempts=0, next_try=0.0,
                        deleted_at=None, next_delete_check=0.0, failures=0)
        if changes.keys() - defaults.keys():
            raise ValueError("Unknown upload state field")
        row = {**defaults, **self._entries.get(sha, {}), **changes}
        if row["status"] not in STATUSES:
            raise ValueError("Invalid upload status")
        row["paths"] = list(row["paths"])
        entries = {**self._entries, sha: row}
        self._save(entries)
        self._entries = entries

    def due(self, now):
        entries = self._entries
        return [sha for sha, row in entries.items()
                if row["status"] not in ("stored", "refused") and row.get("next_try", 0) <= now]

    def items(self):
        """Return a snapshot; callers cannot change the durable rows in place.

        `_entries` is replaced whole on every change, never edited, so reading the reference once
        gives one consistent snapshot to any thread, with no lock (the tray reads it mid-upload).
        """
        entries = self._entries
        return [(sha, {**row, "paths": list(row.get("paths", []))}) for sha, row in entries.items()]

    def drop(self, sha):
        if sha in self._entries:
            entries = {key: row for key, row in self._entries.items() if key != sha}
            self._save(entries)
            self._entries = entries

    def _save(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(entries, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
