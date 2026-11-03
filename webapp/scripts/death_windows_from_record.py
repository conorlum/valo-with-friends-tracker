"""Lost control at a death, for any window and split, from one recorded pass (compare_death_windows.py --record):
no engine pass, seconds per match. Reads only the record file.

    .\\.venv\\Scripts\\python.exe scripts\\death_windows_from_record.py check <record.json> <all_windows.json> [<shap_W.json>]
    .\\.venv\\Scripts\\python.exe scripts\\death_windows_from_record.py table <record.json> <out.json> 5 6 7 8

`check` recomputes a stored multi-window result (proportional) and a stored Shapley result from the record and
reports every figure that differs (it should be none). `table` writes, per window and per death:
  prop        today's rule: the group's joint run split in proportion to each member's own run (own: t .. t + W)
  shapley     each member's Shapley share of the group's joint run (every subset from its first death to the
              group's last death + W)
  shap_norm   shapley × W / the group's stretch (last death + W - first death)
  own_window  the joint run's loss over this member's own [t, t + W) only (not split: overlaps count for each)
  solo        the death's own run over [t, t + W) (no group)
  prop_enemy  prop, counting only ground the enemy holds in the real round (went_to enemy)
all in m² averaged over W. The output names real players: keep it local.
"""

from __future__ import annotations

import base64
import json
import math
import sys
import zlib
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import numpy as np

LEVELS = ("active", "safe", "passive")
WENT = ("enemy", "contested", "nobody")


def _unpack(s: str, n_cols: int) -> np.ndarray:
    return np.frombuffer(zlib.decompress(base64.b64decode(s)), dtype=np.float64).reshape(-1, n_cols)


class Round:
    """One round's recording: deaths, which have runs, and each run's ticks keyed by its statues."""

    def __init__(self, rec: dict, n_cols: int, record_window: float):
        self.t_start, self.t_decided = rec["t_start"], rec["t_decided"]
        self.deaths = [tuple(d) for d in rec["deaths"]]
        self.eligible = [(s, t) for s, t, _ in rec["eligible"]]
        self.side = {(s, t): side for s, t, side in rec["eligible"]}
        self.record_window = record_window
        self.runs = {tuple(tuple(d) for d in r["statues"]): (r["t0"], r["t1"], _unpack(r["ticks"], n_cols))
                     for r in rec["runs"]}

    def groups(self, window: float) -> list[tuple]:
        """plan_statues' grouping at `window`: same-side deaths within `window` of the one before."""
        if window > self.record_window:
            raise ValueError(f"recorded to {self.record_window} s, asked for {window} s")
        by_side: dict = {}
        for d in self.eligible:
            by_side.setdefault(self.side[d], []).append(d)
        out = []
        for deaths in by_side.values():
            groups: list[list] = []
            for d in deaths:
                if groups and d[1] - groups[-1][-1][1] <= window:
                    groups[-1].append(d)
                else:
                    groups.append([d])
            out += [tuple(g) for g in groups]
        return out

    def run(self, statues: tuple, horizon: float, t0: float | None = None) -> SimpleNamespace:
        """The run with these statues, summed as StatueRun.step would to `horizon` (from `t0`: its first death,
        unless given), in tick order so the sums are the engine's to the bit."""
        r_t0, r_t1, ticks = self.runs[statues]
        if horizon > r_t1 + 1e-9:
            raise ValueError(f"run {statues} recorded to {r_t1}, asked to {horizon}")
        t0 = r_t0 if t0 is None else t0
        lost, lv, went = 0.0, dict.fromkeys(LEVELS, 0.0), dict.fromkeys(WENT, 0.0)
        for row in ticks:
            t, w = float(row[0]), float(row[1])
            span = min(t + w, horizon, self.t_decided) - max(t, t0, self.t_start)
            if span <= 0:
                continue
            lost += float(row[2]) * span
            for k, v in zip(LEVELS, row[3:6]):
                lv[k] += float(v) * span
            for k, v in zip(WENT, row[6:9]):
                went[k] += float(v) * span
        return SimpleNamespace(lost_m2s=lost, by_level=lv, went_to=went)


def proportional(rnd: Round, window: float) -> list[dict]:
    """Today's engine rule at `window` (statue_lost), each death's row as compare_death_windows.py writes it."""
    from app.control import engine

    plan = {}
    for group in rnd.groups(window):
        joint = rnd.run(group, group[-1][1] + window) if len(group) > 1 else None
        for d in group:
            plan[d] = (rnd.run((d,), d[1] + window), joint, list(group))
    rows = []
    for slot, t in rnd.deaths:
        if (slot, t) in plan:
            r = engine.statue_lost((slot, t), plan, 0.0, window)
            r.pop("share_of_team")
        else:
            r = {"control_m2": 0.0, "group": 1, "no_run": True}
        rows.append({"slot": slot, "t": round(t, 3), **r})
    return rows


