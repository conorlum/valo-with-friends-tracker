"""archive_replays.py copies verified replays into the archive and never overwrites one (temp dirs only)."""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_replays  # noqa: E402

UUID_A = "00000000-0000-4000-8000-00000000000a"
UUID_B = "00000000-0000-4000-8000-00000000000b"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def dirs(tmp_path):
    source, archive = tmp_path / "Demos", tmp_path / "Archive"
    source.mkdir()
    return source, archive


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_copies_new_replays_and_indexes_them(dirs):
    source, archive = dirs
    (source / f"{UUID_A}.vrf").write_bytes(b"replay-a")
    (source / f"{UUID_B}.vrf").write_bytes(b"replay-b" * 1000)
    outcomes = archive_replays.archive_all(source, archive, NOW)
    assert [o.status for o in outcomes] == ["archived", "archived"]
    assert (archive / f"{UUID_A}.vrf").read_bytes() == b"replay-a"
    index = json.loads((archive / "index.json").read_text())
    assert index["files"][0] == {"file": f"{UUID_A}.vrf", "match_uuid": UUID_A, "sha256": _sha(b"replay-a"),
                                 "size_bytes": 8, "archived_at": "2026-01-01T00:00:00+00:00"}
    assert len(index["files"]) == 2
    assert (source / f"{UUID_A}.vrf").exists(), "the source is only read"
    assert not list(archive.glob("*.partial"))


def test_the_same_bytes_again_are_skipped(dirs):
    source, archive = dirs
    (source / f"{UUID_A}.vrf").write_bytes(b"replay-a")
    archive_replays.archive_all(source, archive, NOW)
    outcomes = archive_replays.archive_all(source, archive, NOW)
    assert [o.status for o in outcomes] == ["already_archived"]
    assert len(json.loads((archive / "index.json").read_text())["files"]) == 1


def test_different_bytes_under_the_same_name_are_kept_beside_never_overwritten(dirs):
    source, archive = dirs
    (source / f"{UUID_A}.vrf").write_bytes(b"first")
    archive_replays.archive_all(source, archive, NOW)
    (source / f"{UUID_A}.vrf").write_bytes(b"second")
    [outcome] = archive_replays.archive_all(source, archive, NOW)
    assert outcome.status == "kept_beside"
    assert outcome.archived_as == f"{UUID_A}.{_sha(b'second')[:8]}.vrf"
    assert (archive / f"{UUID_A}.vrf").read_bytes() == b"first"
    assert (archive / outcome.archived_as).read_bytes() == b"second"
    assert "already archived" in outcome.detail


def test_an_existing_archive_file_not_in_the_index_is_recognised(dirs):
    source, archive = dirs
    archive.mkdir()
    (archive / f"{UUID_A}.vrf").write_bytes(b"same")
    (source / f"{UUID_A}.vrf").write_bytes(b"same")
    [outcome] = archive_replays.archive_all(source, archive, NOW)
    assert outcome.status == "already_archived"


def test_a_copy_whose_hash_does_not_verify_is_discarded(dirs, monkeypatch):
    source, archive = dirs
    (source / f"{UUID_A}.vrf").write_bytes(b"replay-a")
    monkeypatch.setattr(archive_replays, "sha256_of", lambda path: "0" * 64)
    [outcome] = archive_replays.archive_all(source, archive, NOW)
    assert outcome.status == "failed"
    assert not (archive / f"{UUID_A}.vrf").exists()
    assert not list(archive.glob("*.partial"))
    assert not (archive / "index.json").exists()


def test_a_failed_copy_leaves_no_partial_file(dirs, monkeypatch):
    source, archive = dirs
    (source / f"{UUID_A}.vrf").write_bytes(b"replay-a")

    def broken(src, dst):
        dst.write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(archive_replays, "_copy_hashing", broken)
    with pytest.raises(OSError):
        archive_replays.archive_all(source, archive, NOW)
    assert list(archive.iterdir()) == []


def test_non_replay_names_are_reported_not_copied(dirs):
    source, archive = dirs
    (source / "notes.vrf").write_bytes(b"x")
    [outcome] = archive_replays.archive_all(source, archive, NOW)
    assert outcome.status == "failed"
    assert not (archive / "notes.vrf").exists()


def test_main_exits_2_without_a_source_folder(tmp_path, capsys):
    assert archive_replays.main(["--source", str(tmp_path / "missing"), "--archive", str(tmp_path / "a")]) == 2


def test_main_reports_each_file(dirs, capsys):
    source, archive = dirs
    (source / f"{UUID_A}.vrf").write_bytes(b"replay-a")
    assert archive_replays.main(["--source", str(source), "--archive", str(archive)]) == 0
    out = capsys.readouterr().out
    assert f"archived          {UUID_A}.vrf" in out
    assert "1 new, 0 not copied" in out
