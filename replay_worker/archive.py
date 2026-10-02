"""The worker's `.vrf` archive on its persistent disk (docs/superpowers/specs/2026-10-01-control-heights-design.md,
part 1). Standard library only, like the server.

The archive is on only when `REPLAY_ARCHIVE_DIR` names a mount point that the worker can create and rename
files in (`open_archive`); otherwise the server keeps today's behaviour and never builds one of these. Layout
under the mount:

    jobs/<job id>/            a job's folder: upload.vrf, export/ (deleted after condense), job.json, result.json
    pending/<job id>.vrf      a parsed upload waiting for the web app's ack, beside <job id>.json
    archive/<uuid>.vrf        the accepted recording of each match, and archive/index.json
    acks/<job id>.json        the answer given to each ack, so a repeat gets the same answer
    tombstones.json           the deleted matches, as last pushed by the web app (its table is the authority)

Space comes from the worker's own accounting, never from free space measured mid-parse: the archive may use
the disk's total less the parse reserve, ARCHIVE_SLACK and what `pending/` holds, and is evicted earliest
played match first. Every change to `archive/`, `pending/`, the index or the tombstones happens under one lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

GB = 1024 ** 3
# The reserve, slack and TTLs below, and the 50 GB disk in render.yaml.
PARSE_RESERVE_FACTOR = 70      # a parse needs about 65x the upload (README); the running parse at the cap
ARCHIVE_SLACK = 5 * GB
PENDING_TTL_S = 2 * 86400      # pending bytes come out of the archive's budget, so they don't wait long
UNCOLLECTED_TTL_S = 7 * 86400  # a job folder nobody collected
ACK_TTL_S = 7 * 86400
OUTCOMES = ("stored", "replaced", "unchanged", "kept_existing", "failed")
KEEPING = ("stored", "replaced", "unchanged")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_utc(value) -> str | None:
    """A played_at from the web app as a sortable UTC ISO string, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _identity(body: dict) -> tuple:
    return tuple(str(body.get(k) or "").lower() for k in ("match_uuid", "sha256", "outcome", "replay_id"))


def write_json(path: Path, data) -> None:
    """Atomic on one volume: a temp file renamed over the target."""
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_bytes(path: Path) -> int:
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total


def open_archive(directory, *, require_mount: bool = True, total_bytes: int | None = None,
                 max_bytes: int, queue_size: int, log=print) -> tuple["Archive | None", str]:
    """(the archive, "") when it can be on, else (None, the reason it is off)."""
    if not directory:
        return None, "REPLAY_ARCHIVE_DIR is not set"
    root = Path(directory)
    if not root.is_dir():
        return None, f"{root} does not exist"
    if require_mount and not os.path.ismount(root):
        return None, f"{root} is not a mounted disk"
    try:
        probe = root / f".probe-{uuid.uuid4().hex}"
        temporary = probe.with_name(probe.name + ".tmp")
        temporary.write_bytes(b"probe")
        os.replace(temporary, probe)
        probe.unlink()
    except OSError as error:
        return None, f"cannot create and rename files in {root}: {error}"
    total = total_bytes if total_bytes is not None else _disk_total(root)
    return Archive(root, total, max_bytes=max_bytes, queue_size=queue_size, log=log), ""


def _disk_total(root: Path) -> int:
    import shutil
    return shutil.disk_usage(root).total


