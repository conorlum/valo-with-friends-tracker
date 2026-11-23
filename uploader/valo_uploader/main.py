"""Standalone app wiring and process lifetime. The tray owns the main thread."""

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

from . import __version__, instance
from .api import Client
from .config import Config
from .engine import Engine
from .files import Watcher
from .install import ensure_installed, run_key_enabled, set_run_key
from .keywindow import ask_for_key, clipboard_key
from .keystore import load_key, save_key
from .log import configure_logging
from .paths import app_dir, install_dir
from .runner import run
from .state import State
from .tray import Tray
from .updater import after_update_cleanup, cleanup_stale, maybe_update, recover_update


def _wait_exit(pid, timeout):
    """Wait for the old process, without starting a helper or holding its image."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 87  # process already gone
        try:
            return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.1)
    return False


def main(argv=None, *, frozen=None, env=None):
    parser = argparse.ArgumentParser(prog="ValoUploader")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--after-update")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    if args.selftest:
        line = f"ValoUploader {__version__}"
        print(line)
        (Path(tempfile.gettempdir()) / "ValoUploader-selftest.txt").write_text(
            line + "\n", encoding="utf-8")
        return 0
    if args.after_update and (not re.fullmatch(r"[0-9a-f]{16}", args.after_update)
                              or args.parent_pid is None or args.parent_pid <= 0):
        parser.error("--after-update requires a 16-digit hex id and a positive --parent-pid")

    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = Path(sys.executable)
    if frozen and not args.after_update:
        # A failed update can leave the installed copy missing; put .old back first.
        installed = install_dir(env) / "ValoUploader.exe"
        try:
            recovered = recover_update(installed, env, running_exe=executable)
        except Exception:
            recovered = None
        if recovered == "relaunched":
            # A good copy was started in place of this bad image; leave before the mutex.
            return 0
        if recovered == "restored" and executable == installed.with_suffix(".old"):
            executable = installed
    if frozen and ensure_installed(executable, install_dir(env)) == "relaunched":
        return 0

    deadline = time.monotonic() + 30
    handle = instance.acquire(env=env)
    while handle is None and args.after_update and time.monotonic() < deadline:
        time.sleep(0.1)
        handle = instance.acquire(env=env)
    if handle is None:
        return 0

    guard_lock = threading.Lock()
    stop = threading.Event()
    runner = None
    tray = None

    def release_guard():
        nonlocal handle
        with guard_lock:
            if handle is not None:
                instance.release(handle)
                handle = None

    def stop_app():
        stop.set()
        if tray is not None:
            tray.stop()

    try:
        logger = configure_logging(env=env)
        # A freshly renamed .old keeps the installed image's original mtime.
        # During a handoff only cleanup(), after the parent exits, may remove it.
        if frozen and not args.after_update:
            cleanup_stale(executable)
        config = Config.load(env=env)

        def validate(key):
            return Client(config.site, key).key_info()

        try:
            key = load_key(env=env)
        except Exception:
            key = None
        if key is None:
            key = ask_for_key(clipboard_key(), validate)
            if key is None:
                return 0
            save_key(key, env=env)
        engine = Engine(State(env=env), Client(config.site, key), config,
                        watcher=Watcher(env=env))

        try:
            # An app update that bumped RETRY_GENERATION re-sends what failed, once.
            engine.apply_retry_generation(lambda: config.save(env=env))
        except Exception:
            logger.exception("Could not apply the retry generation")

        def pause():
            # No engine lock: a tick may hold it through a long upload, and the tray must never wait.
            # The tick reads the flag at each step boundary.
            config.paused = not config.paused
            config.save(env=env)

        def change_key():
            replacement = ask_for_key(clipboard_key(), validate)
            if replacement is not None:
                save_key(replacement, env=env)
                client = Client(config.site, replacement)
                # key_replaced may wait for a tick's lock: never on the tray's thread.
                threading.Thread(target=engine.key_replaced, args=(client,),
                                 name="uploader-key-replaced", daemon=True).start()

        def startup():
            # Python invocations have no installed executable to register.
            if frozen:
                set_run_key(None if run_key_enabled() else executable)

        def open_log():
            if sys.platform == "win32":
                os.startfile(str(app_dir(env) / "uploader.log"))

        tray = Tray(status=engine.status, run_key_on=run_key_enabled if frozen else lambda: False,
                    on_pause=pause, on_open_log=open_log, on_startup=startup,
                    on_change_key=change_key, on_quit=stop_app)

        def on_poll(now):
            tray.refresh()

        # Called at startup and on closed-game process polls, independently of scans.
        def after_tick(now):
            if frozen and not stop.is_set():
                result = maybe_update(now, engine.idle, engine.procs, config,
                                      stop_app=stop_app, release_mutex=release_guard,
                                      current_exe=executable, env=env)
                if result in ("updated", "rolled_back", "recovery_needed"):
                    stop_app()

        def work():
            try:
                run(engine, stop, after_tick=after_tick, on_poll=on_poll)
            except Exception:
                logger.exception("Uploader runner failed")
                stop_app()

        def cleanup():
            try:
                after_update_cleanup(args.after_update, args.parent_pid, _wait_exit,
                                     current_exe=executable, env=env)
            except Exception:
                logger.exception("Update cleanup failed")

        def ready():
            if frozen and args.after_update and not stop.is_set():
                folder = app_dir(env)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / ("ack-" + args.after_update)).write_text("ready\n", encoding="utf-8")
                threading.Thread(target=cleanup, name="uploader-cleanup", daemon=True).start()
                tray.set_message(f"Updated to v{__version__}")

        runner = threading.Thread(target=work, name="uploader-runner")
        runner.start()
        tray.run(ready)
        return 0
    finally:
        stop_app()
        # Update handoffs run on the runner; only the tray's caller joins it.
        if runner is not None and runner.ident is not None:
            runner.join()
        release_guard()
