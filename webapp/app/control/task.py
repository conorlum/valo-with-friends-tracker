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

Timing gaps (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 7, "Writer"): a task with
`"gaps": {"replay_id", "round", "fingerprint" (the control fingerprint)}` also writes the round's tick cache
and returns `result["gaps"] = {"run", "rows"}`; with `"gaps_only": True` it returns only `status`, `geometry`
and `gaps` (from the tick cache when it is there, else through the engine) and no control `data`. A gap
failure is the gap run's (`gaps.run.status == "failed"`) and never changes control's result.
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
    the specials, the scale and, on a map with heights, their digest (app/services/replay_control.py
    `geometry_inputs`)."""
    import json

    import numpy as np

    from app.control import geometry

    scale = (json.loads(geometry.MAPS_JSON.read_text(encoding="utf-8")).get(geo.name) or {}).get("xMultiplier")
    used = {"sight": hashlib.sha256(np.packbits(geo.sight).tobytes()).hexdigest()[:12],
            "walk": hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12],
            "barrier": geo.barrier_sha, "specials": list(geo.specials), "scale": scale}
    if geo.height_sha:
        used["height"] = geo.height_sha
    return used


def _load(name: str, heights: str | None = None):
    from app.control import geometry

    key = name if heights is None else (name, heights)     # a map's own geometry is keyed by its name
    if key not in _GEOMETRY:
        geo = geometry.load_geometry(name, heights=Path(heights) if heights else None)
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
        _GEOMETRY[key] = geo
    return _GEOMETRY[key]


def _cache_path(job: dict, map_name: str) -> Path:
    from app.gaps import cache
    from app.services.replay_gaps import engine_key

    return cache.cache_path(job["replay_id"], job["round"], engine_key(job["fingerprint"], map_name))


def _plain(value):
    """Rows and notes as plain Python (numpy scalars and arrays out), so they store as JSON."""
    import numpy as np

    if isinstance(value, dict):
        return {_plain(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


class _CacheFailed(Exception):
    pass


class _CacheGuard:
    """The tick cache writer behind a guard (R18): an exception while writing a tick or the file stops any
    further writes, deletes what was written, and becomes the gap run's failure. The engine never sees it."""

    def __init__(self, path: Path | None):
        from app.gaps import cache

        self.path = None if path is None else Path(path)
        self.writer = None if path is None else cache.Writer(self.path)
        self.error: str | None = None

    def __call__(self, record, unknown) -> None:
        if self.error is None:
            try:
                self.writer(record, unknown)
            except Exception as error:  # noqa: BLE001 - the gap run's failure
                self._fail(error)

    def close(self, missing: dict | None = None) -> str | None:
        if self.error is None:
            try:
                self.writer.close(dict(missing or {}))
            except Exception as error:  # noqa: BLE001 - the gap run's failure
                self._fail(error)
        return self.error

    def _fail(self, error: Exception) -> None:
        self.error = f"tick cache: {type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"
        self.writer = None
        if self.path is None:
            return
        for path in (self.path, self.path.with_name(self.path.name + ".tmp")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _gaps(geo, blob, link, job: dict, guard: _CacheGuard | None = None) -> dict:
    """One round's timing gaps (timing-gaps spec, section 7, "Writer"), in their own failure boundary: an error
    here is the gap run's, never control's. With `guard` (a full run: control was just computed through it),
    the detector reads back the tick cache the guard wrote, so a full run and a gaps-only run give the same
    rows. Without (gaps only), it reads the cache when it is there in this format, else runs the engine
    (writing the cache as it goes). Notes: the detector's cases plus the compute-time missing inputs (R20)."""
    run = {"fingerprint": "", "gaps_revision": 0, "chokes_hash": None, "notes": {}, "error": None}
    try:
        from app.replays import choke_assets
        from app.services.replay_gaps import GAPS_REVISION, gap_fingerprint

        run.update({"fingerprint": gap_fingerprint(job["fingerprint"], geo.name), "gaps_revision": GAPS_REVISION,
                    "chokes_hash": choke_assets.asset_hash(geo.name)})
        if guard is not None and guard.error:
            raise _CacheFailed(guard.error)

        from app.control import engine
        from app.gaps import cache, detect, rows

        path = _cache_path(job, geo.name)
        rnd = engine.RoundInputs(blob, geo, link)
        det = detect.GapDetector(geo, rnd)
        missing = None
        if guard is not None or path.exists():
            try:
                missing = cache.read_missing(path)
            except ValueError:            # a file in another format: a miss, unless this run just wrote it
                if guard is not None:
                    raise
        if missing is not None:
            for record, logs in cache.replay(path):
                det.step(record, logs)
        else:
            writer = _CacheGuard(path)

            def both(record, unknown):
                writer(record, unknown)
                det.step(record, unknown.log)

            rc = engine.compute_round(blob, geo, link, observer=both, knowledge=False)
            missing = dict(rc.missing_inputs)
            if writer.close(missing):
                raise _CacheFailed(writer.error)
        gaps = det.finish()
        run.update({"status": "ok", "notes": _plain(dict(det.notes) | dict(missing))})
        return {"run": run, "rows": _plain(rows.to_rows(gaps, rnd, geo))}
    except _CacheFailed as error:
        run.update({"status": "failed", "notes": {}, "error": str(error)})
    except Exception as error:  # noqa: BLE001 - the gap run's failure, stored with its error
        run.update({"status": "failed", "notes": {},
                    "error": f"{type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"})
    return {"run": run, "rows": []}


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
        geo = _load(task["map"], task.get("heights"))
        blob = fmt.decode_blob(task["blob"])
        link = engine.ControlLink(sides={int(s): side for s, side in task["link"]["sides"].items()},
                                  db_deaths=tuple((int(s), float(t)) for s, t in task["link"]["db_deaths"]))
        job = task.get("gaps")
        if job and task.get("gaps_only"):
            result = {"status": "ok", "geometry": geometry_used(geo), "gaps": _gaps(geo, blob, link, job)}
        else:
            guard = None
            if job:
                try:
                    guard = _CacheGuard(_cache_path(job, geo.name))
                except Exception as error:  # noqa: BLE001 - no cache path: the gap run fails, control runs
                    guard = _CacheGuard(None)
                    guard._fail(error)
            rc = engine.compute_round(blob, geo, link, observer=guard)
            result = {"status": "ok", "data": encode_data(rc, blob), "summary": encode_summary(rc, blob),
                      "missing": dict(rc.missing_inputs), "geometry": geometry_used(geo)}
            if job:
                guard.close(rc.missing_inputs)
                result["gaps"] = _gaps(geo, blob, link, job, guard=guard)
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
