"""The worker's archive store (replay_worker/archive.py) on its own: the switch, the ack rules, newer-wins,
eviction, tombstones and TTLs. No HTTP, no parser; files in a temp folder stand in for the disk."""

import hashlib
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from replay_worker import archive as arc  # noqa: E402

GB = arc.GB
UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"
UUID_C = "cccccccc-0000-0000-0000-000000000003"


def make(tmp_path, total=200 * GB, max_bytes=1000, queue_size=5):
    archive, reason = arc.open_archive(tmp_path, require_mount=False, total_bytes=total, max_bytes=max_bytes,
                                       queue_size=queue_size, log=lambda *_: None)
    assert archive is not None, reason
    return archive


def hold(archive, job_id, match_uuid, data: bytes, map_name="Ascent"):
    vrf = archive.jobs / f"{job_id}.vrf"
    vrf.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    archive.hold_pending(job_id, vrf, {"match_uuid": match_uuid, "sha256": sha, "size": len(data), "map": map_name})
    return sha


def ack(archive, job_id, match_uuid, sha, outcome="stored", replay_id=1, played_at=None):
    return archive.ack(job_id, {"match_uuid": match_uuid, "sha256": sha, "outcome": outcome,
                                "replay_id": replay_id, "played_at": played_at}, None)


def test_the_archive_is_off_unless_the_folder_is_set_mounted_and_writable(tmp_path, monkeypatch):
    kwargs = {"max_bytes": 1, "queue_size": 1, "log": lambda *_: None}
    assert arc.open_archive(None, **kwargs) == (None, "REPLAY_ARCHIVE_DIR is not set")
    assert arc.open_archive(tmp_path / "missing", **kwargs)[0] is None
    off, reason = arc.open_archive(tmp_path, require_mount=True, **kwargs)
    assert off is None and "not a mounted disk" in reason
    monkeypatch.setattr(arc.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("read-only")))
    off, reason = arc.open_archive(tmp_path, require_mount=False, **kwargs)
    assert off is None and "cannot create and rename" in reason


def test_a_file_is_archived_only_after_a_keeping_ack(tmp_path):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"one")
    assert not archive.has(UUID_A) and (archive.pending / "j1.vrf").exists()
    status, answer = ack(archive, "j1", UUID_A, sha, played_at="2026-09-30T20:00:00+00:00")
    assert status == 200 and answer == {"archived": True, "result": "archived"}
    assert archive.path_of(UUID_A).read_bytes() == b"one"
    assert not list(archive.pending.iterdir())
    entry = archive.entries()[0]
    assert entry["sha256"] == sha and entry["replay_id"] == 1 and entry["played_at"] == "2026-09-30T20:00:00+00:00"
    # It survives a restart: a new Archive on the same folder reads the index.
    assert make(tmp_path).has(UUID_A)


@pytest.mark.parametrize("outcome", ["kept_existing", "failed"])
def test_a_kept_existing_or_failed_ack_deletes_the_pending_file(tmp_path, outcome):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"one")
    status, answer = ack(archive, "j1", UUID_A, sha, outcome=outcome, replay_id=None)
    assert status == 200 and answer["result"] == "deleted"
    assert not archive.has(UUID_A) and not list(archive.pending.iterdir())


def test_a_mismatched_match_uuid_or_sha256_or_unknown_job_is_refused(tmp_path):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"one")
    assert ack(archive, "j1", UUID_B, sha)[0] == 409
    assert ack(archive, "j1", UUID_A, "0" * 64)[0] == 409
    assert ack(archive, "nope", UUID_A, sha) == (200, {"archived": False, "result": "nothing held"})
    assert (archive.pending / "j1.vrf").exists(), "a refused ack changes nothing"
    assert ack(archive, "j1", UUID_A, sha)[0] == 200


def test_the_same_ack_twice_gives_the_same_answer_and_a_different_one_is_refused(tmp_path):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"one")
    first = ack(archive, "j1", UUID_A, sha)
    assert ack(archive, "j1", UUID_A, sha, played_at="2026-09-30T20:00:00Z") == first
    status, answer = ack(archive, "j1", UUID_A, sha, outcome="failed", replay_id=None)
    assert status == 409 and answer["first"] == first[1]
    assert archive.has(UUID_A)


def test_a_delayed_older_ack_does_not_replace_a_newer_file(tmp_path):
    archive = make(tmp_path)
    old_sha = hold(archive, "old", UUID_A, b"older recording")
    new_sha = hold(archive, "new", UUID_A, b"newer recording")
    assert ack(archive, "new", UUID_A, new_sha, outcome="replaced", replay_id=7)[1]["result"] == "archived"
    status, answer = ack(archive, "old", UUID_A, old_sha, outcome="stored", replay_id=5)
    assert status == 200 and answer["result"] == "stale"
    assert archive.path_of(UUID_A).read_bytes() == b"newer recording"
    assert not (archive.pending / "old.vrf").exists()


def test_unchanged_archives_only_when_the_match_has_no_file(tmp_path):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"same")
    assert ack(archive, "j1", UUID_A, sha, outcome="unchanged", replay_id=3)[1]["result"] == "archived"
    sha2 = hold(archive, "j2", UUID_A, b"same")
    assert ack(archive, "j2", UUID_A, sha2, outcome="unchanged", replay_id=3)[1]["result"] == "kept"
    assert not list(archive.pending.iterdir()) and archive.path_of(UUID_A).read_bytes() == b"same"


