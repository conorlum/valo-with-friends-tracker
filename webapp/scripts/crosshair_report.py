"""Crosshair placement checks over the stored aim events (read-only; run through with_friends_db.py --read-only).

predict: does placement error predict winning the fight? Logistic fit of won on err/10 plus controls, and a
second model adding log time to on target; cluster bootstrap by match. Prints no names and writes nothing.

events: fetch live public round blobs (no database), compute the aim events with this checkout's code and print a
sample, the net-turn and distance deciles and the counts per band. Slots only; stdout only."""

from __future__ import annotations

import argparse
import math
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.moments.peeks import distance_band  # noqa: E402
from app.services import aim_stats, stats_math  # noqa: E402

MIN_ROWS = 50
OPENERS = ("holder", "both", "unclear")  # baseline: peeker
BANDS = ("mid", "long")  # baseline: close
L2 = 0.01
MIN_TTO_S = 0.05


class TooFew(Exception):
    pass


def rows_from_events(events) -> list:
    """ok events with a won/lost outcome as flat rows. `tto` is the time to on target at the active rule's
    ladder value (None = censored); `window` is the censored length."""
    rows = []
    for e in events:
        if e.cls != "ok" or e.outcome not in ("won", "lost") or e.err is None or e.d_m is None:
            continue
        rows.append({
            "match_id": e.match_id, "err": float(e.err), "d_m": float(e.d_m), "hold": bool(e.hold),
            "opener": e.opener or "unclear", "gun_class": e.gun_class or "unknown", "hp": e.hp,
            "enemy_hp": e.enemy_hp, "won": 1 if e.outcome == "won" else 0,
            "tto": aim_stats._on_time(e, aim_stats.ON_RULE), "window": float(e.t_end) - float(e.t),
        })
    return rows


def _encoder(rows):
    guns = sorted({r["gun_class"] for r in rows})[1:]  # first sorted level is the baseline
    med = {}
    for k in ("hp", "enemy_hp"):
        vals = [r[k] for r in rows if r[k] is not None]
        med[k] = float(np.median(vals)) if vals else 0.0

    def design(rs, with_tto):
        X = []
        for r in rs:
            band = distance_band(r["d_m"])
            row = [r["err"] / 10.0]
            row += [1.0 if band == b else 0.0 for b in BANDS]
            row.append(1.0 if r["hold"] else 0.0)
            row += [1.0 if r["opener"] == o else 0.0 for o in OPENERS]
            row += [1.0 if r["gun_class"] == g else 0.0 for g in guns]
            for k in ("hp", "enemy_hp"):
                missing = r[k] is None
                row += [(med[k] if missing else r[k]) / 100.0, 1.0 if missing else 0.0]
            if with_tto:
                censored = r["tto"] is None
                t = r["window"] if censored else r["tto"]
                row += [math.log(max(float(t), MIN_TTO_S)), 1.0 if censored else 0.0]
            X.append(row)
        return np.array(X, dtype=float)

    return design


def _coef(rs, design, with_tto, col):
    X = design(rs, with_tto)
    y = np.array([r["won"] for r in rs], dtype=float)
    return float(stats_math.fit_logistic(X, y, l2=L2)[1 + col])


def fit_effect(rows, draws: int = 1000, seed: int = 0) -> dict:
    """Odds ratio of winning per 10 degrees of error (model 1), and per unit of log time to on target (model 2),
    each with a 95% cluster-bootstrap interval by match."""
    rows = list(rows)
    if len(rows) < MIN_ROWS:
        raise TooFew(f"fewer than {MIN_ROWS} usable events ({len(rows)}); nothing to fit")
    design = _encoder(rows)
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["match_id"], []).append(r)
    tto_col = design(rows[:1], True).shape[1] - 2

    def flat(parts):
        return [r for part in parts for r in part]

    out = {"n": len(rows), "n_matches": len(groups)}
    for name, with_tto, col in (("err", False, 0), ("tto", True, tto_col)):
        est = _coef(rows, design, with_tto, col)
        ci = stats_math.cluster_bootstrap_ci(
            lambda parts, w=with_tto, c=col: _coef(flat(parts), design, w, c), groups, draws=draws, seed=seed)
        out[name] = {"log_or": est, "or": math.exp(est), "ci": ci, "or_ci": (math.exp(ci[0]), math.exp(ci[1]))}
    return out


