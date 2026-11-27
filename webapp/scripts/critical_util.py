"""Critical Util's corpus products (docs/superpowers/specs/2026-10-21-critical-util-design.md).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\critical_util.py fit-win
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\critical_util.py refresh
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\critical_util.py measure

`fit-win` fits the round-win estimator on every match without a replay and stores it (util_model "win"), printing
its held-out check and calibration. `refresh` rebuilds every cast's value and the fitted job weights (util_model
"values"); the site also does this on its own when the stored evidence changes. `measure` prints the spec's
"Measurements to make before code" from the stored evidence and writes nothing. `show` prints the top and bottom
lineups across every friend. `ults` prints the stored build's ult report (ranking, orb chart, held ults, orb risk) from
the stored build only; it never builds or writes, and refuses in demo mode. `fit-win` and `refresh` write, and refuse
in demo mode.

Exits 0 when done, 2 when refused.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

WEBAPP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEBAPP_ROOT))

from app.config import settings  # noqa: E402

REFUSED = "Refused: demo mode is on, and this command writes Critical Util's corpus products."


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))], 2)


def cmd_fit_win(db) -> int:
    from app.services import util_model

    model = util_model.refresh(db)
    print(f"estimator: {model['n']} state entries from {model['matches']} matches without a replay")
    print(f"held-out: {model.get('check')}")
    for row in model.get("calibration") or []:
        print(f"  predicted {row['predicted']:.3f}  actual {row['actual']:.3f}  n {row['n']}")
    return 0


def cmd_refresh(db) -> int:
    from app.services import replay_util

    data = replay_util.ensure_values(db, force=True)
    fit = data["fit"]
    print(f"{len(data['rows'])} valued casts and heals; weights fitted: {fit['fitted']} on {fit['rounds']} side-rounds")
    print(f"held-out check: {fit.get('check')}")
    for k, v in fit["weights"].items():
        print(f"  {k:16s} {v:.5f}")
    print(f"lineup score spread: {100 * data['spread']:.2f} pp")
    return 0


_SHORT_L = (1, 2, 3, 5, 10, 15, 20, 30, 45)


def _gate_cell(g: dict) -> str:
    return (f"{'pass' if g['pass'] else 'FAIL'} {g['n']} {g['diff_mean']:+.4f}±{g['diff_sd']:.4f} "
            f"({g['matches']})")


def gate_lines(data: dict) -> list[str]:
    """The stored build's gate tables and same-type overlap as text lines (pure: no DB)."""
    gate = data.get("gate")
    if not gate:
        return []
    lines = ["gate per length (mode all / yours / device):"]
    for L in range(1, 46):
        lines.append(f"  {L:2d}  all: {_gate_cell(gate['all'][L - 1])}  yours: {_gate_cell(gate['yours'][L - 1])}  "
                     f"device: {_gate_cell(gate['device'][L - 1])}")
    for mode in ("all", "yours"):
        for typ, g in sorted((gate.get("by_type") or {}).get(mode, {}).items()):
            lines.append(f"gate, mode {mode}, type {typ}:")
            lines.extend(f"  {typ} {mode} {L:2d}  {_gate_cell(g[L - 1])}" for L in _SHORT_L)
    lines.append("same-type overlap (share of comparison moments with a same-type cast in [t, t+L]):")
    for typ, arr in sorted((data.get("same_type_overlap") or {}).items()):
        n = arr[0]
        cells = " ".join(f"{L}:{100 * arr[L] / n:.1f}%" if n else f"{L}:-" for L in _SHORT_L)
        lines.append(f"  {typ:12s} n {n:g}  {cells}")
    return lines


