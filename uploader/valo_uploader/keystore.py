"""Current-user DPAPI key storage. Native calls are lazy and replaceable in tests."""

import re

from .paths import app_dir

_KEY_RE = re.compile(r"vwf1_[A-Za-z0-9_-]{43}")


def looks_like_key(value: str) -> bool:
    return isinstance(value, str) and _KEY_RE.fullmatch(value) is not None


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN, without CRYPTPROTECT_LOCAL_MACHINE: CurrentUser protection.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes) -> bytes:
    return _dpapi(data, decrypt=False)


def dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi(data, decrypt=True)


def save_key(key: str, protect=dpapi_protect, *, env=None):
    if not looks_like_key(key):
        raise ValueError("Invalid upload key")
    protected = protect(key.encode("utf-8"))
    path = app_dir(env) / "key.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(protected)


def load_key(unprotect=dpapi_unprotect, *, env=None) -> str | None:
    path = app_dir(env) / "key.bin"
    try:
        protected = path.read_bytes()
    except FileNotFoundError:
        return None
    return unprotect(protected).decode("utf-8")


def clear_key(*, env=None):
    (app_dir(env) / "key.bin").unlink(missing_ok=True)
