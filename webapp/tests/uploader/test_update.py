import hashlib
import os
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from urllib.error import URLError

import pytest

import json
import shutil
import subprocess

from valo_uploader import install, updater
from valo_uploader.config import Config
from valo_uploader.constants import REPO, STARTED_OK_WAIT_S, UPDATE_EVERY_S
from valo_uploader.install import ensure_installed, run_key_enabled, set_run_key
from valo_uploader.paths import app_dir
from valo_uploader.runner import run


PREFIX = f"https://github.com/{REPO}/releases/download/"
RELEASE = ("0.2.0", PREFIX + "uploader-v0.2.0/ValoUploader.exe",
           PREFIX + "uploader-v0.2.0/ValoUploader.exe.sha256")
NOW = UPDATE_EVERY_S * 2


def release(version="0.2.0", **overrides):
    row = {"tag_name": "uploader-v" + version, "draft": False, "prerelease": False,
           "assets": [{"name": name, "browser_download_url":
                       PREFIX + "uploader-v" + version + "/" + name}
                      for name in ("ValoUploader.exe", "ValoUploader.exe.sha256")]}
    return dict(row, **overrides)


@pytest.fixture
def rig(tmp_path):
    env = {"APPDATA": str(tmp_path / "roaming"), "LOCALAPPDATA": str(tmp_path / "local")}
    current = tmp_path / "installed" / "ValoUploader.exe"
    current.parent.mkdir()
    current.write_bytes(b"old")
    downloads = []

    def download(url, path):
        downloads.append(url)
        Path(path).write_bytes((hashlib.sha256(b"new").hexdigest() + "  ValoUploader.exe\n").encode()
                               if url.endswith(".sha256") else b"new")

    proc = Mock()
    proc.poll.return_value = None
    args = dict(current_exe=current, tmp_dir=tmp_path / "stage", env=env,
                fetch_json=Mock(return_value=[release()]), download=download,
                stop_app=Mock(), release_mutex=Mock(), launch=Mock(return_value=proc),
                wait_ack=Mock(return_value=True), state=updater.UpdateState())
    return SimpleNamespace(env=env, current=current, args=args, downloads=downloads,
                           config=Config(), proc=proc)


def maybe(rig, now=NOW):
    return updater.maybe_update(now, rig.args.pop("idle", lambda: True),
                               rig.args.pop("running", lambda: False), rig.config, **rig.args)


def test_latest_semver_and_release_filters():
    rows = [release("9.0.0", draft=True), release("8.0.0", prerelease=True),
            release(tag_name="v7.0.0"), release("0.1.9"), release("0.1.10")]
    fetch = Mock(return_value=rows)
    assert updater.latest_release(fetch)[0] == "0.1.10"
    fetch.assert_called_once_with(f"https://api.github.com/repos/{REPO}/releases?per_page=20")
    assert updater.newer("0.1.10", "0.1.9")
    assert not updater.newer("0.1.10", "0.1.10")


@pytest.mark.parametrize("bad", ["host", "sha", "exe"])
def test_latest_requires_both_trusted_assets(bad):
    row = release()
    if bad == "host":
        row["assets"][0]["browser_download_url"] = "https://example.invalid/ValoUploader.exe"
    else:
        row["assets"].pop(1 if bad == "sha" else 0)
    assert updater.latest_release(lambda url: [row]) is None


