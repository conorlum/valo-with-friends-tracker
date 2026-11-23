"""Idle-only, verified release staging and an acknowledged process handoff.

The caller exits after 'updated' or 'rolled_back'. W7 supplies stop_app and
release_mutex, and writes the acknowledgement only after mutex and tray startup.
Downloads use (url, destination); fetch_json returns parsed JSON or JSON text.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .constants import REPO, STARTED_OK_WAIT_S, UPDATE_EVERY_S
from .install import launch as launch_process
from .paths import app_dir


logger = logging.getLogger("valo_uploader.updater")
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
ASSET_PREFIX = f"https://github.com/{REPO}/releases/download/"
_SEMVER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def _version(value):
    if not re.fullmatch(_SEMVER, value):
        raise ValueError("Expected a major.minor.patch version")
    return tuple(int(part) for part in value.split("."))


def newer(a, b):
    return _version(a) > _version(b)


def _fetch_json(url):
    from urllib.request import Request, urlopen

    request = Request(url, headers={"Accept": "application/vnd.github+json",
                                    "User-Agent": "ValoUploader/" + __version__})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def _download(url, destination):
    from urllib.request import urlopen

    with urlopen(url, timeout=60) as response, Path(destination).open("wb") as output:
        shutil.copyfileobj(response, output)


def latest_release(fetch_json):
    rows = fetch_json(RELEASES_URL)
    if isinstance(rows, (str, bytes)):
        rows = json.loads(rows)
    if not isinstance(rows, list):
        raise ValueError("Releases response must be a list")
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or row.get("draft") or row.get("prerelease"):
            continue
        tag = row.get("tag_name", "")
        if isinstance(tag, str) and re.fullmatch("uploader-v" + _SEMVER, tag):
            candidates.append((tag[len("uploader-v"):], row))
    if not candidates:
        return None
    version, row = max(candidates, key=lambda item: _version(item[0]))
    assets = {asset.get("name"): asset.get("browser_download_url")
              for asset in row.get("assets", []) if isinstance(asset, dict)}
    urls = [assets.get(name) for name in ("ValoUploader.exe", "ValoUploader.exe.sha256")]
    if not all(isinstance(url, str) and url.startswith(ASSET_PREFIX) for url in urls):
        return None
    return version, *urls


def stage(release, download, tmp_dir):
    """Leave only a verified executable; remove partial downloads on any failure."""
    version, exe_url, sha_url = release
    _version(version)
    if not all(url.startswith(ASSET_PREFIX) for url in (exe_url, sha_url)):
        raise ValueError("Untrusted release asset URL")
    folder = Path(tmp_dir)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f"ValoUploader-{version}-", suffix=".exe",
                                     dir=folder, delete=False) as stream:
        staged = Path(stream.name)
    checksum = staged.with_suffix(".sha256")
    keep = False
    try:
        download(exe_url, staged)
        download(sha_url, checksum)
        tokens = checksum.read_text(encoding="ascii").split()
        if not tokens or not re.fullmatch(r"[0-9a-fA-F]{64}", tokens[0]):
            return None
        with staged.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest.lower() != tokens[0].lower():
            return None
        keep = True
        return staged
    finally:
        checksum.unlink(missing_ok=True)
        if not keep:
            staged.unlink(missing_ok=True)


def _wait_ack(attempt, timeout, process, folder):
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if (folder / ("ack-" + attempt)).is_file():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return False


RECOVERY_MARKER = "update-recovery.json"
KILL_WAIT_S = 10
RENAME_TRIES = 10
RENAME_DELAY_S = 0.5


def _retry(operation, sleep, tries=RENAME_TRIES, delay=RENAME_DELAY_S):
    """A just-killed Windows process can hold its image for a moment."""
    for attempt in range(tries):
        try:
            return operation()
        except OSError:
            if attempt == tries - 1:
                raise
            sleep(delay)


def _kill(process):
    """Stop the launched process and, for a one-file build, its child."""
    pid = getattr(process, "pid", None)
    if sys.platform == "win32" and isinstance(pid, int):
        import subprocess

        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                           timeout=KILL_WAIT_S, creationflags=0x08000000)
        except Exception:
            logger.exception("Could not stop the new version's process tree")
    process.kill()


def _roll_back(process, current, old, bad, moved_old, moved_new, version, config, folder,
               env, launch, sleep):
    """Every step is protected: nothing here may raise or stop the next step."""
    if process is not None:
        try:
            if process.poll() is None:
                _kill(process)
        except Exception:
            logger.exception("Could not stop the new version")
        try:
            process.wait(timeout=KILL_WAIT_S)
        except Exception:
            logger.exception("The new version did not exit")
    if moved_new:
        try:
            _retry(lambda: os.replace(current, bad), sleep)
        except Exception:
            logger.exception("Could not move the new executable aside")
    restored = not moved_old
    if moved_old:
        try:
            _retry(lambda: os.replace(old, current), sleep)
            restored = True
        except Exception:
            logger.exception("Could not restore the previous executable")
    # The version is skipped even if the restore or the launch below failed.
    try:
        if version not in config.skip_versions:
            config.skip_versions.append(version)
        config.save(env)
    except Exception:
        logger.exception("Could not save the skipped version")
    if not restored:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / RECOVERY_MARKER).write_text(json.dumps(
                {"current": str(current), "old": str(old), "bad": str(bad),
                 "version": version}), encoding="utf-8")
        except Exception:
            logger.exception("Could not write the recovery marker")
    # After a failed restore, current is the bad new version and .old is the good copy.
    order = (current, old) if restored else (old, current)
    runnable = next((path for path in order if path.exists()), None)
    if runnable is not None:
        try:
            launch(runnable, [])
        except Exception:
            logger.exception("Could not start the previous version")
    return "rolled_back" if restored else "recovery_needed"


def _same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def recover_update(current_exe, env=None, *, sleep=time.sleep, running_exe=None,
                   launch=launch_process):
    """At startup: put .old back when a failed update left no runnable installed copy.

    A healthy install with an ordinary leftover .old is cleanup_stale's, not ours.
    Windows cannot replace a running image but can rename it: when a marker says the installed
    copy is bad and .old is good, the bad current is renamed to .bad first, then .old takes its
    place. If this process is that bad current ("relaunched": the caller exits before the
    instance mutex), the good copy is launched and the marker is removed only after the launch.
    Any failed step puts current back. Never raises.
    """
    try:
        current = Path(current_exe)
        old = current.with_suffix(".old")
        bad = current.with_suffix(".bad")
        marker = app_dir(env) / RECOVERY_MARKER
        marked = marker.exists()
        if not marked and (current.exists() or not old.exists()):
            return "healthy"
        if not old.exists():
            if not current.exists():
                return "unrecoverable"
            marker.unlink(missing_ok=True)
            return "restored"
        moved_aside = False
        if current.exists():
            try:
                bad.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove the previous rejected version")
            _retry(lambda: os.replace(current, bad), sleep, tries=5)
            moved_aside = True
        try:
            _retry(lambda: os.replace(old, current), sleep, tries=5)
        except Exception:
            if moved_aside:
                try:
                    os.replace(bad, current)  # put things back: current must exist
                except Exception:
                    logger.exception("Could not put the installed copy back")
            raise
        if running_exe is not None and moved_aside and _same_file(running_exe, current):
            # This process is the bad image, now at .bad: start the good copy and step aside.
            launch(current, [])
            marker.unlink(missing_ok=True)
            return "relaunched"
        marker.unlink(missing_ok=True)
        return "restored"
    except Exception:
        logger.exception("Update recovery failed")
        return "failed"


def swap(current_exe, staged, *, stop_app, release_mutex, launch=launch_process,
         wait_ack=None, config, version=None, env=None, sleep=time.sleep):
    """Yield the instance mutex before launching; restore on startup failure.

    Backups replace the .exe suffix with .old/.bad. Version can be passed
    explicitly or recovered from the filename returned by stage(). Anything wrong
    before stop_app raises with nothing changed; once the app is stopped nothing
    raises, and the result is 'updated', 'rolled_back' or 'recovery_needed'.
    """
    current, staged = Path(current_exe), Path(staged)
    if version is None:
        match = re.match(r"ValoUploader-(" + _SEMVER + r")-", staged.name)
        if match is None:
            raise ValueError("A staged release version is required")
        version = match[1]
    _version(version)
    old, bad = current.with_suffix(".old"), current.with_suffix(".bad")
    if old.exists():
        # A leftover backup is removed only while a runnable current exists;
        # without one, .old is recover_update's.
        if current.exists():
            try:
                old.unlink()
            except OSError:
                logger.warning("Previous update backup is still locked")
                raise BackupLocked("Previous update backup is locked") from None
        else:
            raise FileExistsError("Previous update backup still exists")
    try:
        bad.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove the previous rejected version")
    folder = app_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    stop_app()
    release_mutex()
    attempt = secrets.token_hex(8)
    process = None
    moved_old = moved_new = False
    try:
        for stale in folder.glob("ack-*"):
            stale.unlink()
        current.rename(old)
        moved_old = True
        shutil.move(str(staged), str(current))
        moved_new = True
        process = launch(current, ["--after-update", attempt, "--parent-pid", str(os.getpid())])
        if process.poll() is None:
            acknowledged = (wait_ack(attempt, STARTED_OK_WAIT_S) if wait_ack is not None
                            else _wait_ack(attempt, STARTED_OK_WAIT_S, process, folder))
            if acknowledged and process.poll() is None:
                logger.info("Updated to v%s", version)
                return "updated"
    except Exception:
        logger.exception("Update handoff failed; restoring previous executable")
    # Wait for termination before moving the image back on Windows.
    return _roll_back(process, current, old, bad, moved_old, moved_new, version, config,
                      folder, env, launch, sleep)


CLEANUP_TRIES = 60
CLEANUP_DELAY_S = 0.5


def after_update_cleanup(attempt, parent_pid, wait_exit, *, current_exe=None, env=None,
                         sleep=time.sleep):
    """Call only after acknowledging; never delete the parent's running image.

    A one-file build's bootloader parent can outlive the process we waited on, so a
    locked file is retried for up to 30 s; each file is handled on its own.
    """
    if not re.fullmatch(r"[0-9a-f]{16}", attempt):
        raise ValueError("Invalid update attempt id")
    if not wait_exit(parent_pid, 60):
        return
    current = Path(current_exe or sys.executable)
    try:
        ack = app_dir(env) / ("ack-" + attempt)
    except Exception:
        logger.exception("Could not locate the update acknowledgement")
        ack = None
    for path in (current.with_suffix(".old"), current.with_suffix(".bad"), ack):
        if path is None:
            continue
        try:
            _retry(lambda: path.unlink(missing_ok=True), sleep, tries=CLEANUP_TRIES,
                   delay=CLEANUP_DELAY_S)
        except Exception:
            logger.exception("Could not remove %s", path.name)


def cleanup_stale(current_exe, *, now=None):
    """Next-start cleanup for backups left by an interrupted parent handoff."""
    now = time.time() if now is None else now
    for suffix in (".old", ".bad"):
        path = Path(current_exe).with_suffix(suffix)
        try:
            if now - path.stat().st_mtime > 86400:
                path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Could not remove stale %s", path.name)


class BackupLocked(FileExistsError):
    """A leftover .old can't be removed yet; nothing was changed."""


