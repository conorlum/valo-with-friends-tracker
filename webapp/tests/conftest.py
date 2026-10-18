"""How much of the machine a test run may use: a third of its cores as xdist workers (`-n auto`), and never its
last four cores, which are kept free for whatever else is running. The whole pytest process is pinned off those
cores before any worker starts, so the workers and every process a test starts (node, a fresh python) inherit
the pin. A machine with four cores or fewer has none to spare and is not pinned."""

from __future__ import annotations

import os
import sys

RESERVED_CORES = 4


def _allowed_cores() -> list[int] | None:
    count = os.cpu_count() or 1
    if count <= RESERVED_CORES:
        return None
    return list(range(count - RESERVED_CORES))


def _pin(cores: list[int]) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        kernel32.SetProcessAffinityMask.restype = wintypes.BOOL
        if not kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(), sum(1 << c for c in cores)):
            raise ctypes.WinError(ctypes.get_last_error())
    elif hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, cores)


def pytest_configure(config):
    cores = _allowed_cores()
    if cores is not None:
        _pin(cores)


def pytest_xdist_auto_num_workers(config):
    return max(1, (os.cpu_count() or 1) // 3)