@pytest.mark.parametrize("checksum", ["0" * 64, "invalid", "", "a" * 65])
def test_stage_rejects_bad_hash_and_removes_files(tmp_path, checksum):
    def download(url, path):
        Path(path).write_bytes(checksum.encode() if url.endswith(".sha256") else b"new")
    assert updater.stage(RELEASE, download, tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_stage_valid_checksum(rig):
    path = updater.stage(RELEASE, rig.args["download"], rig.args["tmp_dir"])
    assert path.read_bytes() == b"new"
    assert len(list(path.parent.iterdir())) == 1


def swap(rig, **overrides):
    staged = updater.stage(RELEASE, rig.args["download"], rig.args["tmp_dir"])
    args = {k: rig.args[k] for k in ("stop_app", "release_mutex", "launch", "wait_ack", "env")}
    args.update(overrides)
    return updater.swap(rig.current, staged, config=rig.config, version=RELEASE[0], **args)


def test_swap_order_args_ack_and_parent_cleanup(rig):
    events = []
    def launch(path, args):
        events.append("launch")
        assert events == ["stop", "release", "launch"]
        assert path == rig.current
        assert args[0] == "--after-update" and len(args[1]) == 16
        assert args[2:] == ["--parent-pid", str(os.getpid())]
        (app_dir(rig.env) / ("ack-" + args[1])).write_text("ok")
        return rig.proc
    def wait_ack(attempt, timeout):
        assert timeout == STARTED_OK_WAIT_S
        return (app_dir(rig.env) / ("ack-" + attempt)).exists()
    assert swap(rig, stop_app=lambda: events.append("stop"),
                release_mutex=lambda: events.append("release"), launch=launch,
                wait_ack=wait_ack) == "updated"
    assert rig.current.read_bytes() == b"new"
    assert rig.current.with_suffix(".old").read_bytes() == b"old"
    ack = next(app_dir(rig.env).glob("ack-*"))
    wait_exit = Mock(return_value=True)
    updater.after_update_cleanup(ack.name[4:], 123, wait_exit, current_exe=rig.current, env=rig.env)
    wait_exit.assert_called_once_with(123, 60)
    assert not rig.current.with_suffix(".old").exists()
    assert not ack.exists()


def test_cleanup_timeout_preserves_backups(rig):
    old = rig.current.with_suffix(".old")
    old.write_bytes(b"old")
    updater.after_update_cleanup("a" * 16, 123, Mock(return_value=False),
                                 current_exe=rig.current, env=rig.env)
    assert old.exists()


@pytest.mark.parametrize("exited", [False, True])
def test_rollback_stale_ack_exit_and_persisted_skip(rig, exited):
    folder = app_dir(rig.env)
    folder.mkdir(parents=True)
    stale = folder / ("ack-" + "b" * 16)
    stale.write_text("ok")
    rig.proc.poll.return_value = 1 if exited else None
    rig.args["wait_ack"].return_value = False
    assert swap(rig) == "rolled_back"
    assert not stale.exists()
    assert rig.current.read_bytes() == b"old"
    assert rig.current.with_suffix(".bad").read_bytes() == b"new"
    rig.args["launch"].assert_called_with(rig.current, [])
    if not exited:
        rig.proc.kill.assert_called_once()
        rig.proc.wait.assert_called_once()
    rig.config = Config.load(rig.env)
    assert RELEASE[0] in rig.config.skip_versions
    assert maybe(rig) == "skipped"
    assert len(rig.downloads) == 2


def test_check_time_persists_across_processes(rig):
    rig.args["fetch_json"].return_value = []
    assert maybe(rig) == "unchanged"
    rig.config = Config.load(rig.env)
    assert rig.config.last_update_check == NOW
    assert maybe(rig, NOW + UPDATE_EVERY_S - 1) == "idle"
    assert rig.args["fetch_json"].call_count == 1
    assert maybe(rig, NOW + UPDATE_EVERY_S) == "unchanged"
    assert rig.args["fetch_json"].call_count == 2


@pytest.mark.parametrize("gate", ["running", "idle"])
def test_download_race_defers_then_swaps_without_redownload(rig, gate):
    values = iter([False, True] if gate == "running" else [True, False])
    rig.args[gate] = lambda: next(values)
    assert maybe(rig) == "deferred"
    rig.args["stop_app"].assert_not_called()
    assert rig.current.read_bytes() == b"old"
    assert len(list(rig.args["tmp_dir"].iterdir())) == 1
    assert maybe(rig, NOW + 1) == "updated"
    assert len(rig.downloads) == 2
    assert rig.args["fetch_json"].call_count == 1


@pytest.mark.parametrize("failure", ["fetch", "download", "json", "list", "hash"])
def test_update_failure_nonfatal_saves_check_and_cleans_stage(rig, failure, caplog):
    if failure == "fetch":
        rig.args["fetch_json"].side_effect = URLError("offline")
    elif failure == "download":
        def broken(url, path):
            Path(path).write_bytes(b"partial")
            raise OSError("download interrupted")
        rig.args["download"] = broken
    elif failure in ("json", "list"):
        rig.args["fetch_json"].return_value = "not JSON" if failure == "json" else {}
    else:
        rig.args["download"] = lambda url, path: Path(path).write_bytes(b"wrong")
    assert maybe(rig) == "error"
    assert rig.current.read_bytes() == b"old"
    assert not rig.current.with_suffix(".old").exists()
    assert not list(rig.args["tmp_dir"].glob("*"))
    assert Config.load(rig.env).last_update_check == NOW
    assert caplog.records


@pytest.mark.parametrize("idle,running,last", [(False, False, 0), (True, True, 0),
                                                 (True, False, NOW - 1)])
def test_gates_do_no_work(rig, idle, running, last):
    rig.config.last_update_check = last
    rig.args.update(idle=lambda: idle, running=lambda: running)
    assert maybe(rig) == "idle"
    rig.args["fetch_json"].assert_not_called()
    assert rig.downloads == []


def test_runner_hook_exception_logs_and_next_tick_runs(caplog):
    stop = Event()
    ticks, hooks = [], []
    clock = iter([0, 1800])
    def tick(now):
        ticks.append(now)
        if len(ticks) == 2:
            stop.set()
    def hook(now):
        hooks.append(now)
        raise URLError("update failed")
    engine = SimpleNamespace(procs=lambda: False, clock=lambda: next(clock), tick=tick,
                             sleep=lambda seconds: None, status=lambda: {"queue_length": 0})
    run(engine, stop, after_tick=hook)
    assert ticks == hooks == [0, 1800]
    assert len(caplog.records) == 2


VALID = b"i" * (install.MIN_INSTALLED_BYTES + 1)


@pytest.mark.parametrize("existing,inside", [(False, False), (True, False), (True, True)])
def test_self_install_never_overwrites_existing(tmp_path, existing, inside):
    folder = tmp_path / "Programs" / "ValoUploader"
    folder.mkdir(parents=True)
    target = folder / "ValoUploader.exe"
    source = tmp_path / "Downloads.exe"
    source.write_bytes(b"download")
    if existing:
        target.write_bytes(VALID)
    import shutil
    copy, key, launch = Mock(wraps=shutil.copy2), Mock(), Mock()
    assert ensure_installed(target if inside else source, folder, copy=copy, run_key=key,
                            launch=launch) == ("installed" if inside else "relaunched")
    assert target.read_bytes() == (VALID if existing else b"download")
    if inside or existing:
        copy.assert_not_called()
        key.assert_not_called()
    else:
        key.assert_called_once_with(target)
    if inside:
        launch.assert_not_called()
    else:
        launch.assert_called_once_with(target, [])


def test_run_key_quoted_toggle_and_lazy_import(tmp_path, monkeypatch):
    values = {}
    registry = MagicMock()
    registry.HKEY_CURRENT_USER = object()
    registry.KEY_READ, registry.KEY_SET_VALUE, registry.REG_SZ = 1, 2, 3
    key = registry.CreateKey.return_value.__enter__.return_value
    registry.OpenKey.return_value.__enter__.return_value = key
    registry.SetValueEx.side_effect = lambda k, name, reserved, kind, value: values.update({name: value})
    def query(k, name):
        if name not in values:
            raise FileNotFoundError()
        return values[name], registry.REG_SZ
    registry.QueryValueEx.side_effect = query
    def delete(k, name):
        if name not in values:
            raise FileNotFoundError()
        del values[name]
    registry.DeleteValue.side_effect = delete
    monkeypatch.setitem(sys.modules, "winreg", registry)
    target = tmp_path / "Programs with spaces" / "ValoUploader.exe"
    assert not run_key_enabled()
    set_run_key(target)
    assert values["ValoUploader"] == '"' + str(target) + '"'
    assert run_key_enabled()
    assert registry.CreateKey.call_args.args[1] == r"Software\Microsoft\Windows\CurrentVersion\Run"
    set_run_key(None)
    set_run_key(None)
    assert not run_key_enabled()


# --- R1.3 F3: independent launches -----------------------------------------------------

def test_launch_resets_pyinstaller_environment_without_touching_parent(monkeypatch):
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    monkeypatch.setenv("SOME_PARENT_VAR", "kept")
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    install.launch(Path("x.exe"), ["--a"])
    command = popen.call_args.args[0]
    assert command == ["x.exe", "--a"]
    env = popen.call_args.kwargs["env"]
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["SOME_PARENT_VAR"] == "kept"
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ


def test_launch_overrides_a_parent_value_in_the_child_only(monkeypatch):
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "0")
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    install.launch("x.exe", [])
    assert popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert os.environ["PYINSTALLER_RESET_ENVIRONMENT"] == "0"