SITE = "https://valowithfriendstracker.onrender.com"
RULES = ("body", "five", "fixed5")
CLASS_ORDER = ("ok", "turning", "behind", "impaired", "in_view_at_start")


def times_to_on_target(ev: dict) -> dict:
    """Seconds from the event to the first on-target sample under each rule (None = not reached in the window)."""
    probe = SimpleNamespace(d_m=ev["d_m"], on_times=ev.get("on"))
    return {rule: aim_stats._on_time(probe, rule) if ev.get("d_m") is not None else None for rule in RULES}


def _spread(items: list) -> list:
    """The items in an order that samples them evenly (a golden-ratio stride over their time order)."""
    ordered = sorted(items, key=lambda e: (e["uuid"], e["round"], e["t"], e.get("i", 0), e["opponent"]))
    keyed = sorted(((k * 0.6180339887 % 1.0, k) for k in range(len(ordered))))
    return [ordered[k] for _, k in keyed]


def select_events(events, n: int = 20) -> list:
    """n events spread across classes and replays: round-robin over classes, and inside a class over replays."""
    by_class: dict = {}
    for e in events:
        by_class.setdefault(e["cls"], []).append(e)
    queues = []
    for cls in sorted(by_class, key=lambda c: (CLASS_ORDER.index(c) if c in CLASS_ORDER else len(CLASS_ORDER), c)):
        by_uuid: dict = {}
        for e in _spread(by_class[cls]):
            by_uuid.setdefault(e["uuid"], []).append(e)
        lists = [by_uuid[u] for u in sorted(by_uuid)]
        queue = []
        while any(lists):
            for lst in lists:
                if lst:
                    queue.append(lst.pop(0))
        queues.append(queue)
    out = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


def _f(v, nd=2):
    return "-" if v is None else f"{v:.{nd}f}"


HEADER = "uuid     rnd    time slot enemy    err   dist class            opener    body  five fixed5  shot   dmg  link"


def format_row(ev: dict) -> str:
    t = times_to_on_target(ev)
    link = f"/replays/{ev['uuid']}?round={ev['round']}&t={int(ev['t'])}"
    return (f"{ev['uuid'][:8]} {ev['round']:>3} {ev['t']:>7.2f} {ev['slot']:>4} {ev['opponent']:>5} "
            f"{_f(ev['err']):>6} {_f(ev['d_m'], 1):>6} {ev['cls']:<16} {str(ev.get('opener')):<8} "
            f"{_f(t['body']):>5} {_f(t['five']):>5} {_f(t['fixed5']):>6} {_f(ev.get('shot')):>5} "
            f"{_f(ev.get('dmg')):>5}  {link}")


def deciles(values) -> list:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return []
    return [float(x) for x in np.percentile(vals, list(range(10, 100, 10)))]


def band_counts(events) -> dict:
    out = {b: 0 for b in aim_stats.BANDS}
    for e in events:
        if e.get("d_m") is not None:
            out[distance_band(e["d_m"])] += 1
    return out


def summary_lines(events) -> str:
    events = list(events)
    counts: dict = {}
    for e in events:
        counts[e["cls"]] = counts.get(e["cls"], 0) + 1
    errs = [e["err"] for e in events if e.get("err") is not None]
    ok_near = [e for e in events if e["cls"] == "ok" and e.get("d_m") is not None and e["d_m"] < 10]
    quick = [e for e in ok_near
             if times_to_on_target(e)["body"] is not None and times_to_on_target(e)["body"] <= 1.0]
    return "\n".join([
        "counts by class: " + ", ".join(f"{c} {counts[c]}" for c in sorted(counts)),
        f"err range: {_f(min(errs)) if errs else '-'} to {_f(max(errs)) if errs else '-'} deg",
        f"ok events under 10 m on target within 1 s (body rule): {len(quick)} of {len(ok_near)}"])


