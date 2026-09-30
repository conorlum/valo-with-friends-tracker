"""Computes and stores map control for every replay round that has none, or whose inputs changed
(docs/replay-map-control-plan.md, "Where it runs"; Stage 3). A local command: never part of the
upload job, and the web app never runs the engine.

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\compute_control.py --dry-run
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\compute_control.py

Run it in a normal terminal, not through Claude Code's `!` (which stops a command after 30
minutes in the background). About 45 core-seconds per average round.

- **Which rounds** (app/services/replay_control.py `plan`): every round of every valid replay
  whose row is missing or stale (its fingerprint differs: a new blob, link, geometry or
  CONTROL_REVISION). A round that failed with the current inputs is skipped, since it would fail
  again, unless `--retry-failed`; `--force` recomputes everything chosen. Rounds of a map without
  the control layer (`no_map`) and blobs from before condenser revision 10 (`old_blob`) are
  listed, never computed. `--match <uuid>` and `--round <n>` (repeatable) narrow the set.
- **Stops safely.** Each round is committed as it finishes, so stopping and rerunning resumes.
  A round the engine raises on is stored as `failed` with its error (the endpoint says so).
- **Workers.** At most one per core but one, keeping `--headroom-gb` (default 4) of RAM free for
  whatever else is running. The first round measures a worker's peak memory; after that a round
  starts only while free RAM covers the headroom plus that peak, so a game that starts mid-run
  slows it down instead of crashing it. `--workers N` sets the most at once.
- Each map's visibility bitsets are built (or loaded from `webapp/.control_cache/`) once, before
  the workers start.

`--dry-run` reads only and lists the plan; `--brief` prints one line (the replay scripts' nudge).
Exits 0 when every computed round is ok, 1 when any failed, 3 on the demo database.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

GB = 1024 ** 3
FIRST_PEAK_GUESS = 0.5 * GB    # a worker's peak before one is measured (real rounds: ~0.2 GB)
POLL_S = 0.5
WAIT_NOTE_S = 60


# ---------------------------------------------------------------- memory


def free_memory() -> int | None:
    """Available physical memory in bytes, or None when it can't be read."""
    if sys.platform == "win32":
        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullAvailPhys)
        return None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


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


