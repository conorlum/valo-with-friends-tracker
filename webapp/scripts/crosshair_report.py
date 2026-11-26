"""Crosshair placement checks over the stored aim events (read-only; run through with_friends_db.py --read-only).

predict: does placement error predict winning the fight? Logistic fit of won on err/10 plus controls, and a
second model adding log time to on target; cluster bootstrap by match. Prints no names and writes nothing."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

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
    args = parser.parse_args(argv)
    db = _open_session()
    try:
        return cmd_predict(db, args.draws)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