def test_a_reparse_ack_takes_the_new_replay_id_and_touches_no_file(tmp_path):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"one")
    ack(archive, "j1", UUID_A, sha, replay_id=1)
    status, answer = archive.ack("rp", {"match_uuid": UUID_A, "sha256": sha, "outcome": "replaced", "replay_id": 4,
                                        "played_at": "2026-09-29T10:00:00Z"}, {"match_uuid": UUID_A, "sha256": sha})
    assert (status, answer) == (200, {"archived": True, "result": "kept"})
    entry = archive.entries()[0]
    assert entry["replay_id"] == 4 and entry["played_at"] == "2026-09-29T10:00:00+00:00"
    assert archive.path_of(UUID_A).read_bytes() == b"one"


def test_eviction_takes_the_earliest_played_match_and_never_touches_jobs_or_pending(tmp_path):
    # Budget: total - 70*max - 5*max - slack - pending. max_bytes=1 makes the reserve negligible.
    archive = make(tmp_path, total=arc.ARCHIVE_SLACK + 100 + 75, max_bytes=1, queue_size=5)
    for job, match_uuid, played in (("j1", UUID_A, "2026-09-03T00:00:00Z"), ("j2", UUID_B, "2026-09-01T00:00:00Z")):
        sha = hold(archive, job, match_uuid, b"x" * 40)
        ack(archive, job, match_uuid, sha, played_at=played)
    assert archive.has(UUID_A) and archive.has(UUID_B)
    (archive.jobs / "running").mkdir()
    (archive.jobs / "running" / "upload.vrf").write_bytes(b"j" * 30)
    hold(archive, "waiting", UUID_C, b"p" * 10)  # pending: budget now 90
    sha = hold(archive, "j3", UUID_C, b"y" * 40)
    ack(archive, "j3", UUID_C, sha, played_at="2026-09-02T00:00:00Z")
    assert not archive.has(UUID_B), "the earliest played match goes first"
    assert archive.has(UUID_A) and archive.has(UUID_C)
    assert (archive.jobs / "running" / "upload.vrf").exists() and (archive.pending / "waiting.vrf").exists()


def test_intake_refuses_an_upload_that_would_eat_the_parse_reserve(tmp_path):
    archive = make(tmp_path, total=arc.ARCHIVE_SLACK + 70 * 10 + 100, max_bytes=10)
    assert archive.intake_ok(100)
    hold(archive, "p", UUID_A, b"z" * 50)
    assert archive.intake_ok(50) and not archive.intake_ok(51)


def test_a_deletion_removes_files_and_refuses_later_acks_and_a_restored_file_goes_on_the_next_push(tmp_path):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"one")
    ack(archive, "j1", UUID_A, sha)
    sha_b = hold(archive, "j2", UUID_B, b"two")
    removed = archive.delete(UUID_B)
    assert removed["pending"] == ["j2"] and not (archive.pending / "j2.vrf").exists()
    assert ack(archive, "j2", UUID_B, sha_b)[1]["archived"] is False
    # Even with the server's own record of the job, a deleted match's ack is refused.
    status, answer = archive.ack("j2b", {"match_uuid": UUID_B, "sha256": sha_b, "outcome": "stored", "replay_id": 2},
                                 {"match_uuid": UUID_B, "sha256": sha_b})
    assert (status, answer["reason"]) == (200, "deleted on request") and not archive.has(UUID_B)
    # A later upload of a deleted match never reaches pending.
    hold(archive, "j3", UUID_B, b"two again")
    assert not (archive.pending / "j3.vrf").exists()
    # A restored snapshot: the file and an older tombstone list come back.
    archive.delete(UUID_A)
    assert not archive.has(UUID_A)
    archive.path_of(UUID_A).write_bytes(b"one")
    arc.write_json(archive.index_path, {"files": {UUID_A: {"sha256": sha, "size": 3, "accepted_at": "x", "replay_id": 1}}})
    arc.write_json(archive.tombstone_path, {"match_uuids": []})
    restored = arc.Archive(tmp_path, 200 * GB, max_bytes=1000, queue_size=5, log=lambda *_: None)
    assert restored.has(UUID_A), "the restored disk alone doesn't know"
    restored.set_tombstones([UUID_A, UUID_B])
    assert not restored.has(UUID_A) and not restored.path_of(UUID_A).exists()


def test_the_ttl_sweep_deletes_pending_files_nobody_acked(tmp_path):
    archive = make(tmp_path)
    hold(archive, "old", UUID_A, b"one")
    hold(archive, "new", UUID_B, b"two")
    removed = archive.sweep(now=time.time() + arc.PENDING_TTL_S - 60)
    assert removed["pending"] == []
    meta = arc.read_json(archive.pending / "old.json", {})
    arc.write_json(archive.pending / "old.json", {**meta, "held_at": time.time() - arc.PENDING_TTL_S - 1})
    assert archive.sweep()["pending"] == ["old"]
    assert (archive.pending / "new.vrf").exists() and not (archive.pending / "old.vrf").exists()


def test_status_reports_what_is_kept(tmp_path):
    archive = make(tmp_path)
    sha = hold(archive, "j1", UUID_A, b"one")
    ack(archive, "j1", UUID_A, sha, played_at="2026-09-30T20:00:00Z")
    status = archive.status()
    assert status["enabled"] and status["kept"] == 1 and status["bytes"] == 3
    assert status["oldest_played_at"] == "2026-09-30T20:00:00+00:00" and status["boot_id"]