@dataclass
class UpdateState:
    """Owned by one runner thread, surviving deferred ticks without a download."""

    pending: tuple[str, Path] | None = None


_state = UpdateState()


def maybe_update(now, engine_idle, valorant_running, config, *, stop_app, release_mutex,
                 current_exe=None, tmp_dir=None, fetch_json=_fetch_json, download=_download,
                 launch=launch_process, wait_ack=None, state=None, env=None,
                 current_version=__version__):
    state = _state if state is None else state
    try:
        if state.pending is None and now - config.last_update_check < UPDATE_EVERY_S:
            return "idle"
        if not engine_idle() or valorant_running():
            return "idle"
        if state.pending is None:
            config.last_update_check = now
            config.save(env)
            release = latest_release(fetch_json)
            if release is None or not newer(release[0], current_version):
                return "unchanged"
            if release[0] in config.skip_versions:
                return "skipped"
            staged = stage(release, download, tmp_dir or app_dir(env) / "updates")
            if staged is None:
                raise ValueError("Release checksum verification failed")
            state.pending = (release[0], staged)
        version, staged = state.pending
        # A replay may have appeared, or the game started, during the download.
        if not engine_idle() or valorant_running():
            return "deferred"
        result = swap(current_exe or sys.executable, staged, stop_app=stop_app,
                      release_mutex=release_mutex, launch=launch, wait_ack=wait_ack,
                      config=config, version=version, env=env)
        state.pending = None
        return result
    except BackupLocked:
        # Nothing changed and the verified download is kept for the next tick.
        return "deferred"
    except Exception:
        logger.exception("Update check failed")
        if state.pending is not None:
            try:
                state.pending[1].unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove staged update")
            state.pending = None
        return "error"
