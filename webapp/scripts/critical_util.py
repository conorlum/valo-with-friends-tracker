"""Critical Util's corpus products (docs/superpowers/specs/2026-10-21-critical-util-design.md).

    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\critical_util.py fit-win
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb scripts\\critical_util.py refresh
    .\\.venv313\\Scripts\\python.exe scripts\\with_friends_db.py --expect-database valowithfriendsdb --read-only scripts\\critical_util.py measure

`fit-win` fits the round-win estimator on every match without a replay and stores it (util_model "win"), printing
its held-out check and calibration. `refresh` rebuilds every cast's value and the fitted job weights (util_model
"values"); the site also does this on its own when the stored evidence changes. `measure` prints the spec's
"Measurements to make before code" from the stored evidence and writes nothing. `show` prints the top and bottom
lineups across every friend. `fit-win` and `refresh` write, and refuse in demo mode.

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
    print(f"6. heals: {len(heals)}, kept someone alive: {sum(1 for h in heals if h.get('saved'))}, "
          f"before a fight: {sum(1 for h in heals if h.get('fought'))}; unused: {dict(unused)}")

    # 7. casts per area
    areas = Counter()
    for rid, n, d in rounds:
        if rid not in ctx:
            continue
        for c in d.get("casts") or ():
            areas[replay_util.area_of(ctx[rid]["map"], c.get("u"), c.get("v"), c.get("side"))] += 1
    print(f"7. casts per area: {dict(areas.most_common())}")

    # 2. overlap: casts in an estimator cell with a real comparison set
    if model:
        thin = 0
        cells = model.get("cells") or {}
        total = 0
        for _, _, d in rounds:
            for c in d.get("casts") or ():
                a, dd = c.get("alive") or (0, 0)
                planted = d.get("plant") is not None and c["t"] >= d["plant"]
                key = util_model.keys(a, dd, planted, util_model.time_bucket(c["t"], planted, d.get("plant")))[0]
                total += 1
                thin += (cells.get(key) or (0, 0))[1] < 30
        print(f"2. casts whose full estimator cell has under 30 observations: {thin} of {total}")
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
            print(f"  {c['score']:+6.2f} pp  {names.get(c['player_id'], '?'):24s} {c['agent']} {c['ability']} "
                  f"{c['map']} {c['side']} {c['area']}  n {c['n']} ({c['matches']} matches)  {c['call']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fit-win")
    sub.add_parser("refresh")
    sub.add_parser("measure")
    show = sub.add_parser("show")
    show.add_argument("--limit", type=int, default=15)
    args = parser.parse_args(argv)
    if args.cmd in ("fit-win", "refresh") and settings.demo_mode:
        print(REFUSED, file=sys.stderr)
        return 2
    from app.db import SessionLocal

    db = SessionLocal()
    try:
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