def worker_count(requested: int | None, cores: int, free: int | None, headroom: int, peak: float) -> int:
    """At most `requested` (else cores - 1), and no more than the free RAM above the headroom holds."""
    most = requested or max(1, cores - 1)
    if free is None:
        return most
    return max(1, min(most, int((free - headroom) // peak)))


# ---------------------------------------------------------------- one round, in a worker

_GEOMETRY: dict = {}


def compute_task(task: dict) -> dict:
    """Runs in a worker process: blob and link in, the row's bytes (or its failure) out."""
    started = time.time()
    try:
        from app.control import engine, geometry
        from app.control.encode import encode_data, encode_summary
        from app.replays import format as fmt

        name = task["map"]
        if name not in _GEOMETRY:
            _GEOMETRY[name] = geometry.visibility(geometry.load_geometry(name))
        blob = fmt.decode_blob(task["blob"])
        link = engine.ControlLink(sides={int(s): side for s, side in task["link"]["sides"].items()},
                                  db_deaths=tuple((int(s), float(t)) for s, t in task["link"]["db_deaths"]))
        rc = engine.compute_round(blob, _GEOMETRY[name], link)
        result = {"status": "ok", "data": encode_data(rc, blob), "summary": encode_summary(rc, blob),
                  "missing": dict(rc.missing_inputs)}
    except Exception as error:  # noqa: BLE001 - stored as the round's failure
        result = {"status": "failed", "error": f"{type(error).__name__}: {error}\n{traceback.format_exc()[-1500:]}"}
    result.update({"key": task["key"], "seconds": time.time() - started, "peak": peak_memory()})
    return result


# ---------------------------------------------------------------- the run


def _fmt_s(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}h{seconds // 60 % 60:02d}m" if seconds >= 3600 else f"{seconds // 60}m{seconds % 60:02d}s"


def describe(planned) -> list[str]:
    lines = []
    todo = [p for p in planned if p.computable]
    reasons = Counter(p.reason for p in todo)
    lines.append(f"{len(todo)} round(s) to compute" + (f" ({', '.join(f'{n} {r}' for r, n in sorted(reasons.items()))})"
                                                        if todo else ""))
    skipped: dict[tuple[str, str], set] = {}
    for p in planned:
        if not p.computable:
            skipped.setdefault((p.reason, p.map_name), set()).add(p.match_uuid)
    for (reason, name), uuids in sorted(skipped.items()):
        why = "waiting for map geometry" if reason == "no_map" else "blob older than condenser revision 10: re-ingest"
        lines.append(f"  {name}: {len(uuids)} replay(s) {why}")
    return lines


def store_result(session_factory, planned, result: dict) -> str:
    from sqlalchemy.exc import IntegrityError

    from app.models.replay import ReplayRoundControl

    session = session_factory()
    try:
        session.merge(ReplayRoundControl(
            replay_id=planned.replay_id, round_number=planned.round_number, status=result["status"],
            fingerprint=planned.fingerprint, data=result.get("data"), summary=result.get("summary"),
            error=result.get("error"), computed_at=datetime.now(timezone.utc)))
        session.commit()
        return "stored"
    except IntegrityError:
        session.rollback()
        return "skipped: the replay changed while computing (re-ingested?)"
    finally:
        session.close()


def run(planned, args, session_factory) -> int:
    import multiprocessing

    from app.control import geometry

    todo = [p for p in planned if p.computable]
    for name in sorted({p.map_name for p in todo}):
        started = time.time()
        geo = geometry.visibility(geometry.load_geometry(name))
        print(f"{name}: visibility {geo.visibility_source} in {time.time() - started:.0f}s", flush=True)

    tasks = []
    session = session_factory()
    try:
        from app.services.replay_control import round_blob_bytes

        for i, p in enumerate(todo):
            tasks.append({"key": i, "map": p.map_name, "blob": round_blob_bytes(session, p.replay_id, p.round_number),
                          "link": p.link})
    finally:
        session.rollback()
        session.close()

    headroom = int(args.headroom_gb * GB)
    cores = os.cpu_count() or 2
    peak = FIRST_PEAK_GUESS
    most = worker_count(args.workers, cores, free_memory(), headroom, peak)
    print(f"{len(tasks)} round(s), up to {most} worker(s) ({cores} cores, keeping {args.headroom_gb:g} GB free)",
          flush=True)
    pool = multiprocessing.get_context("spawn").Pool(processes=most)
    pending, running = list(reversed(tasks)), {}
    done, failed, sizes, started, measured = 0, [], [], time.time(), False
    last_wait_note = 0.0
    try:
        while pending or running:
            limit = most if measured else 1           # the first round measures a worker's peak
            while pending and len(running) < limit:
                free = free_memory()
                if free is not None and free - headroom < peak and running:
                    break
                if free is not None and free - headroom < peak and not running:
                    if time.time() - last_wait_note > WAIT_NOTE_S:
                        print(f"  waiting: {free / GB:.1f} GB free, need {(headroom + peak) / GB:.1f}", flush=True)
                        last_wait_note = time.time()
                    break
                task = pending.pop()
                running[task["key"]] = pool.apply_async(compute_task, (task,))
            for key in [k for k, r in running.items() if r.ready()]:
                result = running.pop(key).get()
                p = todo[key]
                if result.get("peak"):
                    peak = max(peak if measured else 0, result["peak"] * 1.2)
                    measured = True
                outcome = store_result(session_factory, p, result)
                done += 1
                elapsed = time.time() - started
                eta = elapsed / done * (len(todo) - done)
                if result["status"] == "ok":
                    sizes.append((len(result["data"]), len(result["summary"])))
                    what = f"ok {result['seconds']:.0f}s, data {sizes[-1][0] / 1000:.0f} KB, summary {sizes[-1][1] / 1000:.0f} KB"
                else:
                    failed.append((p, result["error"].splitlines()[0]))
                    what = f"FAILED: {result['error'].splitlines()[0]}"
                if outcome != "stored":
                    what += f" ({outcome})"
                print(f"[{done}/{len(todo)}] {p.match_uuid[:8]} r{p.round_number} {p.map_name}: {what}; "
                      f"ETA {_fmt_s(eta)}", flush=True)
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("stopped: finished rounds are stored; rerun to resume", flush=True)
        pool.terminate()
        return 1
    pool.close()
    pool.join()
    print(f"done in {_fmt_s(time.time() - started)}: {len(sizes)} ok, {len(failed)} failed", flush=True)
    if sizes:
        data, summary = [s[0] for s in sizes], [s[1] for s in sizes]
        print(f"  data KB mean {sum(data) / len(data) / 1000:.0f}, max {max(data) / 1000:.0f}; "
              f"summary KB mean {sum(summary) / len(summary) / 1000:.0f}, max {max(summary) / 1000:.0f}", flush=True)
    for p, error in failed:
        print(f"  FAILED {p.match_uuid} r{p.round_number}: {error}", flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what would be computed; write nothing")
    parser.add_argument("--brief", action="store_true", help="with --dry-run: one line, for the replay scripts")
    parser.add_argument("--match", help="only this match UUID")
    parser.add_argument("--round", type=int, action="append", dest="rounds", help="only this round (repeatable)")
    parser.add_argument("--force", action="store_true", help="recompute every chosen round")
    parser.add_argument("--retry-failed", action="store_true", help="also retry rounds that failed with these inputs")
    parser.add_argument("--workers", type=int, default=None, help="the most rounds at once (default: cores - 1)")
    parser.add_argument("--headroom-gb", type=float, default=4.0, help="RAM to leave free (default 4)")
    args = parser.parse_args(argv)

    from app.replays.store import StoreRefused, refuse_demo
    from app.services import replay_control

    if session_factory is None:
        from app.db import SessionLocal as session_factory
    session = session_factory()
    try:
        if not args.dry_run:
            try:
                refuse_demo(session)
            except StoreRefused as refused:
                print(f"REFUSED: {refused}", file=sys.stderr)
                return 3
        if args.brief:
            print(replay_control.nudge(session) or "Map control: every round is up to date")
            return 0
        planned = replay_control.plan(session, match_uuid=args.match,
                                      rounds=set(args.rounds) if args.rounds else None,
                                      force=args.force, retry_failed=args.retry_failed)
    finally:
        session.rollback()
        session.close()
    for line in describe(planned):
        print(line, flush=True)
    if args.dry_run:
        for p in planned:
            if p.computable:
                print(f"  {p.match_uuid} r{p.round_number} {p.map_name}: {p.reason}")
        return 0
    if not any(p.computable for p in planned):
        return 0
    return run(planned, args, session_factory)


if __name__ == "__main__":
    sys.exit(main())
