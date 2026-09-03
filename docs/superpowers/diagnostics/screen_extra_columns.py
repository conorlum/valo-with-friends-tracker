"""SCREENING TEST for item 5: do the seven already-persisted but never-fitted
impact_scores columns add held-out predictive information beyond the four
components that are fitted today?

Uses the STORED columns. That is legitimate for these specific columns: the
documented leakage is in `_realized_econ_swing_factor`, which feeds
`swing_impact` only. clutch_* is raw kill_order_bonus (no factor at all),
post_plant_* uses the time factor, econ_* uses the econ factor -- all clean.
No swing-derived extra column is used here.

Method: the frozen T2 target, the same stable folds, one extra control-ladder
rung. Paired match-clustered bootstrap of the weighted log-loss delta.
"""
import os, sys
import numpy as np
from sqlalchemy import text
sys.path.insert(0, os.path.abspath("."))

from app.db import SessionLocal
from app.services.impact_eval import (
    FEATURE_COMPONENTS, PRIMARY_T2, build_target, controls_for, stable_folds,
)
from app.services.impact_eval_cache import load_observations
from app.services.stats_math import (
    fit_logistic, predict_proba, standardize, weighted_log_loss,
)

EXTRA = ["clutch_kill", "clutch_death", "post_plant_kill", "post_plant_death",
         "econ_kill", "econ_death", "traded_teammate", "traded_by_teammate"]

db = SessionLocal()
cols = ",\n  ".join(
    f"sum(case when mp.team='TEAM_1' then i.{c} else -i.{c} end)::float as {c}" for c in EXTRA
)
rows = db.execute(text(f"""
select i.round_id, {cols}
from impact_scores i
join match_players mp on mp.id = i.match_player_id
group by i.round_id
""")).mappings().all()
db.close()
extra_by_round = {int(r["round_id"]): np.array([float(r[c]) for c in EXTRA]) for r in rows}
print(f"extra columns loaded for {len(extra_by_round)} rounds: {EXTRA}")

obs = load_observations(None)
obs = [o for o in obs if o.round_id in extra_by_round]
print(f"observations with extras: {len(obs)}")

ctrl = list(controls_for(PRIMARY_T2))
names = FEATURE_COMPONENTS + ctrl
ds = build_target(obs, PRIMARY_T2, names)

# build_target collapses rounds; re-derive each target row's source round so
# the extra columns line up. The builder keeps source order within a match,
# so rebuild the same iteration and record round_ids.
from app.services.impact_eval import group_by_match, _half_of
source_rounds = []
for match_id, mobs in group_by_match(obs).items():
    by_number = {o.round_number: o for o in mobs}
    for o in mobs:
        if o.is_terminal:
            continue
        num = den = 0.0
        for step in range(1, PRIMARY_T2.k + 1):
            f = by_number.get(o.round_number + step)
            if f is None or _half_of(f.round_number) != _half_of(o.round_number):
                break
            if f.round_won_by_team_a is None:
                continue
            w_ = PRIMARY_T2.gamma ** (step - 1)
            num += w_ * (1.0 if f.round_won_by_team_a else 0.0); den += w_
        if PRIMARY_T2.match_weight > 0 and o.round_number <= 12 and o.match_won_by_team_a is not None:
            num += PRIMARY_T2.match_weight * (1.0 if o.match_won_by_team_a else 0.0)
            den += PRIMARY_T2.match_weight
        if den == 0:
            continue
        source_rounds.append(o.round_id)
assert len(source_rounds) == len(ds.y), (len(source_rounds), len(ds.y))
X_extra = np.array([extra_by_round[r] for r in source_rounds], dtype=float)
print(f"aligned design: {ds.X.shape} + extras {X_extra.shape}")

folds = stable_folds(sorted({int(m) for m in ds.match_ids}), n_folds=5, seed=0)
fold_of = np.array([folds[int(m)] for m in ds.match_ids])

def oof(X):
    p = np.empty(len(ds.y))
    for f in range(5):
        te = fold_of == f; tr = ~te
        best, best_loss = None, np.inf
        for l2 in (0.01, 0.1, 1.0, 10.0):
            s_tr, s_te, _, _ = standardize(X[tr], X[te])
            b = fit_logistic(s_tr, ds.y[tr], weights=ds.w[tr], l2=l2)
            loss = weighted_log_loss(predict_proba(b, s_tr), ds.y[tr], ds.w[tr])
            if loss < best_loss:
                best, best_loss = (b, s_te), loss
        p[te] = predict_proba(*best)
    return p

base = np.column_stack([ds.X])
wide = np.column_stack([ds.X, X_extra])
p_base, p_wide = oof(base), oof(wide)
l_base = weighted_log_loss(p_base, ds.y, ds.w)
l_wide = weighted_log_loss(p_wide, ds.y, ds.w)
print(f"\n4 components + controls        : {l_base:.6f}")
print(f"+ the 8 never-fitted columns   : {l_wide:.6f}")

groups = {}
for i, m in enumerate(ds.match_ids):
    groups.setdefault(int(m), []).append(i)
keys = sorted(groups)
rng = np.random.default_rng(0)
deltas = []
for _ in range(2000):
    drawn = [keys[i] for i in rng.integers(0, len(keys), len(keys))]
    idx = [i for k in drawn for i in groups[k]]
    deltas.append(weighted_log_loss(p_wide[idx], ds.y[idx], ds.w[idx])
                  - weighted_log_loss(p_base[idx], ds.y[idx], ds.w[idx]))
lo, hi = np.percentile(deltas, [2.5, 97.5])
d = l_wide - l_base
print(f"\ndelta = {d:+.6f}  95% CI [{lo:+.6f}, {hi:+.6f}]   "
      f"{'IMPROVES' if hi < 0 else ('WORSE' if lo > 0 else 'no detectable difference')}")
print("(negative = the extra columns improved held-out prediction)")
print(f"\nfor scale, the existing ladder step 3->4 (adding all four components) = -0.003501")