def test_swap_default_launcher_is_the_resetting_one():
    assert updater.launch_process is install.launch


# --- R1.3 F6: swap phases and recovery -------------------------------------------------

RESULTS = {"updated", "rolled_back", "recovery_needed"}


def fail_replace(monkeypatch, match, error=PermissionError, times=None):
    """Make os.replace fail for (src, dst) pairs the predicate matches."""
    real = os.replace
    state = {"n": 0}

    def fake(src, dst, *a, **k):
        if match(Path(src), Path(dst)) and (times is None or state["n"] < times):
            state["n"] += 1
            raise error("locked")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", fake)
    return state


TO_BAD = lambda src, dst: dst.suffix == ".bad"
TO_CURRENT = lambda src, dst: src.suffix == ".old"


def rollback_rig(rig, **overrides):
    rig.args["wait_ack"].return_value = False
    return swap(rig, sleep=Mock(), **overrides)


@pytest.mark.parametrize("failure", ["kill", "wait", "save"])
def test_rollback_survives_kill_wait_and_save_failures(rig, monkeypatch, failure):
    if failure == "kill":
        rig.proc.kill.side_effect = OSError("denied")
    elif failure == "wait":
        rig.proc.wait.side_effect = subprocess.TimeoutExpired("x", 10)
    else:
        monkeypatch.setattr(rig.config, "save", Mock(side_effect=OSError("disk")))
    assert rollback_rig(rig) == "rolled_back"
    assert rig.current.read_bytes() == b"old"
    assert rig.current.with_suffix(".bad").read_bytes() == b"new"
    assert RELEASE[0] in rig.config.skip_versions
    rig.args["launch"].assert_called_with(rig.current, [])
    assert not (app_dir(rig.env) / "update-recovery.json").exists()


