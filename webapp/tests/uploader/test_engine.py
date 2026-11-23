"""Offline engine journeys: synthetic replay bytes, time and API only."""

import io
import os
import subprocess
from pathlib import Path
from threading import Event
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

    def answer(self, value):
        if self.error:
            raise self.error
        if isinstance(value, Exception):
            raise value
        return value

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
    assert not path.exists()
    assert rig.state.entry(sha)["deleted_at"] == rig.clock.now


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
    ("VALORANT-Win64-Shipping.exe 123 Console 1 42,000 K", True),
    ("INFO: No tasks are running which match the specified criteria.", False),
    ("other.exe 123 Console 1 42,000 K", False),
])
def test_process_listing(output, expected):
    def listing(args, **kwargs):
        assert args == ["tasklist", "/FI", "IMAGENAME eq " + constants.PROCESS_NAME, "/NH"]
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
    assert ticks == [START, START + 10, START + 20, START + 1820]


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
