"""Paired Impact-vs-ACS bootstrap on identical player-matches.

The findings doc originally argued from two non-overlapping marginal CIs, which
is not the direct test available here: both metrics are measured on the SAME
player-matches, so the DIFFERENCE can be bootstrapped directly. Cohort
eligibility is frozen from the original sample by DISTINCT match id, so this is
also immune to the duplicate-match eligibility defect (findings issue 3).
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath("."))

from app.db import SessionLocal
from app.services.impact_eval import (
    load_player_matches, load_player_matches_acs,
)
from app.services.stats_math import point_biserial

db = SessionLocal()
print("=" * 72)
print("A. Paired Impact vs ACS, same player-matches, frozen eligibility")
print("=" * 72)

imp = load_player_matches(db)
acs = load_player_matches_acs(db)
by_key_i = {(r.player_id, r.match_id): r for r in imp}
by_key_a = {(r.player_id, r.match_id): r for r in acs}
keys = sorted(set(by_key_i) & set(by_key_a))
print(f"paired player-matches: {len(keys)}  "
      f"(impact rows {len(imp)}, acs rows {len(acs)})")

# Freeze eligibility from the ORIGINAL sample, by DISTINCT match id.
matches_per_player = {}
for pid, mid in keys:
    matches_per_player.setdefault(pid, set()).add(mid)
elig2  = {p for p, m in matches_per_player.items() if len(m) >= 2}
elig9  = {p for p, m in matches_per_player.items() if len(m) >= 9}
print(f"frozen cohorts: >=2 distinct matches {len(elig2)} players, "
      f">=9 {len(elig9)} players")

rows_i = [by_key_i[k] for k in keys]
rows_a = [by_key_a[k] for k in keys]
groups = {}
for idx, (pid, mid) in enumerate(keys):
    groups.setdefault(mid, []).append(idx)
group_keys = sorted(groups)

def pooled_pb(rows, idx):
    v = np.array([rows[i].avg_impact for i in idx], dtype=float)
    w = np.array([1 if rows[i].won else 0 for i in idx], dtype=int)
    return point_biserial(v, w)

def within_pb(rows, idx, eligible):
    """Centre on each player's own mean; eligibility is FROZEN, and each
    player's rows must come from >=2 distinct matches in this sample."""
    per = {}
    for i in idx:
        r = rows[i]
        if r.player_id in eligible:
            per.setdefault(r.player_id, []).append(r)
    vals, labs = [], []
    for prows in per.values():
        if len({r.match_id for r in prows}) < 2:
            continue
        mean = float(np.mean([r.avg_impact for r in prows]))
        for r in prows:
            vals.append(r.avg_impact - mean)
            labs.append(1 if r.won else 0)
    if len(vals) < 10:
        return np.nan
    return point_biserial(np.array(vals), np.array(labs))

def paired_ci(fn, draws=2000, seed=0):
    """Bootstrap the DIFFERENCE, both metrics on the same resample."""
    rng = np.random.default_rng(seed)
    point = fn(rows_i, list(range(len(keys)))) - fn(rows_a, list(range(len(keys))))
    deltas = []
    for _ in range(draws):
        drawn = rng.choice(len(group_keys), size=len(group_keys), replace=True)
        idx = [i for g in drawn for i in groups[group_keys[g]]]
        a, b = fn(rows_i, idx), fn(rows_a, idx)
        if np.isfinite(a) and np.isfinite(b):
            deltas.append(a - b)
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    return point, lo, hi, len(deltas)

p, lo, hi, n = paired_ci(pooled_pb)
print(f"\npooled point-biserial   Impact - ACS = {p:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]  ({n} draws)")
p, lo, hi, n = paired_ci(lambda r, i: within_pb(r, i, elig2))
print(f"within-player centred   Impact - ACS = {p:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]  ({n} draws)")

# The unpaired numbers the doc reported, for comparison, and the repaired
# within-player point estimate under frozen distinct-match eligibility.
print(f"\nfor reference, frozen-eligibility point estimates:")
print(f"  within-player Impact = {within_pb(rows_i, list(range(len(keys))), elig2):.4f}")
print(f"  within-player ACS    = {within_pb(rows_a, list(range(len(keys))), elig2):.4f}")
