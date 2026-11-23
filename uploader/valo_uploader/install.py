"""First-run installation and the per-user Windows startup preference."""

import shutil
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "ValoUploader"


def launch(executable, args):
    import subprocess

    return subprocess.Popen([str(executable), *args])


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


def ensure_installed(exe_path, install_dir, copy=shutil.copy2, run_key=set_run_key,
                     launch=launch):
    """Call before acquiring the instance mutex; exit after 'relaunched'."""
    source, folder = Path(exe_path).resolve(), Path(install_dir).resolve()
    if source.is_relative_to(folder):
        return "installed"
    target = folder / "ValoUploader.exe"
    folder.mkdir(parents=True, exist_ok=True)
    # Only the updater may replace an installed executable.
    if not target.exists():
        copy(source, target)
        run_key(target)
    launch(target, [])
    return "relaunched"