def cmd_measure(db) -> int:
    """The spec's measurements, from the stored evidence only."""
    from app.services import replay_util, util_model

    stored = replay_util._stored_rounds(db)
    ctx = replay_util._replay_context(db, set(stored))
    model = util_model.load(db)
    rounds = [(rid, n, d) for rid, rs in stored.items() for n, d in rs.items()]
    print(f"rounds with evidence: {len(rounds)} from {len(stored)} replays; estimator: {'yes' if model else 'NO'}")

    # 3. grace: how long after an effect ended did kills on affected enemies come
    after = []
    for _, _, d in rounds:
        for c in d.get("casts") or ():
            for e in c.get("effects") or ():
                if e.get("how") in ("damage", "reveal"):
                    continue
                for k in d.get("kills") or ():
                    if k["victim"] == e["target"] and k.get("k_side") == c.get("side") and e["t0"] <= k["t"] <= e["t1"] + 5:
                        after.append(k["t"] - e["t1"])
    print(f"3. kills on affected enemies, seconds after the effect ended (n {len(after)}): "
          f"p25 {_pct(after, .25)} p50 {_pct(after, .5)} p75 {_pct(after, .75)} p90 {_pct(after, .9)}")

    # 4. combo window: gaps between consecutive teammate casts on the same side
    gaps = []
    for _, _, d in rounds:
        for side in ("attack", "defense"):
            ts = sorted(c["t"] for c in d.get("casts") or () if c.get("side") == side)
            gaps += [b - a for a, b in zip(ts, ts[1:])]
    print(f"4. gaps between a side's consecutive casts (n {len(gaps)}): p25 {_pct(gaps, .25)} p50 {_pct(gaps, .5)} "
          f"share under 3 s {round(100 * sum(g < 3 for g in gaps) / max(len(gaps), 1), 1)}%")

    # 5. samples per friend x ability and lineup
    data = replay_util.build_corpus(db) if model else None      # never stored: measure is read-only
    if data:
        per = Counter((r["player_id"], r["ability"]) for r in data["rows"] if r["kind"] != "heal")
        lineups = defaultdict(set)
        counts = Counter()
        for r in data["rows"]:
            if r["kind"] != "heal":
                key = (r["player_id"], r["ability"], r["map"], r["side"], r["area"])
                counts[key] += 1
                lineups[key].add(r["match"])
        print(f"5. player x ability pairs: {len(per)}, with 5+ casts: {sum(v >= 5 for v in per.values())}; "
              f"lineups: {len(counts)}, with 5+ casts: {sum(v >= 5 for v in counts.values())}, "
              f"of those from 3+ matches: {sum(1 for k, v in counts.items() if v >= 5 and len(lineups[k]) >= 3)}")

    # 6. heals and unused util
    heals = [h for _, _, d in rounds for h in d.get("heals") or ()]
    unused = Counter(u["kind"] for _, _, d in rounds for u in d.get("unused") or ())
    unused["ult_unused"] = sum(len(v) for rs in stored.values() for v in replay_util.unused_ults(rs).values())
    print(f"6. heals: {len(heals)}, kept someone alive: {sum(1 for h in heals if h.get('saved'))}, "
          f"before a fight: {sum(1 for h in heals if h.get('fought'))}; unused: {dict(unused)}")

    # 7. casts per area
    areas = Counter()
    for rid, n, d in rounds:
        if rid not in ctx:
            continue
        for c in d.get("casts") or ():
            areas[replay_util.area_of(ctx[rid].get("asset_key", ctx[rid]["map"]), c.get("u"), c.get("v"),
                                      c.get("side"))] += 1
    print(f"7. casts per area: {dict(areas.most_common())}")

    # 2. overlap: per ability, the share of casts with a no-util comparison set at the full level, a fallback, none
    if data:
        print("2. baseline overlap per ability (casts: full level / fallback / none):")
        for ability, o in sorted(data["overlap"].items(), key=lambda kv: -kv[1]["casts"]):
            print(f"   {ability:24s} {o['casts']:4d}: {o['full']} / {o['fallback']} / {o['none']}")
    if data:
        for line in gate_lines(data):
            print(line)
    kinds = Counter(k for _, _, d in rounds for c in d.get("casts") or () for k in c.get("kinds") or ())
    print(f"cast kinds: {dict(kinds.most_common())}")
    return 0


def cmd_show(db, limit: int) -> int:
    from app.models.player import Player
    from app.services import replay_util

    data = replay_util.ensure_values(db)
    ids = {r["player_id"] for r in data["rows"] if r.get("player_id") is not None}
    page = replay_util.page_for(data, ids)
    names = {p.id: p.display_name for p in db.query(Player.id, Player.display_name).filter(Player.id.in_(list(ids)))}
    for title, rows in (("top", page["lineups"][:limit]), ("bottom", page["lineups"][-limit:])):
        print(title)
        for c in rows:
            score = "     –" if c["score"] is None else f"{c['score']:+6.2f}"
            print(f"  {score} pp  {names.get(c['player_id'], '?'):24s} {c['agent']} {c['ability']} "
                  f"{c['map']} {c['side']} {c['area']}  n {c['n']} ({c['matches']} matches)  {c['call']}")
    return 0


ULTS_REFUSED = "Refused: demo mode is on, and the demo has no Critical Util build to read."
ULTS_MISSING = "The stored Critical Util build has no ult figures: the build hasn't run yet."


def _pp(x) -> str:
    """A fraction of round-win chance as signed percentage points."""
    return "—" if x is None else f"{100 * x:+.1f} pp"


