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
  set; after a map's heights change (a new active row in `control_heights`), `--map <Map>` recomputes
  just that map's stale rounds; the asset is read from the database into `webapp/.control_cache/heights/`.
- **Timing gaps** (docs/superpowers/specs/2026-10-02-timing-gaps-design.md, section 7): every computed
  round also gets its timing gaps, in their own failure boundary (a gap failure never changes the
  control row). Also every round whose control is fresh and ok but whose timing gaps are missing or
  stale (reason `gaps`: new gap rules, an edited choke asset or hearing table; app/services/replay_gaps.py
  `plan_gaps`): those run from the local tick cache (`webapp/.control_cache/gaps/`) when it is there,
  else through the engine, without rewriting control. A gap run that failed with the current inputs is
  skipped unless `--retry-failed`. Like control, gaps are never written to the demo database.
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
Exits 0 when every computed round is ok, 1 when any failed (control or its gaps, or a gap run that
could not be stored), 3 on the demo database.
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
    """app/services/replay_control_store.py, with the fingerprint the round was planned with. A result computed
    with another feature generation than the map's current one (one was published while it ran) is not stored:
    its row would carry outputs that don't match its inputs. Maps without features compare None with None."""
    from app.services import replay_control
    from app.services.replay_control_store import store_round

    if result.get('error_kind') in ('infra', 'compat'):
        return 'skipped: retryable infrastructure or compatibility failure'

    used = (result.get("geometry") or {}).get("features")
    current = (replay_control.geometry_inputs(planned.map_name, heights=None) or {}).get("features")
    if result.get("status") == "ok" and used != current:
        return "skipped: its feature inputs changed while computing"
    return store_round(session_factory, planned.replay_id, planned.round_number, planned.fingerprint, result)


def cache_heights(session, maps) -> dict[str, str]:
    """{map: digest} for the maps whose heights are in the database, each asset written to the local control
    cache (app/control/geometry.py `height_cache_path`) when it isn't there yet."""
    from app.control import geometry
    from app.services import control_heights

    out = {}
    active = control_heights.active_digests(session)
    for name in maps:
        digest = active.get(name)
        if not digest:
            continue
        path = geometry.height_cache_path(name, digest)
        if not path.is_file():
            data = control_heights.asset_bytes(session, name, digest)
            if data is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        out[name] = digest
    return out


def run(planned, args, session_factory) -> int:
    import multiprocessing

    from app.control import geometry
    from app.models.replay import ReplayRound

    todo = [p for p in planned if p.computable]
    reader = session_factory()   # blobs are read as their rounds start, not all up front
    heights = cache_heights(reader, sorted({p.map_name for p in todo}))
    reader.rollback()
    for name in sorted({p.map_name for p in todo}):
        started = time.time()
        cached = geometry.height_cache_path(name, heights[name]) if name in heights else None
        geo = geometry.visibility(geometry.load_geometry(name, heights=cached))
        print(f"{name}: visibility {geo.visibility_source} in {time.time() - started:.0f}s"
              + (f" (heights {heights[name]})" if name in heights else ""), flush=True)

    headroom = int(args.headroom_gb * GB)
    cores = os.cpu_count() or 2
    peak = FIRST_PEAK_GUESS
    most = worker_count(args.workers, cores)
    print(f"{len(todo)} round(s), up to {most} at once ({cores} cores), fewer while under {args.headroom_gb:g} GB "
          f"would be free", flush=True)
    pool = multiprocessing.get_context("spawn").Pool(processes=most)
    pending, running = list(reversed(range(len(todo)))), {}   # key -> (AsyncResult, start time)
    done, failed, sizes, started, measured, finished = 0, [], [], time.time(), False, []
    gap_runs, gap_failed, gap_unstored = 0, [], []       # R19: counted apart from control's
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
                task = {"key": key, "map": p.map_name, "blob": blob, "link": p.link,
                        "gaps": {"replay_id": p.replay_id, "round": p.round_number, "fingerprint": p.fingerprint},
                        "gaps_only": p.reason == "gaps"}
                if p.map_name in heights:
                    task["height"] = heights[p.map_name]
                running[key] = (pool.apply_async(compute_task, (task,)), time.time())
            for key in [k for k, (r, _) in running.items() if r.ready()]:
                result = running.pop(key)[0].get()
                p = todo[key]
                gaps_only = p.reason == "gaps"
                if result["status"] == "ok" and result.get("peak"):
                    # a gaps-only round (often read from the tick cache) never lowers the guess
                    peak = max(peak if measured or gaps_only else 0, result["peak"] * 1.2)
                    measured = True
                outcome = "stored" if gaps_only else store_result(session_factory, p, result)
                gap_note = ""
                if result.get("gaps") and outcome == "stored":     # not for a round whose control was skipped
                    from app.services.replay_gaps_store import store_gaps

                    gap_runs += 1
                    run_row = result["gaps"]["run"]
                    rows = result.get("gaps", {}).get("rows", [])
                    gap_outcome = store_gaps(session_factory, p.replay_id, p.round_number, run_row, rows)
                    if run_row["status"] != "ok":
                        gap_failed.append((p, (run_row.get("error") or "failed").splitlines()[0]))
                        gap_note = f"gaps FAILED: {gap_failed[-1][1]}"
                    else:
                        gap_note = f"gaps {len(rows)}"
                    if gap_outcome != "stored":
                        gap_unstored.append((p, gap_outcome))
                        gap_note += f" ({gap_outcome})"
                elif gaps_only:                # the task failed before its gaps (geometry, the blob)
                    error = result.get("error") or "no gaps returned"
                    gap_failed.append((p, error.splitlines()[0]))
                    gap_note = f"gaps FAILED: {gap_failed[-1][1]}"
                done += 1
                finished.append(time.time())
                eta = eta_seconds(finished, len(todo) - done)
                if gaps_only:
                    what = f"{result.get('seconds', 0):.0f}s, {gap_note}"
                elif result["status"] == "ok":
                    sizes.append((len(result["data"]), len(result["summary"])))
                    what = f"ok {result['seconds']:.0f}s, data {sizes[-1][0] / 1000:.0f} KB, summary {sizes[-1][1] / 1000:.0f} KB"
                    if gap_note:
                        what += f", {gap_note}"
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
    print(f"  timing gaps: {gap_runs} run(s), {len(gap_failed)} failed, {len(gap_unstored)} not stored", flush=True)
    for p, error in gap_failed:
        print(f"  GAPS FAILED {p.match_uuid} r{p.round_number}: {error}", flush=True)
    for p, outcome in gap_unstored:
        print(f"  GAPS NOT STORED {p.match_uuid} r{p.round_number}: {outcome}", flush=True)
    return 1 if failed or gap_failed or gap_unstored else 0


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
        # fresh, ok control whose timing gaps are missing or stale (app/services/replay_gaps.py, R17)
        from app.services import replay_gaps

        every = replay_control.plan(session, match_uuid=args.match,
                                    rounds=set(args.rounds) if args.rounds else None, force=True)
        if args.map_name:
            every = [p for p in every if p.map_name == args.map_name]
        planned = planned + replay_gaps.plan_gaps(session, planned, every, retry_failed=args.retry_failed)
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