def test_rollback_wait_is_bounded(rig):
    rollback_rig(rig)
    assert rig.proc.wait.call_args.kwargs.get("timeout") == 10


def test_rollback_launch_failure_is_survived(rig):
    rig.args["launch"].side_effect = [rig.proc, OSError("cannot start")]
    assert rollback_rig(rig) == "rolled_back"
    assert rig.current.read_bytes() == b"old"
    assert RELEASE[0] in rig.config.skip_versions


def test_first_launch_failure_rolls_back(rig):
    rig.args["launch"].side_effect = [OSError("cannot start"), rig.proc]
    assert rollback_rig(rig) == "rolled_back"
    assert rig.current.read_bytes() == b"old"
    assert RELEASE[0] in Config.load(rig.env).skip_versions


def test_new_to_bad_rename_failure_still_restores_old(rig, monkeypatch):
    fail_replace(monkeypatch, TO_BAD)
    assert rollback_rig(rig) == "rolled_back"
    assert rig.current.read_bytes() == b"old"
    assert RELEASE[0] in Config.load(rig.env).skip_versions


def test_restore_failure_marks_recovery_and_launches_the_old_copy(rig, monkeypatch):
    fail_replace(monkeypatch, TO_CURRENT)
    assert rollback_rig(rig) == "recovery_needed"
    old = rig.current.with_suffix(".old")
    assert old.read_bytes() == b"old"
    assert not rig.current.exists()
    rig.args["launch"].assert_called_with(old, [])
    marker = json.loads((app_dir(rig.env) / "update-recovery.json").read_text())
    assert marker["current"] == str(rig.current) and marker["old"] == str(old)
    assert marker["version"] == RELEASE[0]
    assert RELEASE[0] in Config.load(rig.env).skip_versions


def test_both_renames_failing_launches_the_good_old_copy(rig, monkeypatch):
    fail_replace(monkeypatch, lambda s, d: TO_BAD(s, d) or TO_CURRENT(s, d))
    assert rollback_rig(rig) == "recovery_needed"
    assert rig.current.exists()
    rig.args["launch"].assert_called_with(rig.current.with_suffix(".old"), [])
    assert rig.current.with_suffix(".old").read_bytes() == b"old"
    assert (app_dir(rig.env) / "update-recovery.json").exists()
    assert RELEASE[0] in Config.load(rig.env).skip_versions


@pytest.mark.parametrize("which", ["bad", "current"])
def test_transient_lock_on_rollback_rename_is_retried(rig, monkeypatch, which):
    rig.args["wait_ack"].return_value = False
    state = fail_replace(monkeypatch, TO_BAD if which == "bad" else TO_CURRENT, times=3)
    sleep = Mock()
    assert swap(rig, sleep=sleep) == "rolled_back"
    assert state["n"] == 3
    assert sleep.call_count == 3
    assert rig.current.read_bytes() == b"old"
    assert not (app_dir(rig.env) / "update-recovery.json").exists()


def lock_unlink(monkeypatch, *suffixes, always=True):
    real = Path.unlink

    def fake(self, *a, **k):
        if self.suffix in suffixes:
            raise PermissionError("locked")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", fake)


