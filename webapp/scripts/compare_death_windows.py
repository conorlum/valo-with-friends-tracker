"""Every death of a live replay's rounds at several statue windows (lost control at a death), from the friends
site's public responses only: no database, no writes but `<out.json>`.

    .\\.venv\\Scripts\\python.exe scripts\\compare_death_windows.py <match uuid> <out.json> <workers> 5 8 10 [--rounds 7,9]
    ... <match uuid> <out.json> <workers> --record 15 [--masks]     (per-tick record; death_windows_from_record.py)

One engine pass per round serves every window: the real round is stepped once, and each statue run (a death's
own, or a group's joint run) is forked once and stepped to the longest window that needs it, adding its loss to
every window still open. The runs and their sums stay in this script; the stored summary isn't used. One task per
round, `workers` at a time; `<out.json>` is rewritten after each, so a partial run can be read. It names real
players: keep it local.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WEBAPP_ROOT), str(WEBAPP_ROOT / "scripts")]

WINDOWS: list[float] = []
_captured: dict = {}


def _blank() -> dict:
    return {"lost": 0.0, "by_level": {"active": 0.0, "safe": 0.0, "passive": 0.0},
            "went_to": {"enemy": 0.0, "contested": 0.0, "nobody": 0.0}, "steps": 0}


def _install(windows: list[float]) -> None:
    """Patch the engine in this process: plan_statues plans every window's runs, shared where they are the same
    run (same statues, same fork), and compute_round runs at the longest window and is kept for its timings."""
    from app.control import engine
    from app.control.engine import CONTESTED, CONTESTED_ACTIVE, level_of, score

    WINDOWS[:] = sorted(windows)
    plan_one, compute_one = engine.plan_statues, engine.compute_round

    @dataclass
    class MultiRun(engine.StatueRun):
        horizons: dict = field(default_factory=dict)       # window -> t1
        sums: dict = field(default_factory=dict)           # window -> _blank()

        def step(self, t, w, real, rnd, cell_m2):
            tick = self.runner.step(engine.Tick(self.rnd, t))
            spans = {}
            for win, t1 in self.horizons.items():
                if t < t1:
                    self.sums[win]["steps"] += 1
                spans[win] = min(t + w, t1, rnd.t_decided) - max(t, self.t0, rnd.t_start)
            if max(spans.values()) <= 0:
                return
            geo = rnd.geo
            state = tick.compose()["state"]
            lost = (score(state, self.side) > 0) & ~(score(real, self.side) > 0)
            if not lost.any():
                return
            lv = level_of(state, self.side)
            theirs = lost & (score(real, self.side) < 0)
            contested = lost & ((real == CONTESTED) | (real == CONTESTED_ACTIVE))
            area = geo.area(lost) * cell_m2
            levels = {name: geo.area(lost & (lv == code)) * cell_m2
                      for name, code in (("active", 3), ("safe", 2), ("passive", 1))}
            went = {"enemy": geo.area(theirs) * cell_m2, "contested": geo.area(contested) * cell_m2,
                    "nobody": geo.area(lost & ~theirs & ~contested) * cell_m2}
            for win, span in spans.items():
                if span <= 0:
                    continue
                s = self.sums[win]
                s["lost"] += area * span
                for k, v in levels.items():
                    s["by_level"][k] += v * span
                for k, v in went.items():
                    s["went_to"][k] += v * span
            top = self.sums[max(self.horizons)]          # the engine's own read, at the longest window
            self.lost_m2s, self.by_level, self.went_to = top["lost"], top["by_level"], top["went_to"]

    def plan_all(rnd, window):
        runs: dict = {}
        plans: dict = {}

        def shared(run):
            key = (run.side, tuple(sorted(run.statues.items())), run.t_fork, run.t0)
            if key not in runs:
                runs[key] = MultiRun(run.side, dict(run.statues), run.t_fork, run.t0, run.t1)
            m = runs[key]
            m.horizons[win] = run.t1
            m.sums[win] = _blank()
            m.t1 = max(m.t1, run.t1)
            return key

        for win in WINDOWS:
            # inf: the statue stands until the round is decided (a window longer than the round)
            _, plan = plan_one(rnd, win if math.isfinite(win) else rnd.t_end - rnd.t_start + 1.0)
            plans[win] = {death: (shared(own), shared(joint) if joint is not None else None, group)
                          for death, (own, joint, group) in plan.items()}
        _captured.update(runs=runs, plans=plans, deaths=list(rnd.deaths()), t_start=rnd.t_start,
                         t_decided=rnd.t_decided)
        longest = max(WINDOWS)
        return list(runs.values()), {d: (runs[o], runs[j] if j else None, g) for d, (o, j, g) in plans[longest].items()}

    def compute_all(*args, **kwargs):
        kwargs["death_window_s"] = max(WINDOWS) if math.isfinite(max(WINDOWS)) else 1000.0
        rc = compute_one(*args, **kwargs)
        _captured["timings"] = dict(rc.timings)
        return rc

    engine.plan_statues, engine.compute_round = plan_all, compute_all


def _install_shapley(window: float) -> None:
    """Patch the engine for Shapley groups at one window: for each group (plan_statues' grouping), a statue run
    for every non-empty subset of its deaths, each from that subset's first death to the group's last death plus
    the window. compute_round runs at `window`; the subsets' sums are read back in _shapley()."""
    from itertools import combinations

    from app.control import engine

    plan_one, compute_one = engine.plan_statues, engine.compute_round

    def plan_all(rnd, _window):
        _, plan = plan_one(rnd, window)
        groups = {tuple(group) for _, _, group in plan.values()}
        runs, subsets = [], {}
        for group in sorted(groups, key=lambda g: g[0][1]):
            side = rnd.team[group[0][0]]
            t1 = max(t for _, t in group) + window
            for k in range(1, len(group) + 1):
                for sub in combinations(group, k):
                    t0 = min(t for _, t in sub)
                    run = engine.StatueRun(side, dict(sub), engine.snap_before(t0), t0, t1)
                    runs.append(run)
                    subsets[(group, sub)] = run
        _captured.update(groups=groups, subsets=subsets, deaths=list(rnd.deaths()))
        # the engine's own summary reads own/joint: give it the subset runs (not used by this script)
        eplan = {d: (subsets[(tuple(g), (d,))], subsets[(tuple(g), tuple(g))] if len(g) > 1 else None, g)
                 for d, (_, _, g) in plan.items()}
        return runs, eplan

    def compute_all(*args, **kwargs):
        kwargs["death_window_s"] = window
        rc = compute_one(*args, **kwargs)
        _captured["timings"] = dict(rc.timings)
        return rc

    engine.plan_statues, engine.compute_round = plan_all, compute_all
    _captured["shapley_window"] = window


def _shapley() -> list[dict]:
    """Each death's Shapley share of its group's joint loss (m², over the window), with its solo and the joint."""
    from itertools import combinations

    window = SHAPLEY_WINDOW[0]
    subsets = _captured["subsets"]
    rows = {}
    for group in _captured["groups"]:
        n = len(group)
        v = {(): 0.0}
        for k in range(1, n + 1):
            for sub in combinations(group, k):
                v[sub] = subsets[(group, sub)].lost_m2s
        joint = v[group]
        solos = {d: v[(d,)] for d in group}
        for d in group:
            others = [o for o in group if o != d]
            phi = 0.0
            for k in range(0, n):
                for sub in combinations(others, k):
                    with_d = tuple(sorted(sub + (d,), key=group.index))
                    phi += math.factorial(k) * math.factorial(n - k - 1) / math.factorial(n) * (v[with_d] - v[sub])
            total = sum(solos.values())
            prop = joint * solos[d] / total if total > 0 else joint / n
            rows[d] = {"shapley_m2": round(phi / window, 1), "prop_m2": round(prop / window, 1),
                       "solo_m2": round(solos[d] / window, 1), "joint_m2": round(joint / window, 1), "group": n,
                       "runs": 2 ** n - 1}
    out = []
    for slot, t in _captured["deaths"]:
        r = rows.get((slot, t), {"shapley_m2": 0.0, "prop_m2": 0.0, "solo_m2": 0.0, "joint_m2": 0.0, "group": 1,
                                 "runs": 0, "no_run": True})
        out.append({"slot": slot, "t": round(t, 3), **r})
    return out


SHAPLEY_WINDOW: list[float] = []
RECORD: dict = {}

# A recorded tick's columns: the tick, its weight, then m² (area × cell_m2, not yet × span) lost, by level, went to.
TICK_COLUMNS = ("t", "w", "lost", "active", "safe", "passive", "enemy", "contested", "nobody")


def _pack(a) -> str:
    import base64
    import zlib
    return base64.b64encode(zlib.compress(a.tobytes(), 6)).decode("ascii")


def _install_record(window: float, masks: bool) -> None:
    """Patch the engine to record, for each group at `window` (plan_statues' grouping), a statue run for every
    non-empty subset of its deaths, from that subset's first death to the group's last death plus `window`, and
    each run's unclipped loss on every tick (TICK_COLUMNS; with `masks`, the lost cells too). Any window up to
    `window`, the proportional or Shapley split and the stretch rules are then sums over these ticks
    (death_windows_from_record.py). A shorter window's groups are runs of consecutive deaths inside these
    groups, so their subsets are among the runs; statues standing longer don't change earlier ticks."""
    from itertools import combinations

    import numpy as np
    from app.control import engine
    from app.control.engine import CONTESTED, CONTESTED_ACTIVE, level_of, score

    plan_one, compute_one = engine.plan_statues, engine.compute_round

    @dataclass
    class RecordRun(engine.StatueRun):
        ticks: list = field(default_factory=list)
        lost_cells: list = field(default_factory=list)
        steps: int = 0

        def step(self, t, w, real, rnd, cell_m2):
            tick = self.runner.step(engine.Tick(self.rnd, t))
            self.steps += 1
            if min(t + w, self.t1, rnd.t_decided) - max(t, self.t0, rnd.t_start) <= 0:
                return
            geo = rnd.geo
            state = tick.compose()["state"]
            lost = (score(state, self.side) > 0) & ~(score(real, self.side) > 0)
            if not lost.any():
                return
            lv = level_of(state, self.side)
            theirs = lost & (score(real, self.side) < 0)
            contested = lost & ((real == CONTESTED) | (real == CONTESTED_ACTIVE))
            self.ticks.append((t, w, geo.area(lost) * cell_m2,
                               *(geo.area(lost & (lv == code)) * cell_m2 for code in (3, 2, 1)),
                               geo.area(theirs) * cell_m2, geo.area(contested) * cell_m2,
                               geo.area(lost & ~theirs & ~contested) * cell_m2))
            if masks:
                self.lost_cells.append(np.packbits(lost))

    def plan_all(rnd, _window):
        _, plan = plan_one(rnd, window)
        groups = sorted({tuple(group) for _, _, group in plan.values()}, key=lambda g: g[0][1])
        runs, subsets = [], {}
        for group in groups:
            side = rnd.team[group[0][0]]
            t1 = max(t for _, t in group) + window
            for k in range(1, len(group) + 1):
                for sub in combinations(group, k):
                    t0 = min(t for _, t in sub)
                    run = RecordRun(side, dict(sub), engine.snap_before(t0), t0, t1)
                    runs.append(run)
                    subsets[(group, sub)] = run
        eligible = sorted(plan, key=lambda d: d[1])
        _captured.update(groups=groups, subsets=subsets, deaths=list(rnd.deaths()), t_start=rnd.t_start,
                         t_decided=rnd.t_decided, eligible=[(s, t, rnd.team[s]) for s, t in eligible],
                         n_cells=rnd.geo.n)
        eplan = {d: (subsets[(tuple(g), (d,))], subsets[(tuple(g), tuple(g))] if len(g) > 1 else None, g)
                 for d, (_, _, g) in plan.items()}
        return runs, eplan

    def compute_all(*args, **kwargs):
        kwargs["death_window_s"] = window
        rc = compute_one(*args, **kwargs)
        _captured["timings"] = dict(rc.timings)
        return rc

    engine.plan_statues, engine.compute_round = plan_all, compute_all
    RECORD.update(window=window, masks=masks)


def _recorded() -> dict:
    """This round's recording: the deaths, which of them have runs (and their side), and every run's ticks."""
    import numpy as np

    runs = []
    for (group, sub), run in _captured["subsets"].items():
        ticks = np.array(run.ticks, dtype=np.float64).reshape(-1, len(TICK_COLUMNS))
        r = {"group": [list(d) for d in group], "statues": [list(d) for d in sub], "side": run.side,
             "t_fork": run.t_fork, "t0": run.t0, "t1": run.t1, "steps": run.steps, "n_ticks": len(ticks),
             "ticks": _pack(ticks)}
        if RECORD["masks"]:
            r["lost_cells"] = _pack(np.array(run.lost_cells, dtype=np.uint8))
        runs.append(r)
    return {"t_start": _captured["t_start"], "t_decided": _captured["t_decided"],
            "deaths": [list(d) for d in _captured["deaths"]],
            "eligible": [list(d) for d in _captured["eligible"]], "n_cells": _captured["n_cells"], "runs": runs}


PATH_CAP_M = 100.0      # a route that doesn't exist counts as this long
NEAR_ALLY_M = 3.0       # the ground around the ally where the route may be seen (it has reached them)


def _install_path() -> None:
    """Patch TickRunner.step: on the real round's tick before each death, the enemy's shortest quiet, unseen route
    to the dying player's nearest living ally, with the dying player and without them (route_before/after_m)."""
    from app.control import engine

    step_one, fork_one = engine.TickRunner.step, engine.TickRunner.fork

    def fork(self):
        out = fork_one(self)
        out._statue = True
        return out

    def step(self, tick, timings=None):
        tick = step_one(self, tick) if timings is None else step_one(self, tick, timings)
        rnd = getattr(tick, "rnd", None)
        if rnd is None or getattr(self, "_statue", False):
            return tick
        if _captured.get("path_rnd") is not rnd:
            _captured["path_rnd"] = rnd
            _captured["path"] = {}
            _captured["path_due"] = {}
            for slot, t in rnd.deaths():
                _captured["path_due"].setdefault(round(engine.snap_before(t), 6), []).append((slot, t))
        for slot, t_death in _captured["path_due"].get(round(float(tick.t), 6), []):
            a = time.perf_counter()
            r = _route_cost(tick, self.unknown, slot)
            r["seconds"] = time.perf_counter() - a
            _captured["path"][(slot, t_death)] = r
        return tick

    engine.TickRunner.step, engine.TickRunner.fork = step, fork


def _route_cost(tick, unknown, dead: int) -> dict:
    import numpy as np
    from app.control import engine

    h_dead = tick.holders.get(dead)
    if h_dead is None:
        return {"note": "not on the tick"}
    side = h_dead.team
    allies = [h for h in tick.holders.values() if h.team == side and h.slot != dead]
    if not allies:
        return {"note": "last alive"}
    geo, topo = unknown.geo, unknown.topo
    n = geo.n
    enemies = [h for h in tick.holders.values() if h.team != side]
    src = tick.unknown[side].copy() if getattr(tick, "unknown", None) else np.zeros(n, bool)
    for e in enemies:
        src[e.cell] = True
    if not src.any():
        return {"note": "no enemy anywhere"}
    shut = np.asarray(tick.sealed, bool).copy() if getattr(tick, "sealed", None) is not None else np.zeros(n, bool)
    hazard = getattr(tick, "hazard", None)
    if hazard and hazard.get(side) is not None:
        shut |= np.asarray(hazard[side], bool)
    for e in enemies:
        shut[e.cell] = False
    rnd = tick.rnd
    trips_by = {}
    for w in rnd.watchers:
        if w.kind == "trip" and rnd.team.get(w.by) == side and engine._watching(w, tick.t) and rnd.alive(w.by, tick.t):
            trips_by.setdefault(w.by, np.zeros(n, bool))[w.cells] = True
    live = {h.slot: engine.live_mask(h) for h in tick.holders.values() if h.team == side}
    straight = geo.cell_m / engine.UNKNOWN_MPS
    near_steps = max(1, int(round(NEAR_ALLY_M / geo.cell_m)))

    def nearest(without_dead: bool) -> tuple[float, int | None]:
        best, who = np.inf, None
        for a in allies:
            others = [s for s in live if s != a.slot and not (without_dead and s == dead)]
            seen = np.zeros(n, bool)
            for s in others:
                seen |= live[s]
            target = np.zeros(n, bool)
            target[a.cell] = True
            target = topo.dilate(target, eight=True, iterations=near_steps)
            room = geo.walk_n & ~shut & (~seen | target)
            solid = np.zeros(n, bool)
            for s, m in trips_by.items():
                if not (without_dead and s == dead):
                    solid |= m
            reached = np.where(src, 0.0, np.inf)
            arr = topo.spread(reached, room, np.zeros(n), 1e9, straight, unknown.links, solid=solid, quiet=True)
            d = float(arr[a.cell]) * engine.UNKNOWN_MPS
            if d < best:
                best, who = d, a.slot
        return best, who

    before, who_b = nearest(False)
    after, who_a = nearest(True)
    cap = lambda d: min(d, PATH_CAP_M)
    return {"before_m": None if not np.isfinite(before) else round(before, 1),
            "after_m": None if not np.isfinite(after) else round(after, 1),
            "cost_m": round(cap(before) - cap(after), 1), "ally_before": who_b, "ally_after": who_a}


def _per_window() -> dict:
    """Each window's deaths, (slot, t) -> its statue_lost figures, from the shared runs' sums."""
    from app.control import engine

    out = {}
    runs = _captured["runs"]
    for win, plan in _captured["plans"].items():
        view = {key: SimpleNamespace(lost_m2s=r.sums[win]["lost"], by_level=r.sums[win]["by_level"],
                                     went_to=r.sums[win]["went_to"]) for key, r in runs.items() if win in r.sums}
        vplan = {d: (view[o], view[j] if j else None, g) for d, (o, j, g) in plan.items()}
        rows = []
        for slot, t in _captured["deaths"]:
            if (slot, t) in vplan:
                # inf: averaged over the time from the death until the round was decided
                over = win if math.isfinite(win) else max(_captured["t_decided"] - t, 1.0)
                r = engine.statue_lost((slot, t), vplan, 0.0, over)
                r.pop("share_of_team")
            else:
                r = {"control_m2": 0.0, "group": 1, "no_run": True}
            rows.append({"slot": slot, "t": round(t, 3), **r})
        out[str(win)] = {"deaths": rows,
                         "fork_steps": sum(r.sums[win]["steps"] for r in runs.values() if win in r.sums),
                         "runs": sum(1 for r in runs.values() if win in r.sums)}
    return out


def _init(windows, shapley=None, record=None):
    if record:      # no route measure: it's dropped
        _install_record(*record)
        return
    _install_path()
    if shapley:
        SHAPLEY_WINDOW[:] = [shapley]
        _install_shapley(shapley)
    elif windows:
        _install(windows)
    else:       # the real round only: the instant rule, no statues
        import functools
        from app.control import engine
        engine.compute_round = functools.partial(engine.compute_round, death_window_s=0.0)


def work(task: dict) -> dict:
    from app.control.task import compute_task

    _captured.clear()
    started = time.time()
    r = compute_task(task)
    out = {"round": task["key"], "status": r["status"], "error": r.get("error", "")[:1500],
           "seconds": time.time() - started, "peak": r.get("peak")}
    if r["status"] == "ok":
        out["route"] = [{"slot": s, "t": round(t, 3), **v} for (s, t), v in sorted(_captured.get("path", {}).items(),
                                                                                    key=lambda kv: kv[0][1])]
    if r["status"] == "ok" and RECORD:
        out["statue_s"] = _captured.get("timings", {}).get("statues", 0.0)
        out["union_runs"] = len(_captured["subsets"])
        out["union_steps"] = sum(run.steps for run in _captured["subsets"].values())
        out["record"] = _recorded()
    if r["status"] == "ok" and SHAPLEY_WINDOW:
        out["statue_s"] = _captured.get("timings", {}).get("statues", 0.0)
        out["union_runs"] = len(_captured["subsets"])
        out["union_steps"] = 0
        out["shapley"] = _shapley()
    if r["status"] == "ok" and WINDOWS:
        tm = _captured.get("timings", {})
        out["statue_s"] = tm.get("statues", 0.0)
        out["union_runs"] = len(_captured["runs"])
        out["union_steps"] = sum(max(s["steps"] for s in r_.sums.values()) for r_ in _captured["runs"].values())
        out["windows"] = _per_window()
    return out


def main() -> int:
    from preview_control_live import SITE, fetch, link_for, page_data
    from app.control import geometry

    args = sys.argv[1:]
    only = shapley = record = None
    resume = "--resume" in args
    if resume:
        args.remove("--resume")
    if "--record" in args:
        i = args.index("--record")
        record = (float(args[i + 1]), "--masks" in args)
        del args[i:i + 2]
        if "--masks" in args:
            args.remove("--masks")
    if "--shapley" in args:
        i = args.index("--shapley")
        shapley = float(args[i + 1])
        del args[i:i + 2]
    if "--rounds" in args:
        i = args.index("--rounds")
        only = {int(n) for n in args[i + 1].split(",")}
        del args[i:i + 2]
    uuid, out_path, workers = args[0], Path(args[1]), int(args[2])
    windows = [float(w) for w in args[3:]]
    ctx = page_data(uuid)
    rounds = sorted(int(n) for n in ctx["rounds"] if only is None or int(n) in only)
    blobs = {n: fetch(f"{SITE}/replays/{uuid}/{n}.json") for n in rounds}
    # the visibility bitsets once, before the workers (as preview_control_live does)
    geometry.visibility(geometry.load_geometry(ctx["match"]["map"]))
    names = {s: p["name"] for s, p in ctx["players"].items()}
    results: dict[int, dict] = {}
    if resume and out_path.exists():     # keep the rounds already done (a run that was stopped)
        old = json.loads(out_path.read_text(encoding="utf-8"))
        if old.get("record_window") == (record and record[0]) and old.get("windows") == windows:
            results = {int(n): r for n, r in old["results"].items() if r["status"] == "ok"}
            print(f"resuming: {len(results)} rounds already done", flush=True)
    rounds = [n for n in rounds if n not in results]
    tasks = [{"key": n, "map": ctx["match"]["map"], "blob": blobs[n], "link": link_for(ctx, n)} for n in rounds]
    with multiprocessing.Pool(workers, initializer=_init, initargs=(windows, shapley, record)) as pool:
        for r in pool.imap_unordered(work, tasks):
            results[r["round"]] = r
            extra = (f" statues {r['statue_s']:.0f}s runs {r['union_runs']} steps {r['union_steps']}"
                     if "statue_s" in r else "")
            if r.get("route"):
                extra += f" route {sum(x.get('seconds', 0) for x in r['route']):.1f}s"
            print(f"r{r['round']}: {r['status']} {r['seconds']:.0f}s{extra} {r['error']}", flush=True)
            out_path.write_text(json.dumps({"match": uuid, "map": ctx["match"]["map"], "names": names,
                                            "windows": windows, "record_window": record and record[0],
                                            "tick_columns": TICK_COLUMNS, "results": results}), encoding="utf-8")
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
