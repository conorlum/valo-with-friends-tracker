"""First-run installation and the per-user Windows startup preference."""

import os
import secrets
import shutil
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ValoUploader"
# A real PyInstaller one-file build is many MB; anything smaller is a leftover of an
# interrupted copy by an older build, which wrote straight to the final path.
MIN_INSTALLED_BYTES = 1_000_000


def launch(executable, args):
    """Start an independent instance.

    PyInstaller one-file children reuse the parent's extraction directory unless the
    environment is reset; the parent's own environment is never changed.
    """
    import subprocess

    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return subprocess.Popen([str(executable), *args], env=env)


def set_run_key(path):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if path is None:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, f'"{Path(path)}"')


def run_key_enabled():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, RUN_NAME)
            return kind == winreg.REG_SZ and bool(value)
    except FileNotFoundError:
        return False


def _publish(temp, target, replace_incomplete):
    """Move temp to target; never overwrite a target that may be a valid install.

    Returns True when this call published the target, False when another install won.
    """
    if replace_incomplete:
        os.replace(temp, target)
        return True
    try:
        os.link(temp, target)
    except FileExistsError:
        return False
    except OSError:
        # No hard links here: rename refuses an existing target on Windows.
        if target.exists():
            return False
        try:
            os.rename(temp, target)
        except FileExistsError:
            return False
        return True
    temp.unlink(missing_ok=True)
    return True


def ensure_installed(exe_path, install_dir, copy=shutil.copy2, run_key=set_run_key,
                     launch=launch):
    """Call before acquiring the instance mutex; exit after 'relaunched'."""
    source, folder = Path(exe_path).resolve(), Path(install_dir).resolve()
    if source.is_relative_to(folder):
        return "installed"
    target = folder / "ValoUploader.exe"
    folder.mkdir(parents=True, exist_ok=True)
    # Only the updater may replace an installed executable; the one exception is a
    # file too small to be a real build, left by an interrupted older install.
    try:
        existing_size = target.stat().st_size
    except FileNotFoundError:
        existing_size = None
    if existing_size is None or existing_size < MIN_INSTALLED_BYTES:
        temp = folder / f".ValoUploader-{secrets.token_hex(8)}.tmp"
        try:
            copy(source, temp)
            if temp.stat().st_size != source.stat().st_size:
                raise OSError("Installed copy is incomplete")
            # Startup registration comes first: a failed write leaves no installed copy, so the
            # next run repeats both. (A valid existing install never reaches here, so a friend who
            # turned "Start with Windows" off stays off.)
            run_key(target)
            _publish(temp, target, existing_size is not None)
        finally:
            temp.unlink(missing_ok=True)
    launch(target, [])
    return "relaunched"
