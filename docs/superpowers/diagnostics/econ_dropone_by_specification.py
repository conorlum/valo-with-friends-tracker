"""Every component's sign and drop-one cost, with and without the controls,
scored OUT OF FOLD so the comparison is honest."""
import numpy as np, sys, os
sys.path.insert(0, os.path.abspath("."))
from app.services.stats_math import back_transform, fit_logistic, predict_proba, standardize, weighted_log_loss
from app.services.impact_eval import stable_folds

d = np.load(r"C:/Users/User/AppData/Local/Temp/claude/C--Users-User-Documents-GitHub-valo-with-friends-tracker/0b4fe174-30b0-48fa-8d46-9389e837f3e2/scratchpad/t2_design.npz", allow_pickle=True)
X, y, w, names, mids = d["X"], d["y"], d["w"], list(d["names"]), d["match_ids"]
col = {n: i for i, n in enumerate(names)}
COMP = ["damage", "econ_impact", "time_impact", "swing_impact"]
CTRL = ["round_result", "score_diff_before", "attacking_is_team_a", "loadout_diff", "full_buy_count_diff"]

folds = stable_folds(sorted(set(int(m) for m in mids)), n_folds=5, seed=0)
fold_of = np.array([folds[int(m)] for m in mids])

def oof_loss(cols, l2=1.0):
    """Out-of-fold weighted log loss for a column set."""
    probs = np.empty(len(y))
    for f in range(5):
        te = fold_of == f
        tr = ~te
        design_tr = X[tr][:, [col[c] for c in cols]]
        design_te = X[te][:, [col[c] for c in cols]]
        s_tr, s_te, _, _ = standardize(design_tr, design_te)
        beta = fit_logistic(s_tr, y[tr], weights=w[tr], l2=l2)
        probs[te] = predict_proba(beta, s_te)
    return float(weighted_log_loss(probs, y, w))

def signs(cols, l2=1.0):
    out = {}
    for f in range(5):
        tr = fold_of != f
        design = X[tr][:, [col[c] for c in cols]]
        s, _, c_, sc_ = standardize(design, design)
        raw = back_transform(fit_logistic(s, y[tr], weights=w[tr], l2=l2), c_, sc_)
        for i, c in enumerate(cols):
            out.setdefault(c, []).append(raw[1 + i])
    return out

for label, base in [("WITHOUT controls (what Stage A reports)", COMP),
                    ("WITH the 5 controls (what the ladder uses)", COMP + CTRL)]:
    print("=" * 72)
    print(label)
    print("=" * 72)
    full = oof_loss(base)
    sg = signs(base)
    print(f"full model OOF weighted log loss = {full:.6f}")
    print(f"{'component':14s} {'coef (median)':>15s} {'neg folds':>10s} {'drop-one cost':>15s}")
    for c in COMP:
        without = oof_loss([x for x in base if x != c])
        vals = sg[c]
        neg = sum(1 for v in vals if v < 0)
        print(f"{c:14s} {np.median(vals):+15.8f} {neg:>7d}/5 {without - full:+15.6f}")
    print()