def test_failure_before_shutdown_changes_nothing(rig, monkeypatch):
    rig.current.with_suffix(".old").write_bytes(b"older")
    lock_unlink(monkeypatch, ".old")
    with pytest.raises(FileExistsError):
        swap(rig)
    rig.args["stop_app"].assert_not_called()
    rig.args["release_mutex"].assert_not_called()
    assert rig.current.read_bytes() == b"old"
    assert rig.current.with_suffix(".old").read_bytes() == b"older"


def test_swap_removes_leftover_backups_before_shutdown(rig):
    rig.current.with_suffix(".old").write_bytes(b"older")
    rig.current.with_suffix(".bad").write_bytes(b"worse")
    assert swap(rig) == "updated"
    assert rig.current.read_bytes() == b"new"
    assert rig.current.with_suffix(".old").read_bytes() == b"old"
    assert not rig.current.with_suffix(".bad").exists()


def test_swap_never_removes_old_when_current_is_missing(rig):
    old = rig.current.with_suffix(".old")
    old.write_bytes(b"older")
    staged = updater.stage(RELEASE, rig.args["download"], rig.args["tmp_dir"])
    rig.current.unlink()
    with pytest.raises(FileExistsError):
        updater.swap(rig.current, staged, stop_app=rig.args["stop_app"],
                     release_mutex=rig.args["release_mutex"], config=rig.config,
                     version=RELEASE[0], env=rig.env)
    assert old.read_bytes() == b"older"
    rig.args["stop_app"].assert_not_called()


def test_locked_old_defers_and_keeps_the_verified_stage(rig, monkeypatch):
    rig.current.with_suffix(".old").write_bytes(b"older")
    lock_unlink(monkeypatch, ".old")
    assert maybe(rig) == "deferred"
    staged = list(rig.args["tmp_dir"].iterdir())
    assert len(staged) == 1 and staged[0].read_bytes() == b"new"
    rig.args["stop_app"].assert_not_called()
    monkeypatch.undo()
    assert maybe(rig, NOW + 1) == "updated"
    assert len(rig.downloads) == 2
    assert rig.args["fetch_json"].call_count == 1


def test_cleanup_handles_each_file_alone_and_retries(rig, monkeypatch):
    folder = app_dir(rig.env)
    folder.mkdir(parents=True)
    old, bad = rig.current.with_suffix(".old"), rig.current.with_suffix(".bad")
    ack = folder / ("ack-" + "a" * 16)
    for path in (old, bad, ack):
        path.write_bytes(b"x")
    real, left = Path.unlink, {"n": 3}

    def flaky(self, *a, **k):
        if self.suffix == ".old" and left["n"]:
            left["n"] -= 1
            raise PermissionError("locked")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", flaky)
    sleep = Mock()
    updater.after_update_cleanup("a" * 16, 1, Mock(return_value=True), current_exe=rig.current,
                                 env=rig.env, sleep=sleep)
    assert not old.exists() and not bad.exists() and not ack.exists()
    assert sleep.call_count == 3


def test_cleanup_permanent_lock_is_bounded_and_isolated(rig, monkeypatch):
    folder = app_dir(rig.env)
    folder.mkdir(parents=True)
    old, bad = rig.current.with_suffix(".old"), rig.current.with_suffix(".bad")
    ack = folder / ("ack-" + "a" * 16)
    for path in (old, bad, ack):
        path.write_bytes(b"x")
    lock_unlink(monkeypatch, ".old")
    sleep = Mock()
    updater.after_update_cleanup("a" * 16, 1, Mock(return_value=True), current_exe=rig.current,
                                 env=rig.env, sleep=sleep)
    assert old.exists() and not bad.exists() and not ack.exists()
    assert 0 < sleep.call_count * 0.5 <= 30


def test_rollback_prefers_old_when_restore_failed(rig, monkeypatch):
    fail_replace(monkeypatch, lambda s, d: TO_BAD(s, d) or TO_CURRENT(s, d))
    assert rollback_rig(rig) == "recovery_needed"
    rig.args["launch"].assert_called_with(rig.current.with_suffix(".old"), [])


def test_cleanup_stale_survives_locked_files(tmp_path, monkeypatch):
    exe = tmp_path / "ValoUploader.exe"
    old, bad = exe.with_suffix(".old"), exe.with_suffix(".bad")
    for path in (old, bad):
        path.write_bytes(b"x")
        os.utime(path, (1, 1))
    lock_unlink(monkeypatch, ".old")
    updater.cleanup_stale(exe, now=10 ** 9)
    assert old.exists() and not bad.exists()
    real = Path.stat
    monkeypatch.setattr(Path, "stat", Mock(side_effect=PermissionError("denied")))
    updater.cleanup_stale(exe, now=10 ** 9)