def build_report(events, n: int = 20, fine: bool = False) -> str:
    events = list(events)
    out = [f"crosshair events: {len(events)} events from {len({e['uuid'] for e in events})} replays; "
           f"fine: {'true' if fine else 'false'}", summary_lines(events), "",
           f"(a) {min(n, len(events))} events spread across classes and replays", HEADER]
    out += [format_row(e) for e in select_events(events, n)]
    turn = deciles([e.get("turn") for e in events])
    out += ["", "(b) net signed yaw turn in the 250 ms before each event, deciles 10..90 (deg)",
            "    " + (", ".join(f"{x:.1f}" for x in turn) if turn else "none")]
    dist = deciles([e.get("d_m") for e in events])
    out += ["", "(c) distance deciles 10..90 (m)", "    " + (", ".join(f"{x:.1f}" for x in dist) if dist else "none"),
            "    counts per band: " + ", ".join(f"{b} {c}" for b, c in band_counts(events).items())]
    return "\n".join(out)


def _decode(raw: bytes) -> dict:
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def _events_for_replay(uuid: str, rounds, live) -> list:
    """The aim events of a replay's rounds (the first `rounds` stored ones, all when None), each with the replay
    uuid, round, and the signed net turn in the 250 ms before it."""
    from app.control import engine
    from app.control import task as ctask
    from app.control.feature_runtime import prepare_geometry
    from app.moments import aim, fights
    from app.moments.constants import AIM_TURN_WINDOW_S

    ctx = live.page_data(uuid)
    geo = prepare_geometry(ctask._load(ctx["match"]["map"]))
    out = []
    numbers = sorted(int(k) for k in ctx["rounds"])
    if rounds:
        numbers = numbers[:rounds]
    for n in numbers:
        blob = _decode(live.fetch(f"{live.SITE}/replays/{uuid}/{n}.json"))
        link = live.link_for(ctx, n)
        elink = engine.ControlLink(sides={int(s): side for s, side in link["sides"].items()},
                                   db_deaths=tuple((int(s), float(t)) for s, t in link["db_deaths"]))
        view = engine.control_view(blob)
        rnd = engine.RoundInputs(blob, geo, elink)
        made = fights.round_moments(rnd, view)
        if not isinstance(made.get("aim"), list):
            print(f"{uuid[:8]} round {n}: no aim events", file=sys.stderr)
            continue
        fine = aim.fine_yaw(view)
        for ev in made["aim"]:
            before = aim.yaw_at(rnd, fine, ev["slot"], ev["t"] - AIM_TURN_WINDOW_S)
            now = aim.yaw_at(rnd, fine, ev["slot"], ev["t"])
            turn = None if before is None or now is None else aim._arc(now[0], before[0])
            out.append({**ev, "uuid": uuid, "round": n, "turn": turn})
    return out


def cmd_events(uuids, rounds, n) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import preview_control_live as live

    events = []
    for u in uuids:
        events += _events_for_replay(u, rounds, live)
    if not events:
        print("no events", file=sys.stderr)
        return 2
    print(build_report(events, n, fine=all(bool(e.get("fine")) for e in events)))
    return 0


def load_rows(db) -> list:
    return rows_from_events(aim_stats.events_for(db, None))


def _open_session():
    from app.db import SessionLocal

    return SessionLocal()


def cmd_predict(db, draws: int) -> int:
    rows = load_rows(db)
    try:
        res = fit_effect(rows, draws=draws)
    except TooFew as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"{res['n']} events, {res['n_matches']} matches")
    for key, label in (("err", "per 10 deg of error"), ("tto", "per unit of log time to on target")):
        r = res[key]
        lo, hi = r["or_ci"]
        print(f"odds ratio of winning {label}: {r['or']:.3f} (95% interval {lo:.3f} to {hi:.3f}, "
              f"n {res['n']} events, {res['n_matches']} matches)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    predict = sub.add_parser("predict")
    predict.add_argument("--draws", type=int, default=1000)
    events = sub.add_parser("events")
    events.add_argument("uuids", nargs="+")
    events.add_argument("--rounds", type=int, default=None)
    events.add_argument("--n", type=int, default=20)
    args = parser.parse_args(argv)
    if args.cmd == "events":
        return cmd_events(args.uuids, args.rounds, args.n)
    db = _open_session()
    try:
        return cmd_predict(db, args.draws)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
