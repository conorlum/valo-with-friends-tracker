"""Offline engine journeys: synthetic replay bytes, time and API only."""

import io
import os
import subprocess
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from valo_uploader import api, constants
from valo_uploader.config import Config
from valo_uploader.engine import Engine
from valo_uploader.files import Watcher, exclusive_ok, sha256_file
from valo_uploader.procs import valorant_running
from valo_uploader.runner import run
from valo_uploader.state import State


DAY = 86400
START = 30 * DAY


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Client:
    def __init__(self, *, known=False, statuses=()):
        self.known = known
        self.statuses = list(statuses)
        self.calls = []
        self.error = None
        self.kept_value = True  # what the site says about a stored file: kept, or discarded.

    def answer(self, value):
        if self.error:
            raise self.error
        if isinstance(value, Exception):
            raise value
        return value

    def kept(self, sha):
        self.calls.append(("kept", sha))
        return self.answer(self.kept_value)

    def check(self, sha):
        self.calls.append(("check", sha))
        return self.answer(self.known)

    def upload(self, path):
        self.calls.append(("upload", Path(path).name))
        return self.answer("upload-" + str(len(self.calls)))

    def status(self, upload_id):
        self.calls.append(("status", upload_id))
        value = self.statuses.pop(0) if self.statuses else {"status": "parsing"}
        return self.answer(value)


@pytest.fixture
def rig(tmp_path):
    class Rig:
        env = {"APPDATA": str(tmp_path / "settings")}
        demos = tmp_path / "demos"
        clock = Clock()
        client = Client()
        config = Config()
        locked = False

        def __init__(self):
            self.demos.mkdir()
            self.state = State(env=self.env)
            self.restart()

        def restart(self, client=None):
            if client is not None:
                self.client = client
            self.state = State(env=self.env)
            self.watcher = Watcher(self.demos, exclusive=lambda path: not self.locked)
            self.engine = Engine(self.state, self.client, self.config, self.clock,
                                 self.clock.sleep, self.watcher, lambda: False)

        def file(self, name="sample.vrf", content=b"synthetic replay", age=0):
            path = self.demos / name
            path.write_bytes(content)
            os.utime(path, (self.clock.now - age, self.clock.now - age))
            return path

        def tick(self, seconds=0):
            self.clock.now += seconds
            self.engine.tick(self.clock.now)

        def discover(self):
            self.tick()
            self.tick(constants.STABLE_GAP_S)

    return Rig()


def uploads(client):
    return [call for call in client.calls if call[0] == "upload"]


def test_watcher_requires_unchanged_signature_and_exclusive_read(rig):
    path = rig.file()
    rig.file("ignore.txt")
    assert rig.watcher.poll(0) == []
    path.write_bytes(b"longer synthetic bytes")
    assert rig.watcher.poll(5) == []
    assert rig.watcher.poll(9) == []
    rig.locked = True
    assert rig.watcher.poll(10) == []
    rig.locked = False
    assert rig.watcher.poll(15) == [path]
    path.unlink()
    assert rig.watcher.poll(20) == []


def test_exclusive_seam_closes_immediately_and_handles_errors(tmp_path):
    seen = []
    assert exclusive_ok(tmp_path / "sample.vrf", opener=lambda p: seen.append(p) or True)
    assert seen == [tmp_path / "sample.vrf"]
    assert not exclusive_ok(tmp_path / "sample.vrf", opener=lambda p: False)

    def denied(path):
        raise PermissionError("locked")

    assert not exclusive_ok(tmp_path / "sample.vrf", opener=denied)