def shapley(rnd: Round, window: float) -> list[dict]:
    """Shapley groups at `window`, each death's row as compare_death_windows.py --shapley writes it."""
    rows = {}
    for group in rnd.groups(window):
        n, t1 = len(group), group[-1][1] + window
        v = {(): 0.0}
        for k in range(1, n + 1):
            for sub in combinations(group, k):
                v[sub] = rnd.run(sub, t1).lost_m2s
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
                       "runs": 2 ** n - 1, "_phi": phi, "_stretch": t1 - group[0][1]}
    out = []
    for slot, t in rnd.deaths:
        r = rows.get((slot, t), {"shapley_m2": 0.0, "prop_m2": 0.0, "solo_m2": 0.0, "joint_m2": 0.0, "group": 1,
                                 "runs": 0, "no_run": True})
        out.append({"slot": slot, "t": round(t, 3), **r})
    return out


def variants(rnd: Round, window: float) -> list[dict]:
    """Every split and stretch rule at `window`, one row per death (m² over the window)."""
    prop = {(r["slot"], r["t"]): r for r in proportional(rnd, window)}
    shap = {(r["slot"], r["t"]): r for r in shapley(rnd, window)}
    group_of = {d: g for g in rnd.groups(window) for d in g}
    rows = []
    for slot, t in rnd.deaths:
        key = (slot, round(t, 3))
        g = group_of.get((slot, t))
        row = {"slot": slot, "t": round(t, 3), "group": len(g) if g else 1, "prop": prop[key]["control_m2"],
               "prop_enemy": prop[key].get("went_to_m2", {}).get("enemy", 0.0)}
        if g is None:
            row.update(shapley=0.0, shap_norm=0.0, own_window=0.0, solo=0.0, no_run=True)
        else:
            s = shap[key]
            row.update(shapley=s["shapley_m2"], shap_norm=round(s["_phi"] / s["_stretch"], 1),
                       own_window=round(rnd.run(g, t + window, t0=t).lost_m2s / window, 1),
                       solo=round(rnd.run(((slot, t),), t + window).lost_m2s / window, 1),
                       stretch=round(s["_stretch"], 3))
        rows.append(row)
    return rows


def load(path: Path) -> tuple[dict, dict[int, Round]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    n_cols, w = len(data["tick_columns"]), data["record_window"]
    return data, {int(n): Round(r["record"], n_cols, w) for n, r in data["results"].items() if r["status"] == "ok"}


def _diff(a, b, where: str, out: list) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k.startswith("_"):
                continue
            _diff(a.get(k), b.get(k), f"{where}.{k}", out)
    elif a != b:
        out.append(f"{where}: stored {a!r}, from record {b!r}")


def check(record: Path, windows_path: Path, shap_path: Path | None) -> int:
    _, rounds = load(record)
    diffs, compared = [], 0
    stored = json.loads(windows_path.read_text(encoding="utf-8"))["results"]
    for n, rnd in sorted(rounds.items()):
        res = stored.get(str(n))
        if not res or res["status"] != "ok":
            continue
        for win, block in res["windows"].items():
            ours = proportional(rnd, float(win))
            for a, b in zip(block["deaths"], ours, strict=True):
                _diff(a, b, f"r{n} {win} s slot {a['slot']} t {a['t']}", diffs)
                compared += 1
    if shap_path is not None:
        sdata = json.loads(shap_path.read_text(encoding="utf-8"))
        res_all = sdata["results"]
        for n, rnd in sorted(rounds.items()):
            res = res_all.get(str(n))
            if not res or res["status"] != "ok":
                continue
            win = _shapley_window(res)
            for a, b in zip(res["shapley"], shapley(rnd, win), strict=True):
                _diff(a, b, f"r{n} Shapley {win} s slot {a['slot']} t {a['t']}", diffs)
                compared += 1
    print(f"{compared} death rows compared over rounds {sorted(rounds)}; {len(diffs)} differences")
    for d in diffs[:60]:
        print("  " + d)
    return 1 if diffs else 0


def _shapley_window(res: dict) -> float:
    return float(res.get("shapley_window", 8.0))     # the stored Shapley run was at 8 s and didn't say so


def table(record: Path, out: Path, windows: list[float]) -> int:
    data, rounds = load(record)
    result = {"match": data.get("match"), "names": data["names"], "windows": windows,
              "rounds": {str(n): {str(w): variants(rnd, w) for w in windows} for n, rnd in sorted(rounds.items())}}
    out.write_text(json.dumps(result), encoding="utf-8")
    print("wrote", out)
    return 0


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    args = sys.argv[1:]
    if args[:1] == ["check"]:
        return check(Path(args[1]), Path(args[2]), Path(args[3]) if len(args) > 3 else None)
    if args[:1] == ["table"]:
        return table(Path(args[1]), Path(args[2]), [float(w) for w in args[3:]])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