def _pp_already(x) -> str:
    """A figure already in percentage points, signed."""
    return "—" if x is None else f"{x:+.1f} pp"


def _ci(lo, hi, scale=_pp) -> str:
    return "—" if lo is None or hi is None else f"[{scale(lo)}, {scale(hi)}]"


def ult_lines(block: dict) -> list[str]:
    """The stored build's ult block as text lines (pure: no DB). Agent rows only: no player ids or names."""
    lines = ["Ranking (mean lift in round-win chance per ult charge; difference is from the average of agents with 5+ uses)"]
    ranking = block.get("ranking") or {}
    lines.append(f"  {'agent':12s} {'uses':>5s} {'charges':>8s} {'wasted':>7s} {'mean lift':>10s} {'95% CI':>22s} {'vs avg':>10s}")
    for r in ranking.get("rows") or ():
        lines.append(f"  {str(r['agent']):12s} {r['n_uses']:5d} {r['n_charges']:8d} {r['wasted']:7d} "
                     f"{_pp(r.get('value')):>10s} {_ci(r.get('lo'), r.get('hi')):>22s} {_pp(r.get('diff')):>10s}")
    lines.append(f"  average over agents with 5+ uses: {_pp(ranking.get('cast_mean'))}")

    orbs = block.get("orbs") or {}
    lines += ["", "Orb chart (round-win change from one more orb point at that many held, over the rest of the match)"]
    if not orbs.get("rows"):
        lines.append("  no agent has 5+ uses with a proven cost")
    else:
        lines.append("  " + " " * 21 + " ".join(f"{p:>9d}" for p in range(orbs.get("max_cost", 0))) + "   (points held)")
        for r in orbs["rows"]:
            cells = " ".join(f"{_pp(c):>9s}" for c in r["cells"])
            lines.append(f"  {str(r['agent']):12s} cost {r['cost']:2d} {cells}{'   (CI spans 0)' if r.get('muted') else ''}")

    held = block.get("held") or {}
    lines += ["", f"Held ults (round-win change per extra ready ult at the round's start; {held.get('rounds', 0)} rounds, "
                  f"{held.get('matches', 0)} matches, pistols out)",
              "  Used and unused is a split of the same ready ults. It is not cause and effect: a team that uses its ult may "
              "already be winning."]
    for key, label in (("any", "any ready ult"), ("used", "used that round"), ("unused", "held and not used")):
        h = held.get(key) or {}
        lines.append(f"  {label:20s} {_pp_already(h.get('pp')):>10s} {_ci(h.get('lo'), h.get('hi'), _pp_already):>22s}")

    risk = block.get("risk") or {}
    lines += ["", "Orb risk (cost in round-win chance of a death during or within 3 s of picking up or letting go of an orb, per try)",
              f"  {'map':14s} {'tries':>6s} {'deaths':>7s} {'cost per try':>13s} {'95% CI':>22s}"]
    rows = [("all maps", risk.get("overall") or {})] + [(str(m.get("map")), m) for m in risk.get("maps") or ()]
    for name, m in rows:
        lines.append(f"  {name:14s} {m.get('tries', 0):6d} {m.get('deaths', 0):7d} {_pp_already(m.get('cost_pp')):>13s} "
                     f"{_ci(m.get('lo'), m.get('hi'), _pp_already):>22s}")
    return lines


def cmd_ults(db) -> int:
    """Print the stored build's ult block. Reads only; never builds or writes."""
    from app.services import replay_util

    row = replay_util._values_row(db)
    block = (row[1] or {}).get("ults") if row else None
    if not block:
        print(ULTS_MISSING, file=sys.stderr)
        return 2
    for line in ult_lines(block):
        print(line)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fit-win")
    sub.add_parser("refresh")
    sub.add_parser("measure")
    sub.add_parser("ults")
    show = sub.add_parser("show")
    show.add_argument("--limit", type=int, default=15)
    args = parser.parse_args(argv)
    if args.cmd in ("fit-win", "refresh") and settings.demo_mode:
        print(REFUSED, file=sys.stderr)
        return 2
    if args.cmd == "ults" and settings.demo_mode:
        print(ULTS_REFUSED, file=sys.stderr)
        return 2
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        if args.cmd == "ults":
            return cmd_ults(db)
        if args.cmd == "fit-win":
            return cmd_fit_win(db)
        if args.cmd == "refresh":
            return cmd_refresh(db)
        if args.cmd == "measure":
            return cmd_measure(db)
        return cmd_show(db, args.limit)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
