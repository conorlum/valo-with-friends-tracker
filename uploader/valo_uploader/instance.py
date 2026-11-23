"""A named object's existence guards the app, without thread ownership."""

import sys
import threading
from dataclasses import dataclass, field

from .paths import app_dir


@dataclass
class _Handle:
    value: object
    close: object
    lock: object = field(default_factory=threading.Lock)
    closed: bool = False


def acquire(*, env=None, kernel32=None):
    if kernel32 is not None or sys.platform == "win32":
        if kernel32 is None:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.GetLastError.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            # ctypes preserves LastError separately when use_last_error is set.
            last_error = ctypes.get_last_error
        else:
            last_error = kernel32.GetLastError
        value = kernel32.CreateMutexW(None, False, "Local\\ValoUploader")
        error = last_error()
        if not value:
            raise OSError(error, "Could not create ValoUploader instance guard")
        if error == 183:
            kernel32.CloseHandle(value)
            return None
        return _Handle(value, kernel32.CloseHandle)

    import fcntl

    folder = app_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    stream = (folder / "instance.lock").open("a+b")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    # Keep the inode stable: unlinking a lock file allows two independent locks.
    return _Handle(stream, lambda file: file.close())


def release(handle):
    if handle is None:
        return
    with handle.lock:
        if not handle.closed:
            handle.close(handle.value)
            handle.closed = True
