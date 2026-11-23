import hashlib
import os
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from urllib.error import URLError

import pytest

from valo_uploader import updater
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


@pytest.mark.parametrize("existing,inside", [(False, False), (True, False), (True, True)])
def test_self_install_never_overwrites_existing(tmp_path, existing, inside):
    folder = tmp_path / "Programs" / "ValoUploader"
    folder.mkdir(parents=True)
    target = folder / "ValoUploader.exe"
    source = tmp_path / "Downloads.exe"
    source.write_bytes(b"download")
    if existing:
        target.write_bytes(b"installed")
    import shutil
    copy, key, launch = Mock(wraps=shutil.copy2), Mock(), Mock()
    assert ensure_installed(target if inside else source, folder, copy=copy, run_key=key,
                            launch=launch) == ("installed" if inside else "relaunched")
    assert target.read_bytes() == (b"installed" if existing else b"download")
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
