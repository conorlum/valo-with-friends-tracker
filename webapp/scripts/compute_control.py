"""Computes and stores map control for every replay round that has none, or whose inputs changed
(docs/replay-map-control-plan.md, "Where it runs"; Stage 3). A local command: never part of the
upload job, and the web app never runs the engine. New rounds of linked replays can also be computed
on the replay worker (docs/map-control-worker-plan.md, off unless REPLAY_CONTROL_REMOTE is set);
stale rounds are always this command's.

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\compute_control.py --dry-run
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\compute_control.py

Run it in a normal terminal, not through Claude Code's `!` (which stops a command after 30
minutes in the background). About 45 core-seconds per average round.

- **Which rounds** (app/services/replay_control.py `plan`): every round of every valid replay
  whose row is missing or stale (its fingerprint differs: a new blob, link, geometry or
  CONTROL_REVISION). A round that failed with the current inputs is skipped, since it would fail
  again, unless `--retry-failed`; `--force` recomputes everything chosen. Rounds of a map without
  the control layer (`no_map`) and blobs from before condenser revision 10 (`old_blob`) are
  listed, never computed. `--match <uuid>`, `--map <Map>` and `--round <n>` (repeatable) narrow the
  set; after committing one map's heights, `--map <Map>` recomputes just that map's stale rounds.
- **Stops safely.** Each round is committed as it finishes, so stopping and rerunning resumes.
  A round the engine raises on is stored as `failed` with its error (the endpoint says so).
- **Workers.** A pool of one per core but one (`--workers N` sets it), keeping `--headroom-gb`
  (default 4) of RAM free for whatever else is running. The first round runs alone and measures a
  worker's peak memory; after that a round starts only while free RAM covers the headroom plus
  that peak (and the peaks of rounds just started), so a game that starts mid-run slows it down
  instead of crashing it, and it speeds up again when the game ends.
- **ETA** from the last 20 rounds' finish times, not counting the first (solo) round, so it
  follows the current map's pace (an Abyss round takes about twice an Ascent one).
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
from collections import Counter
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.control.task import compute_task, peak_memory  # noqa: E402,F401  (stdlib at import; the engine loads per task)

GB = 1024 ** 3
FIRST_PEAK_GUESS = 0.5 * GB    # a worker's peak before one is measured (real rounds: ~0.2 GB)
POLL_S = 0.5
WAIT_NOTE_S = 60
YOUNG_S = 30                  # a round started this recently may not have allocated its memory yet


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


def room_for_one(free: int | None, headroom: int, peak: float, young: int) -> bool:
    """Whether another round fits: free RAM, less what the `young` rounds (started too recently to
    have allocated yet) will still take, covers the headroom plus one more peak."""
    return free is None or free - headroom - young * peak >= peak


def worker_count(requested: int | None, cores: int) -> int:
    """The pool: `requested`, else one per core but one. Memory never sizes it (a startup guess
    would fix the pool too small for the whole run); `room_for_one` decides how many run at once."""
    return max(1, requested or cores - 1)


ETA_WINDOW = 20


def eta_seconds(finished: list[float], remaining: int, window: int = ETA_WINDOW) -> float | None:
    """Seconds left at the recent rate: rounds finished per second over the last `window` finish
    times, leaving out the first round's (it runs alone, to measure a worker's memory). None
    until two parallel finishes give a rate."""
    times = finished[1:][-(window + 1):]
    if len(times) < 2 or times[-1] <= times[0]:
        return None
    return remaining * (times[-1] - times[0]) / (len(times) - 1)


# ---------------------------------------------------------------- one round, in a worker
# app/control/task.py (shared with the replay worker's control child): blob and link in, the
# row's bytes (or its failure) out, run in the pool's processes.


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
    """app/services/replay_control_store.py, with the fingerprint the round was planned with."""
    from app.services.replay_control_store import store_round

    return store_round(session_factory, planned.replay_id, planned.round_number, planned.fingerprint, result)


def run(planned, args, session_factory) -> int:
    import multiprocessing

    from app.control import geometry
    from app.models.replay import ReplayRound

    todo = [p for p in planned if p.computable]
    for name in sorted({p.map_name for p in todo}):
        started = time.time()
        geo = geometry.visibility(geometry.load_geometry(name))
        print(f"{name}: visibility {geo.visibility_source} in {time.time() - started:.0f}s", flush=True)

    headroom = int(args.headroom_gb * GB)
    cores = os.cpu_count() or 2
    peak = FIRST_PEAK_GUESS
    most = worker_count(args.workers, cores)
    print(f"{len(todo)} round(s), up to {most} at once ({cores} cores), fewer while under {args.headroom_gb:g} GB "
          f"would be free", flush=True)
    reader = session_factory()   # blobs are read as their rounds start, not all up front
    pool = multiprocessing.get_context("spawn").Pool(processes=most)
    pending, running = list(reversed(range(len(todo)))), {}   # key -> (AsyncResult, start time)
    done, failed, sizes, started, measured, finished = 0, [], [], time.time(), False, []
    last_wait_note = 0.0
    try:
        while pending or running:
            limit = most if measured else 1           # the first ok round measures a worker's peak
            while pending and len(running) < limit:
                young = sum(1 for _, t0 in running.values() if time.time() - t0 < YOUNG_S)
                if not room_for_one(free_memory(), headroom, peak, young):
                    if not running and time.time() - last_wait_note > WAIT_NOTE_S:
                        print(f"  waiting for {(headroom + peak) / GB:.1f} GB free", flush=True)
                        last_wait_note = time.time()
                    break
                key = pending.pop()
                p = todo[key]
                row = reader.get(ReplayRound, (p.replay_id, p.round_number))
                blob = None if row is None else row.data
                reader.rollback()
                if blob is None:
                    done += 1
                    print(f"[{done}/{len(todo)}] {p.match_uuid[:8]} r{p.round_number}: skipped, the round is gone "
                          f"(re-ingested?); rerun to pick it up", flush=True)
                    continue
                task = {"key": key, "map": p.map_name, "blob": blob, "link": p.link}
                running[key] = (pool.apply_async(compute_task, (task,)), time.time())
            for key in [k for k, (r, _) in running.items() if r.ready()]:
                result = running.pop(key)[0].get()
                p = todo[key]
                if result["status"] == "ok" and result.get("peak"):
                    peak = max(peak if measured else 0, result["peak"] * 1.2)
                    measured = True
                outcome = store_result(session_factory, p, result)
                done += 1
                finished.append(time.time())
                eta = eta_seconds(finished, len(todo) - done)
                if result["status"] == "ok":
                    sizes.append((len(result["data"]), len(result["summary"])))
                    what = f"ok {result['seconds']:.0f}s, data {sizes[-1][0] / 1000:.0f} KB, summary {sizes[-1][1] / 1000:.0f} KB"
                else:
                    failed.append((p, result["error"].splitlines()[0]))
                    what = f"FAILED: {result['error'].splitlines()[0]}"
                if outcome != "stored":
                    what += f" ({outcome})"
                left = "measuring" if eta is None else _fmt_s(eta)
                print(f"[{done}/{len(todo)}] {p.match_uuid[:8]} r{p.round_number} {p.map_name}: {what}; "
                      f"ETA {left}", flush=True)
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("stopped: finished rounds are stored; rerun to resume", flush=True)
        pool.terminate()
        return 1
    finally:
        reader.close()
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
    parser.add_argument("--map", dest="map_name", help="only this map's replays (e.g. after its heights changed)")
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
        if args.map_name:
            planned = [p for p in planned if p.map_name == args.map_name]
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