@pytest.mark.parametrize("valid", [True, False])
def test_windows_exclusive_open_uses_read_access_no_sharing_and_closes(monkeypatch, tmp_path, valid):
    import ctypes
    import valo_uploader.files as files

    handle = 42 if valid else ctypes.c_void_p(-1).value
    kernel = SimpleNamespace(CreateFileW=Mock(return_value=handle), CloseHandle=Mock())
    dll = Mock(return_value=kernel)
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    monkeypatch.setattr(files, "os", SimpleNamespace(name="nt"))
    path = tmp_path / "sample.vrf"
    assert files.win_exclusive_open(path) is valid
    dll.assert_called_once_with("kernel32", use_last_error=True)
    kernel.CreateFileW.assert_called_once_with(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if valid:
        kernel.CloseHandle.assert_called_once_with(42)
    else:
        kernel.CloseHandle.assert_not_called()


def test_non_windows_default_exclusive_opener_is_portable(monkeypatch, tmp_path):
    import valo_uploader.files as files

    monkeypatch.setattr(files, "os", SimpleNamespace(name="posix"))
    assert files.win_exclusive_open(tmp_path / "sample.vrf")


def test_known_file_is_stored_without_upload_and_status_is_small(rig):
    path = rig.file()
    rig.client.known = True
    rig.discover()
    row = rig.state.entry(sha256_file(path))
    assert row["status"] == "stored"
    assert row["stored_at"] == rig.clock.now
    assert rig.client.calls == [("check", sha256_file(path))]
    status = rig.engine.status()
    assert status["queue_length"] == 0
    assert status["last_stored"] == [path.name]
    assert status["needs_key"] is False
    assert status["paused"] is False


@pytest.mark.parametrize("change", ["mtime", "size"])
def test_discovery_hashes_unchanged_paths_once_and_rehashes_changed_signature(rig, monkeypatch, change):
    import valo_uploader.engine as engine

    first = rig.file("a.vrf", b"first")
    second = rig.file("b.vrf", b"second")
    for path in (first, second):
        rig.state.mark(sha256_file(path), paths=[str(path)], status="stored", stored_at=START)
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    # Isolate discovery from API revalidation when changed bytes become pending.
    rig.engine.needs_key = True
    rig.discover()
    rig.tick(10)
    rig.tick(10)
    assert [call.args[0] for call in counted.call_args_list] == [first, second]

    info = first.stat()
    if change == "mtime":
        os.utime(first, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
    else:
        first.write_bytes(b"longer first replay")
        os.utime(first, ns=(info.st_atime_ns, info.st_mtime_ns))
    rig.tick(10)
    assert counted.call_count == 2  # Changed files must become stable again.
    rig.tick(5)
    rig.tick(10)
    assert [call.args[0] for call in counted.call_args_list] == [first, second, first]


def test_upload_revalidation_bypasses_discovery_hash_cache(rig, monkeypatch):
    import valo_uploader.engine as engine

    path = rig.file()
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    check, upload = rig.client.check, rig.client.upload

    def checked(sha):
        assert counted.call_count == 2  # Discovery, then pre-check revalidation.
        return check(sha)

    def uploaded(value):
        assert counted.call_count == 3  # A fresh hash immediately before upload.
        return upload(value)

    rig.client.check, rig.client.upload = checked, uploaded
    rig.discover()
    rig.tick(10)
    assert [call.args[0] for call in counted.call_args_list] == [path] * 3
    assert len(uploads(rig.client)) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_deletion_rehashes_even_when_discovery_signature_is_cached(rig, monkeypatch, changed):
    import valo_uploader.engine as engine

    path = rig.file(age=13 * DAY)
    sha = sha256_file(path)
    rig.state.mark(sha, paths=[str(path)], status="stored", stored_at=START)
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    rig.discover()
    assert counted.call_count == 1
    if changed:
        info = path.stat()
        path.write_bytes(b"x" * info.st_size)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    rig.tick(2 * DAY)
    assert counted.call_count == 2  # The second read is the required deletion hash.
    assert path.exists() is changed


def test_changed_bytes_with_cached_signature_are_revalidated_and_requeued(rig, monkeypatch):
    import valo_uploader.engine as engine

    path = rig.file()
    sha = sha256_file(path)
    rig.state.mark(sha, paths=[str(path)], status="failed", next_try=START + DAY)
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    rig.discover()
    assert counted.call_count == 1
    info = path.stat()
    path.write_bytes(b"x" * info.st_size)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    new_sha = sha256_file(path)
    rig.tick(DAY)
    assert rig.client.calls == []
    assert rig.state.entry(sha) is None
    assert counted.call_count == 2
    rig.tick(5)
    rig.tick(5)
    assert rig.state.entry(new_sha)["status"] == "uploading"
    assert rig.client.calls == [("check", new_sha), ("upload", path.name)]


@pytest.mark.parametrize("age,keep_days,deleted", [(13 * DAY, 14, False),
                                                   (14 * DAY, 14, True),
                                                   (15 * DAY, 14, True), (0, 0, True)])
def test_upload_status_progress_and_retention(rig, age, keep_days, deleted):
    path = rig.file(age=age)
    sha = sha256_file(path)
    rig.config.keep_days = keep_days
    rig.client.statuses = [{"status": value} for value in ("parsing", "parsing", "stored")]
    rig.discover()
    assert rig.state.entry(sha)["status"] == "uploading"
    rig.tick(9)
    assert not any(c[0] == "status" for c in rig.client.calls)
    for delay in (1, 10):
        rig.tick(delay)
        assert rig.state.entry(sha)["status"] == "uploading"
        assert path.exists()
    rig.tick(10)
    row = rig.state.entry(sha)
    assert row["status"] == "stored"
    assert row["stored_at"] == rig.clock.now
    assert path.exists() is not deleted
    assert (row["deleted_at"] == rig.clock.now) is deleted
    assert len(uploads(rig.client)) == 1


def test_stored_changed_bytes_are_kept_and_logged(rig, caplog):
    path = rig.file(age=15 * DAY)
    sha = sha256_file(path)
    rig.state.mark(sha, paths=[str(path)], status="stored", stored_at=START)
    path.write_bytes(b"replacement synthetic bytes")
    os.utime(path, (START - 15 * DAY, START - 15 * DAY))
    rig.tick()
    assert path.exists()
    assert rig.state.entry(sha)["deleted_at"] is None
    assert "changed" in caplog.text.lower()


@pytest.mark.parametrize("error", [api.Refused(), api.Offline()])
def test_refusal_window_and_daily_retry(rig, error):
    path = rig.file()
    sha = sha256_file(path)
    rig.client.error = error
    rig.discover()
    first = rig.clock.now
    assert rig.state.entry(sha)["first_refused_at"] == first
    for step in range(1, 31):
        calls = len(rig.client.calls)
        rig.tick(599)
        assert len(rig.client.calls) == calls
        rig.tick(1)
        row = rig.state.entry(sha)
        if step < 30:
            assert row["next_try"] == rig.clock.now + 600
            assert row["status"] == "pending"
    assert rig.clock.now - first == 5 * 3600
    assert row["status"] == "failed"
    assert row["failures"] == 1  # the whole window counts as one failure
    assert row["next_try"] == rig.clock.now + DAY
    assert row["first_refused_at"] is None
    assert path.exists()
    rig.client.error = None
    rig.tick(DAY)
    assert rig.state.entry(sha)["status"] == "uploading"


@pytest.mark.parametrize("kind", ["rejected", "failed"])
def test_failed_or_rejected_is_kept_and_retried_daily(rig, kind):
    path = rig.file()
    sha = sha256_file(path)
    if kind == "rejected":
        rig.client.error = api.Rejected("bad replay")
    else:
        rig.client.statuses = [{"status": "failed"}]
    rig.discover()
    if kind == "failed":
        rig.tick(10)
    assert rig.state.entry(sha)["status"] == kind
    assert rig.state.entry(sha)["next_try"] == rig.clock.now + DAY
    assert path.exists()
    calls = len(rig.client.calls)
    rig.tick(DAY - 1)
    assert len(rig.client.calls) == calls
    rig.client.error = None
    rig.tick(1)
    assert rig.state.entry(sha)["status"] == "uploading"


@pytest.mark.parametrize("terminal", ["stored", "failed"])
def test_one_slot_with_status_backoff_and_restart(rig, terminal):
    first = rig.file("a.vrf", b"first")
    second = rig.file("b.vrf", b"second")
    rig.discover()
    assert uploads(rig.client) == [("upload", "a.vrf")]
    rig.client.statuses = [api.Offline(), api.Refused()]
    rig.tick(10)
    assert rig.state.entry(sha256_file(first))["next_try"] == rig.clock.now + 600
    rig.restart()
    rig.tick(5)
    assert uploads(rig.client) == [("upload", "a.vrf")]
    rig.tick(595)
    rig.tick(599)
    assert uploads(rig.client) == [("upload", "a.vrf")]
    rig.client.statuses = [{"status": terminal}]
    rig.tick(1)
    rig.tick(10)
    assert uploads(rig.client) == [("upload", "a.vrf"), ("upload", "b.vrf")]
    assert rig.state.entry(sha256_file(second))["status"] == "uploading"


@pytest.mark.parametrize("restart", [False, True])
def test_bad_key_blocks_then_replacement_rechecks_uploading_and_keeps_stored(rig, restart):
    first = rig.file("a.vrf", b"first")
    sha = sha256_file(first)
    stored = rig.file("c.vrf", b"stored")
    stored_sha = sha256_file(stored)
    rig.state.mark(stored_sha, paths=[str(stored)], status="stored", stored_at=START)
    rig.discover()
    rig.client.error = api.BadKey()
    rig.tick(10)
    assert rig.engine.status()["needs_key"]
    calls = len(rig.client.calls)
    rig.tick(10)
    assert len(rig.client.calls) == calls
    if restart:
        rig.restart()
        rig.tick()
        assert rig.engine.status()["needs_key"]
    new = Client()
    rig.engine.key_replaced(new)
    assert not rig.engine.status()["needs_key"]
    assert rig.state.entry(sha)["status"] == "pending"
    assert rig.state.entry(sha)["upload_id"] is None
    assert rig.state.entry(sha)["next_try"] == rig.clock.now
    assert rig.state.entry(stored_sha)["status"] == "stored"
    rig.tick()
    assert new.calls == [("check", sha), ("upload", "a.vrf")]


@pytest.mark.parametrize("operation", ["key_replaced", "status", "idle"])
@pytest.mark.parametrize("response", ["parsing", "stored"])
def test_tick_serializes_key_replacement_but_never_blocks_status_reads(rig, monkeypatch, operation, response):
    """R2.1 F5: state changes stay serial (key replacement waits for the tick); the tray's reads never wait."""
    path = rig.file()
    sha = sha256_file(path)
    rig.discover()
    polling, finish_poll, attempted, completed = (Event() for _ in range(4))
    order, errors, snapshots = [], [], []
    replacement = Client()

    def blocked_status(upload_id):
        # A client callback may read status on the tick's own thread: the guard
        # must be reentrant, while excluding callbacks on the tray thread.
        assert rig.engine.status()["state"] == "uploading"
        order.append("poll_entered")
        polling.set()
        assert finish_poll.wait(3)
        order.append("poll_returned")
        return {"status": response}

    original_mark = rig.state.mark

    def record_mark(key, **changes):
        original_mark(key, **changes)
        order.append("wrote_" + changes.get("status", "poll_time"))

    monkeypatch.setattr(rig.client, "status", blocked_status)
    monkeypatch.setattr(rig.state, "mark", record_mark)

    def tick():
        try:
            rig.tick(constants.STATUS_POLL_S)
        except BaseException as error:
            errors.append(error)

    def tray_call():
        try:
            order.append("tray_attempted")
            attempted.set()
            if operation == "key_replaced":
                rig.engine.key_replaced(replacement)
            elif operation == "idle":
                snapshots.append(rig.engine.idle())
            else:
                snapshots.append(rig.engine.status())
            order.append("tray_completed")
        except BaseException as error:
            errors.append(error)
        finally:
            completed.set()

    worker = Thread(target=tick, daemon=True)
    tray = Thread(target=tray_call, daemon=True)
    worker.start()
    try:
        assert polling.wait(2)
        tray.start()
        assert attempted.wait(2)
        if operation == "key_replaced":
            assert not completed.wait(0.1), order
        else:
            # A read of the tray's status returns at once, while the poll is still held.
            assert completed.wait(1), order
            assert "poll_returned" not in order
    finally:
        finish_poll.set()
        worker.join(3)
        if tray.ident is not None:
            tray.join(3)
    assert not worker.is_alive() and not tray.is_alive()
    assert not errors
    tick_write = "wrote_stored" if response == "stored" else "wrote_poll_time"
    assert order.index("tray_attempted") < order.index("poll_returned")
    if operation == "key_replaced":
        assert order.index("poll_returned") < order.index(tick_write) < order.index("tray_completed")
    else:
        assert order.index("tray_completed") < order.index("poll_returned")
    expected = "stored" if response == "stored" else (
        "pending" if operation == "key_replaced" else "uploading")
    assert rig.state.entry(sha)["status"] == expected
    assert State(env=rig.env).entry(sha)["status"] == expected
    if operation == "key_replaced":
        assert rig.engine.client is replacement
        if response == "parsing":
            assert order.index(tick_write) < order.index("wrote_pending")
            assert rig.state.entry(sha)["upload_id"] is None
    elif operation == "status":
        assert snapshots[0]["state"] == "uploading"  # one consistent snapshot from before the poll returned
    else:
        assert snapshots == [False]


def test_status_not_found_requeues_for_check(rig):
    path = rig.file()
    sha = sha256_file(path)
    rig.discover()
    rig.client.statuses = [api.NotFound()]
    rig.tick(10)
    row = rig.state.entry(sha)
    assert row["status"] == "pending"
    assert row["upload_id"] is None
    rig.client.known = True
    rig.tick(10)
    assert rig.state.entry(sha)["status"] == "stored"
    assert len(uploads(rig.client)) == 1


def test_uploading_refusals_eventually_release_slot(rig):
    first = rig.file("a.vrf", b"first")
    rig.file("b.vrf", b"second")
    sha = sha256_file(first)
    rig.discover()
    rig.client.error = api.Offline()
    rig.tick(10)
    rig.tick(5 * 3600)
    assert rig.state.entry(sha)["status"] == "failed"
    assert rig.state.entry(sha)["next_try"] == rig.clock.now + DAY
    assert rig.state.entry(sha)["first_refused_at"] is None
    assert uploads(rig.client) == [("upload", "a.vrf")]
    rig.client.error = None
    rig.tick(10)
    assert uploads(rig.client) == [("upload", "a.vrf")]  # the site was just seen down: engine-wide wait
    rig.tick(constants.REFUSED_RETRY_S)
    assert uploads(rig.client)[-1] == ("upload", "b.vrf")


def test_status_poll_retains_slot_if_local_file_disappears(rig):
    first = rig.file("a.vrf", b"first")
    sha = sha256_file(first)
    rig.file("b.vrf", b"second")
    rig.discover()
    first.unlink()
    rig.tick(10)
    assert rig.state.entry(sha)["status"] == "uploading"
    assert uploads(rig.client) == [("upload", "a.vrf")]
    rig.client.statuses = [{"status": "stored"}]
    rig.tick(10)
    assert rig.state.entry(sha)["status"] == "stored"
    assert rig.state.entry(sha)["paths"] == []


@pytest.mark.parametrize("rename", [False, True])
def test_stored_duplicate_or_rename_tracks_each_path_and_deletes_old_copies(rig, rename):
    original = rig.file(age=13 * DAY)
    sha = sha256_file(original)
    rig.client.known = True
    rig.discover()
    copy = rig.demos / "copy.vrf"
    if rename:
        original.rename(copy)
    else:
        copy.write_bytes(original.read_bytes())
    os.utime(copy, (START - 15 * DAY, START - 15 * DAY))
    rig.discover()
    assert not copy.exists()
    row = rig.state.entry(sha)
    assert row["paths"] == ([] if rename else [str(original)])
    assert not uploads(rig.client)
    assert row["deleted_at"] == rig.clock.now


@pytest.mark.parametrize("change", ["removed", "locked", "replaced"])
def test_due_entry_revalidated_before_transmission(rig, change):
    path = rig.file()
    sha = sha256_file(path)
    rig.client.error = api.Rejected()
    rig.discover()
    attempts = rig.state.entry(sha)["attempts"]
    calls = len(rig.client.calls)
    rig.client.error = None
    if change == "removed":
        path.unlink()
    elif change == "locked":
        rig.locked = True
    else:
        path.write_bytes(b"new synthetic replay bytes")
    rig.tick(DAY)
    assert len(rig.client.calls) == calls
    if change == "locked":
        assert rig.state.entry(sha)["attempts"] == attempts
        rig.locked = False
        rig.tick(10)
        assert len(uploads(rig.client)) == 1
    else:
        assert rig.state.entry(sha) is None
        if change == "replaced":
            rig.tick(5)
            rig.tick(5)
            assert rig.state.entry(sha256_file(path))["status"] == "uploading"
            assert ("check", sha256_file(path)) in rig.client.calls


def test_file_changed_by_check_is_not_transmitted(rig):
    path = rig.file()
    sha = sha256_file(path)

    def check(value):
        path.write_bytes(b"bytes changed during check")
        return False

    rig.client.check = check
    rig.discover()
    assert not uploads(rig.client)
    assert rig.state.entry(sha) is None


def test_restart_resumes_upload_id_and_pause_does_nothing(rig):
    path = rig.file()
    sha = sha256_file(path)
    rig.discover()
    upload_id = rig.state.entry(sha)["upload_id"]
    rig.restart()
    rig.config.paused = True
    calls = list(rig.client.calls)
    rig.tick(10)
    assert rig.client.calls == calls
    assert rig.engine.status()["paused"]
    rig.config.paused = False
    rig.client.statuses = [{"status": "stored"}]
    rig.tick()
    assert rig.client.calls[-1] == ("status", upload_id)
    assert rig.state.entry(sha)["status"] == "stored"


def test_paused_engine_neither_discovers_nor_deletes(rig):
    old = rig.file("stored.vrf", b"stored", age=15 * DAY)
    sha = sha256_file(old)
    rig.state.mark(sha, paths=[str(old)], status="stored", stored_at=START)
    pending = rig.file("new.vrf", b"new")
    rig.config.paused = True
    rig.discover()
    assert old.exists()
    assert rig.state.entry(sha256_file(pending)) is None
    assert rig.client.calls == []


def test_deletion_failure_keeps_path_for_later_sweep(rig):
    path = rig.file(age=15 * DAY)
    sha = sha256_file(path)
    rig.client.known = True
    rig.engine.deleter = Mock(side_effect=PermissionError("locked"))
    rig.discover()
    assert path.exists()
    assert rig.state.entry(sha)["paths"] == [str(path)]
    assert rig.state.entry(sha)["deleted_at"] is None
    rig.engine.deleter = os.remove
    rig.tick(10)
    assert path.exists()  # a locked file is not retried every tick
    rig.tick(constants.REFUSED_RETRY_S)
    assert not path.exists()
    assert rig.state.entry(sha)["deleted_at"] == rig.clock.now


# --- deletion needs the site to hold that exact file's replay (kept) -----------------------------

def kept_calls(client):
    return [call for call in client.calls if call[0] == "kept"]


def stored(rig, name="old.vrf", content=b"old replay", age=15 * DAY, **fields):
    path = rig.file(name, content, age=age)
    sha = sha256_file(path)
    rig.state.mark(sha, paths=[str(path)], status="stored", stored_at=START, **fields)
    return path, sha


def test_stored_old_and_kept_is_deleted_after_asking_once(rig):
    path, sha = stored(rig)
    rig.tick()
    assert not path.exists()
    assert kept_calls(rig.client) == [("kept", sha)]
    assert rig.state.entry(sha)["deleted_at"] == rig.clock.now


def test_not_kept_stays_forever_is_never_uploaded_and_is_asked_again_after_a_day(rig):
    rig.client.kept_value = False
    path, sha = stored(rig)
    rig.tick()
    for _ in range(20):
        rig.tick(10)
    assert path.exists() and rig.state.entry(sha)["status"] == "stored"
    assert len(kept_calls(rig.client)) == 1
    assert not uploads(rig.client) and not any(c[0] == "check" for c in rig.client.calls)
    rig.tick(constants.FAILED_RETRY_S - 300)
    assert len(kept_calls(rig.client)) == 1
    rig.tick(300)
    assert len(kept_calls(rig.client)) == 2 and path.exists()
    rig.client.kept_value = True
    rig.tick(constants.FAILED_RETRY_S)
    assert not path.exists()
    assert not uploads(rig.client)


def test_younger_than_keep_days_makes_no_site_call(rig):
    path, sha = stored(rig, age=13 * DAY)
    rig.tick()
    rig.tick(10)
    assert path.exists() and rig.client.calls == []


def test_keep_days_zero_still_needs_kept(rig):
    rig.config.keep_days = 0
    rig.client.kept_value = False
    path, sha = stored(rig, age=0)
    rig.tick()
    assert path.exists() and len(kept_calls(rig.client)) == 1


@pytest.mark.parametrize("error", [api.Offline("down"), api.Refused(5), RuntimeError("bug"),
                                   api.Rejected("no"), api.NotFound("gone")])
def test_site_error_keeps_the_file_and_retries_in_ten_minutes(rig, error):
    path, sha = stored(rig)
    rig.client.kept_value = error
    rig.tick()
    assert path.exists() and len(kept_calls(rig.client)) == 1
    rig.tick(constants.REFUSED_RETRY_S - 10)
    assert len(kept_calls(rig.client)) == 1
    rig.client.kept_value = True
    rig.tick(10)
    assert len(kept_calls(rig.client)) == 2 and not path.exists()


def test_bad_key_on_the_check_sets_needs_key_and_deletes_nothing(rig):
    path, sha = stored(rig)
    rig.client.kept_value = api.BadKey("revoked")
    rig.tick()
    assert rig.engine.needs_key and path.exists()
    rig.client.kept_value = True
    rig.tick(10)
    assert path.exists() and len(kept_calls(rig.client)) == 1  # needs_key: no deletion at all
    new = Client()
    rig.engine.key_replaced(new)
    rig.tick(10)
    assert not path.exists() and len(kept_calls(new)) == 1


def test_changed_file_is_kept_without_a_site_call_and_not_rehashed_every_tick(rig, monkeypatch):
    import valo_uploader.engine as engine

    path, sha = stored(rig, age=13 * DAY)
    rig.discover()
    info = path.stat()
    path.write_bytes(b"x" * info.st_size)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    rig.tick(2 * DAY)
    assert counted.call_count == 1
    rig.engine.watcher.poll = lambda now: []  # discovery re-reading the changed file is not under test
    for _ in range(10):
        rig.tick(10)
    assert counted.call_count == 1
    assert path.exists() and rig.client.calls == []
    rig.tick(constants.REFUSED_RETRY_S)
    assert counted.call_count == 2


def test_locked_delete_is_not_retried_every_tick(rig):
    path, sha = stored(rig)
    rig.engine.deleter = Mock(side_effect=PermissionError("locked"))
    rig.tick()
    for _ in range(10):
        rig.tick(10)
    assert rig.engine.deleter.call_count == 1 and len(kept_calls(rig.client)) == 1
    assert rig.state.entry(sha)["paths"] == [str(path)]


def test_two_paths_of_one_sha_ask_once_and_both_are_deleted(rig):
    first, sha = stored(rig, "a.vrf")
    second = rig.file("b.vrf", b"old replay", age=15 * DAY)
    rig.state.mark(sha, paths=[str(first), str(second)])
    rig.tick()
    assert not first.exists() and not second.exists()
    assert kept_calls(rig.client) == [("kept", sha)]
    assert rig.state.entry(sha)["paths"] == []


def test_a_path_that_is_gone_is_dropped_without_a_site_call(rig):
    path, sha = stored(rig)
    path.unlink()
    rig.tick()
    assert rig.state.entry(sha)["paths"] == [] and rig.client.calls == []


def _backlog(rig, count, age):
    return [stored(rig, f"r{n}.vrf", f"replay {n}".encode(), age=age)[0] for n in range(count)]


def test_a_large_backlog_is_handled_five_entries_per_tick(rig, monkeypatch):
    import valo_uploader.engine as engine

    assert constants.DELETE_BATCH == 5
    paths = _backlog(rig, 12, 13 * DAY)
    rig.discover()  # scan hashes are cached; no entry is old enough yet
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    rig.tick(2 * DAY)
    assert counted.call_count == 5 and len(kept_calls(rig.client)) == 5
    rig.tick(10)
    assert counted.call_count == 10
    rig.tick(10)
    assert counted.call_count == 12
    assert not any(path.exists() for path in paths)


def test_not_kept_backlog_entries_do_not_starve_each_other(rig):
    rig.client.kept_value = False
    paths = _backlog(rig, 12, 15 * DAY)
    for _ in range(4):
        rig.tick(10)
    asked = [call[1] for call in kept_calls(rig.client)]
    assert len(asked) == 12 and len(set(asked)) == 12
    assert all(path.exists() for path in paths)


def test_throttled_entries_do_not_use_up_the_batch(rig):
    rig.client.kept_value = False
    shas = [stored(rig, f"t{n}.vrf", f"throttled {n}".encode())[1] for n in range(6)]
    for sha in shas:
        rig.state.mark(sha, next_delete_check=START + DAY)
    due = [stored(rig, f"d{n}.vrf", f"due {n}".encode())[1] for n in range(3)]
    rig.tick()
    assert sorted(call[1] for call in kept_calls(rig.client)) == sorted(due)


def test_state_file_without_next_delete_check_still_works(rig):
    import json

    path, sha = stored(rig)
    data = json.loads(rig.state.path.read_text(encoding="utf-8"))
    del data[sha]["next_delete_check"]
    rig.state.path.write_text(json.dumps(data), encoding="utf-8")
    rig.restart()
    assert "next_delete_check" not in rig.state.entry(sha)
    rig.tick()
    assert not path.exists()


def test_state_drop_and_snapshot_are_durable(rig):
    path = rig.file()
    sha = sha256_file(path)
    rig.state.mark(sha, paths=[str(path)])
    snapshot = rig.state.items()
    snapshot[0][1]["paths"].clear()
    assert rig.state.entry(sha)["paths"] == [str(path)]
    rig.state.drop(sha)
    rig.state.drop(sha)
    assert State(env=rig.env).entry(sha) is None


@pytest.mark.parametrize("output,expected", [
    ('"VALORANT-Win64-Shipping.exe","123","Console","1","42,000 K"\r\n', True),
    ('"valorant-win64-shipping.exe","123","Console","1","42,000 K"\r\n', True),
    ('"other.exe","1","Console","1","1 K"\r\n"VALORANT-Win64-Shipping.exe","123","Console","1","42,000 K"\r\n', True),
    ('"VALORANT-Win64-Shipping.exe.bak","123","Console","1","42,000 K"\r\n', False),
    ('"VALORANT-Win64-Ship","123","Console","1","42,000 K"\r\n', False),
    ("INFO: No tasks are running which match the specified criteria.\r\n", False),
    ('"other.exe","123","Console","1","42,000 K"\r\n', False),
    ("", False),
    ('"unterminated', False),
])
def test_process_listing(output, expected):
    def listing(args, **kwargs):
        assert args == ["tasklist", "/FI", "IMAGENAME eq " + constants.PROCESS_NAME, "/NH",
                        "/FO", "CSV"]
        assert kwargs["timeout"] > 0
        if os.name == "nt":
            assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
        return subprocess.CompletedProcess(args, 0, stdout=output)

    assert valorant_running(run=listing) is expected


def test_process_listing_errors_are_false():
    def broken(*args, **kwargs):
        raise OSError("not available")

    assert not valorant_running(run=broken)
    assert not valorant_running(run=lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=""))


@pytest.mark.parametrize("running,queue,interval", [(False, 0, 1800), (True, 0, 10),
                                                   (False, 1, 10)])
def test_runner_cadence(running, queue, interval):
    clock = Clock()
    stop = Event()
    ticks, polls = [], []

    class FakeEngine:
        def __init__(self):
            self.clock = clock
            self.sleep = clock.sleep

        def procs(self):
            polls.append(clock.now)
            return running

        def tick(self, now):
            ticks.append(now)
            if len(ticks) == 3:
                stop.set()

        def status(self):
            return {"queue_length": queue}

    run(FakeEngine(), stop)
    assert ticks == [START, START + interval, START + 2 * interval]
    assert all(b - a == 5 for a, b in zip(polls, polls[1:]))


@pytest.mark.parametrize("statuses,interval,queue", [
    (("failed", "rejected"), 1800, 0), (("pending",), 10, 1), (("uploading",), 10, 1),
])
def test_real_engine_queue_and_idle_control_runner_cadence(rig, monkeypatch, statuses, interval, queue):
    for index, status in enumerate(statuses):
        path = rig.file(f"replay-{index}.vrf", bytes([index]))
        rig.state.mark(sha256_file(path), paths=[str(path)], status=status,
                       next_try=START + 10 * DAY)
    assert rig.engine.status()["queue_length"] == queue
    assert rig.engine.idle() is (queue == 0)
    assert rig.engine.status()["state"] == ("idle" if queue == 0 else statuses[0].replace("pending", "queued"))
    rig.watcher.poll(START - 10)  # the files were seen long ago: nothing awaits a second look
    stop = Event()
    ticks = []
    tick = rig.engine.tick

    def recorded(now):
        ticks.append(now)
        tick(now)
        if len(ticks) == 3:
            stop.set()

    monkeypatch.setattr(rig.engine, "tick", recorded)
    run(rig.engine, stop)
    assert ticks == [START, START + interval, START + 2 * interval]


@pytest.mark.parametrize("offline", [False, True])
def test_failed_entry_is_retried_on_next_idle_tick(rig, monkeypatch, offline):
    path = rig.file()
    sha = sha256_file(path)
    rig.state.mark(sha, paths=[str(path)], status="failed", next_try=START + 600)
    rig.client.known = True
    if offline:
        rig.client.error = api.Offline()
    rig.watcher.poll(START - 10)  # seen long ago: nothing awaits a second look
    stop = Event()
    ticks = []
    tick = rig.engine.tick

    def recorded(now):
        ticks.append(now)
        tick(now)
        if len(ticks) == 2:
            stop.set()

    monkeypatch.setattr(rig.engine, "tick", recorded)
    run(rig.engine, stop)
    assert ticks == [START, START + 1800]
    assert rig.client.calls == [("check", sha)]
    if offline:
        assert rig.state.entry(sha)["status"] == "pending"
        assert rig.state.entry(sha)["next_try"] == START + 1800 + 600
        assert rig.engine.status()["queue_length"] == 1
        assert not rig.engine.idle()
    else:
        assert rig.state.entry(sha)["status"] == "stored"
        assert rig.engine.idle()


def test_runner_rescans_immediately_on_valorant_start():
    clock = Clock()
    stop = Event()
    ticks = []

    class FakeEngine:
        def __init__(self):
            self.clock = clock
            self.sleep = clock.sleep

        def procs(self):
            return clock.now >= START + 5

        def tick(self, now):
            ticks.append(now)
            if len(ticks) == 3:
                stop.set()

        def status(self):
            return {"queue_length": 0}

    run(FakeEngine(), stop)
    assert ticks == [START, START + 5, START + 15]


def test_runner_stays_active_after_game_closes_until_queue_drains():
    clock = Clock()
    stop = Event()
    ticks = []

    class FakeEngine:
        def __init__(self):
            self.clock = clock
            self.sleep = clock.sleep

        def procs(self):
            return clock.now == START

        def tick(self, now):
            ticks.append(now)
            if len(ticks) == 4:
                stop.set()

        def status(self):
            return {"queue_length": int(len(ticks) < 3)}

    run(FakeEngine(), stop)
    # The game closing ticks once at once (R2.1 F1), then fast scans while work remains.
    assert ticks == [START, START + 5, START + 15, START + 1815]


def test_runner_stop_before_start_does_no_work():
    stop = Event()
    stop.set()
    engine = Mock()
    run(engine, stop)
    engine.tick.assert_not_called()
    engine.procs.assert_not_called()


@pytest.mark.parametrize("code", [404, 500, 502, 504, 599])
def test_additional_api_error_mapping(code):
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError("https://example.invalid", code, "synthetic error", {}, io.BytesIO(b"{}"))

    expected = api.NotFound if code == 404 else api.Offline
    with pytest.raises(expected):
        api.Client("https://example.invalid", "synthetic-key", opener=Opener()).key_info()


# --- R1.1: held replays, unavailable first file, unexpected exceptions ---------------------------

def make_held(rig, name="held.vrf", content=b"held replay"):
    """Discover a file and move it to held through the real status path."""
    path = rig.file(name, content)
    sha = sha256_file(path)
    rig.discover()
    assert rig.state.entry(sha)["status"] == "uploading"
    rig.client.statuses = [{"status": "held"}]
    rig.tick(10)
    return path, sha


def test_held_keeps_id_frees_slot_and_is_not_queued(rig):
    path, sha = make_held(rig)
    row = rig.state.entry(sha)
    assert row["status"] == "held"
    assert row["upload_id"]
    assert row["next_try"] == rig.clock.now + constants.IDLE_SCAN_S
    status = rig.engine.status()
    assert status["queue_length"] == 0 and status["state"] == "idle"
    assert rig.engine.idle()
    # A supported replay behind it uploads, and the held sha is never uploaded again.
    other = rig.file("next.vrf", b"supported replay")
    rig.discover()
    rig.tick(10)
    assert [call[1] for call in uploads(rig.client)] == ["held.vrf", "next.vrf"]
    assert rig.state.entry(sha256_file(other))["status"] == "uploading"
    assert rig.state.entry(sha)["status"] == "held"
    # Seeing the same bytes again (copy or rescan) adds a path but never uploads.
    rig.file("copy.vrf", b"held replay")
    rig.discover()
    rig.tick(10)
    assert [call[1] for call in uploads(rig.client)] == ["held.vrf", "next.vrf"]
    assert rig.state.entry(sha)["status"] == "held"


def test_held_survives_restart_and_polls_at_most_every_half_hour(rig):
    path, sha = make_held(rig)
    rig.restart()
    polls = lambda: [c for c in rig.client.calls if c[0] == "status"]
    before = len(polls())
    rig.tick(constants.IDLE_SCAN_S - 1)
    assert len(polls()) == before
    rig.client.statuses = [{"status": "held"}]
    rig.tick(1)
    assert len(polls()) == before + 1
    assert rig.state.entry(sha)["status"] == "held"
    assert rig.state.entry(sha)["next_try"] == rig.clock.now + constants.IDLE_SCAN_S
    rig.tick(constants.IDLE_SCAN_S - 1)
    assert len(polls()) == before + 1


@pytest.mark.parametrize("outcome,expected", [("stored", "stored"), ("failed", "failed"),
                                              ("parsing", "uploading")])
def test_held_outcomes(rig, outcome, expected):
    path, sha = make_held(rig)
    rig.client.statuses = [{"status": outcome}]
    rig.tick(constants.IDLE_SCAN_S)
    row = rig.state.entry(sha)
    assert row["status"] == expected
    if expected == "stored":
        assert path.name in rig.engine.status()["last_stored"]
        assert row["stored_at"] == rig.clock.now
    if expected == "failed":
        assert row["upload_id"] is None and row["next_try"] == rig.clock.now + DAY
    if expected == "uploading":
        assert row["upload_id"]
        assert rig.engine.status()["state"] == "uploading"


def test_stored_after_held_is_deleted_by_retention_rules(rig):
    path, sha = make_held(rig)
    os.utime(path, (rig.clock.now - 20 * DAY, rig.clock.now - 20 * DAY))
    rig.client.statuses = [{"status": "stored"}]
    rig.tick(constants.IDLE_SCAN_S)
    assert rig.state.entry(sha)["status"] == "stored"
    assert not path.exists()


def test_held_notfound_and_key_replacement_behave_like_uploading(rig):
    path, sha = make_held(rig)
    old_id = rig.state.entry(sha)["upload_id"]
    rig.client.statuses = [api.NotFound()]
    rig.tick(constants.IDLE_SCAN_S)
    # Back to pending, then sent again as a new upload in the freed slot.
    assert rig.state.entry(sha)["upload_id"] != old_id
    assert [call[1] for call in uploads(rig.client)] == ["held.vrf", "held.vrf"]
    rig.client.statuses = [{"status": "stored"}]
    rig.tick(constants.STATUS_POLL_S)
    assert rig.state.entry(sha)["status"] == "stored"
    path2, sha2 = make_held(rig, "h2.vrf", b"second held")
    replacement = Client()
    rig.engine.key_replaced(replacement)
    assert rig.state.entry(sha2)["status"] == "pending"
    assert rig.state.entry(sha2)["upload_id"] is None


def test_held_poll_offline_keeps_held_and_backs_off(rig):
    path, sha = make_held(rig)
    rig.client.statuses = [api.Offline()]
    rig.tick(constants.IDLE_SCAN_S)
    row = rig.state.entry(sha)
    assert row["status"] == "held"
    assert row["next_try"] == rig.clock.now + constants.REFUSED_RETRY_S


def test_held_does_not_block_uploading_slot_polls(rig):
    path, sha = make_held(rig)
    other = rig.file("next.vrf", b"supported replay")
    rig.discover()
    assert rig.state.entry(sha256_file(other))["status"] == "uploading"
    rig.client.statuses = [{"status": "stored"}]
    rig.tick(10)
    assert rig.state.entry(sha256_file(other))["status"] == "stored"
    assert rig.state.entry(sha)["status"] == "held"


def test_held_state_round_trips_through_state_file(rig):
    path, sha = make_held(rig)
    assert State(env=rig.env).entry(sha)["status"] == "held"


@pytest.mark.parametrize("failure", ["locked", "hash_error"])
def test_unavailable_first_due_file_does_not_starve_later_files(rig, monkeypatch, failure):
    import valo_uploader.engine as engine

    first = rig.file("a.vrf", b"first replay")
    second = rig.file("b.vrf", b"second replay")
    sha_first, sha_second = sha256_file(first), sha256_file(second)
    rig.client.known = True
    rig.engine.needs_key = True          # discover only, send nothing yet
    rig.discover()
    rig.engine.needs_key = False
    order = list(rig.state.due(rig.clock.now))
    assert set(order) == {sha_first, sha_second}
    blocked = order[0]
    blocked_path = first if blocked == sha_first else second
    free = sha_second if blocked == sha_first else sha_first
    if failure == "locked":
        rig.watcher.exclusive_ok = lambda path: path != blocked_path
    elif failure == "hash_error":
        real = engine.sha256_file

        def flaky(path):
            if path == blocked_path:
                raise PermissionError("locked")
            return real(path)

        monkeypatch.setattr(engine, "sha256_file", flaky)
    rig.tick()
    assert ("check", free) in rig.client.calls
    assert rig.state.entry(free)["status"] == "stored"
    row = rig.state.entry(blocked)
    assert row["status"] == "pending"
    assert row["attempts"] == 0, "readiness failures are not upload attempts"
    assert row["next_try"] > rig.clock.now
    # Once the file is available again it is uploaded on a later tick.
    rig.watcher.exclusive_ok = lambda path: True
    monkeypatch.undo()
    rig.tick(constants.STATUS_POLL_S)
    assert rig.state.entry(blocked)["status"] == "stored"


def test_missing_first_file_is_dropped_and_next_file_proceeds(rig):
    first = rig.file("a.vrf", b"first replay")
    second = rig.file("b.vrf", b"second replay")
    rig.client.known = True
    rig.engine.needs_key = True
    rig.discover()
    rig.engine.needs_key = False
    order = list(rig.state.due(rig.clock.now))
    gone = first if order[0] == sha256_file(first) else second
    gone.unlink()
    rig.tick()
    assert rig.state.entry(order[0]) is None
    assert rig.state.entry(order[1])["status"] == "stored"


def test_oserror_during_upload_defers_instead_of_blocking(rig):
    first = rig.file("a.vrf", b"first replay")
    second = rig.file("b.vrf", b"second replay")
    rig.engine.needs_key = True
    rig.discover()
    rig.engine.needs_key = False
    order = list(rig.state.due(rig.clock.now))
    failing = first.name if order[0] == sha256_file(first) else second.name
    original = rig.client.upload

    def upload(path):
        if Path(path).name == failing:
            raise PermissionError("locked")
        return original(path)

    rig.client.upload = upload
    rig.tick()
    assert [call[1] for call in uploads(rig.client)] == [
        second.name if failing == first.name else first.name]


def test_unexpected_exception_in_one_entry_is_logged_and_others_continue(rig, caplog):
    first = rig.file("a.vrf", b"first replay")
    second = rig.file("b.vrf", b"second replay")
    rig.engine.needs_key = True
    rig.discover()
    rig.engine.needs_key = False
    order = list(rig.state.due(rig.clock.now))
    broken = order[0]
    original = rig.client.check

    def check(sha):
        if sha == broken:
            raise RuntimeError("boom")
        return original(sha)

    rig.client.check = check
    with caplog.at_level("ERROR"):
        rig.tick()                                   # must not raise
    assert any("boom" in (r.exc_text or "") or "failed" in r.getMessage().lower()
               for r in caplog.records)
    row = rig.state.entry(broken)
    assert row["next_try"] > rig.clock.now
    rig.tick(constants.STATUS_POLL_S)
    assert rig.state.entry(order[1])["status"] == "uploading"


def test_unmapped_4xx_on_upload_uploads_once_rejects_and_lets_next_file_proceed(rig):
    first = rig.file("a.vrf", b"first replay")
    second = rig.file("b.vrf", b"second replay")
    rig.engine.needs_key = True
    rig.discover()
    rig.engine.needs_key = False
    order = list(rig.state.due(rig.clock.now))
    bad = first.name if order[0] == sha256_file(first) else second.name
    original = rig.client.upload

    def upload(path):
        rig.client.calls.append(("upload", Path(path).name))
        if Path(path).name == bad:
            raise api.Rejected("too big")
        return "upload-ok"

    rig.client.upload = upload
    for _ in range(12):
        rig.tick(constants.STATUS_POLL_S)
    names = [call[1] for call in uploads(rig.client)]
    assert names.count(bad) == 1
    assert len(names) == 2
    bad_path = first if bad == first.name else second
    row = rig.state.entry(sha256_file(bad_path))
    assert row["status"] == "rejected"
    assert row["next_try"] > rig.clock.now + DAY - 200
    assert bad_path.exists()
    other = second if bad_path == first else first
    assert rig.state.entry(sha256_file(other))["status"] in ("uploading", "stored")


def test_http_413_through_real_client_uploads_once(rig):
    path = rig.file()
    sha = sha256_file(path)
    seen = []

    class Opener:
        def open(self, request, timeout):
            data = request.data
            if data is not None and not isinstance(data, bytes):
                b"".join(data)
            seen.append(request.full_url)
            if request.full_url.endswith("/check"):
                return io.BytesIO(b'{"known": false}')
            raise HTTPError(request.full_url, 413, "too large", {}, io.BytesIO(b"{}"))

    rig.restart(api.Client("https://example.invalid", "key", opener=Opener()))
    for _ in range(10):
        rig.tick(constants.STATUS_POLL_S)
    assert [u for u in seen if u.endswith("/upload")] == [seen[1]]
    assert rig.state.entry(sha)["status"] == "rejected"
    assert path.exists()


def test_held_entry_poll_rejected_stays_held(rig):
    path, sha = make_held(rig)
    rig.client.statuses = [api.Rejected("forbidden")]
    rig.tick(constants.IDLE_SCAN_S)
    row = rig.state.entry(sha)
    assert row["status"] == "held"
    assert row["upload_id"]
    assert row["next_try"] == rig.clock.now + constants.IDLE_SCAN_S


def test_uploading_entry_poll_rejected_is_rejected_for_a_day(rig):
    path = rig.file()
    sha = sha256_file(path)
    rig.discover()
    rig.client.statuses = [api.Rejected("forbidden")]
    rig.tick(constants.STATUS_POLL_S)
    row = rig.state.entry(sha)
    assert row["status"] == "rejected" and row["next_try"] == rig.clock.now + DAY


def test_unexpected_exception_while_polling_status_is_contained(rig):
    path = rig.file()
    sha = sha256_file(path)
    rig.discover()
    rig.client.statuses = [{"unexpected": "shape"}]   # no "status" key
    rig.tick(10)                                       # must not raise
    assert rig.state.entry(sha)["status"] == "uploading"
    assert rig.state.entry(sha)["next_try"] > rig.clock.now


def test_runner_survives_engine_tick_exception():
    clock = Clock()
    stop = Event()
    ticks = []

    class FakeEngine:
        def __init__(self):
            self.clock = clock
            self.sleep = clock.sleep

        def procs(self):
            return True

        def tick(self, now):
            ticks.append(now)
            if len(ticks) == 3:
                stop.set()
            raise RuntimeError("bug in one tick")

        def status(self):
            return {"queue_length": 0}

    run(FakeEngine(), stop)
    assert ticks == [START, START + 10, START + 20]


# --- R2.1 F1: first-run and game-close scans use the real Watcher ---------------------------------

def run_real(rig, until, limit=4000, procs=None):
    """Drive the real runner on the simulated clock; return the tick times."""
    stop, ticks = Event(), []
    real_tick = rig.engine.tick
    if procs is not None:
        rig.engine.procs = procs

    def recorded(now):
        ticks.append(now)
        real_tick(now)
        if until() or now - START > limit:
            stop.set()

    rig.engine.tick = recorded
    run(rig.engine, stop)
    return ticks


def test_watcher_reports_a_file_awaiting_its_second_look(rig):
    path = rig.file()
    assert not rig.watcher.awaiting
    rig.watcher.poll(0)
    assert rig.watcher.awaiting                       # seen once
    rig.watcher.poll(constants.STABLE_GAP_S - 1)
    assert rig.watcher.awaiting                       # unchanged for less than the gap
    rig.watcher.poll(constants.STABLE_GAP_S)
    assert not rig.watcher.awaiting                   # stable: second look done
    path.write_bytes(b"grown synthetic replay")
    rig.watcher.poll(constants.STABLE_GAP_S + 1)
    assert rig.watcher.awaiting                       # signature changed on the last poll
    rig.locked = True
    rig.watcher.poll(2 * constants.STABLE_GAP_S + 1)
    assert not rig.watcher.awaiting                   # stable but only locked: not awaiting
    path.unlink()
    rig.watcher.poll(100)
    assert not rig.watcher.awaiting


def test_first_run_with_valorant_closed_uploads_within_seconds_not_half_an_hour(rig):
    rig.file()
    ticks = run_real(rig, until=lambda: bool(uploads(rig.client)))
    assert len(uploads(rig.client)) == 1
    assert rig.clock.now - START <= 15, rig.clock.now - START
    assert ticks[:2] == [START, START + constants.ACTIVE_SCAN_S]


def test_replay_finishing_as_the_game_closes_is_picked_up_on_fast_scans(rig):
    path = rig.file("match.vrf", b"recording")
    state = {"close": START + 100}

    def procs():
        running = rig.clock.now < state["close"]
        rig.locked = running
        if running:
            path.write_bytes(path.read_bytes() + b".")   # still being written while the game runs
        return running

    ticks = run_real(rig, until=lambda: bool(uploads(rig.client)), procs=procs)
    assert len(uploads(rig.client)) == 1
    assert rig.clock.now - state["close"] <= 30, rig.clock.now - state["close"]
    assert ticks[-1] < state["close"] + 30


def test_idle_folder_of_finished_files_goes_back_to_the_half_hour_scan(rig):
    rig.client.known = True
    rig.file("a.vrf", b"first")
    rig.file("b.vrf", b"second")
    ticks = run_real(rig, until=lambda: len(rig.client.calls) >= 2 and rig.clock.now > START + 2000)
    fast = constants.ACTIVE_SCAN_S
    # One file is handled per tick, so the second follows on the next fast scan; then the folder is
    # quiet and only the half-hour scan remains.
    assert ticks[:4] == [START, START + fast, START + 2 * fast, START + 2 * fast + constants.IDLE_SCAN_S]


def test_a_locked_stable_file_cannot_keep_the_app_on_fast_scans(rig):
    rig.file()
    rig.locked = True
    ticks = run_real(rig, until=lambda: rig.clock.now > START + 2500)
    assert ticks[0:2] == [START, START + constants.ACTIVE_SCAN_S]
    assert ticks[2] - ticks[1] == constants.IDLE_SCAN_S


# --- R2.1 F2: back off, never resend a match deleted on request, retrigger by app update -----------

BACKOFF_DAYS = [1, 2, 4, 8, 14, 14, 14]


def fail_once(rig, kind, first, gap):
    if kind == "rejected":
        rig.client.error = api.Rejected("bad replay")
        rig.discover() if first else rig.tick(gap)
    else:
        rig.client.error = None
        rig.client.statuses = [{"status": "failed"}]
        rig.discover() if first else rig.tick(gap)
        rig.tick(constants.STATUS_POLL_S)


@pytest.mark.parametrize("kind", ["failed", "rejected"])
def test_failed_and_rejected_back_off_1_2_4_8_then_every_14_days(rig, kind):
    path = rig.file()
    sha = sha256_file(path)
    gap = 0
    for count, days in enumerate(BACKOFF_DAYS, 1):
        fail_once(rig, kind, count == 1, gap)
        row = rig.state.entry(sha)
        assert (row["status"], row["failures"]) == (kind, count)
        assert row["next_try"] == rig.clock.now + days * DAY
        gap = days * DAY
    assert path.exists()


def test_a_retry_that_is_stored_clears_the_failure_count(rig):
    path = rig.file()
    sha = sha256_file(path)
    fail_once(rig, "failed", True, 0)
    fail_once(rig, "failed", False, DAY)
    assert rig.state.entry(sha)["failures"] == 2
    rig.client.statuses = [{"status": "stored"}]
    rig.tick(2 * DAY)
    rig.tick(constants.STATUS_POLL_S)
    row = rig.state.entry(sha)
    assert row["status"] == "stored" and row["failures"] == 0


def test_state_files_without_failures_still_work(rig):
    import json

    path = rig.file()
    sha = sha256_file(path)
    rig.state.mark(sha, paths=[str(path)], status="failed", next_try=START)
    data = json.loads(rig.state.path.read_text(encoding="utf-8"))
    del data[sha]["failures"]
    rig.state.path.write_text(json.dumps(data), encoding="utf-8")
    rig.restart()
    rig.client.error = api.Rejected("bad")
    rig.tick()
    row = rig.state.entry(sha)
    assert (row["status"], row["failures"]) == ("rejected", 1)
    assert row["next_try"] == rig.clock.now + DAY


def test_not_found_and_key_replacement_are_not_failures(rig):
    path = rig.file()
    sha = sha256_file(path)
    fail_once(rig, "failed", True, 0)
    fail_once(rig, "failed", False, DAY)
    before = rig.state.entry(sha)["failures"]
    rig.client.error = api.NotFound()
    rig.tick(2 * DAY)                      # a 404 on /check or /upload
    row = rig.state.entry(sha)
    assert row["status"] == "failed" and row["failures"] == before
    assert row["next_try"] > rig.clock.now
    rig.client.error = None
    other = rig.file("b.vrf", b"second")
    rig.discover()
    other_sha = sha256_file(other)
    assert rig.state.entry(other_sha)["status"] == "uploading"
    rig.engine.key_replaced(Client())
    assert rig.state.entry(other_sha)["failures"] == 0
    assert rig.state.entry(sha)["failures"] == before


def deleted_failure(rig):
    path = rig.file("gone.vrf", b"deleted match")
    sha = sha256_file(path)
    rig.discover()
    rig.client.statuses = [{"status": "failed", "error": "could not store it: this match was deleted on request",
                            "deleted": True}]
    rig.tick(constants.STATUS_POLL_S)
    return path, sha


def test_a_match_deleted_on_request_becomes_refused_and_is_never_sent_again(rig):
    path, sha = deleted_failure(rig)
    row = rig.state.entry(sha)
    assert row["status"] == "refused" and row["upload_id"] is None
    assert path.exists()                                   # kept on disk
    assert rig.state.due(rig.clock.now + 400 * DAY) == []  # never due
    status = rig.engine.status()
    assert status["queue_length"] == 0 and rig.engine.idle() and status["state"] == "idle"
    calls = len(rig.client.calls)
    for _ in range(400):
        rig.tick(DAY)
    rig.file("copy.vrf", b"deleted match")                  # the same bytes again, under a new name
    rig.discover()
    rig.tick(DAY)
    assert len(uploads(rig.client)) == 1 and len(rig.client.calls) == calls
    assert rig.state.entry(sha)["status"] == "refused"
    assert not any(c[0] == "check" and c[1] == sha for c in rig.client.calls[calls:])
    rig.restart()
    rig.tick(DAY)
    assert rig.state.entry(sha)["status"] == "refused"
    rig.engine.key_replaced(Client())
    assert rig.state.entry(sha)["status"] == "refused"
    assert State(env=rig.env).entry(sha)["status"] == "refused"


def test_a_held_match_deleted_on_request_is_refused_too(rig):
    path, sha = make_held(rig)
    rig.client.statuses = [{"status": "failed", "deleted": True}]
    rig.tick(constants.IDLE_SCAN_S)
    assert rig.state.entry(sha)["status"] == "refused"


def test_failed_without_the_deleted_flag_is_an_ordinary_failure(rig):
    path = rig.file()
    sha = sha256_file(path)
    rig.discover()
    rig.client.statuses = [{"status": "failed", "error": "could not store it: x", "deleted": False}]
    rig.tick(constants.STATUS_POLL_S)
    assert rig.state.entry(sha)["status"] == "failed"


# --- R2.1 F2: the manual retrigger (RETRY_GENERATION) -----------------------------------------------

def make_retrigger_rows(rig):
    shas = {}
    for kind, content in (("failed", b"one"), ("rejected", b"two"), ("refused", b"three"),
                          ("stored", b"four"), ("pending", b"five")):
        path = rig.file(kind + ".vrf", content)
        shas[kind] = sha256_file(path)
        rig.state.mark(shas[kind], paths=[str(path)], status=kind, failures=3,
                       next_try=START + 9 * DAY, stored_at=START if kind == "stored" else None)
    return shas


def test_retrigger_resets_failed_and_rejected_once_per_generation(rig):
    shas = make_retrigger_rows(rig)
    saved = []
    assert rig.config.retry_generation == 0 < constants.RETRY_GENERATION
    order = []
    original_mark = rig.state.mark
    rig.state.mark = lambda sha, **kw: (order.append("state"), original_mark(sha, **kw))[1]
    rig.engine.apply_retry_generation(lambda: (order.append("config"), saved.append(rig.config.retry_generation)))
    for kind in ("failed", "rejected"):
        row = rig.state.entry(shas[kind])
        assert row["failures"] == 0 and row["next_try"] == rig.clock.now
    for kind in ("refused", "stored", "pending"):
        row = rig.state.entry(shas[kind])
        assert row["failures"] == 3 and row["next_try"] == START + 9 * DAY
    assert rig.state.entry(shas["refused"])["status"] == "refused"
    assert saved == [constants.RETRY_GENERATION] and order[-1] == "config" and "state" in order
    rig.engine.apply_retry_generation(lambda: saved.append("again"))  # a routine restart re-sends nothing
    assert saved == [constants.RETRY_GENERATION]


def test_retrigger_crash_before_the_config_save_repeats_it(rig):
    shas = make_retrigger_rows(rig)

    def broken_save():
        raise OSError("disk")

    with pytest.raises(OSError):
        rig.engine.apply_retry_generation(broken_save)
    assert rig.state.entry(shas["failed"])["failures"] == 0  # the state change was written first
    rig.config.retry_generation = 0                           # as after a restart: the file never changed
    again = []
    rig.engine.apply_retry_generation(lambda: again.append(True))
    assert again == [True]


def test_retriggered_files_go_out_one_at_a_time_after_a_check(rig):
    rig.client.known = False
    for name, content in (("a.vrf", b"aa"), ("b.vrf", b"bb"), ("c.vrf", b"cc")):
        path = rig.file(name, content)
        rig.state.mark(sha256_file(path), paths=[str(path)], status="failed", failures=4,
                       next_try=START + 14 * DAY)
    rig.engine.apply_retry_generation(lambda: None)
    rig.tick()
    assert [call[0] for call in rig.client.calls][:2] == ["check", "upload"]
    assert len(uploads(rig.client)) == 1
    rig.tick(constants.STATUS_POLL_S)                     # the slot is still the first upload's
    assert len(uploads(rig.client)) == 1
    rig.client.statuses = [{"status": "stored"}]
    rig.tick(constants.STATUS_POLL_S)
    rig.tick(constants.STATUS_POLL_S)
    assert len(uploads(rig.client)) == 2


# --- R2.1 F5: the tray never waits on the engine -------------------------------------------------

def test_pause_during_an_upload_stops_the_tick_at_the_next_step_boundary(rig):
    old, _ = stored(rig)                              # old enough that the sweep would ask `kept`
    rig.file("new.vrf", b"new replay")
    rig.client.kept_value = True
    rig.engine.needs_key = True                       # discover only, so the sweep is not reached yet
    rig.discover()
    rig.engine.needs_key = False
    gate, entered = Event(), Event()
    real_upload = rig.client.upload

    def slow_upload(path):
        entered.set()
        assert gate.wait(3)
        rig.config.paused = True                      # the tray's pause lands mid-upload, without the lock
        return real_upload(path)

    rig.client.upload = slow_upload
    worker = Thread(target=lambda: rig.tick(constants.STATUS_POLL_S), daemon=True)
    worker.start()
    assert entered.wait(2)
    answered = Event()
    reader = Thread(target=lambda: (rig.engine.status(), rig.engine.idle(), answered.set()), daemon=True)
    reader.start()
    assert answered.wait(1), "the tray's reads must not wait for the upload"
    gate.set()
    worker.join(3)
    assert not worker.is_alive()
    assert not any(c[0] == "kept" for c in rig.client.calls)   # the sweep after the step saw the flag
    assert old.exists()


def test_paused_flag_is_checked_after_a_check_and_between_held_polls(rig):
    old, _ = stored(rig)
    rig.client.known = True
    rig.file("a.vrf", b"aa")
    rig.engine.needs_key = True
    rig.discover()
    rig.engine.needs_key = False
    first_check = rig.client.check

    def check(sha):
        rig.config.paused = True
        return first_check(sha)

    rig.client.check = check
    rig.tick()
    assert len([c for c in rig.client.calls if c[0] == "check"]) == 1
    assert not any(c[0] == "kept" for c in rig.client.calls) and old.exists()
    # Two held rows are due: the first poll pauses, so the second is not polled.
    rig.config.paused = False
    rig.client.check = first_check
    for name in ("h1", "h2"):
        rig.state.mark(name * 32, paths=[], status="held", upload_id="u-" + name, next_try=START)
    polled = []

    def status(upload_id):
        polled.append(upload_id)
        rig.config.paused = True
        return {"status": "held"}

    rig.client.status = status
    rig.tick(1)
    assert len(polled) == 1


# --- R2.1 F6: one engine-wide wait after a refusal or outage -------------------------------------

def test_outage_backs_off_the_whole_engine_not_each_file(rig, monkeypatch):
    import valo_uploader.engine as engine

    for number in range(40):
        rig.file(f"r{number}.vrf", f"replay {number}".encode())
    rig.client.error = api.Offline()
    rig.discover()                                    # hashes each file once and probes the first
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    before = len(rig.client.calls)
    for _ in range(360):                              # one hour at 10 s ticks
        rig.tick(10)
    assert len(rig.client.calls) - before <= 6, len(rig.client.calls) - before
    assert counted.call_count <= 6, counted.call_count
    charged = [sha for sha, row in rig.state.items() if row["first_refused_at"] is not None]
    assert len(charged) <= 7                          # files that were never probed pay nothing


@pytest.mark.parametrize("retry_after,wait", [(5, 600), (1800, 1800), (7200, 3600)])
def test_refused_waits_from_retry_after_between_ten_minutes_and_an_hour(rig, monkeypatch, retry_after, wait):
    import valo_uploader.engine as engine

    rig.file("a.vrf", b"a")
    rig.file("b.vrf", b"b")
    rig.client.error = api.Refused(retry_after)
    rig.discover()
    rig.client.error = None
    counted = Mock(wraps=sha256_file)
    monkeypatch.setattr(engine, "sha256_file", counted)
    calls = len(rig.client.calls)
    rig.tick(wait - 1)
    assert len(rig.client.calls) == calls and counted.call_count == 0
    rig.tick(1)
    assert len(rig.client.calls) > calls


def test_while_waiting_no_call_of_any_kind_is_made(rig):
    old, old_sha = stored(rig)
    other = rig.file("h.vrf", b"held replay")
    rig.discover()
    held_sha = sha256_file(other)
    rig.state.mark(held_sha, status="held", upload_id="u-held", next_try=START)
    rig.client.error = api.Offline()
    rig.tick()
    rig.client.error = None
    calls = len(rig.client.calls)
    for _ in range(50):
        rig.tick(10)
    assert len(rig.client.calls) == calls          # uploads, /check, status, held polls and `kept` all wait
    rig.tick(constants.REFUSED_RETRY_S)
    assert len(rig.client.calls) > calls


def test_an_outage_found_by_the_deletion_sweep_also_pauses_uploads(rig):
    path, sha = stored(rig)
    rig.client.kept_value = api.Offline("down")
    rig.tick()
    other = rig.file("n.vrf", b"new replay")
    rig.client.kept_value = True
    rig.discover()
    assert not uploads(rig.client)
    rig.tick(constants.REFUSED_RETRY_S)
    assert uploads(rig.client)


def test_bad_key_still_blocks_and_does_not_set_the_wait(rig):
    rig.file()
    rig.client.error = api.BadKey()
    rig.discover()
    assert rig.engine.needs_key
    rig.client.error = None
    rig.engine.key_replaced(Client())
    rig.tick()
    assert rig.engine.client.calls                    # the replacement key is used at once
