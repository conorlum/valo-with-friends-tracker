"""Why the TOP-LEVEL within_player_centered CI misses its point estimate but
the `recurrent` cohort's CI does not. Mirrors the real cohort shape:
94.7% of players have exactly one match."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.abspath("."))
from app.services.impact_stage0 import (
    PlayerMatch, within_player_centered, _ci, _grouped_by_match, filter_cohort,
)
from app.services.stats_math import _resample

rng = np.random.default_rng(0)
rows, mid = [], 0
N_MATCHES = 1100
for m in range(N_MATCHES):
    for slot in range(10):
        # 5% of slots are recurrent players (small id pool), 95% are one-offs
        if rng.random() < 0.30:
            pid = int(rng.integers(0, 430))          # recurrent pool
        else:
            pid = 100000 + m * 10 + slot             # unique, one match only
        base = 200 + (pid % 37)
        won = rng.random() < 0.5
        rows.append(PlayerMatch(pid, m, base + (30 if won else -30) + rng.normal(0, 15), won))

recurrent = filter_cohort(rows, 2)
for label, subset in (("TOP-LEVEL (all rows)", rows), ("recurrent cohort", recurrent)):
    point = within_player_centered(subset)["point_biserial"]
    lo, hi = _ci(lambda r: within_player_centered(r)["point_biserial"], subset, draws=200, seed=0)
    print(f"{label:24s} point={point:.4f}  CI=[{lo:.4f}, {hi:.4f}]  inside={lo <= point <= hi}")

print("\n-- one resample of the TOP-LEVEL set --")
sample = _resample(_grouped_by_match(rows), np.random.default_rng(0))
flat = [r for g in sample for r in g]
by = {}
for r in flat:
    by.setdefault(r.player_id, []).append(r)
elig = [v for v in by.values() if len(v) >= 2]
degen = [v for v in elig if len({r.match_id for r in v}) == 1]
print(f"eligible players           : {len(elig)}")
print(f"  all rows from ONE match  : {len(degen)} ({100*len(degen)/len(elig):.1f}%)")
print(f"rows contributed by those  : {sum(len(v) for v in degen)} of "
      f"{sum(len(v) for v in elig)} ({100*sum(len(v) for v in degen)/sum(len(v) for v in elig):.1f}%)"
      "  -- every one centres to exactly 0.0")
print(f"true eligible players      : {len({r.player_id for r in recurrent})}")
