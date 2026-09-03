"""Why does every fitted Stage C candidate return a NEGATIVE damage
coefficient d, making b = q/d sign-flip the whole graph?

Nested designs on the SAME frozen T2 target and the SAME rows. Only the
columns change, so any sign change in d is attributable to the columns.
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath("."))

from app.db import SessionLocal
from app.services.impact_eval import PRIMARY_T2, load_all_observations
from app.services.kill_order_leverage import load_all_leverage
from app.services.kill_order_refit import align_target
from app.services.kill_order_curves import LATTICE
from app.services.kill_order_leverage import shipped_graph
from app.services.stats_math import standardize, back_transform, fit_logistic

db = SessionLocal()
team_rows, _player_rows = load_all_leverage(db)
match_ids = {r.match_id for r in team_rows}
observations = [o for o in load_all_observations(db, use_realized_swing=False)
                if o.match_id in match_ids]
db.close()

aligned = align_target(team_rows, observations, PRIMARY_T2)
X_ctrl = np.asarray(aligned.controls, dtype=float)
lev = np.asarray(aligned.leverage, dtype=float)
dmg = np.asarray(aligned.damage, dtype=float)
y, w = aligned.y, aligned.weights
shipped = np.asarray(shipped_graph(), dtype=float)
agg = lev @ shipped                      # the shipped graph's single leverage column

print(f"rows={len(y)}  controls={X_ctrl.shape[1]}  leverage cols={lev.shape[1]}")
print(f"corr(damage_diff, shipped-graph leverage) = {np.corrcoef(dmg, agg)[0,1]:.4f}")

def d_of(label, *blocks):
    design = np.column_stack([b for b in blocks if b is not None and np.size(b)])
    s, _, c, sc = standardize(design, design)
    beta = fit_logistic(s, y, weights=w, l2=1.0)
    raw = back_transform(beta, c, sc)
    # +1 for the intercept that back_transform prepends
    d = raw[1 + X_ctrl.shape[1]]
    print(f"{label:52s} d = {d:+.7f}   ({'POSITIVE' if d > 0 else 'NEGATIVE'})")
    return d

d_of("controls + damage", X_ctrl, dmg.reshape(-1, 1))
d_of("controls + damage + shipped leverage (1 col)", X_ctrl, dmg.reshape(-1, 1), agg.reshape(-1, 1))
d_of("controls + damage + 26 free leverage cols", X_ctrl, dmg.reshape(-1, 1), lev)
d_of("controls + damage + 25 lattice cols", X_ctrl, dmg.reshape(-1, 1), lev[:, LATTICE])
