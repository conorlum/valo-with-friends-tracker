"""One round's control as a task: blob and link in, the row's bytes (or its failure) out.

Run by scripts/compute_control.py's worker processes and by the replay worker's control child
(replay_worker/control_job.py; docs/map-control-worker-plan.md). A task is
`{"key", "map", "blob" (the stored gzip bytes), "link": {"sides", "db_deaths"}}`; the result is
`{"status": "ok", "data", "summary", "missing", "geometry"}` or `{"status": "failed", "error",
"error_kind"}`, plus the key, the seconds it took and the process's peak memory.

`error_kind` says whether a failure is the round's own (`engine`: the engine or the formats refused
it, and it would fail again with the same inputs) or the machine's (`infra`: memory, missing assets,
a bad cache, an import), which is worth retrying elsewhere. `geometry` is what the round was computed
with, in the terms of app/services/replay_control.py `geometry_inputs`, so a caller can tell whether
it matches its own.
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
import time
import traceback
from pathlib import Path

_GEOMETRY: dict = {}


def peak_memory() -> int | None:
    """This process's peak working set in bytes."""
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        # Declared types: an undeclared HANDLE is truncated to 32 bits on 64-bit Windows.
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = ctypes.c_void_p
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if info(current(), ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize)
        return None
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    except (ImportError, OSError):
        return None


def geometry_used(geo) -> dict:
    """The geometry a round was computed with: the mask hashes build_control_geometry.py records,
    the specials and the scale (app/services/replay_control.py `geometry_inputs`)."""
    import json

    import numpy as np

    from app.control import geometry

    scale = (json.loads(geometry.MAPS_JSON.read_text(encoding="utf-8")).get(geo.name) or {}).get("xMultiplier")
    return {"sight": hashlib.sha256(np.packbits(geo.sight).tobytes()).hexdigest()[:12],
            "walk": hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12],
            "barrier": geo.barrier_sha, "specials": list(geo.specials), "scale": scale}


def _load(name: str):
    from app.control import geometry

    if name not in _GEOMETRY:
        geo = geometry.load_geometry(name)
        try:
            geometry.visibility(geo)
        except Exception:
            # A cache file that won't load is removed, so the next try rebuilds it.
            for path in Path(geometry.cache_dir()).glob(f"{name}.*.npz"):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
        _GEOMETRY[name] = geo
    return _GEOMETRY[name]


def compute_task(task: dict) -> dict:
    started = time.time()
    try:
        from app.control import engine
        from app.control.encode import encode_data, encode_summary
        from app.replays import control_format as cf
        from app.replays import format as fmt

        engine_errors = (engine.ControlError, cf.ControlFormatError)
    except Exception as error:  # noqa: BLE001 - an import failure is the machine's
        return {"status": "failed", "error_kind": "infra", "error": f"{type(error).__name__}: {error}",
                "key": task.get("key"), "seconds": time.time() - started, "peak": peak_memory()}
    try:
        geo = _load(task["map"])
        blob = fmt.decode_blob(task["blob"])
        link = engine.ControlLink(sides={int(s): side for s, side in task["link"]["sides"].items()},
                                  db_deaths=tuple((int(s), float(t)) for s, t in task["link"]["db_deaths"]))
        rc = engine.compute_round(blob, geo, link)
        result = {"status": "ok", "data": encode_data(rc, blob), "summary": encode_summary(rc, blob),
                  "missing": dict(rc.missing_inputs), "geometry": geometry_used(geo)}
    except engine_errors as error:
        result = {"status": "failed", "error_kind": "engine",
                  "error": f"{type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"}
    except Exception as error:  # noqa: BLE001 - stored as the round's failure by the local command
        kind = "infra" if isinstance(error, (MemoryError, OSError, ImportError)) or \
            type(error).__name__ == "GeometryError" else "engine"
        result = {"status": "failed", "error_kind": kind,
                  "error": f"{type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"}
    result.update({"key": task.get("key"), "seconds": time.time() - started, "peak": peak_memory()})
    return result
