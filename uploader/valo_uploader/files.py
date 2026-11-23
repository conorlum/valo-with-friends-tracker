"""Read-only replay discovery and Windows exclusive-read readiness checks."""

import hashlib
import os
import stat
from pathlib import Path

from .constants import STABLE_GAP_S
from .paths import demos_dir


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def win_exclusive_open(path):
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # GENERIC_READ, no sharing, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL.
    handle = create(str(Path(path)), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    try:
        return True
    finally:
        close(handle)


def exclusive_ok(path, opener=win_exclusive_open):
    try:
        return bool(opener(path))
    except OSError:
        return False


class Watcher:
    def __init__(self, directory=None, *, env=None, exclusive=exclusive_ok):
        self.directory = demos_dir(env) if directory is None else Path(directory)
        self.exclusive_ok = exclusive
        self._seen = {}
        # True while some .vrf is seen but has not yet been unchanged for STABLE_GAP_S: it needs a
        # single follow-up look for this scheduled scan. A stable but locked file does not count.
        # One assignment per poll: safe to read from any thread.
        self.awaiting = False

    def forget(self, path):
        """Changed bytes must pass the normal two-observation check again."""
        self._seen.pop(Path(path), None)

    def poll(self, now) -> list[Path]:
        try:
            paths = sorted(self.directory.iterdir())
        except OSError:
            self._seen.clear()
            self.awaiting = False
            return []
        observations, finished, awaiting = {}, [], False
        for path in paths:
            if path.suffix.lower() != ".vrf":
                continue
            try:
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    continue
                signature = (info.st_size, info.st_mtime_ns)
                previous = self._seen.get(path)
                since = previous[1] if previous and previous[0] == signature else now
                if now < since:
                    since = now
                observations[path] = (signature, since)
                if now - since < STABLE_GAP_S:
                    awaiting = True
                elif self.exclusive_ok(path):
                    finished.append(path)
            except OSError:
                continue
        self._seen = observations
        self.awaiting = awaiting
        return finished