def test_bad_version_fails_before_shutdown(rig):
    staged = updater.stage(RELEASE, rig.args["download"], rig.args["tmp_dir"])
    with pytest.raises(ValueError):
        updater.swap(rig.current, staged, stop_app=rig.args["stop_app"],
                     release_mutex=rig.args["release_mutex"], config=rig.config,
                     version="latest", env=rig.env)
    rig.args["stop_app"].assert_not_called()
    assert rig.current.read_bytes() == b"old"


def test_maybe_update_returns_recovery_needed(rig, monkeypatch):
    monkeypatch.setattr(updater, "swap", Mock(return_value="recovery_needed"))
    assert maybe(rig) == "recovery_needed"


def test_forward_failure_after_shutdown_is_a_rollback_not_an_error(rig, monkeypatch):
    real = shutil.move
    monkeypatch.setattr(updater.shutil, "move", Mock(side_effect=OSError("locked")))
    rig.args["wait_ack"].return_value = True
    assert swap(rig, sleep=Mock()) == "rolled_back"
    assert rig.current.read_bytes() == b"old"
    assert RELEASE[0] in Config.load(rig.env).skip_versions
    monkeypatch.setattr(updater.shutil, "move", real)


# recover_update

def recovery_rig(rig, *, current=None, old=None, marker=False, **kwargs):
    for path, data in ((rig.current, current), (rig.current.with_suffix(".old"), old)):
        path.unlink(missing_ok=True)
        if data is not None:
            path.write_bytes(data)
    if marker:
        folder = app_dir(rig.env)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "update-recovery.json").write_text(json.dumps(
            {"current": str(rig.current), "old": str(rig.current.with_suffix(".old")),
             "version": "0.2.0"}))
    return updater.recover_update(rig.current, rig.env, sleep=Mock(), **kwargs)


# --- R2.1 F4: recovery from the very image that needs replacing ------------------------------

MARKER = lambda rig: app_dir(rig.env) / "update-recovery.json"


def test_recovery_from_the_running_bad_image_moves_it_aside_and_relaunches_the_old_one(rig):
    launch = Mock()
    result = recovery_rig(rig, current=b"bad", old=b"good", marker=True,
                          running_exe=rig.current, launch=launch)
    assert result == "relaunched"
    assert rig.current.read_bytes() == b"good"
    assert rig.current.with_suffix(".bad").read_bytes() == b"bad"
    assert not rig.current.with_suffix(".old").exists()
    launch.assert_called_once_with(rig.current, [])
    assert not MARKER(rig).exists()


def test_recovery_keeps_the_marker_until_the_launch_succeeded(rig):
    seen = []
    launch = Mock(side_effect=lambda *a: seen.append(MARKER(rig).exists()))
    recovery_rig(rig, current=b"bad", old=b"good", marker=True, running_exe=rig.current, launch=launch)
    assert seen == [True]                      # still there while launching
    failing = Mock(side_effect=OSError("cannot start"))
    result = recovery_rig(rig, current=b"bad", old=b"good", marker=True, running_exe=rig.current,
                          launch=failing)
    assert result == "failed"                  # never raises
    assert MARKER(rig).exists()
    assert rig.current.exists()                # a runnable copy is in place for the next start


def test_recovery_step_failure_puts_the_running_image_back_at_current(rig, monkeypatch):
    fail_replace(monkeypatch, TO_CURRENT)
    launch = Mock()
    result = recovery_rig(rig, current=b"bad", old=b"good", marker=True, running_exe=rig.current,
                          launch=launch)
    assert result == "failed"
    assert rig.current.read_bytes() == b"bad"             # current exists again
    assert not rig.current.with_suffix(".bad").exists()
    assert rig.current.with_suffix(".old").read_bytes() == b"good"
    assert MARKER(rig).exists()
    launch.assert_not_called()


def test_recovery_running_from_old_while_a_bad_process_holds_current(rig):
    """The launched .old copy restores itself over a current that cannot be replaced."""
    result = recovery_rig(rig, current=b"bad", old=b"good", marker=True,
                          running_exe=rig.current.with_suffix(".old"), launch=Mock())
    assert result == "restored"
    assert rig.current.read_bytes() == b"good"
    assert rig.current.with_suffix(".bad").read_bytes() == b"bad"
    assert not MARKER(rig).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks a running image")
