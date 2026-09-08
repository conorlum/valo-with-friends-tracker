"""Follow-up to investigate_preplant_dip_1s_buckets.py: does the attacker
dip at dt~2-5s (found via BOTH direct standardization and the joint
dt-bucket x side regression) survive a match-based 50/50 split? The
original 2-knot spline's sensitivity checks (regularization, match-split)
were run against the SUPERSEDED finding (a dip at dt~20 that turned out to
be a shape/amplitude reconstruction artifact) -- this checks the actual
finding instead.

Run from webapp/:
    .\\.venv\\Scripts\\python.exe ..\\docs\\superpowers\\diagnostics\\investigate_preplant_dip_match_split_check.py
"""
import collections
import os
import sys

sys.path.insert(0, os.path.abspath("."))

import numpy as np

from app.db import SessionLocal
from app.scoring.preplant_time_model import extract_preplant_observations
from app.services.stats_math import back_transform, fit_logistic, standardize

BUCKET_REF = "REF"


def bucket_label(dt: float) -> str:
    if dt == 0.0:
        return "b0_exact"
    if dt > 30.0:
        return BUCKET_REF
    return f"b{int(np.ceil(dt))}"


def fit_dt_bucket_regression(usable):
    states = sorted({o.exact_state for o in usable})
    ref_state = "5v5" if "5v5" in states else states[0]
    other_states = [s for s in states if s != ref_state]
    dt_buckets_ordered = [f"b{k}" for k in range(1, 31)]

    rows_X, labels_y = [], []
    for o in usable:
        atk = 1.0 if o.is_attacker else 0.0
        b = bucket_label(o.dt)
        b = BUCKET_REF if b == "b0_exact" else b
        row = [1.0 if o.exact_state == s else 0.0 for s in other_states]
        row += [1.0 if b == bl else 0.0 for bl in dt_buckets_ordered]
        row += [atk]
        row += [(1.0 if b == bl else 0.0) * atk for bl in dt_buckets_ordered]
        rows_X.append(row)
        labels_y.append(1.0 if o.round_won_by_killer_team else 0.0)
    X = np.array(rows_X, dtype=float)
    y = np.array(labels_y, dtype=float)
    scaled, _, centre, scale = standardize(X, X)
    beta = back_transform(fit_logistic(scaled, y, l2=1.0), centre, scale)

    n_state = len(other_states)
    idx_bucket_def = 1 + n_state
    idx_atk_main = idx_bucket_def + 30
    idx_bucket_atk = idx_atk_main + 1
    def_coef = {bl: float(beta[idx_bucket_def + i]) for i, bl in enumerate(dt_buckets_ordered)}
    atk_main = float(beta[idx_atk_main])
    atk_coef = {bl: def_coef[bl] + atk_main + float(beta[idx_bucket_atk + i])
                for i, bl in enumerate(dt_buckets_ordered)}
    return atk_coef, def_coef


def main():
    db = SessionLocal()
    all_obs = extract_preplant_observations(db)
    usable = [o for o in all_obs if o.round_won_by_killer_team is not None]

    match_ids = sorted({o.match_id for o in usable})
    rng = np.random.default_rng(1)  # SAME seed as the main script's 6b split
    shuffled = match_ids.copy()
    rng.shuffle(shuffled)
    half = len(shuffled) // 2
    half_a_ids, half_b_ids = set(shuffled[:half]), set(shuffled[half:])
    half_a = [o for o in usable if o.match_id in half_a_ids]
    half_b = [o for o in usable if o.match_id in half_b_ids]
    print(f"half A: {len(half_a):,} obs, {len(half_a_ids):,} matches")
    print(f"half B: {len(half_b):,} obs, {len(half_b_ids):,} matches")

    atk_a, def_a = fit_dt_bucket_regression(half_a)
    atk_b, def_b = fit_dt_bucket_regression(half_b)

    print("\n  bucket  atk_half_A  atk_half_B  def_half_A  def_half_B")
    for k in range(1, 31):
        bl = f"b{k}"
        print(f"  {bl:>6}  {atk_a[bl]:+.3f}      {atk_b[bl]:+.3f}      "
              f"{def_a[bl]:+.3f}      {def_b[bl]:+.3f}")

    # The specific claim to check: is atk at b2-b5 BELOW both b1 and the
    # b15-b25 plateau, IN BOTH HALVES INDEPENDENTLY?
    verdicts = {}
    for label, coef in (("A", atk_a), ("B", atk_b)):
        dip = np.mean([coef[f"b{k}"] for k in (2, 3, 4, 5)])
        plateau = np.mean([coef[f"b{k}"] for k in range(15, 26)])
        b1 = coef["b1"]
        verdicts[label] = bool(dip < plateau and dip < b1)
        print(f"\n  half {label}: atk dip(b2-5) mean={dip:+.3f}   "
              f"atk plateau(b15-25) mean={plateau:+.3f}   atk b1={b1:+.3f}   "
              f"dip < plateau: {dip < plateau}   dip < b1: {dip < b1}")

    import json
    from pathlib import Path
    out = {
        "atk_a": atk_a, "atk_b": atk_b, "def_a": def_a, "def_b": def_b,
        "verdict_a": verdicts["A"], "verdict_b": verdicts["B"],
    }
    out_path = Path(__file__).resolve().parent / "preplant_dip_match_split.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nJSON written: {out_path}")


if __name__ == "__main__":
    main()