class ArchiveError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Archive:
    def __init__(self, root: Path, total_bytes: int, *, max_bytes: int, queue_size: int, log=print):
        self.root = Path(root)
        self.total = int(total_bytes)
        self.max_bytes = int(max_bytes)
        self.queue_size = int(queue_size)
        self.log = log
        self.lock = threading.RLock()
        self.boot_id = uuid.uuid4().hex
        self.jobs = self.root / "jobs"
        self.pending = self.root / "pending"
        self.files = self.root / "archive"
        self.acks = self.root / "acks"
        for folder in (self.jobs, self.pending, self.files, self.acks):
            folder.mkdir(parents=True, exist_ok=True)
        for stray in self.files.glob("*.tmp"):
            stray.unlink(missing_ok=True)
        self.index_path = self.files / "index.json"
        self.tombstone_path = self.root / "tombstones.json"
        self.index: dict[str, dict] = read_json(self.index_path, {}).get("files", {})
        # A file the index doesn't know (a crash between rename and index write) is kept out of the
        # budget's blind spot by dropping it: the index is what eviction and acks trust.
        for path in self.files.glob("*.vrf"):
            if path.stem not in self.index:
                self.log(f"archive: dropping {path.name}, not in the index")
                path.unlink(missing_ok=True)
        self.tombstones: set[str] = {u.lower() for u in read_json(self.tombstone_path, {}).get("match_uuids", [])}
        self._purge_tombstoned()

    # ------------------------------------------------------------------ accounting

    @property
    def parse_reserve(self) -> int:
        return PARSE_RESERVE_FACTOR * self.max_bytes + self.queue_size * self.max_bytes

    def archive_bytes(self) -> int:
        return sum(int(entry["size"]) for entry in self.index.values())

    def pending_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.pending.glob("*.vrf"))

    def budget(self) -> int:
        return self.total - self.parse_reserve - ARCHIVE_SLACK - self.pending_bytes()

    def intake_ok(self, size: int) -> bool:
        """Room for one more upload: what jobs/ and pending/ hold plus it must leave the running parse's
        reserve and the slack free, with the archive evicted to nothing if need be."""
        held = _tree_bytes(self.jobs) + self.pending_bytes()
        return held + size <= self.total - ARCHIVE_SLACK - PARSE_RESERVE_FACTOR * self.max_bytes

    def evict(self) -> list[str]:
        """Earliest-played match first, until the archive fits its budget. Never touches jobs/ or pending/."""
        removed = []
        with self.lock:
            budget = self.budget()
            while self.index and self.archive_bytes() > budget:
                victim = min(self.index, key=lambda u: (self.index[u].get("played_at") or self.index[u]["accepted_at"], u))
                entry = self.index.pop(victim)
                (self.files / f"{victim}.vrf").unlink(missing_ok=True)
                removed.append(victim)
                self.log(f"archive: evicted {victim} (played {entry.get('played_at') or 'unknown'}, "
                         f"{entry['size']} bytes) to stay within {budget} bytes")
            if removed:
                write_json(self.index_path, {"files": self.index})
        return removed

    def status(self) -> dict:
        with self.lock:
            dates = [e.get("played_at") for e in self.index.values() if e.get("played_at")]
            return {"enabled": True, "kept": len(self.index), "bytes": self.archive_bytes(),
                    "pending": len(list(self.pending.glob("*.vrf"))), "budget_bytes": self.budget(),
                    "total_bytes": self.total, "oldest_played_at": min(dates) if dates else None,
                    "tombstones": len(self.tombstones), "boot_id": self.boot_id}

    def entries(self) -> list[dict]:
        with self.lock:
            return [{"match_uuid": u, **{k: e.get(k) for k in ("sha256", "size", "map", "played_at",
                                                                   "accepted_at", "replay_id")}}
                    for u, e in sorted(self.index.items())]

    def has(self, match_uuid: str) -> bool:
        with self.lock:
            return match_uuid.lower() in self.index and (self.files / f"{match_uuid.lower()}.vrf").exists()

    def path_of(self, match_uuid: str) -> Path:
        return self.files / f"{match_uuid.lower()}.vrf"

    def is_tombstoned(self, match_uuid: str | None) -> bool:
        with self.lock:
            return bool(match_uuid) and match_uuid.lower() in self.tombstones

    # ------------------------------------------------------------------ pending

    def hold_pending(self, job_id: str, vrf: Path, meta: dict) -> bool:
        """Moves a parsed upload into pending/ to wait for its ack. A tombstoned match's file is deleted."""
        with self.lock:
            if self.is_tombstoned(meta.get("match_uuid")):
                vrf.unlink(missing_ok=True)
                self.log(f"archive: job {job_id} is a deleted match; its file is dropped")
                return False
            write_json(self.pending / f"{job_id}.json", {**meta, "held_at": time.time()})
            os.replace(vrf, self.pending / f"{job_id}.vrf")
            return True

    def _drop_pending(self, job_id: str) -> None:
        (self.pending / f"{job_id}.vrf").unlink(missing_ok=True)
        (self.pending / f"{job_id}.json").unlink(missing_ok=True)

    # ------------------------------------------------------------------ the ack

    def ack(self, job_id: str, body: dict, job: dict | None) -> tuple[int, dict]:
        """Applies the web app's ack for one job. `job` is the server's own record of it ({match_uuid, sha256,
        kind}), or None when the server no longer has the job. Returns (HTTP status, answer)."""
        with self.lock:
            record_path = self.acks / f"{job_id}.json"
            record = read_json(record_path, None)
            if record is not None:
                # played_at isn't part of an ack's identity: a re-send may know the match's date by then.
                if _identity(record.get("body") or {}) == _identity(body):
                    return record["status"], record["answer"]
                return 409, {"error": "a different ack for this job was already applied", "first": record["answer"]}
            meta = read_json(self.pending / f"{job_id}.json", None) or job
            if meta is None or not meta.get("match_uuid"):
                # A failed job, or one long gone: nothing of it is held, and that is a final answer.
                return 200, {"archived": False, "result": "nothing held"}
            status, answer = self._apply(job_id, body, meta)
            if "result" in answer:  # applied; a refused or malformed ack changes nothing and isn't recorded
                write_json(record_path, {"body": body, "status": status, "answer": answer, "at": time.time()})
            return status, answer

    def _apply(self, job_id: str, body: dict, meta: dict) -> tuple[int, dict]:
        outcome = body.get("outcome")
        if outcome not in OUTCOMES:
            return 400, {"error": f"outcome must be one of {OUTCOMES}"}
        match_uuid = str(meta["match_uuid"]).lower()
        if outcome not in KEEPING:
            # Dropping a file is always safe, and a refused store may not know the match uuid.
            self._drop_pending(job_id)
            return 200, {"archived": False, "result": "deleted"}
        if str(body.get("match_uuid") or "").lower() != match_uuid:
            self.log(f"archive: ack for job {job_id} refused: match uuid {body.get('match_uuid')!r} is not the job's")
            return 409, {"error": "the match uuid is not this job's"}
        if body.get("sha256") != meta.get("sha256"):
            self.log(f"archive: ack for job {job_id} refused: sha256 is not the job's")
            return 409, {"error": "the sha256 is not this job's file"}
        if not isinstance(body.get("replay_id"), int):
            return 400, {"error": "replay_id is required for this outcome"}
        pending = self.pending / f"{job_id}.vrf"
        if match_uuid in self.tombstones:
            self._drop_pending(job_id)
            return 200, {"archived": False, "result": "refused", "reason": "deleted on request"}
        replay_id = body["replay_id"]
        entry = self.index.get(match_uuid)
        if entry is not None and replay_id < int(entry.get("replay_id") or 0):
            self._drop_pending(job_id)
            return 200, {"archived": False, "result": "stale", "reason": "a newer recording is archived"}
        if entry is not None and entry["sha256"] == body["sha256"]:
            # The same bytes are already archived (unchanged, a reparse, a re-upload of the same file).
            entry["replay_id"] = max(replay_id, int(entry.get("replay_id") or 0))
            entry["played_at"] = _iso_utc(body.get("played_at")) or entry.get("played_at")
            write_json(self.index_path, {"files": self.index})
            self._drop_pending(job_id)
            return 200, {"archived": True, "result": "kept"}
        if not pending.exists():
            return 200, {"archived": False, "result": "nothing pending"}
        if sha256_file(pending) != body["sha256"]:
            self._drop_pending(job_id)
            self.log(f"archive: job {job_id}'s pending file doesn't match its hash; dropped")
            return 500, {"error": "the pending file is damaged"}
        target = self.path_of(match_uuid)
        os.replace(pending, target)
        (self.pending / f"{job_id}.json").unlink(missing_ok=True)
        self.index[match_uuid] = {"sha256": body["sha256"], "size": target.stat().st_size, "map": meta.get("map"),
                                  "played_at": _iso_utc(body.get("played_at")), "accepted_at": _now_iso(),
                                  "replay_id": replay_id, "job_id": job_id}
        write_json(self.index_path, {"files": self.index})
        self.log(f"archive: kept {match_uuid} from job {job_id} ({outcome}, replay {replay_id})")
        evicted = self.evict()
        return 200, {"archived": match_uuid not in evicted, "result": "archived"}

    def note_replay(self, match_uuid: str, replay_id: int, played_at=None) -> None:
        """A reparse stored this match again: its archived entry takes the new replay id."""
        with self.lock:
            entry = self.index.get(match_uuid.lower())
            if entry is not None and replay_id >= int(entry.get("replay_id") or 0):
                entry["replay_id"] = replay_id
                entry["played_at"] = _iso_utc(played_at) or entry.get("played_at")
                write_json(self.index_path, {"files": self.index})

    # ------------------------------------------------------------------ deletion

    def delete(self, match_uuid: str) -> dict:
        with self.lock:
            match_uuid = match_uuid.lower()
            self.tombstones.add(match_uuid)
            write_json(self.tombstone_path, {"match_uuids": sorted(self.tombstones)})
            return self._purge_tombstoned()

    def set_tombstones(self, match_uuids) -> dict:
        """The web app's full list replaces the local copy (which a restored snapshot may have rolled back)."""
        with self.lock:
            self.tombstones = {str(u).lower() for u in match_uuids}
            write_json(self.tombstone_path, {"match_uuids": sorted(self.tombstones)})
            return self._purge_tombstoned()

    def _purge_tombstoned(self) -> dict:
        removed = {"archived": [], "pending": []}
        with self.lock:
            for match_uuid in [u for u in self.index if u in self.tombstones]:
                self.index.pop(match_uuid)
                self.path_of(match_uuid).unlink(missing_ok=True)
                removed["archived"].append(match_uuid)
            for path in self.files.glob("*.vrf"):
                if path.stem in self.tombstones:
                    path.unlink(missing_ok=True)
            if removed["archived"]:
                write_json(self.index_path, {"files": self.index})
            for meta_path in self.pending.glob("*.json"):
                meta = read_json(meta_path, {})
                if str(meta.get("match_uuid") or "").lower() in self.tombstones:
                    self._drop_pending(meta_path.stem)
                    removed["pending"].append(meta_path.stem)
        for match_uuid in removed["archived"]:
            self.log(f"archive: deleted {match_uuid} on request")
        return removed

    # ------------------------------------------------------------------ TTLs

    def sweep(self, now: float | None = None) -> dict:
        """Pending files nobody acked within PENDING_TTL_S, and ack records past ACK_TTL_S."""
        now = time.time() if now is None else now
        removed = {"pending": [], "acks": 0}
        with self.lock:
            for meta_path in self.pending.glob("*.json"):
                held = read_json(meta_path, {}).get("held_at") or meta_path.stat().st_mtime
                if now - held > PENDING_TTL_S:
                    self._drop_pending(meta_path.stem)
                    removed["pending"].append(meta_path.stem)
                    self.log(f"archive: pending file of job {meta_path.stem} was never acked; deleted")
            for vrf in self.pending.glob("*.vrf"):
                if not (self.pending / f"{vrf.stem}.json").exists():
                    vrf.unlink(missing_ok=True)
            for record in self.acks.glob("*.json"):
                if now - record.stat().st_mtime > ACK_TTL_S:
                    record.unlink(missing_ok=True)
                    removed["acks"] += 1
        return removed