def test_a_running_exe_can_be_renamed_but_not_replaced(tmp_path):
    """The premise of F4: os.replace over a running image fails, a rename of it works."""
    import shutil as _shutil

    ping = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
    if not ping.exists():
        pytest.skip("PING.EXE not available")
    running = tmp_path / "running.exe"
    spare = tmp_path / "spare.exe"
    _shutil.copy2(ping, running)
    _shutil.copy2(ping, spare)
    process = subprocess.Popen([str(running), "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL,
                               creationflags=0x08000000)
    try:
        with pytest.raises(PermissionError):
            os.replace(spare, running)
        os.replace(running, tmp_path / "running.bad")       # renaming the running image works
        os.replace(spare, running)                         # and then its name is free
        assert running.exists() and (tmp_path / "running.bad").exists()
    finally:
        process.kill()
        process.wait(10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks a running image")
def test_real_recovery_replaces_the_running_image_by_renaming_it(rig, tmp_path):
    ping = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
    if not ping.exists():
        pytest.skip("PING.EXE not available")
    shutil.copy2(ping, rig.current)
    rig.current.with_suffix(".old").write_bytes(b"good copy")
    process = subprocess.Popen([str(rig.current), "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL,
                               creationflags=0x08000000)
    try:
        launch = Mock()
        folder = app_dir(rig.env)
        folder.mkdir(parents=True, exist_ok=True)
        MARKER(rig).write_text(json.dumps({"version": "0.2.0"}))
        result = updater.recover_update(rig.current, rig.env, sleep=Mock(), running_exe=rig.current,
                                        launch=launch)
        assert result == "relaunched"
        assert rig.current.read_bytes() == b"good copy"
        assert rig.current.with_suffix(".bad").stat().st_size == ping.stat().st_size
        launch.assert_called_once_with(rig.current, [])
    finally:
        process.kill()
        process.wait(10)


def test_recover_restores_old_when_current_is_missing(rig):
    recovery_rig(rig, current=None, old=b"old")
    assert rig.current.read_bytes() == b"old"
    assert not rig.current.with_suffix(".old").exists()


def test_recover_with_marker_replaces_the_bad_current(rig):
    recovery_rig(rig, current=b"new", old=b"old", marker=True)
    assert rig.current.read_bytes() == b"old"
    assert not (app_dir(rig.env) / "update-recovery.json").exists()


def test_recover_leaves_a_healthy_install_with_a_leftover_old_alone(rig):
    recovery_rig(rig, current=b"new", old=b"old")
    assert rig.current.read_bytes() == b"new"
    assert rig.current.with_suffix(".old").read_bytes() == b"old"


def test_recover_with_nothing_to_do_or_nothing_to_restore(rig):
    recovery_rig(rig, current=b"cur")
    assert rig.current.read_bytes() == b"cur"
    recovery_rig(rig, current=None, old=None)
    recovery_rig(rig, current=None, old=None, marker=True)


def test_recover_marker_without_old_but_with_current_clears_marker(rig):
    recovery_rig(rig, current=b"cur", old=None, marker=True)
    assert not (app_dir(rig.env) / "update-recovery.json").exists()
    assert rig.current.read_bytes() == b"cur"


def test_recover_retries_a_transient_lock(rig, monkeypatch):
    fail_replace(monkeypatch, TO_CURRENT, times=2)
    recovery_rig(rig, current=None, old=b"old")
    assert rig.current.read_bytes() == b"old"


def test_recover_never_raises(rig, monkeypatch):
    fail_replace(monkeypatch, TO_CURRENT)
    recovery_rig(rig, current=None, old=b"old", marker=True)
    assert (app_dir(rig.env) / "update-recovery.json").exists()
    folder = app_dir(rig.env)
    (folder / "update-recovery.json").write_text("{not json")
    updater.recover_update(rig.current, rig.env, sleep=Mock())
    monkeypatch.setattr(updater, "app_dir", Mock(side_effect=RuntimeError("boom")))
    updater.recover_update(rig.current, rig.env, sleep=Mock())


# --- R1.3 F8: atomic, serialised first install -----------------------------------------

def install_rig(tmp_path, source_bytes=b"download"):
    folder = tmp_path / "Programs" / "ValoUploader"
    source = tmp_path / "Downloads.exe"
    source.write_bytes(source_bytes)
    return folder, folder / "ValoUploader.exe", source


def test_partial_copy_leaves_no_target_and_no_temp_and_retry_installs(tmp_path):
    folder, target, source = install_rig(tmp_path)
    seen = []

    def partial(src, dst):
        seen.append(Path(dst))
        Path(dst).write_bytes(b"dow")
        raise OSError("disk full")
    key, launch = Mock(), Mock()
    with pytest.raises(OSError):
        ensure_installed(source, folder, copy=partial, run_key=key, launch=launch)
    assert list(folder.iterdir()) == []
    key.assert_not_called()
    launch.assert_not_called()
    assert seen[0].parent == folder.resolve()
    assert seen[0].name.startswith(".ValoUploader-") and seen[0].name.endswith(".tmp")
    assert ensure_installed(source, folder, run_key=key, launch=launch) == "relaunched"
    assert target.read_bytes() == b"download"
    key.assert_called_once_with(target.resolve())
    assert [p.name for p in folder.iterdir()] == ["ValoUploader.exe"]


def test_short_copy_is_rejected_and_cleaned(tmp_path):
    folder, target, source = install_rig(tmp_path)

    def short(src, dst):
        Path(dst).write_bytes(b"dow")
    with pytest.raises(OSError):
        ensure_installed(source, folder, copy=short, run_key=Mock(), launch=Mock())
    assert list(folder.iterdir()) == []


def test_tiny_poisoned_target_is_replaced_and_key_set(tmp_path):
    folder, target, source = install_rig(tmp_path)
    folder.mkdir(parents=True)
    target.write_bytes(b"partial")
    key, launch = Mock(), Mock()
    assert ensure_installed(source, folder, run_key=key, launch=launch) == "relaunched"
    assert target.read_bytes() == b"download"
    key.assert_called_once_with(target.resolve())
    launch.assert_called_once_with(target.resolve(), [])
    assert [p.name for p in folder.iterdir()] == ["ValoUploader.exe"]


def test_valid_target_smaller_than_source_is_never_overwritten(tmp_path):
    folder, target, source = install_rig(tmp_path, VALID + b"more")
    folder.mkdir(parents=True)
    target.write_bytes(VALID)
    key, launch = Mock(), Mock()
    ensure_installed(source, folder, run_key=key, launch=launch)
    assert target.read_bytes() == VALID
    key.assert_not_called()
    launch.assert_called_once()


def test_user_disabled_run_key_stays_off_with_a_valid_install(tmp_path):
    folder, target, source = install_rig(tmp_path)
    folder.mkdir(parents=True)
    target.write_bytes(VALID)
    key = Mock()
    ensure_installed(source, folder, run_key=key, launch=Mock())
    key.assert_not_called()


def test_run_key_is_set_before_the_target_is_published(tmp_path):
    folder, target, source = install_rig(tmp_path)
    seen = []
    key = Mock(side_effect=lambda path: seen.append(target.exists()))
    ensure_installed(source, folder, run_key=key, launch=Mock())
    assert seen == [False]                          # registered first, then published
    assert target.read_bytes() == b"download"


def test_failed_run_key_write_leaves_no_install_so_the_next_run_repeats_both(tmp_path):
    folder, target, source = install_rig(tmp_path)
    launch = Mock()
    with pytest.raises(OSError):
        ensure_installed(source, folder, run_key=Mock(side_effect=OSError("registry")), launch=launch)
    assert not target.exists() and list(folder.iterdir()) == []
    launch.assert_not_called()
    key = Mock()
    assert ensure_installed(source, folder, run_key=key, launch=launch) == "relaunched"
    key.assert_called_once_with(target.resolve())
    assert target.read_bytes() == b"download"


def test_a_crash_between_publish_and_run_key_is_not_possible_to_leave_a_keyless_install(tmp_path):
    folder, target, source = install_rig(tmp_path)

    def key(path):
        raise KeyboardInterrupt            # the process dies at the registry write
    with pytest.raises(KeyboardInterrupt):
        ensure_installed(source, folder, run_key=key, launch=Mock())
    assert not target.exists()


def test_losing_the_publish_race_launches_the_winner(tmp_path):
    folder, target, source = install_rig(tmp_path)
    key, launch = Mock(), Mock()

    def copy_then_rival_wins(src, dst):
        shutil.copy2(src, dst)
        target.write_bytes(VALID)
    assert ensure_installed(source, folder, copy=copy_then_rival_wins, run_key=key,
                            launch=launch) == "relaunched"
    assert target.read_bytes() == VALID
    assert [p.name for p in folder.iterdir()] == ["ValoUploader.exe"]
    key.assert_called_once_with(target.resolve())  # registered before publishing; same path as the winner's
    launch.assert_called_once_with(target.resolve(), [])
