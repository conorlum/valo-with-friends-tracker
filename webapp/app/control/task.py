"""One round's control as a task: blob and link in, the row's bytes (or its failure) out.

Run by scripts/compute_control.py's worker processes and by the replay worker's control child
(replay_worker/control_job.py; docs/map-control-worker-plan.md). A task is
`{"key", "map", "blob" (the stored gzip bytes), "link": {"sides", "db_deaths"}}`; the result is
`{"status": "ok", "data", "summary", "missing", "geometry"}` or `{"status": "failed", "error",
"error_kind"}`, plus the key, the seconds it took and the process's peak memory. A task may name the map's
heights by digest (`"height"`): the asset is then read from the control cache (`geometry.height_cache_path`),
where the replay worker's `/heights` endpoint or scripts/compute_control.py put it; a missing or wrong file is
the machine's failure.

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
The replay worker's tasks also carry `gap_fingerprint` and `engine_key` in that block (the web app computes
them; the worker's interpreter cannot import app.services).
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
import time
import traceback
from pathlib import Path

_GEOMETRY: dict = {}
from app.replays.map_feature_artifacts import FeatureArtifactError, feature_failure, VerifiedArtifactCache
_ARTIFACTS = VerifiedArtifactCache()


def load_task_features(task, geo, cache_dir):
    from app.replays import map_feature_artifacts as fa, map_feature_inputs as fi
    from app.control.features import verify_artifact
    expected = task.get('features')
    context = task.get('geometry') or {}
    if expected != context.get('features') and 'geometry' in task:
        raise fa.FeatureArtifactCorrupt('task feature digest differs from geometry inputs')
    if not expected:
        geo.features, geo.features_sha = None, None
        return None
    try:
        item = fa.load_cached_artifact(cache_dir, expected)
        if task['map'] != geo.name or item.key.map_name != geo.name or \
                item.key.height_digest != (getattr(geo, 'height_sha', None) or 'flat') or \
                item.key.height_digest != (task.get('height') or 'flat'):
            raise fa.FeatureArtifactCorrupt('task map/height differs from archived inputs')
        envelope = fi.read_json(item.inputs)
        if item.key.compiler_version != fi.FEATURE_COMPILER_VERSION or \
                envelope['normalization'] != fi.FEATURE_NORMALIZATION_VERSION or \
                any(fi.CONSUMER_VERSIONS.get(k) != v for k, v in envelope['consumers'].items()):
            raise fa.UnsupportedFeatureCompiler('recorded feature semantics unavailable')
        try:
            _ARTIFACTS.get_or_verify(item, lambda artifact: verify_artifact(artifact, geo))
        except fa.FeatureArtifactCorrupt:
            fa.cache_path(cache_dir, expected).unlink(missing_ok=True)
            raise
        geo.features, geo.features_sha = item, item.digest
        return item
    except fa.FeatureArtifactError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise fa.FeatureArtifactCorrupt(str(exc)) from exc


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

    from app.replays import format as fmt
    scale = geo.uv_per_unit / fmt.UV_SCALE
    used = {"sight": hashlib.sha256(np.packbits(geo.sight).tobytes()).hexdigest()[:12],
            "walk": hashlib.sha256(np.packbits(geo.walk_px).tobytes()).hexdigest()[:12],
            "barrier": geo.barrier_sha, "specials": list(geo.specials), "scale": scale}
    if geo.height_sha:
        used["height"] = geo.height_sha
    if getattr(geo, "features_sha", None):     # the feature generation actually loaded (absent: none)
        used["features"] = geo.features_sha
    return used


def height_file(name: str, digest: str) -> Path:
    """The cached asset a task names, checked: it must be there and be that asset. A wrong file is removed."""
    from app.control import geometry
    from app.control import heights as hc

    path = geometry.height_cache_path(name, digest)
    if not path.is_file():
        raise geometry.GeometryError(f"{name}: height asset {digest} is not on this machine")
    try:
        found = hc.load_asset(path).digest
    except Exception as error:  # noqa: BLE001 - a truncated or foreign file
        found = f"unreadable ({type(error).__name__})"
    if found != digest:
        path.unlink(missing_ok=True)
        raise geometry.GeometryError(f"{name}: {path.name} is not the asset {digest} (it is {found})")
    return path


def _load(name: str, heights: str | None = None, digest: str | None = None, *, exact=False, height_mode='legacy_default'):
    from app.control import features, geometry

    if heights is None and digest:
        heights = str(geometry.height_cache_path(name, digest))
    # A map's own geometry is keyed by its name; with an active feature generation, by that generation too, so
    # a worker never keeps computing with a superseded one (absent for every map today: the key is unchanged).
    generation = None if exact else features.active_sha(name)
    key = (name, heights, exact, height_mode) if exact or height_mode != 'legacy_default' else name if heights is None and generation is None else (name, heights, generation)
    if key not in _GEOMETRY:
        if digest:
            height_file(name, digest)
        options = {'load_features': False} if exact else {}
        if height_mode != 'legacy_default':
            options['height_mode'] = height_mode
        geo = geometry.load_geometry(name, heights=Path(heights) if heights else None, **options)
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
        _VERIFIED[key] = None if exact else verify_features(geo)
        _GEOMETRY[key] = geo
    elif generation is not None:
        # The generation pointer can stay put while its definitions change (an edited tags.json): a cached
        # generation is verified again whenever the definition inputs differ from the ones it was verified
        # against, so a worker never keeps computing with geometry its definitions no longer compile to.
        geo = _GEOMETRY[key]
        entry = _definitions(geo.name)
        if _VERIFIED.get(key) != _definitions_digest(entry):
            try:
                _VERIFIED[key] = verify_features(geo, entry=entry)
            except Exception:
                _GEOMETRY.pop(key, None)       # the next try loads the generation again
                _VERIFIED.pop(key, None)
                raise
    return _GEOMETRY[key]


_VERIFIED: dict = {}        # cache key -> digest of the definition inputs its generation was verified against


def _definitions(name: str) -> dict:
    """A map's tags.json entry: what a generation's definitions are compiled from. An unreadable file (say, caught
    mid-write) is the machine's failure (GeometryError), never the round's."""
    from app.control import geometry

    try:
        return geometry.load_tags().get("maps", {}).get(name, {})
    except (OSError, ValueError) as error:
        raise geometry.GeometryError(f"{name}: tags.json unreadable: {error}") from error


def _definitions_digest(entry: dict) -> str:
    """Everything `features.verify` recompiles from besides the geometry: the definitions, the legacy paints
    and the registered consumers."""
    import json

    from app.control import features

    inputs = {"map_features": entry.get("map_features"), "cover_paint": entry.get("cover_paint"),
              "cant_walk_paint": entry.get("cant_walk_paint"), "consumers": sorted(features.RUNTIME_CONSUMERS)}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def verify_features(geo, expected: str | None = None, full: bool = True, entry: dict | None = None) -> str | None:
    """Map features (M5): the loaded generation must be the expected one (a task may name it), its compiled
    assets must hash to its manifest, and the map's current definitions (tags.json, or `entry`) must still
    compile to that manifest. Any mismatch raises GeometryError: the machine's (infra) failure, retried, never
    stored as the round's. Nothing to check on a map without a generation, which is every map today. Returns the
    digest of the definition inputs a full check verified (None when there was nothing to check)."""
    from app.control import features, geometry

    if expected is not None and expected != geo.features_sha:
        raise geometry.GeometryError(f"{geo.name}: expected feature generation {expected}, loaded {geo.features_sha}")
    if not geo.features_sha or not full:
        return None
    entry = _definitions(geo.name) if entry is None else entry
    problems = features.verify(geo.features["manifest"], geo.features["assets"], geo, entry.get("map_features"),
                               features.legacy_masks(entry))
    if problems:
        raise geometry.GeometryError(f"{geo.name}: feature generation {geo.features_sha} is stale: {'; '.join(problems)}")
    return _definitions_digest(entry)


def _cache_path(job: dict, map_name: str) -> Path:
    from app.gaps import cache

    key = job.get("engine_key")
    if key is None:             # the local command; the replay worker's tasks carry the key (no SQLAlchemy there)
        from app.services.replay_gaps import engine_key

        key = engine_key(job["fingerprint"], map_name)
    return cache.cache_path(job["replay_id"], job["round"], key)


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


class _CacheUnreadable(Exception):
    pass


def _replay_cache(cache, path: Path, det) -> dict:
    """Steps `det` through the tick cache at `path` and returns its stored missing-input counts. Any error
    while reading the file is raised as _CacheUnreadable; an error in `det.step` propagates unchanged."""
    try:
        missing = cache.read_missing(path)
        ticks = iter(cache.replay(path))
    except Exception as error:  # noqa: BLE001 - any read error is a miss
        raise _CacheUnreadable(f"{type(error).__name__}: {error}") from error
    while True:
        try:
            record, logs = next(ticks)
        except StopIteration:
            return missing
        except Exception as error:  # noqa: BLE001 - any read error is a miss
            raise _CacheUnreadable(f"{type(error).__name__}: {error}") from error
        det.step(record, logs)


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

    def on_tick(self, tick, unknown) -> None:
        """The engine's hook (compute_round): the tick record is built here, inside the guard (final review M1),
        so an error building it fails the gap run and never control."""
        if self.error is None:
            try:
                from app.control import observe

                self.writer(observe.record(tick, unknown), unknown)
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
    rows. Without (gaps only), it reads the cache when it is there and readable (an unreadable file is a miss
    and is deleted), else runs the engine (writing the cache as it goes). Notes: the detector's cases plus the compute-time missing inputs (R20)."""
    run = {"fingerprint": "", "gaps_revision": 0, "chokes_hash": None, "notes": {}, "error": None}
    try:
        from app.replays import choke_assets

        if job.get("gap_fingerprint"):
            # The replay worker: the web app sent the fingerprint it will check the result against, and the
            # control venv has no SQLAlchemy to import app.services with. The revision is the detector's own,
            # so a worker on other gap rules says so.
            from app.gaps.detect import GAPS_REVISION

            fingerprint = job["gap_fingerprint"]
        else:
            from app.services.replay_gaps import GAPS_REVISION, gap_fingerprint

            fingerprint = gap_fingerprint(job["fingerprint"], geo.name)
        run.update({"fingerprint": fingerprint, "gaps_revision": GAPS_REVISION,
                    "chokes_hash": choke_assets.asset_hash(geo.name)})
        if guard is not None and guard.error:
            raise _CacheFailed(guard.error)

        from app.control import engine
        from app.gaps import cache, detect, rows

        path = _cache_path(job, geo.name)
        rnd = engine.RoundInputs(blob, geo, link)
        det = detect.GapDetector(geo, rnd)
        missing = None
        if guard is not None:             # a full run reads back the file it just wrote: any error is the run's
            missing = cache.read_missing(path)
            for record, logs in cache.replay(path):
                det.step(record, logs)
        elif path.exists():
            # gaps only: any error reading the file (another format, truncated, corrupt) is a miss, and the
            # unreadable file is deleted so the engine run below rewrites it (final review M7). An error in the
            # detector itself is not a read error and still fails the run.
            try:
                missing = _replay_cache(cache, path, det)
            except _CacheUnreadable:
                for stale in (path, path.with_name(path.name + ".tmp")):
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError:
                        pass
                rnd = engine.RoundInputs(blob, geo, link)        # a fresh detector: it may have stepped
                det, missing = detect.GapDetector(geo, rnd), None
        if missing is None:
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
        options = {'exact': task['features']} if task.get('features') else {}
        if task.get('height_mode'):
            options['height_mode'] = task['height_mode']
        geo = _load(task["map"], task.get("heights"), task.get("height"), **options)
        if task.get("features"):           # the generation the dispatcher planned with (map features)
            from app.control import geometry
            load_task_features(task, geo, geometry.cache_dir() / 'features')
        blob = fmt.decode_blob(task["blob"])
        provenance = None
        if task.get('features'):
            from app.replays.map_feature_artifacts import FeatureArtifactCorrupt
            envelope = task.get('inputs')
            actual = geometry_used(geo)
            if not isinstance(envelope, dict) or set(envelope) != {'control', 'data', 'summary', 'recipe', 'source', 'link', 'geometry', 'figures'} \
                    or envelope['geometry'] != actual or envelope['figures'] != cf.figures_hash() or \
                    (envelope['control'], envelope['data'], envelope['summary']) != (cf.CONTROL_REVISION, cf.DATA_VERSION, cf.SUMMARY_VERSION) \
                    or any(envelope['link'].get(k) != task['link'].get(k) for k in ('sides', 'db_deaths')):
                raise FeatureArtifactCorrupt('actual child inputs differ from planned envelope')
            provenance = {'v': 1, 'inputs': envelope, 'fingerprint': cf.fingerprint_from_inputs(envelope)}
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
            summary_options = {'provenance': provenance} if provenance is not None else {}
            result = {"status": "ok", "data": encode_data(rc, blob), "summary": encode_summary(rc, blob, **summary_options),
                      "missing": dict(rc.missing_inputs), "geometry": geometry_used(geo)}
            if job:
                guard.close(rc.missing_inputs)
                result["gaps"] = _gaps(geo, blob, link, job, guard=guard)
    except FeatureArtifactError as error:
        result = feature_failure(error)
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
